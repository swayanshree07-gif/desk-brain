"""Weekly digest (Day 23): what changed on the desk in a 7-day window, what is still open.

Window: whole local days ``start``..``end`` (inclusive). Default = the 7 days ending
today; ``week="2026-W45"`` = that ISO week (Monday..Sunday); ``week="2026-11-03"`` =
the ISO week containing that date.

Sources (all from the index, nothing extra is read from the desk):
  changed pages   page mtime inside the window; ``new`` if a page_added event is in it
  removed pages   page_removed events in the window (path only)
  new doubts      ``Asked`` date in the window, or a doubt_new event in it (undated doubts)
  answered        doubt_answered events in the window, plus doubts that showed up
                  already answered (doubt_new with status answered) in the window
  still open      every open doubt now, oldest first, with its age in days
  findings        dated findings (``- **YYYY-MM-DD** · …``) with the date in the window;
                  activity sections (log / now / notes / to-do / next) are skipped, like cards
Answer and new-page tracking only works from ``history_since`` (first index build) on;
``tracking.complete`` says whether the whole window is covered.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta

from .cards import skip_section
from .doubts import area_of, list_doubts
from .scanner import is_allowed

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
WEEK_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")


def window(week: str | None = None, today: date | None = None) -> tuple[date, date]:
    """(start, end) dates of the digest window. Raises ValueError on a bad ``week``."""
    today = today or date.today()
    if not week:
        return today - timedelta(days=6), today
    m = WEEK_RE.match(week.strip())
    if m:
        start = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    else:
        d = date.fromisoformat(week.strip())
        start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _ts(d: date) -> float:
    return datetime.combine(d, dtime.min).timestamp()


def _day(ts: float) -> date:
    return datetime.fromtimestamp(ts).date()


def _asked_date(asked: str | None) -> date | None:
    m = DATE_RE.search(asked or "")
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(0))
    except ValueError:
        return None


def build_digest(index, start: date, end: date, today: date | None = None) -> dict:
    today = today or date.today()
    lo, hi = _ts(start), _ts(end + timedelta(days=1))
    db = index.db
    pages = {r["rel"]: dict(r) for r in db.execute("SELECT rel, title, mtime FROM pages") if is_allowed(r["rel"])}
    events = [e for e in index.events(lo, hi) if is_allowed(e["rel"])]
    added = {e["rel"] for e in events if e["kind"] == "page_added"}

    changed = sorted(
        ({"rel": rel, "title": p["title"], "area": area_of(rel), "mtime": p["mtime"],
          "day": _day(p["mtime"]).isoformat(), "new": rel in added}
         for rel, p in pages.items() if lo <= p["mtime"] < hi),
        key=lambda x: (-x["mtime"], x["rel"]))
    removed = sorted({e["rel"] for e in events if e["kind"] == "page_removed" and e["rel"] not in pages})

    board = list_doubts(index)["doubts"]
    by_id = {d["id"]: d for d in board}
    ev_new = {e["key"]: e for e in events if e["kind"] == "doubt_new"}
    ev_ans = {e["key"]: e for e in events if e["kind"] == "doubt_answered"}
    ev_ans.update({k: e for k, e in ev_new.items() if e["detail"] == "answered" and k not in ev_ans})

    def item(d: dict, extra: dict | None = None) -> dict:
        keep = ("id", "rel", "page_title", "area", "line", "question", "status", "asked", "answer", "anchor")
        out = {k: d[k] for k in keep}
        out.update(extra or {})
        return out

    new_doubts, answered, still_open = [], [], []
    for d in board:
        ad = _asked_date(d["asked"])
        if (ad is not None and start <= ad <= end) or (ad is None and d["id"] in ev_new):
            new_doubts.append(item(d))
        if d["status"] == "answered" and d["id"] in ev_ans:
            answered.append(item(d, {"answered_day": _day(ev_ans[d["id"]]["ts"]).isoformat()}))
        if d["status"] == "open":
            still_open.append(item(d, {"age_days": (today - ad).days if ad else None}))
    answered.sort(key=lambda x: (x["answered_day"], x["rel"], x["line"]), reverse=True)
    still_open.sort(key=lambda x: (x["age_days"] is None, -(x["age_days"] or 0), x["rel"], x["line"]))
    # Flag new doubts that were also answered inside the window.
    for d in new_doubts:
        d["answered_this_week"] = d["id"] in ev_ans and d["status"] == "answered"

    findings = []
    for r in db.execute("SELECT rel, line, date, text, section FROM findings ORDER BY date DESC, rel, line"):
        if r["rel"] not in pages or skip_section(r["section"] or ""):
            continue
        try:
            fd = date.fromisoformat(r["date"])
        except ValueError:
            continue
        if start <= fd <= end:
            findings.append({"rel": r["rel"], "page_title": pages[r["rel"]]["title"], "area": area_of(r["rel"]),
                             "line": r["line"], "date": r["date"], "text": r["text"], "section": r["section"] or ""})

    since = index.meta("history_since")
    since_day = _day(float(since)).isoformat() if since else None
    areas = Counter(x["area"] for x in changed)
    areas.update(x["area"] for x in findings)
    return {
        "start": start.isoformat(), "end": end.isoformat(), "today": today.isoformat(),
        "week": f"{start.isocalendar()[0]}-W{start.isocalendar()[1]:02d}" if start.weekday() == 0 and
                (end - start).days == 6 else None,
        "pages": {"changed": changed, "removed": removed},
        "doubts": {"new": new_doubts, "answered": answered, "open": still_open},
        "findings": findings,
        "counts": {"changed": len(changed), "new_pages": sum(x["new"] for x in changed), "removed": len(removed),
                   "new_doubts": len(new_doubts), "answered": len(answered), "open": len(still_open),
                   "findings": len(findings)},
        "areas": dict(sorted(areas.items(), key=lambda kv: (-kv[1], kv[0]))),
        "tracking": {"since": since_day, "complete": bool(since_day) and since_day <= start.isoformat()},
    }
