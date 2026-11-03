import pytest

from deskbrain.bm25 import BM25Index, snippet, stem, tokenize
from deskbrain.chunker import Chunk, chunk_page
from deskbrain.parser import parse
from deskbrain.scanner import read_page, scan


@pytest.fixture
def bm(desk):
    chunks = [c for f in scan(desk) for c in chunk_page(parse(read_page(desk, f.rel), f.rel))]
    return BM25Index(chunks)


def mk(rel, text, crumb=None):
    return Chunk(id=rel, rel=rel, heading="", level=0, breadcrumb=crumb or [], text=text)


@pytest.mark.parametrize("words,same", [
    (["plant", "plants", "planted", "planting"], True),
    (["seed", "seeds", "seeded"], True),
    (["leaves", "leave"], True),
    (["bake", "baked", "baking"], True),
    (["run", "running", "runs"], True),
    (["berries", "berry"], True),
    (["boxes", "box"], True),
    (["class", "classes"], True),
    (["plant", "planet"], False),
])
def test_stem_consistency(words, same):
    stems = {stem(w) for w in words}
    assert (len(stems) == 1) == same, stems


def test_tokenize_drops_stopwords_and_splits_compounds():
    toks = tokenize("How does the snake_case parser handle Wi-Fi?")
    assert "the" not in toks and "how" not in toks
    assert "snake_case" in toks and stem("snake") in toks and stem("case") in toks
    assert "pars" in toks or "parser" in toks
    assert "wi-fi" in toks


def test_known_queries_rank_expected_page_first(bm):
    cases = {
        "sourdough starter": "02-cooking/bread.md",
        "why are the lower leaves yellow": "01-garden/tomatoes.md",
        "how deep to plant seeds": "01-garden/tomatoes.md",
        "plastic greenhouse": "01-garden/greenhouse/README.md",
        "crust darker overnight proof": "02-cooking/bread.md",
    }
    for q, rel in cases.items():
        hits = bm.search(q)
        assert hits, q
        assert hits[0].chunk.rel == rel, (q, [(h.chunk.rel, round(h.score, 2)) for h in hits[:3]])


def test_best_chunk_is_the_right_section(bm):
    top = bm.search("seeds planted deep")[0]
    assert top.chunk.heading == "🟢 Q: How deep should seeds be planted?"
    assert set(top.terms) >= {"seed", "plant", "deep"}


def test_no_hits_for_unknown_or_private_words(bm):
    assert bm.search("xylophone quantum") == []
    assert bm.search("the of and") == []
    # Words that only appear in private / excluded fixture pages are not findable.
    assert bm.search("secret personal") == []


def test_link_urls_are_not_indexed(bm):
    # "nowhere.md" only occurs as a link URL in tomatoes.md; the link text is "missing page".
    assert bm.search("nowhere") == []
    assert "01-garden/tomatoes.md" in {h.chunk.rel for h in bm.search("missing page")}


def test_bm25_properties():
    ix = BM25Index([
        mk("a.md", "apple apple apple banana"),
        mk("b.md", "apple banana cherry " + "filler " * 40),
        mk("c.md", "cherry durian"),
    ])
    # Higher term frequency in a shorter doc ranks first.
    assert [h.chunk.rel for h in ix.search("apple")] == ["a.md", "b.md"]
    # Rare term (durian) outweighs common term (banana... appears twice) -> c first for "durian banana".
    assert ix.search("durian banana")[0].chunk.rel == "c.md"
    assert ix.search("apple", k=1)[0].chunk.rel == "a.md"


def test_heading_boost_and_per_page_cap():
    ix = BM25Index([
        mk("x.md", "some text about other things and compost", ["Compost"]),
        mk("y.md", "compost is mentioned once here among many other words in a long line", ["Misc"]),
        mk("x.md", "more compost notes", ["Compost", "More"]),
    ])
    assert ix.search("compost")[0].chunk.rel == "x.md"
    assert [h.chunk.rel for h in ix.search("compost", per_page=1)] == ["x.md", "y.md"]


def test_empty_index():
    assert BM25Index([]).search("anything") == []


def test_snippet_highlights_matched_words():
    text = "Intro line. " + "filler words here " * 20 + "The starter was too young; feed the Starter daily."
    s = snippet(text, tokenize("starters"), width=80)
    assert "**starter**" in s and "**Starter**" in s
    assert s.startswith("… ")
    assert len(s) < 110


def test_snippet_strips_link_urls_and_short_text():
    s = snippet("See [bread](../02-cooking/bread.md) now", tokenize("bread"))
    assert s == "See [**bread**] now"
