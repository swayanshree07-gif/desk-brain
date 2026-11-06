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
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

START_EASE = 2.5
MIN_EASE = 1.3
NEW_PER_DAY = 20

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
"""


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

    def grade(self, card_id: str, grade: int, today: date | None = None, card_hash: str = "") -> CardState:
        today = today or date.today()
        with self.lock:
            new = sm2(self.get(card_id) or CardState(), grade, today)
            self.db.execute(
                "INSERT OR REPLACE INTO state (card_id, ease, interval, reps, lapses, due, last, first, card_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (card_id, new.ease, new.interval, new.reps, new.lapses, new.due, new.last, new.first, card_hash))
            self.db.execute("INSERT INTO log (card_id, day, grade, ease, interval, card_hash) VALUES (?,?,?,?,?,?)",
                            (card_id, today.isoformat(), grade, new.ease, new.interval, card_hash))
            self.db.commit()
        return new

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

    Cards with state but no longer on the desk are ignored here (Day 22 retires them).
    """
    today = today or date.today()
    iso = today.isoformat()
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
    return {"today": iso, "due": due, "new": new_today,
            "counts": {"due": len(due), "new": len(new_today), "new_total": len(new),
                       "later": later, "total": len(cards)}}
