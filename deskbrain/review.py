"""Spaced repetition for flashcards (Day 20): SM-2 scheduling + review state in SQLite.

The cards themselves are derived from the desk on every request (cards.py); this module
stores only *review state*, keyed by card id, in ``data/flashcards.sqlite`` (next to the
index file; tests use tmp dirs). Nothing here ever touches the desk.

SM-2 (Wozniak 1990), grades 0–5:
  grade >= 3 (recalled): reps 0 → interval 1 day, reps 1 → 6 days, then round(interval * ease);
                         reps += 1; ease += 0.1 - (5-g)*(0.08 + (5-g)*0.02), floor 1.3.
  grade <  3 (lapse):    reps = 0, interval = 1 day, lapses += 1, ease unchanged
                         (as in the original algorithm).
  due = review day + interval. A card is due when due <= today.

Queue = due reviews (most overdue first) + up to ``new_per_day`` never-seen cards
(in desk order), minus the new cards already started today.

Card lifecycle (Day 22, ``ReviewStore.reconcile``), run before every queue/grade:
  * For every card with review state we keep a snapshot (rel, date, kind, front, back, hash)
    in ``info``. If the card's hash changed (answer or text edited, same id), the old text
    goes to ``history`` (event "edited") and the snapshot is updated; the schedule is kept.
  * A card whose id vanished ("orphan") is matched to a new, stateless card:
    1. same hash anywhere (page renamed/moved, duplicate suffix shifted)  → "renamed";
    2. same page, same kind family (qa vs finding), same date for findings, and text
       similarity (difflib ratio of front+back) >= MATCH_RATIO                → "renamed".
    Best pairs first, each card used once. State, grade log and history move to the new id.
  * Orphans left unmatched are retired (``info.retired`` = today, event "retired"); their
    schedule is kept but they never show up in the queue. If the id comes back, "revived".
    Privacy: a retired card's stored text (snapshot + history) is wiped, since its page may
    have been moved into a private folder; only id, rel, kind, date and schedule remain.
"""

from __future__ import annotations

import difflib
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

START_EASE = 2.5
MIN_EASE = 1.3
NEW_PER_DAY = 20
MATCH_RATIO = 0.7   # min text similarity to carry review state over to an edited card

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    card_id   TEXT PRIMARY KEY,
    ease      REAL NOT NULL,
    interval  INTEGER NOT NULL,
    reps      INTEGER NOT NULL,
    lapses    INTEGER NOT NULL,
    due       TEXT NOT NULL,      -- ISO date
    last      TEXT NOT NULL,      -- ISO date of last review
    first     TEXT NOT NULL,      -- ISO date of first review
    card_hash TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id   TEXT NOT NULL,
    day       TEXT NOT NULL,
    grade     INTEGER NOT NULL,
    ease      REAL NOT NULL,
    interval  INTEGER NOT NULL,
    card_hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS log_card ON log(card_id);
CREATE TABLE IF NOT EXISTS info (     -- last seen version of every card that has state
    card_id   TEXT PRIMARY KEY,
    rel       TEXT NOT NULL,
    date      TEXT,
    kind      TEXT NOT NULL,
    front     TEXT NOT NULL,
    back      TEXT NOT NULL,
    hash      TEXT NOT NULL,
    retired   TEXT                -- ISO date when the card left the desk, NULL while live
);
CREATE TABLE IF NOT EXISTS history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id   TEXT NOT NULL,      -- id at the time of the event (renames update it)
    day       TEXT NOT NULL,
    event     TEXT NOT NULL,      -- edited | renamed | retired | revived
    old_id    TEXT,
    front     TEXT,               -- text before the event
    back      TEXT
);
CREATE INDEX IF NOT EXISTS history_card ON history(card_id);
"""


def _family(kind: str) -> str:
    return "qa" if kind == "qa" else "finding"


def _text(front: str, back: str) -> str:
    return " ".join(f"{front} {back}".split()).casefold()


def similarity(a: dict, b: dict) -> float:
    """Text similarity of two cards (or snapshots), 0..1."""
    return difflib.SequenceMatcher(None, _text(a["front"], a["back"]), _text(b["front"], b["back"])).ratio()


@dataclass
class CardState:
    ease: float = START_EASE
    interval: int = 0
    reps: int = 0
    lapses: int = 0
    due: str = ""
    last: str = ""
    first: str = ""


def sm2(st: CardState, grade: int, today: date) -> CardState:
    """Pure SM-2 step: the state after reviewing a card with ``grade`` (0–5) on ``today``."""
    if not isinstance(grade, int) or isinstance(grade, bool) or not 0 <= grade <= 5:
        raise ValueError("grade must be an integer 0–5")
    ease, interval, reps, lapses = st.ease, st.interval, st.reps, st.lapses
    if grade < 3:
        reps, interval, lapses = 0, 1, lapses + 1
    else:
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
        reps += 1
        q = 5 - grade
        ease = max(MIN_EASE, round(ease + 0.1 - q * (0.08 + q * 0.02), 4))
    iso = today.isoformat()
    return CardState(ease=ease, interval=interval, reps=reps, lapses=lapses,
                     due=(today + timedelta(days=interval)).isoformat(), last=iso,
                     first=st.first or iso)


def default_review_path(db_path: Path) -> Path:
    return Path(db_path).parent / "flashcards.sqlite"


class ReviewStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        # Overwrite deleted content, so wiped card text does not linger in free pages.
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def get(self, card_id: str) -> CardState | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM state WHERE card_id=?", (card_id,)).fetchone()
        if not r:
            return None
        return CardState(ease=r["ease"], interval=r["interval"], reps=r["reps"], lapses=r["lapses"],
                         due=r["due"], last=r["last"], first=r["first"])

    def all(self) -> dict[str, CardState]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM state").fetchall()
        return {r["card_id"]: CardState(ease=r["ease"], interval=r["interval"], reps=r["reps"],
                                        lapses=r["lapses"], due=r["due"], last=r["last"], first=r["first"])
                for r in rows}

    def grade(self, card_id: str, grade: int, today: date | None = None, card_hash: str = "",
              card: dict | None = None) -> CardState:
        """Apply one grade. ``card`` (the current card dict) refreshes the lifecycle snapshot."""
        today = today or date.today()
        card_hash = card_hash or (card or {}).get("hash", "")
        with self.lock:
            if card is not None:
                self._snapshot(card, today)
            new = sm2(self.get(card_id) or CardState(), grade, today)
            self.db.execute(
                "INSERT OR REPLACE INTO state (card_id, ease, interval, reps, lapses, due, last, first, card_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (card_id, new.ease, new.interval, new.reps, new.lapses, new.due, new.last, new.first, card_hash))
            self.db.execute("INSERT INTO log (card_id, day, grade, ease, interval, card_hash) VALUES (?,?,?,?,?,?)",
                            (card_id, today.isoformat(), grade, new.ease, new.interval, card_hash))
            self.db.commit()
        return new

    # ---- lifecycle (Day 22) ----

    def _info(self) -> dict[str, sqlite3.Row]:
        return {r["card_id"]: r for r in self.db.execute("SELECT * FROM info")}

    def _snapshot(self, c: dict, today: date) -> None:
        """Insert/refresh the info row for a live card, logging an edit if its text changed."""
        old = self.db.execute("SELECT * FROM info WHERE card_id=?", (c["id"],)).fetchone()
        if old is not None and old["hash"] != c["hash"]:
            self._event(c["id"], today, "edited", front=old["front"], back=old["back"])
        if old is not None and old["retired"]:
            self._event(c["id"], today, "revived")
        self.db.execute(
            "INSERT OR REPLACE INTO info (card_id, rel, date, kind, front, back, hash, retired)"
            " VALUES (?,?,?,?,?,?,?,NULL)",
            (c["id"], c.get("rel", ""), c.get("date"), c.get("kind", ""), c.get("front", ""),
             c.get("back", ""), c.get("hash", "")))

    def _event(self, card_id: str, today: date, event: str, old_id: str | None = None,
               front: str | None = None, back: str | None = None) -> None:
        self.db.execute("INSERT INTO history (card_id, day, event, old_id, front, back) VALUES (?,?,?,?,?,?)",
                        (card_id, today.isoformat(), event, old_id, front, back))

    def _rename(self, old_id: str, new_id: str, today: date, old_row) -> None:
        for table in ("state", "log", "history"):
            self.db.execute(f"UPDATE {table} SET card_id=? WHERE card_id=?", (new_id, old_id))
        self.db.execute("DELETE FROM info WHERE card_id=?", (old_id,))
        self._event(new_id, today, "renamed", old_id=old_id,
                    front=old_row["front"] if old_row else None, back=old_row["back"] if old_row else None)

    def reconcile(self, cards: list[dict], today: date | None = None) -> dict:
        """Bring review state in line with the current cards (see module docstring).

        Returns {"edited", "renamed", "retired", "revived"} counts for this run.
        Cheap when nothing changed: only cards that have state are looked at.
        """
        today = today or date.today()
        out = {"edited": 0, "renamed": 0, "retired": 0, "revived": 0}
        with self.lock:
            states = {r[0] for r in self.db.execute("SELECT card_id FROM state")}
            if not states:
                return out
            info = self._info()
            by_id = {c["id"]: c for c in cards}
            # 1. live cards with state: refresh snapshot, note edits/revivals.
            for cid in states & by_id.keys():
                c, old = by_id[cid], info.get(cid)
                if old is None or old["hash"] != c["hash"] or old["retired"]:
                    if old is not None and old["hash"] != c["hash"]:
                        out["edited"] += 1
                    if old is not None and old["retired"]:
                        out["revived"] += 1
                    self._snapshot(c, today)
            # 2. orphans (state, not on the desk, not yet retired) → match to stateless cards.
            orphans = [cid for cid in states - by_id.keys() if not (info.get(cid) and info[cid]["retired"])]
            fresh = [c for c in cards if c["id"] not in states]
            pairs = []
            for oid in orphans:
                o = info.get(oid)
                if o is None:      # state from before Day 22: nothing to match on
                    continue
                for c in fresh:
                    if c["hash"] == o["hash"]:
                        pairs.append((2.0, oid, c))
                    elif (c["rel"] == o["rel"] and _family(c["kind"]) == _family(o["kind"])
                          and (o["kind"] == "qa" or (c.get("date") or "") == (o["date"] or ""))):
                        r = similarity(c, o)
                        if r >= MATCH_RATIO:
                            pairs.append((r, oid, c))
            pairs.sort(key=lambda p: (-p[0], p[1], p[2]["id"]))
            used_old, used_new = set(), set()
            for _, oid, c in pairs:
                if oid in used_old or c["id"] in used_new:
                    continue
                used_old.add(oid)
                used_new.add(c["id"])
                self._rename(oid, c["id"], today, info.get(oid))
                old = info[oid]
                if old["hash"] != c["hash"]:
                    self._event(c["id"], today, "edited", front=old["front"], back=old["back"])
                self._snapshot(c, today)
                out["renamed"] += 1
            # 3. the rest retire (state is kept in case the card comes back).
            for oid in orphans:
                if oid in used_old:
                    continue
                # Privacy: forget the text of a retired card (its page may have moved into
                # Personal/ or been deleted on purpose). Only rel/kind/date and the schedule stay.
                self.db.execute("UPDATE history SET front=NULL, back=NULL WHERE card_id=?", (oid,))
                if oid in info:
                    self.db.execute("UPDATE info SET retired=?, front='', back='' WHERE card_id=?",
                                    (today.isoformat(), oid))
                else:
                    self.db.execute("INSERT INTO info (card_id, rel, date, kind, front, back, hash, retired)"
                                    " VALUES (?, '', NULL, '', '', '', '', ?)", (oid, today.isoformat()))
                self._event(oid, today, "retired")
                out["retired"] += 1
            self.db.commit()
            if out["retired"]:
                # Flush the WAL too, so old text is not left in data/flashcards.sqlite-wal.
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return out

    def retired(self) -> list[dict]:
        """Retired cards (newest first). Their text is not kept (see reconcile)."""
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT i.card_id AS id, i.rel, i.date, i.kind, i.retired, "
                "s.reps, s.lapses, s.interval FROM info i JOIN state s ON s.card_id = i.card_id "
                "WHERE i.retired IS NOT NULL ORDER BY i.retired DESC, i.card_id")]

    def events(self, card_id: str) -> list[dict]:
        """Lifecycle events of one card (oldest first)."""
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT day, event, old_id, front, back FROM history WHERE card_id=? ORDER BY id", (card_id,))]

    def history(self, card_id: str) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT day, grade, ease, interval FROM log WHERE card_id=? ORDER BY id", (card_id,))]

    def new_started_on(self, day: date) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM state WHERE first=?", (day.isoformat(),)).fetchone()[0]


def due_queue(cards: list[dict], store: ReviewStore, today: date | None = None,
              new_per_day: int = NEW_PER_DAY) -> dict:
    """Cards to study today: due reviews (most overdue first), then new cards in desk order.

    Runs ``store.reconcile`` first, so edited cards keep their schedule and gone cards
    are retired (they never appear in the queue; ``counts.retired`` says how many).
    """
    today = today or date.today()
    iso = today.isoformat()
    changes = store.reconcile(cards, today)
    states = store.all()
    due, new, later = [], [], 0
    for c in cards:
        st = states.get(c["id"])
        if st is None:
            new.append(c)
        elif st.due <= iso:
            due.append({**c, "review": asdict(st)})
        else:
            later += 1
    due.sort(key=lambda c: (c["review"]["due"], c["rel"], c["line"]))
    allowed = max(0, new_per_day - store.new_started_on(today))
    new_today = [{**c, "review": None} for c in new[:allowed]]
    with store.lock:
        n_retired = store.db.execute("SELECT COUNT(*) FROM info WHERE retired IS NOT NULL").fetchone()[0]
    return {"today": iso, "due": due, "new": new_today, "changes": changes,
            "counts": {"due": len(due), "new": len(new_today), "new_total": len(new),
                       "later": later, "total": len(cards), "retired": n_retired}}
