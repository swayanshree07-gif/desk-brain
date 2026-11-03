"""Embedding tests. Skipped if the ONNX model is not yet in data/models (run `deskbrain embed` once)."""

import shutil

import numpy as np
import pytest

from deskbrain.config import DATA_DIR
from deskbrain.embed import DIM, Embedder, VectorStore, model_cached
from deskbrain.store import Index

pytestmark = pytest.mark.skipif(not model_cached(), reason="embedding model not downloaded")


@pytest.fixture(scope="module")
def embedder():
    return Embedder()


def _chunks(root, db):
    with Index(db, root) as ix:
        ix.update()
        return ix.chunks()


def test_sync_embeds_once_then_nothing(desk, tmp_path, embedder):
    chunks = _chunks(desk, tmp_path / "ix.sqlite")
    vs = VectorStore(tmp_path / "vec")
    r = vs.sync(chunks, embedder)
    assert r == {"embedded": len(chunks), "kept": 0, "removed": 0}
    assert vs.vecs.shape == (len(chunks), DIM)
    assert np.allclose(np.linalg.norm(vs.vecs, axis=1), 1.0, atol=1e-4)
    # Reload from disk: nothing to embed.
    vs2 = VectorStore(tmp_path / "vec")
    assert vs2.sync(chunks, embedder) == {"embedded": 0, "kept": len(chunks), "removed": 0}
    assert np.allclose(vs2.vecs, vs.vecs)


def test_edit_reembeds_only_changed_chunks(desk, tmp_path, embedder):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    db = tmp_path / "ix.sqlite"
    vs = VectorStore(tmp_path / "vec")
    vs.sync(_chunks(d, db), embedder)
    p = d / "02-cooking" / "bread.md"
    p.write_text(p.read_text().replace("cold proof", "cold retard proof"))   # edits one section
    (d / "03-music" / "guitar.md").unlink()
    chunks = _chunks(d, db)
    r = vs.sync(chunks, embedder)
    assert r["embedded"] == 1 and r["removed"] >= 1
    assert len(vs.ids) == len(chunks)


@pytest.mark.parametrize("query, rel, heading_part", [
    ("my loaf stays flat and does not grow", "02-cooking/bread.md", "dough not rise"),
    ("leaves going pale on the tomato plant", "01-garden/tomatoes.md", "yellow"),
    ("how much light do young plants need", "01-garden/tomatoes.md", "Findings"),
])
def test_paraphrase_finds_right_chunk(desk, tmp_path, embedder, query, rel, heading_part):
    chunks = _chunks(desk, tmp_path / "ix.sqlite")
    vs = VectorStore(tmp_path / "vec")
    vs.sync(chunks, embedder)
    top_id, _ = vs.search(embedder.embed([query])[0], k=1)[0]
    top = next(c for c in chunks if c.id == top_id)
    assert top.rel == rel and heading_part in top.heading, (top.rel, top.heading)


def test_model_cache_is_small():
    size = sum(p.stat().st_size for p in (DATA_DIR / "models").rglob("*") if p.is_file())
    assert size < 300 * 1024 * 1024
