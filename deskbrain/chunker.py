"""Split a parsed page into heading-based sections ("chunks") for search.

Each chunk covers one heading and the text up to the next heading (any level).
Text before the first heading is a "preamble" chunk. Long sections are split
at blank lines into parts of at most ``max_chars`` characters.

Chunk ids are stable: they depend only on the page path, the heading
breadcrumb, how many earlier sections share that breadcrumb, and the part
number — not on the text — so editing a section's body keeps its id.
Line numbers are 1-based and inclusive.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .parser import Page

MAX_CHARS = 1500


@dataclass
class Chunk:
    id: str
    rel: str
    heading: str              # "" for the preamble
    level: int                # 0 for the preamble
    breadcrumb: list[str] = field(default_factory=list)  # e.g. ["Tomatoes", "🔍 Findings"]
    start_line: int = 1
    end_line: int = 1
    text: str = ""

    @property
    def title(self) -> str:
        return " › ".join(self.breadcrumb) if self.breadcrumb else self.rel


def chunk_id(rel: str, breadcrumb: list[str], dup: int, part: int) -> str:
    key = "\x1f".join([rel, *breadcrumb, str(dup), str(part)])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _split_long(lines: list[str], start: int, max_chars: int) -> list[tuple[int, int, str]]:
    """Split lines (first one is line `start`) into parts of <= max_chars, packing whole
    paragraphs greedily; a paragraph that is too long by itself is split by lines.
    Returns contiguous (start, end, text) parts covering every line."""
    # Blocks: a paragraph plus the blank lines after it, as (first_index, last_index).
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1].strip() != "":
            j += 1
        while j + 1 < len(lines) and lines[j + 1].strip() == "":
            j += 1
        blocks.append((i, j))
        i = j + 1
    # Break over-long blocks into single lines.
    units: list[tuple[int, int]] = []
    for a, b in blocks:
        if len("\n".join(lines[a:b + 1]).strip()) > max_chars:
            units += [(k, k) for k in range(a, b + 1)]
        else:
            units.append((a, b))
    parts: list[tuple[int, int, str]] = []
    cur_a = None
    cur_b = -1
    for a, b in units:
        if cur_a is not None and len("\n".join(lines[cur_a:b + 1]).strip()) > max_chars:
            parts.append((start + cur_a, start + cur_b, "\n".join(lines[cur_a:cur_b + 1])))
            cur_a = None
        if cur_a is None:
            cur_a = a
        cur_b = b
    if cur_a is not None:
        parts.append((start + cur_a, start + cur_b, "\n".join(lines[cur_a:cur_b + 1])))
    return parts


def chunk_page(page: Page, max_chars: int = MAX_CHARS) -> list[Chunk]:
    lines = page.text.split("\n") if page.text else []
    total = len(lines)
    # Section boundaries: (start_line, level, heading)
    bounds: list[tuple[int, int, str]] = [(1, 0, "")]
    bounds += [(h.line, h.level, h.text) for h in page.headings]
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []    # (level, text) of open headings
    seen: dict[tuple[str, ...], int] = {}
    for i, (start, level, heading) in enumerate(bounds):
        end = bounds[i + 1][0] - 1 if i + 1 < len(bounds) else total
        if level:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
        crumb = [t for _, t in stack]
        body = lines[start - 1:end]
        if not "\n".join(body).strip():
            continue
        if level == 0 and end < start:
            continue
        key = tuple(crumb)
        dup = seen.get(key, 0)
        seen[key] = dup + 1
        text = "\n".join(body)
        pieces = [(start, end, text)] if len(text) <= max_chars else _split_long(body, start, max_chars)
        for part, (s, e, t) in enumerate(pieces):
            if not t.strip():
                continue
            chunks.append(Chunk(
                id=chunk_id(page.rel, crumb, dup, part), rel=page.rel, heading=heading, level=level,
                breadcrumb=crumb, start_line=s, end_line=e, text=t.strip("\n"),
            ))
    return chunks
