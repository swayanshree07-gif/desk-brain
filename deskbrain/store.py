"""SQLite index of the desk, kept in ``data/`` (git-ignored), with incremental updates.

Tables: pages, headings, chunks, links (raw + resolved), doubts, findings, meta.

Incremental re-index (``Index.update``):
  * a page whose (mtime, size) is unchanged is skipped without being read;
  * a page whose mtime changed but whose content hash is the same only gets its
    mtime refreshed (not counted as changed);
  * new / changed pages are re-parsed and their rows replaced;
  * pages that disappeared (or became excluded) are deleted with all their rows.
If anything changed, all links are re-resolved from the stored headings and raw
links (no page files are re-read), since a new page can fix a broken link elsewhere.

The desk is only read, never written. Every read goes through ``scanner.read_page``,
which refuses excluded paths.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .chunker import Chunk, chunk_page
from .links import LinkResolver
from .parser import Heading, Link, Page, parse
from .scanner import is_allowed, read_page, scan

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS pages (
    rel TEXT PRIMARY KEY, title TEXT NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL,
    sha1 TEXT NOT NULL, indexed_at REAL NOT NULL, first_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS headings (
    rel TEXT NOT NULL, ord INTEGER NOT NULL, level INTEGER NOT NULL, text TEXT NOT NULL, line INTEGER NOT NULL,
    PRIMARY KEY (rel, ord)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, rel TEXT NOT NULL, ord INTEGER NOT NULL, heading TEXT NOT NULL, level INTEGER NOT NULL,
    breadcrumb TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
    text TEXT NOT NULL, sha1 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_rel ON chunks(rel);
CREATE TABLE IF NOT EXISTS links (
    src TEXT NOT NULL, ord INTEGER NOT NULL, line INTEGER NOT NULL, raw TEXT NOT NULL, text TEXT NOT NULL,
    kind TEXT NOT NULL, status TEXT, target TEXT, anchor TEXT,
    PRIMARY KEY (src, ord)
);
CREATE INDEX IF NOT EXISTS links_target ON links(target);
CREATE TABLE IF NOT EXISTS doubts (
    rel TEXT NOT NULL, line INTEGER NOT NULL, question TEXT NOT NULL, status TEXT NOT NULL,
    asked TEXT, context TEXT, answer TEXT, section TEXT,
    PRIMARY KEY (rel, line)
);
CREATE TABLE IF NOT EXISTS findings (
    rel TEXT NOT NULL, line INTEGER NOT NULL, date TEXT NOT NULL, text TEXT NOT NULL, section TEXT,
    PRIMARY KEY (rel, line)
);
"""

PAGE_TABLES = (("headings", "rel"), ("chunks", "rel"), ("links", "src"), ("doubts", "rel"), ("findings", "rel"))


@dataclass
class UpdateStats:
    scanned: int = 0
    unchanged: int = 0       # skipped by (mtime, size), file not read
    touched: int = 0         # mtime changed, same content: only mtime refreshed
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    links_resolved: int = 0
    seconds: float = 0.0

    @property
    def n_changed(self) -> int:
        """Pages whose content was (re)indexed or removed."""
        return len(self.added) + len(self.changed) + len(self.removed)

    def summary(self) -> str:
        return (f"{self.scanned} pages scanned: {len(self.added)} added, {len(self.changed)} changed, "
                f"{len(self.removed)} removed, {self.unchanged + self.touched} unchanged; "
                f"{self.links_resolved} links resolved; {self.seconds:.2f}s")


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


class Index:
    """The desk index in one SQLite file."""

    def __init__(self, db_path: Path | str, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.db_path = Path(db_path)
        if str(db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # Never let the index land inside the desk (the desk is read-only for us).
            dbp = self.db_path.resolve()
            if dbp == self.root or self.root in dbp.parents:
                raise ValueError(f"index must not be inside the desk: {dbp}")
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        row = None
        try:
            row = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        except sqlite3.OperationalError:
            pass
        if row is not None and int(row[0]) != SCHEMA_VERSION:
            # Old layout: drop everything and rebuild from the desk.
            for (name,) in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                self.db.execute(f"DROP TABLE IF EXISTS {name}")
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        old_root = self.db.execute("SELECT value FROM meta WHERE key='root'").fetchone()
        if old_root is not None and old_root[0] != str(self.root):
            # Index was built for another desk: start over.
            for t in ("pages", *(t for t, _ in PAGE_TABLES)):
                self.db.execute(f"DELETE FROM {t}")
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('root', ?)", (str(self.root),))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing -------------------------------------------------------------
    def _delete_page(self, rel: str) -> None:
        self.db.execute("DELETE FROM pages WHERE rel=?", (rel,))
        for table, col in PAGE_TABLES:
            self.db.execute(f"DELETE FROM {table} WHERE {col}=?", (rel,))

    def _store_page(self, page: Page, mtime: float, size: int, sha1: str, first_seen: float, now: float) -> None:
        rel = page.rel
        for table, col in PAGE_TABLES:
            self.db.execute(f"DELETE FROM {table} WHERE {col}=?", (rel,))
        self.db.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?)",
                        (rel, page.title, mtime, size, sha1, now, first_seen))
        self.db.executemany("INSERT INTO headings VALUES (?,?,?,?,?)",
                            [(rel, i, h.level, h.text, h.line) for i, h in enumerate(page.headings)])
        self.db.executemany(
            "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(c.id, rel, i, c.heading, c.level, json.dumps(c.breadcrumb, ensure_ascii=False),
              c.start_line, c.end_line, c.text, _sha1(c.text)) for i, c in enumerate(chunk_page(page))])
        self.db.executemany("INSERT INTO links (src, ord, line, raw, text, kind) VALUES (?,?,?,?,?,?)",
                            [(rel, i, l.line, l.target, l.text, l.kind) for i, l in enumerate(page.links)])
        self.db.executemany(
            "INSERT OR REPLACE INTO doubts VALUES (?,?,?,?,?,?,?,?)",
            [(rel, d.line, d.question, d.status, d.asked, d.context, d.answer, d.section) for d in page.doubts])
        self.db.executemany("INSERT OR REPLACE INTO findings VALUES (?,?,?,?,?)",
                            [(rel, f.line, f.date, f.text, f.section) for f in page.findings])

    def update(self) -> UpdateStats:
        """Bring the index in line with the desk. Only new/changed pages are read."""
        t0 = time.perf_counter()
        now = time.time()
        st = UpdateStats()
        files = scan(self.root)
        st.scanned = len(files)
        known = {r["rel"]: r for r in self.db.execute("SELECT rel, mtime, size, sha1, first_seen FROM pages")}
        seen = set()
        with self.db:
            for f in files:
                seen.add(f.rel)
                old = known.get(f.rel)
                if old is not None and old["mtime"] == f.mtime and old["size"] == f.size:
                    st.unchanged += 1
                    continue
                text = read_page(self.root, f.rel)
                sha = _sha1(text)
                if old is not None and old["sha1"] == sha:
                    self.db.execute("UPDATE pages SET mtime=?, size=? WHERE rel=?", (f.mtime, f.size, f.rel))
                    st.touched += 1
                    continue
                page = parse(text, f.rel)
                self._store_page(page, f.mtime, f.size, sha, old["first_seen"] if old else now, now)
                (st.changed if old is not None else st.added).append(f.rel)
            for rel in sorted(set(known) - seen):
                self._delete_page(rel)
                st.removed.append(rel)
            if st.n_changed:
                st.links_resolved = self._resolve_links()
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_update', ?)", (str(now),))
        st.seconds = time.perf_counter() - t0
        return st

    def _resolve_links(self) -> int:
        """Re-resolve every stored link from stored titles/headings (no file reads)."""
        pages = self.light_pages()
        resolved = LinkResolver(pages, self.root).resolve_all()
        # resolve_all walks each page's links in stored order, so ord = position within src.
        rows, counters = [], {}
        for r in resolved:
            i = counters.get(r.src, 0)
            counters[r.src] = i + 1
            rows.append((r.status, r.target, r.anchor, r.src, i))
        self.db.executemany("UPDATE links SET status=?, target=?, anchor=? WHERE src=? AND ord=?", rows)
        return len(resolved)

    # -- reading -------------------------------------------------------------
    def light_pages(self) -> list[Page]:
        """Pages rebuilt from the index with title, headings and links (no text)."""
        pages = {r["rel"]: Page(rel=r["rel"], title=r["title"])
                 for r in self.db.execute("SELECT rel, title FROM pages ORDER BY rel")}
        for r in self.db.execute("SELECT rel, level, text, line FROM headings ORDER BY rel, ord"):
            pages[r["rel"]].headings.append(Heading(r["level"], r["text"], r["line"]))
        for r in self.db.execute("SELECT src, raw, text, line, kind FROM links ORDER BY src, ord"):
            pages[r["src"]].links.append(Link(r["raw"], r["text"], r["line"], r["kind"]))
        return list(pages.values())

    def chunks(self, rel: str | None = None) -> list[Chunk]:
        q = "SELECT * FROM chunks" + (" WHERE rel=?" if rel else "") + " ORDER BY rel, ord"
        return [Chunk(id=r["id"], rel=r["rel"], heading=r["heading"], level=r["level"],
                      breadcrumb=json.loads(r["breadcrumb"]), start_line=r["start_line"],
                      end_line=r["end_line"], text=r["text"])
                for r in self.db.execute(q, (rel,) if rel else ())]

    def counts(self) -> dict[str, int]:
        out = {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("pages", "chunks", "links", "doubts", "findings")}
        out["open_doubts"] = self.db.execute("SELECT COUNT(*) FROM doubts WHERE status='open'").fetchone()[0]
        out["bad_links"] = self.db.execute(
            "SELECT COUNT(*) FROM links WHERE status IN ('broken','bad-anchor','outside')").fetchone()[0]
        return out

    def page_rels(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT rel FROM pages ORDER BY rel")]

    def check_privacy(self) -> list[str]:
        """Rels in the index that should never be there (must be empty)."""
        bad = [r for r in self.page_rels() if not is_allowed(r)]
        bad += [r[0] for r in self.db.execute("SELECT DISTINCT rel FROM chunks") if not is_allowed(r[0])]
        return bad


def default_db_path() -> Path:
    from .config import DATA_DIR
    return DATA_DIR / "index.sqlite"
