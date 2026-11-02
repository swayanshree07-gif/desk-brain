"""Parse one desk Markdown page into structured parts.

Extracts: title, headings, links, doubts (### 🔴 Q: / ### 🟢 Q:) and dated
findings (- **YYYY-MM-DD** · text). Text inside HTML comments and fenced code
blocks is ignored, so page templates and code samples never count as content.
Line numbers are 1-based and refer to the original file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
DOUBT_RE = re.compile(r"^###\s+(🔴|🟢)\s*Q:\s*(.*?)\s*$")
FIELD_RE = re.compile(r"^\*\*(Asked|Context|A):\*\*\s*(.*)$")
FINDING_RE = re.compile(r"^\s*[-*]\s+\*\*(\d{4}-\d{2}-\d{2})\*\*\s*[·:\-–—]\s*(.+?)\s*$")
MDLINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#([^\]|]*))?(?:\|([^\]]*))?\]\]")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
PLACEHOLDER_QUESTIONS = {"question?", "question", ""}


@dataclass
class Heading:
    level: int
    text: str
    line: int


@dataclass
class Link:
    target: str          # raw target as written ("../x.md#a", "dir/", "Page Name")
    text: str
    line: int
    kind: str            # "md" | "wiki" | "external" | "image"


@dataclass
class Doubt:
    question: str
    status: str          # "open" | "answered"
    line: int
    asked: str | None = None
    context: str | None = None
    answer: str | None = None
    section: str | None = None   # nearest heading above (level 1-2)


@dataclass
class Finding:
    date: str
    text: str
    line: int
    section: str | None = None


@dataclass
class Page:
    rel: str
    title: str
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    doubts: list[Doubt] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    text: str = ""           # visible text (comments and code fences blanked)


def visible_lines(text: str) -> tuple[list[str], list[bool]]:
    """Return lines with HTML comments blanked, plus a per-line 'inside code fence' flag."""
    out: list[str] = []
    in_code: list[bool] = []
    in_comment = False
    in_fence = False
    for raw in text.splitlines():
        if in_fence:
            out.append(raw)
            in_code.append(True)
            if FENCE_RE.match(raw):
                in_fence = False
            continue
        if not in_comment and FENCE_RE.match(raw):
            out.append(raw)
            in_code.append(True)
            in_fence = True
            continue
        # Remove comment spans, possibly several per line or spanning lines.
        buf = []
        i = 0
        while i < len(raw):
            if in_comment:
                end = raw.find("-->", i)
                if end == -1:
                    i = len(raw)
                else:
                    in_comment = False
                    i = end + 3
            else:
                start = raw.find("<!--", i)
                if start == -1:
                    buf.append(raw[i:])
                    i = len(raw)
                else:
                    buf.append(raw[i:start])
                    in_comment = True
                    i = start + 4
        out.append("".join(buf))
        in_code.append(False)
    return out, in_code


def _link_kind(target: str, image: bool) -> str:
    if image:
        return "image"
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("//"):
        return "external"
    return "md"


def parse(text: str, rel: str = "") -> Page:
    lines, in_code = visible_lines(text)
    page = Page(rel=rel, title="")
    section: str | None = None
    current: Doubt | None = None
    answer_lines: list[str] | None = None

    def close_doubt() -> None:
        nonlocal current, answer_lines
        if current is not None:
            if answer_lines is not None:
                ans = "\n".join(answer_lines).strip()
                current.answer = ans or None
            page.doubts.append(current)
        current, answer_lines = None, None

    for idx, line in enumerate(lines):
        n = idx + 1
        if in_code[idx]:
            continue
        h = HEADING_RE.match(line)
        if h:
            level, htext = len(h.group(1)), h.group(2)
            close_doubt()
            page.headings.append(Heading(level, htext, n))
            if level == 1 and not page.title:
                page.title = htext
            if level <= 2:
                section = htext
            d = DOUBT_RE.match(line)
            if d and d.group(2).strip().lower() not in PLACEHOLDER_QUESTIONS:
                current = Doubt(
                    question=d.group(2).strip(),
                    status="open" if d.group(1) == "🔴" else "answered",
                    line=n,
                    section=section,
                )
        elif current is not None:
            f = FIELD_RE.match(line.strip())
            if f:
                key, val = f.group(1), f.group(2).strip()
                if key == "A":
                    answer_lines = [val]
                else:
                    if answer_lines is not None:  # a field after the answer ends it
                        current.answer = "\n".join(answer_lines).strip() or None
                        answer_lines = None
                    if key == "Asked":
                        current.asked = val
                    else:
                        current.context = val
                        if current.asked is None:
                            m = DATE_RE.search(val)
                            if m:
                                current.asked = m.group(0)
            elif answer_lines is not None:
                answer_lines.append(line)

        fm = FINDING_RE.match(line)
        if fm and current is None:
            page.findings.append(Finding(fm.group(1), fm.group(2), n, section))

        for m in MDLINK_RE.finditer(line):
            image, ltext, target = m.group(1) == "!", m.group(2), m.group(3)
            page.links.append(Link(target, ltext, n, _link_kind(target, image)))
        for m in WIKILINK_RE.finditer(line):
            target = m.group(1).strip()
            if m.group(2):
                target += "#" + m.group(2).strip()
            page.links.append(Link(target, (m.group(3) or m.group(1)).strip(), n, "wiki"))
    close_doubt()

    if not page.title:
        stem = rel.rsplit("/", 1)[-1].removesuffix(".md") if rel else ""
        if stem.lower() == "readme" and "/" in rel:
            stem = rel.rsplit("/", 2)[-2]
        page.title = stem or "(untitled)"
    page.text = "\n".join(lines)  # comments blanked; code kept for search
    return page
