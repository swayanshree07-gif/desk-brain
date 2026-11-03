from pathlib import Path

import pytest

from deskbrain.links import (
    ASSET, BAD_ANCHOR, BROKEN, EXCLUDED, EXTERNAL, FOLDER, OK, OUTSIDE,
    LinkResolver, anchor_matches, broken_links, slugify,
)
from deskbrain.parser import Link, parse
from deskbrain.scanner import read_page, scan


@pytest.fixture
def resolver(desk):
    pages = [parse(read_page(desk, f.rel), f.rel) for f in scan(desk)]
    return LinkResolver(pages, desk)


def by_text(resolver, src):
    return {r.text: r for r in resolver.resolve_all() if r.src == src}


def test_slugify_github_style():
    assert slugify("🔍 Findings") == "-findings"
    assert slugify("❓ Doubts & Answers") == "-doubts--answers"
    assert slugify("CycloneDDS vs Zenoh") == "cyclonedds-vs-zenoh"
    assert slugify("YOLO learning path") == "yolo-learning-path"
    assert slugify("snake_case & C++") == "snake_case--c"


def test_anchor_lenient_on_emoji_prefix():
    slugs = {"-findings", "tomatoes"}
    assert anchor_matches("findings", slugs)
    assert anchor_matches("-findings", slugs)
    assert not anchor_matches("finding", slugs)


def test_good_links(resolver):
    r = by_text(resolver, "03-music/practice.md")
    assert (r["guitar"].status, r["guitar"].target) == (OK, "03-music/guitar.md")
    assert (r["Sourdough Bread"].status, r["Sourdough Bread"].target) == (OK, "02-cooking/bread.md")
    assert r["Sourdough Bread"].anchor == "Findings"
    assert (r["my tomato page"].status, r["my tomato page"].target) == (OK, "01-garden/tomatoes.md")
    assert (r["jump"].status, r["jump"].target) == (OK, "03-music/practice.md")
    assert (r["doubts"].status, r["doubts"].target) == (OK, "01-garden/tomatoes.md")
    assert (r["greenhouse"].status, r["greenhouse"].target) == (OK, "01-garden/greenhouse/README.md")
    assert (r["cooking folder"].status, r["cooking folder"].target) == (FOLDER, "02-cooking/")
    assert (r["recipe pdf"].status, r["recipe pdf"].target) == (ASSET, "02-cooking/recipe.pdf")
    assert (r["guitar again"].status, r["guitar again"].target) == (OK, "03-music/guitar.md")
    assert (r["home"].status, r["home"].target) == (OK, "README.md")


def test_bad_links(resolver):
    r = by_text(resolver, "03-music/practice.md")
    assert (r["lost"].status, r["lost"].target) == (BROKEN, "03-music/lost.md")
    assert r["Banjo"].status == BROKEN
    assert r["bad anchor"].status == BAD_ANCHOR
    assert r["scan"].status == BROKEN
    assert r["outside"].status == OUTSIDE and r["outside"].target is None


def test_private_links_never_resolved(resolver):
    r = by_text(resolver, "03-music/practice.md")
    assert r["log"].status == EXCLUDED and r["log"].target is None
    # Personal/diary.md is never scanned, so a wikilink to it is just "broken"
    # and nothing about the private file is revealed.
    assert r["diary"].status == BROKEN and r["diary"].target is None
    for x in resolver.resolve_all():
        assert not (x.target or "").startswith(("activity", "Personal"))


def test_folder_link_with_and_without_slash(resolver):
    r = by_text(resolver, "01-garden/tomatoes.md")
    assert (r["the greenhouse"].status, r["the greenhouse"].target) == (OK, "01-garden/greenhouse/README.md")
    assert r["missing page"].status == BROKEN
    assert r["bread"].status == OK


def test_anchor_to_other_page_with_emoji_heading(resolver):
    r = by_text(resolver, "01-garden/greenhouse/README.md")
    assert (r["tomatoes"].status, r["tomatoes"].anchor) == (OK, "findings")


def test_broken_link_list(resolver):
    bad = broken_links(resolver.resolve_all())
    got = sorted((b.src, b.raw, b.status) for b in bad)
    assert got == [
        ("01-garden/tomatoes.md", "nowhere.md", BROKEN),
        ("03-music/practice.md", "../../outside.md", OUTSIDE),
        ("03-music/practice.md", "Banjo", BROKEN),
        ("03-music/practice.md", "diary", BROKEN),
        ("03-music/practice.md", "guitar.md#no-such-heading", BAD_ANCHOR),
        ("03-music/practice.md", "lost.md", BROKEN),
        ("03-music/practice.md", "scan.png", BROKEN),
    ]


def test_external_and_no_root():
    p = parse("# A\n[x](https://example.com) [y](b.md) [z](pic.png)", "a.md")
    q = parse("# B", "b.md")
    res = LinkResolver([p, q])  # no root: no filesystem checks at all
    st = {r.text: r.status for r in res.resolve_all()}
    assert st == {"x": EXTERNAL, "y": OK, "z": BROKEN}


def test_duplicate_heading_slugs():
    p = parse("# Notes\n## Setup\n## Setup\n[a](#setup-1) [b](#setup-2)", "n.md")
    st = {r.text: r.status for r in LinkResolver([p]).resolve_all()}
    assert st == {"a": OK, "b": BAD_ANCHOR}


def test_wikilink_prefers_same_folder():
    a = parse("# X", "one/notes.md")
    b = parse("# Y", "two/notes.md")
    src = parse("[[notes]]", "two/index.md")
    res = LinkResolver([a, b, src])
    assert res.resolve("two/index.md", src.links[0]).target == "two/notes.md"


def test_hidden_and_template_targets_excluded():
    p = parse("[t](_template/) [o](.obsidian/x.md) [u](../tools/a.md)", "03-music/p.md")
    st = {r.text: r.status for r in LinkResolver([p]).resolve_all()}
    assert st == {"t": EXCLUDED, "o": EXCLUDED, "u": EXCLUDED}


def test_resolver_never_writes(desk, resolver):
    before = {p: p.stat().st_mtime_ns for p in Path(desk).rglob("*")}
    resolver.resolve_all()
    after = {p: p.stat().st_mtime_ns for p in Path(desk).rglob("*")}
    assert before == after
