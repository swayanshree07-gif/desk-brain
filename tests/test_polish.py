"""Day 28 polish: first-run empty states, help overlay fits a phone, dark-safe styles."""
import re

from fastapi.testclient import TestClient

from deskbrain.app import STATIC_DIR, create_app


def read(name):
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_graph_first_run_empty_state():
    js = read("graph.js")
    assert "no pages indexed yet" in js
    assert "No pages to show with these filters." in js


def test_review_first_run_empty_state():
    js = read("app.js")
    assert "No flashcards yet." in js
    assert "s.counts.total ?" in js


def test_help_overlay_fits_375px():
    css = read("style.css")
    block = re.search(r"\.keys-help \{[^}]*\}", css).group(0)
    assert "max-width: calc(100vw - 2rem)" in block
    assert "max-height" in block and "overflow: auto" in block
    assert "@media (max-width: 420px)" in css


def test_no_hardcoded_light_background_in_sources():
    assert "#f6f6f4" not in read("style.css")
    assert ":focus-visible" in read("style.css")


def test_polished_assets_served(desk, tmp_path):
    c = TestClient(create_app(desk, tmp_path / "ix.sqlite", semantic=False))
    for name in ("style.css", "app.js", "graph.js"):
        r = c.get(f"/static/{name}")
        assert r.status_code == 200
