"""BM25 keyword search over chunks (own small implementation, no dependencies).

Tokenizer: lowercase Unicode words; ``snake_case`` / ``kebab-case`` words are kept
whole *and* split into parts; English stop words are dropped; a light suffix
stemmer folds plurals and -ing/-ed/-ly forms ("plants", "planting", "planted" ->
"plant"). The same pipeline runs on documents and queries, so the stems only
need to be consistent, not linguistically perfect.

Each chunk is indexed as: its heading breadcrumb (counted twice, as a title boost)
plus its text with Markdown link URLs and emphasis markers removed.
The index is built in memory from the SQLite store; for a desk of a few thousand
chunks this takes well under a second, so it is not persisted.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .chunker import Chunk

STOP = frozenset("""
a an and are as at be been but by can could did do does for from had has have how i if in into is it its
me my no not of on or our so than that the their them then there these they this to too was we were what
when where which who why will with would you your about also just like than via vs q
""".split())

_WORD = re.compile(r"\w+(?:[-_]\w+)*")
_LINK_URL = re.compile(r"\]\([^)]*\)")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def stem(w: str) -> str:
    """Very light English suffix stripping (plural, -ing, -ed, -ly)."""
    if len(w) <= 3 or not w.isalpha():
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("es") and len(w) > 4 and w[-3] in "sxz" and not w.endswith("ses"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    for suf in ("ingly", "edly", "ing", "ed", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            base = w[: -len(suf)]
            if not any(c in "aeiouy" for c in base):
                break
            # undouble: "running" -> "runn" -> "run" (but keep "ll", "ss", "zz")
            if len(base) > 3 and base[-1] == base[-2] and base[-1] not in "lsz":
                base = base[:-1]
            w = base
            break
    if w.endswith("e") and len(w) >= 4 and not w.endswith("ee"):  # "bake"/"baked"/"baking" -> "bak"
        w = w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for m in _WORD.finditer(text.lower()):
        word = m.group(0)
        parts = re.split(r"[-_]", word) if ("-" in word or "_" in word) else []
        if parts:
            out.append(word)
        for t in parts or [word]:
            if t and t not in STOP:
                out.append(stem(t))
    return out


def chunk_text_for_index(c: Chunk) -> str:
    body = _LINK_URL.sub("]", _HTML_COMMENT.sub(" ", c.text))
    body = body.replace("**", " ").replace("__", " ")
    crumb = " ".join(c.breadcrumb)
    return f"{crumb}\n{crumb}\n{body}"


@dataclass
class Hit:
    chunk: Chunk
    score: float
    terms: list[str] = field(default_factory=list)   # matched query stems


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.chunks = chunks
        self.tf: list[Counter] = []
        self.lens: list[int] = []
        self.df: Counter = Counter()
        self.postings: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            toks = tokenize(chunk_text_for_index(c))
            tf = Counter(toks)
            self.tf.append(tf)
            self.lens.append(len(toks))
            for t in tf:
                self.df[t] += 1
                self.postings.setdefault(t, []).append(i)
        self.n = len(chunks)
        self.avglen = (sum(self.lens) / self.n) if self.n else 0.0

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int = 10, per_page: int | None = None) -> list[Hit]:
        """Top-k chunks for the query. ``per_page`` caps hits from the same page."""
        qterms = list(dict.fromkeys(tokenize(query)))
        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}
        for t in qterms:
            idf = self.idf(t)
            for i in self.postings.get(t, ()):
                f = self.tf[i][t]
                norm = self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avglen or 1))
                scores[i] = scores.get(i, 0.0) + idf * f * (self.k1 + 1) / (f + norm)
                matched.setdefault(i, []).append(t)
        order = sorted(scores, key=lambda i: (-scores[i], self.chunks[i].rel, i))
        hits: list[Hit] = []
        per: Counter = Counter()
        for i in order:
            rel = self.chunks[i].rel
            if per_page is not None and per[rel] >= per_page:
                continue
            per[rel] += 1
            hits.append(Hit(self.chunks[i], scores[i], matched[i]))
            if len(hits) >= k:
                break
        return hits


def snippet(text: str, terms: list[str], width: int = 160, mark: tuple[str, str] = ("**", "**")) -> str:
    """A one-line excerpt around the first matched word, with matched words wrapped in ``mark``.

    ``terms`` are query stems (as in ``Hit.terms``); a word matches if its stem is one of them.
    """
    flat = " ".join(_HTML_COMMENT.sub(" ", _LINK_URL.sub("]", text)).split())
    stems = set(terms)

    def hit(word: str) -> bool:
        w = word.lower()
        return w in stems or stem(w) in stems or any(stem(p) in stems for p in re.split(r"[-_]", w) if p)

    words = list(_WORD.finditer(flat))
    first = next((m for m in words if hit(m.group(0))), None)
    start = 0
    if first is not None and first.start() > width // 3:
        start = flat.rfind(" ", 0, first.start() - width // 3) + 1
    end = min(len(flat), start + width)
    if end < len(flat):
        sp = flat.rfind(" ", start, end)
        end = sp if sp > start else end
    out, pos = [], start
    for m in _WORD.finditer(flat, start, end):
        if hit(m.group(0)):
            out += [flat[pos:m.start()], mark[0], m.group(0), mark[1]]
            pos = m.end()
    out.append(flat[pos:end])
    return ("… " if start > 0 else "") + "".join(out) + (" …" if end < len(flat) else "")
