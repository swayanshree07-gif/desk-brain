"""Flashcards built from the desk (Day 19). Read-only: cards are derived from the index.

Card kinds
  qa      an answered doubt (### 🟢 Q: with a non-empty **A:**): front = question, back = answer.
  lead    a dated finding that starts with a bold lead (``**7-drone failure = …** rest``):
          front = the lead, back = the whole finding.
  cloze   any other finding with a **bold** or `code` span: front = the finding with the
          first such span (bold preferred) blanked as ``[…]``, back = the span.
  recall  a plain finding: front = "page › section, <date>: what did you find?", back = text.
Findings in activity sections are skipped: the heading has the word log, now, notes,
to-do/todo or next (e.g. "📅 Daily log", "🎯 Now", "📓 Notes"). On the desk those hold
progress notes ("Started …", "Looked up …"), not something to remember. Open doubts and doubts without
an answer give no card.

Ids (stable, so the spaced-repetition state of Day 20 can key on them)
  qa      "q-" + doubts.doubt_id(rel, question): survives line moves and answer edits.
  others  "f-" + sha1(rel, date, normalised text)[:12]: survives line moves; a text edit
          gives a new id (Day 22 matches old/new cards by rel + date + similarity).
  Duplicate ids on one page get "-1", "-2" … in line order.
Each card also has ``hash`` = sha1(front, back)[:12] so later days can spot content edits.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from .doubts import area_of, doubt_id, heading_anchors
from .scanner import is_allowed

KINDS = ("qa", "lead", "cloze", "recall")
BLANK = "[…]"
SKIP_SECTION_RE = re.compile(r"\b(logs?|now|notes?|to-?dos?|next)\b", re.IGNORECASE)
LEAD_RE = re.compile(r"^\*\*(.+?)\*\*\s*[:.\-–—]?\s*(.*)$", re.DOTALL)
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
CODE_RE = re.compile(r"`([^`\n]+)`")
MIN_LEAD_REST = 15     # a bold lead needs this much text after it to be a question
MIN_SPAN = 2           # shorter bold/code spans are not worth a cloze


def _sha(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def skip_section(section: str) -> bool:
    """True for activity sections whose dated bullets give no cards."""
    return bool(SKIP_SECTION_RE.search(section or ""))


def finding_card(text: str) -> tuple[str, str, str]:
    """(kind, front, back) for one finding's text; front is '' for kind 'recall'."""
    text = text.strip()
    m = LEAD_RE.match(text)
    if m and len(m.group(2)) >= MIN_LEAD_REST:
        lead = m.group(1).strip().rstrip(":.").strip()
        if len(lead) >= MIN_SPAN:
            return "lead", lead, text
    for rx in (BOLD_RE, CODE_RE):
        for sm in rx.finditer(text):
            span = sm.group(1).strip()
            if len(span) >= MIN_SPAN:
                front = text[:sm.start()] + BLANK + text[sm.end():]
                return "cloze", front, span
    return "recall", "", text


def build_cards(index) -> list[dict]:
    """All cards from the indexed (non-private) pages, ordered by path and line."""
    db = index.db
    pages = {r["rel"]: r["title"] for r in db.execute("SELECT rel, title FROM pages") if is_allowed(r["rel"])}
    cards: list[dict] = []
    anchors: dict[str, dict[int, str]] = {}

    def anchor(rel: str, line: int) -> str:
        if rel not in anchors:
            anchors[rel] = heading_anchors(index, rel)
        return anchors[rel].get(line, "")

    def add(cid, kind, rel, line, front, back, section, date=None, a=""):
        cards.append({
            "id": cid, "kind": kind, "rel": rel, "page_title": pages[rel], "area": area_of(rel),
            "line": line, "section": section or "", "date": date, "anchor": a,
            "front": front, "back": back, "hash": _sha(front, back),
        })

    for r in db.execute("SELECT * FROM doubts WHERE status='answered' ORDER BY rel, line"):
        if r["rel"] in pages and (r["answer"] or "").strip() and r["question"].strip():
            add("q-" + doubt_id(r["rel"], r["question"]), "qa", r["rel"], r["line"],
                r["question"].strip(), r["answer"].strip(), r["section"], r["asked"], anchor(r["rel"], r["line"]))

    for r in db.execute("SELECT * FROM findings ORDER BY rel, line"):
        rel, section = r["rel"], r["section"] or ""
        if rel not in pages or skip_section(section) or not r["text"].strip():
            continue
        kind, front, back = finding_card(r["text"])
        if kind == "recall":
            where = pages[rel] + (f" › {section}" if section else "")
            front = f"{where}, {r['date']}: what did you find?"
        add("f-" + _sha(rel, r["date"], _norm(r["text"])), kind, rel, r["line"], front, back, section, r["date"])

    cards.sort(key=lambda c: (c["rel"], c["line"]))
    seen: Counter = Counter()
    for c in cards:
        k = seen[c["id"]]
        seen[c["id"]] += 1
        if k:
            c["id"] = f"{c['id']}-{k}"
    return cards


def list_cards(index, kind: str = "all", area: str = "") -> dict:
    cards = build_cards(index)
    counts = Counter(c["kind"] for c in cards)
    areas = sorted({c["area"] for c in cards})
    items = [c for c in cards if (kind == "all" or c["kind"] == kind) and (not area or c["area"] == area)]
    return {"cards": items, "total": len(cards), "counts": {k: counts[k] for k in KINDS}, "areas": areas}
