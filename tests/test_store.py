import os
import shutil

import pytest

from deskbrain.chunker import chunk_page
from deskbrain.links import LinkResolver
from deskbrain.parser import parse
from deskbrain.scanner import read_page, scan
from deskbrain.store import Index


@pytest.fixture
def desk_copy(desk, tmp_path):
    """A writable copy of the fixture desk (the fixture itself is never modified)."""
    dst = tmp_path / "desk"
    shutil.copytree(desk, dst)
    return dst


@pytest.fixture
def index(desk_copy, tmp_path):
    with Index(tmp_path / "data" / "index.sqlite", desk_copy) as ix:
        yield ix


def bump_mtime(path, seconds=10):
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + seconds))


def test_first_run_indexes_all_allowed_pages(index, desk_copy):
    st = index.update()
    expected = [f.rel for f in scan(desk_copy)]
    assert sorted(st.added) == expected
    assert index.page_rels() == expected
    assert st.changed == [] and st.removed == []
    c = index.counts()
    assert c["pages"] == len(expected)
    assert c["doubts"] > 0 and c["findings"] > 0 and c["chunks"] >= c["pages"]


def test_second_run_touches_nothing(index):
    index.update()
    st = index.update()
    assert st.n_changed == 0
    assert st.unchanged == st.scanned
    assert st.links_resolved == 0


def test_edit_one_page_reindexes_only_it(index, desk_copy):
    index.update()
    p = desk_copy / "02-cooking" / "bread.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n### 🔴 Q: Why did the crust burn?\n", encoding="utf-8")
    bump_mtime(p)
    before = index.counts()["open_doubts"]
    st = index.update()
    assert st.changed == ["02-cooking/bread.md"]
    assert st.added == [] and st.removed == []
    assert index.counts()["open_doubts"] == before + 1


def test_touch_without_content_change_is_not_a_change(index, desk_copy):
    index.update()
    bump_mtime(desk_copy / "01-garden" / "tomatoes.md")
    st = index.update()
    assert st.n_changed == 0 and st.touched == 1
    assert index.update().unchanged == st.scanned  # mtime was refreshed


def test_delete_page_removes_all_rows(index, desk_copy):
    index.update()
    (desk_copy / "03-music" / "guitar.md").unlink()
    st = index.update()
    assert st.removed == ["03-music/guitar.md"]
    for table, col in (("pages", "rel"), ("chunks", "rel"), ("headings", "rel"),
                       ("links", "src"), ("doubts", "rel"), ("findings", "rel")):
        n = index.db.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", ("03-music/guitar.md",)).fetchone()[0]
        assert n == 0, table
    # Links to it from other pages are now broken.
    row = index.db.execute("SELECT status FROM links WHERE src='03-music/practice.md' AND text='guitar'").fetchone()
    assert row["status"] == "broken"


def test_new_page_fixes_broken_link_elsewhere(index, desk_copy):
    index.update()
    q = "SELECT status, target FROM links WHERE src='03-music/practice.md' AND raw='lost.md'"
    assert index.db.execute(q).fetchone()["status"] == "broken"
    (desk_copy / "03-music" / "lost.md").write_text("# Lost\n\nFound again.\n", encoding="utf-8")
    st = index.update()
    assert st.added == ["03-music/lost.md"]
    row = index.db.execute(q).fetchone()
    assert (row["status"], row["target"]) == ("ok", "03-music/lost.md")


def test_stored_links_match_direct_resolution(index, desk_copy):
    index.update()
    pages = [parse(read_page(desk_copy, f.rel), f.rel) for f in scan(desk_copy)]
    direct = [(r.src, r.line, r.raw, r.status, r.target) for r in LinkResolver(pages, desk_copy).resolve_all()]
    stored = [tuple(r) for r in index.db.execute(
        "SELECT src, line, raw, status, target FROM links ORDER BY src, ord")]
    assert stored == direct


def test_stored_chunks_match_chunker_and_are_stable(index, desk_copy):
    index.update()
    direct = [c.id for f in scan(desk_copy) for c in chunk_page(parse(read_page(desk_copy, f.rel), f.rel))]
    assert [c.id for c in index.chunks()] == direct
    # Editing one section body keeps every chunk id.
    p = desk_copy / "01-garden" / "tomatoes.md"
    p.write_text(p.read_text(encoding="utf-8").replace("cherry plant", "cherry tomato plant"), encoding="utf-8")
    bump_mtime(p)
    assert index.update().changed == ["01-garden/tomatoes.md"]
    assert [c.id for c in index.chunks()] == direct
    assert any("cherry tomato plant" in c.text for c in index.chunks("01-garden/tomatoes.md"))


def test_privacy_nothing_private_in_index(index):
    index.update()
    assert index.check_privacy() == []
    rels = index.page_rels()
    for bad in ("activity/", "Personal/", "tools/", "_templates/", "_template/", ".obsidian/"):
        assert not any(bad in r for r in rels)
    texts = " ".join(r[0] for r in index.db.execute("SELECT text FROM chunks"))
    diary = (index.root / "Personal" / "diary.md").read_text(encoding="utf-8")
    secret_line = next(l for l in diary.splitlines() if len(l.strip()) > 20)
    assert secret_line.strip() not in texts


def test_index_refuses_to_live_inside_desk(desk_copy):
    with pytest.raises(ValueError):
        Index(desk_copy / "data" / "index.sqlite", desk_copy)


def test_reopen_persists_and_switching_desk_resets(desk_copy, tmp_path, desk):
    db = tmp_path / "data" / "ix.sqlite"
    with Index(db, desk_copy) as ix:
        n = len(ix.update().added)
    with Index(db, desk_copy) as ix:
        assert ix.update().n_changed == 0
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.md").write_text("# A\n", encoding="utf-8")
    with Index(db, other) as ix:
        st = ix.update()
        assert st.added == ["a.md"] and ix.page_rels() == ["a.md"]
    assert n > 1
