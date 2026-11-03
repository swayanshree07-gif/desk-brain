import os

import pytest

from deskbrain.scanner import is_allowed, read_page, scan

EXPECTED = [
    "01-garden/greenhouse/README.md",
    "01-garden/tomatoes.md",
    "02-cooking/bread.md",
    "03-music/guitar.md",
    "03-music/practice.md",
    "README.md",
]


def test_scan_lists_only_allowed_pages(desk):
    assert [f.rel for f in scan(desk)] == EXPECTED


@pytest.mark.parametrize("rel", [
    "activity/2026-11-01.md", "Personal/diary.md", "tools/notes.md", "_templates/page.md",
    "03-music/_template/README.md", ".obsidian/cache.md", ".git/HEAD.md", "02-cooking/recipe.pdf",
    "../outside.md", "/etc/passwd.md", "a/../activity/x.md",
])
def test_excluded_paths_rejected(rel):
    assert not is_allowed(rel)


def test_nested_activity_dir_is_fine_but_top_level_is_not():
    # Only the top-level private folders are excluded by name.
    assert is_allowed("01-projects/activity/notes.md")
    assert not is_allowed("activity/notes.md")


def test_read_page_refuses_private(desk):
    with pytest.raises(PermissionError):
        read_page(desk, "Personal/diary.md")
    with pytest.raises(PermissionError):
        read_page(desk, "activity/2026-11-01.md")
    assert "Sourdough" in read_page(desk, "02-cooking/bread.md")


def test_private_text_never_reachable(desk):
    text = "".join(read_page(desk, f.rel) for f in scan(desk))
    assert "must never be indexed" not in text
    assert "skipped" not in text


def test_symlinks_not_followed(tmp_path):
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "s.md").write_text("# secret")
    d = tmp_path / "desk"
    d.mkdir()
    (d / "ok.md").write_text("# ok")
    os.symlink(tmp_path / "secret", d / "linkdir")
    os.symlink(tmp_path / "secret" / "s.md", d / "link.md")
    (d / ".git").mkdir()
    (d / ".git" / "x.md").write_text("# git")
    assert [f.rel for f in scan(d)] == ["ok.md"]
    with pytest.raises(PermissionError):
        read_page(d, "link.md")


def test_scan_does_not_modify_desk(desk):
    before = {p: p.stat().st_mtime_ns for p in desk.rglob("*")}
    scan(desk)
    after = {p: p.stat().st_mtime_ns for p in desk.rglob("*")}
    assert before == after
