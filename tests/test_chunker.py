from deskbrain.chunker import chunk_page
from deskbrain.parser import parse
from deskbrain.scanner import read_page, scan


def chunks_of(desk, rel):
    return chunk_page(parse(read_page(desk, rel), rel))


def test_tomatoes_sections(desk):
    ch = chunks_of(desk, "01-garden/tomatoes.md")
    # H1 + 4 H2 + 2 real doubt H3 + placeholder H3 (heading-only, still a section)
    assert [c.heading for c in ch] == [
        "Tomatoes", "🎯 Now", "❓ Doubts & Answers",
        "🔴 Q: Why do the lower leaves turn yellow?", "🟢 Q: How deep should seeds be planted?",
        "🔴 Q: question?", "🔍 Findings", "📓 Notes",
    ]
    d = ch[3]
    assert d.breadcrumb == ["Tomatoes", "❓ Doubts & Answers", "🔴 Q: Why do the lower leaves turn yellow?"]
    assert (d.start_line, d.end_line) == (15, 18)
    assert d.level == 3
    assert "cherry plant" in d.text


def test_comments_not_in_chunks(desk):
    for c in chunks_of(desk, "01-garden/tomatoes.md"):
        assert "where it came up" not in c.text
        assert "YYYY-MM-DD" not in c.text


def test_code_block_heading_is_not_a_section(desk):
    ch = chunks_of(desk, "01-garden/tomatoes.md")
    assert not any("Inside a code block" in c.heading for c in ch)
    assert "Inside a code block" in ch[-1].text  # still searchable as text


def test_ids_stable_and_unique(desk):
    a = [c.id for f in scan(desk) for c in chunks_of(desk, f.rel)]
    b = [c.id for f in scan(desk) for c in chunks_of(desk, f.rel)]
    assert a == b
    assert len(set(a)) == len(a)
    assert len(a) == 19


def test_id_survives_body_edit_and_other_sections():
    v1 = parse("# P\nintro\n## A\nold body\n## B\nbee", "p.md")
    v2 = parse("# P\nintro changed\nmore\n## New\nx\n## A\nnew body, longer\n## B\nbee", "p.md")
    ids1 = {c.heading: c.id for c in chunk_page(v1)}
    ids2 = {c.heading: c.id for c in chunk_page(v2)}
    assert ids1["A"] == ids2["A"] and ids1["B"] == ids2["B"] and ids1["P"] == ids2["P"]


def test_preamble_and_duplicate_headings():
    p = parse("intro line\n\n# T\n## Notes\na\n## Notes\nb", "x.md")
    ch = chunk_page(p)
    assert ch[0].heading == "" and ch[0].level == 0 and ch[0].text == "intro line"
    notes = [c for c in ch if c.heading == "Notes"]
    assert len(notes) == 2 and notes[0].id != notes[1].id


def test_long_section_split_with_line_numbers():
    body = "\n\n".join(f"paragraph {i} " + "word " * 60 for i in range(10))
    p = parse("# Big\n" + body, "big.md")
    ch = chunk_page(p, max_chars=800)
    assert len(ch) > 1
    assert all(len(c.text) <= 800 for c in ch)
    assert ch[0].start_line == 1 and ch[-1].end_line == len(("# Big\n" + body).split("\n"))
    for a, b in zip(ch, ch[1:]):
        assert b.start_line == a.end_line + 1
    assert len({c.id for c in ch}) == len(ch)


def test_empty_page():
    assert chunk_page(parse("", "e.md")) == []
