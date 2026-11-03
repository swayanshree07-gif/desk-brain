"""Resolve links between desk pages and find broken ones.

Handles relative ``.md`` links, folder links (``dir/`` -> ``dir/README.md``),
``#anchors`` (GitHub-style heading slugs), ``[[wikilinks]]`` (by file name or
page title, case-insensitive) and links to non-Markdown files (assets).

Privacy: a link that points into an excluded area (``activity/``, ``Personal/``,
...) gets status ``"excluded"`` and its target is never looked up or exposed.
The filesystem is only touched (``exists``/``is_dir``) for allowed, in-desk paths,
and never written to.
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from .config import EXCLUDED_ANY_DIRS, EXCLUDED_TOP_DIRS
from .parser import Link, Page

# Status values
OK = "ok"                  # page (and anchor, if any) exists
FOLDER = "folder"          # existing folder without README.md / index.md
ASSET = "asset"            # existing non-Markdown file (pdf, png, ...)
EXTERNAL = "external"      # http:, mailto:, ...
BROKEN = "broken"          # target page / file does not exist
BAD_ANCHOR = "bad-anchor"  # page exists but has no such heading
EXCLUDED = "excluded"      # points into a private / excluded area (never resolved)
OUTSIDE = "outside"        # escapes the desk root

BAD_STATUSES = frozenset({BROKEN, BAD_ANCHOR, OUTSIDE})
FOLDER_INDEX_NAMES = ("README.md", "readme.md", "index.md")


def slugify(heading: str) -> str:
    """GitHub-style anchor slug: lowercase, drop punctuation/emoji, spaces -> '-'."""
    s = heading.strip().lower()
    out = []
    for ch in s:
        if ch == " " or ch == "-" or ch == "_":
            out.append("-" if ch == " " else ch)
        elif unicodedata.category(ch)[0] in ("L", "N"):
            out.append(ch)
    return "".join(out)


def anchor_matches(anchor: str, slugs: set[str]) -> bool:
    """Lenient anchor check: compare slugs with edge hyphens trimmed, so '#findings'
    matches the heading '🔍 Findings' (GitHub slug '-findings')."""
    a = slugify(unquote(anchor)).strip("-")
    return any(s.strip("-") == a for s in slugs)


def page_slugs(page: Page) -> set[str]:
    """All anchor slugs of a page, with GitHub's -1, -2 suffixes for duplicates."""
    seen: dict[str, int] = {}
    out: set[str] = set()
    for h in page.headings:
        base = slugify(h.text)
        n = seen.get(base, 0)
        out.add(base if n == 0 else f"{base}-{n}")
        seen[base] = n + 1
    return out


def _is_private(rel: str) -> bool:
    """True if a desk-relative path (file or folder) lies in an excluded area."""
    parts = PurePosixPath(rel).parts
    if not parts:
        return False
    return parts[0] in EXCLUDED_TOP_DIRS or any(p in EXCLUDED_ANY_DIRS or p.startswith(".") for p in parts)


@dataclass
class ResolvedLink:
    src: str                 # page the link is on
    line: int
    raw: str                 # target as written
    text: str
    kind: str                # md | wiki | image | external
    status: str              # see constants above
    target: str | None = None  # desk-relative page/file/folder (None when excluded/unknown)
    anchor: str | None = None

    @property
    def is_bad(self) -> bool:
        return self.status in BAD_STATUSES


class LinkResolver:
    """Resolves links against a set of parsed pages (plus read-only file checks)."""

    def __init__(self, pages: list[Page], root: Path | None = None):
        self.root = Path(root).resolve() if root else None
        self.pages = {p.rel: p for p in pages}
        self._slugs = {p.rel: page_slugs(p) for p in pages}
        self._by_stem: dict[str, list[str]] = {}
        self._by_title: dict[str, list[str]] = {}
        for p in pages:
            stem = PurePosixPath(p.rel).stem.lower()
            self._by_stem.setdefault(stem, []).append(p.rel)
            if stem == "readme" and "/" in p.rel:  # folder README: also known by folder name
                self._by_stem.setdefault(PurePosixPath(p.rel).parent.name.lower(), []).append(p.rel)
            self._by_title.setdefault(p.title.strip().lower(), []).append(p.rel)

    # -- helpers -----------------------------------------------------------
    def _exists(self, rel: str) -> tuple[bool, bool]:
        """(exists, is_dir) for an allowed in-desk path; (False, False) without a root."""
        if self.root is None:
            return False, False
        p = self.root / rel
        if p.is_symlink():
            return False, False
        return p.exists(), p.is_dir()

    def _check_anchor(self, rel: str, anchor: str | None) -> str:
        if not anchor:
            return OK
        return OK if anchor_matches(anchor, self._slugs.get(rel, set())) else BAD_ANCHOR

    @staticmethod
    def _pick(cands: list[str], src: str) -> str:
        """Prefer a candidate in the same folder, then the shortest path."""
        src_dir = posixpath.dirname(src)
        return sorted(cands, key=lambda r: (posixpath.dirname(r) != src_dir, len(r), r))[0]

    # -- main --------------------------------------------------------------
    def resolve(self, src: str, link: Link) -> ResolvedLink:
        rl = ResolvedLink(src=src, line=link.line, raw=link.target, text=link.text, kind=link.kind, status=BROKEN)
        if link.kind == "external":
            rl.status = EXTERNAL
            return rl
        if link.kind == "wiki":
            return self._resolve_wiki(rl)
        return self._resolve_path(rl)

    def _resolve_wiki(self, rl: ResolvedLink) -> ResolvedLink:
        name, _, anchor = rl.raw.partition("#")
        rl.anchor = anchor or None
        key = name.strip().lower().removesuffix(".md")
        if "/" in key:  # [[folder/page]] -> try as a path from the desk root
            rel = posixpath.normpath(key + ".md")
            cands = [r for r in self.pages if r.lower() == rel]
        else:
            cands = self._by_stem.get(key) or self._by_title.get(key) or []
        if not cands:
            rl.status = BROKEN
            return rl
        rl.target = self._pick(cands, rl.src)
        rl.status = self._check_anchor(rl.target, rl.anchor)
        return rl

    def _resolve_path(self, rl: ResolvedLink) -> ResolvedLink:
        raw = rl.raw
        path, _, anchor = raw.partition("#")
        path = path.split("?", 1)[0]
        path = unquote(path)
        rl.anchor = anchor or None
        if not path:  # "#anchor" on the same page
            rl.target = rl.src
            rl.status = self._check_anchor(rl.src, rl.anchor)
            return rl
        if path.startswith("/"):
            joined = path.lstrip("/")  # treat as desk-root-relative
        else:
            joined = posixpath.join(posixpath.dirname(rl.src), path)
        norm = posixpath.normpath(joined)
        if norm == ".." or norm.startswith("../") or norm.startswith("/"):
            rl.status = OUTSIDE
            return rl
        if norm == ".":
            norm = ""
        if norm and _is_private(norm):
            rl.status = EXCLUDED
            return rl

        # Known page?
        if norm in self.pages:
            rl.target = norm
            rl.status = self._check_anchor(norm, rl.anchor)
            return rl
        # Folder link -> its README / index page.
        for idx in FOLDER_INDEX_NAMES:
            cand = f"{norm}/{idx}" if norm else idx
            if cand in self.pages:
                rl.target = cand
                rl.status = self._check_anchor(cand, rl.anchor)
                return rl
        exists, is_dir = self._exists(norm) if norm else (True, True)
        if is_dir:
            rl.target = norm + "/" if norm else ""
            rl.status = FOLDER
        elif exists and not norm.lower().endswith(".md"):
            rl.target = norm
            rl.status = ASSET
        else:
            rl.target = norm
            rl.status = BROKEN
        return rl

    def resolve_all(self) -> list[ResolvedLink]:
        out = []
        for rel in sorted(self.pages):
            for link in self.pages[rel].links:
                out.append(self.resolve(rel, link))
        return out


def broken_links(resolved: list[ResolvedLink]) -> list[ResolvedLink]:
    return [r for r in resolved if r.is_bad]
