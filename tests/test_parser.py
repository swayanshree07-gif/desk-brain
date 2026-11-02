from deskbrain.parser import parse, visible_lines
from deskbrain.scanner import read_page


def tomatoes(desk):
    return parse(read_page(desk, "01-garden/tomatoes.md"), "01-garden/tomatoes.md")


def test_title_and_headings(desk):
    p = tomatoes(desk)
    assert p.title == "Tomatoes"
    texts = [h.text for h in p.headings]
    assert "🔍 Findings" in texts
    assert not any("Inside a code block" in t for t in texts)
    assert all(h.line > 0 for h in p.headings)


def test_doubts_skip_comments_placeholders_and_code(desk):
    p = tomatoes(desk)
    qs = [(d.status, d.question) for d in p.doubts]
    assert qs == [
        ("open", "Why do the lower leaves turn yellow?"),
        ("answered", "How deep should seeds be planted?"),
    ]


def test_doubt_fields(desk):
    open_d, ans = tomatoes(desk).doubts
    assert open_d.asked == "2026-10-30 09:15"
    assert open_d.context == "noticed on the cherry plant"
    assert open_d.answer is None
    assert open_d.section == "❓ Doubts & Answers"
    assert ans.asked == "2026-10-01"  # taken from the date in Context
    assert ans.answer == "About 6 mm deep, in moist seed compost.\nKeep them warm until they sprout."
    assert ans.line == 19


def test_findings(desk):
    f = tomatoes(desk).findings
    assert [(x.date, x.text) for x in f] == [
        ("2026-10-20", "Pinching side shoots gives bigger fruit (tried on two plants)."),
        ("2026-10-05", "Seedlings need 14 hours of light."),
    ]
    assert f[0].section == "🔍 Findings"


def test_links(desk):
    p = tomatoes(desk)
    assert [(l.target, l.kind) for l in p.links] == [
        ("../02-cooking/bread.md", "md"),
        ("greenhouse/", "md"),
        ("nowhere.md", "md"),
    ]


def test_link_kinds_and_wikilinks():
    p = parse("# X\nsee [[Other Page#sec|alias]] and ![img](a.png) and [web](https://x.org) and [m](<a.md>)\n")
    assert [(l.target, l.text, l.kind) for l in p.links] == [
        ("a.png", "img", "image"),
        ("https://x.org", "web", "external"),
        ("a.md", "m", "md"),
        ("Other Page#sec", "alias", "wiki"),
    ]


def test_comment_spanning_lines_and_inline():
    lines, code = visible_lines("a <!-- x --> b\n<!-- start\n### 🔴 Q: hidden\nend --> tail\n```\n<!-- not a comment in code\n```\n")
    assert lines[0] == "a  b"
    assert lines[1] == "" and lines[2] == ""
    assert lines[3] == " tail"
    assert code[4:7] == [True, True, True]


def test_title_fallbacks():
    assert parse("no heading", "03-music/guitar-notes.md").title == "guitar-notes"
    assert parse("no heading", "01-garden/greenhouse/README.md").title == "greenhouse"


def test_answer_ends_at_next_field_or_heading():
    md = "### 🟢 Q: Q1?\n**A:** first\nmore\n**Context:** later ctx\n## Next\ntext\n"
    d = parse(md).doubts[0]
    assert d.answer == "first\nmore"
    assert d.context == "later ctx"


def test_parser_handles_real_world_oddities():
    md = "#NoSpace heading\n###   🔴   Q:   spaced?  \n- **2026-1-1** · bad date\n"
    p = parse(md)
    assert p.doubts[0].question == "spaced?"
    assert p.findings == []
