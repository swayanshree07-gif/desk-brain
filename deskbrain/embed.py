"""Chunk embeddings with a small ONNX model (fastembed, no PyTorch).

Model: ``sentence-transformers/all-MiniLM-L6-v2`` (384-dim, ~90 MB), cached in
``data/models/`` (git-ignored). Vectors are L2-normalised float32.

Storage (``data/vectors.npy`` + ``data/vectors.json``): one row per chunk, with
the chunk id and the sha1 of the text that was embedded. ``VectorStore.sync``
embeds only chunks whose (id, text hash) is new, drops rows for chunks that no
longer exist and keeps the rest, so an edit re-embeds only the edited sections.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from .chunker import Chunk
from .config import DATA_DIR

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_DIR = DATA_DIR / "models"
DIM = 384


def embed_text(c: Chunk) -> str:
    """What gets embedded for a chunk: breadcrumb as context + section text."""
    crumb = " > ".join(c.breadcrumb)
    return f"{crumb}\n{c.text}" if crumb else c.text


def text_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


class Embedder:
    """Lazy wrapper so importing this module never loads the model."""

    def __init__(self, model_name: str = MODEL_NAME, cache_dir: Path = MODEL_DIR):
        self.model_name, self.cache_dir = model_name, Path(cache_dir)
        self._model = None
        self.calls = 0          # number of texts embedded (used by tests)

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir))
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        self.calls += len(texts)
        v = np.asarray(list(self._load().embed(texts, batch_size=64)), dtype=np.float32)
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(n, 1e-12)


def model_cached(cache_dir: Path = MODEL_DIR) -> bool:
    """True if the model files are already downloaded (tests skip otherwise)."""
    return any(Path(cache_dir).glob("**/*.onnx"))


class VectorStore:
    def __init__(self, base: Path):
        """``base`` without suffix, e.g. data/vectors -> vectors.npy + vectors.json."""
        self.npy, self.meta = Path(f"{base}.npy"), Path(f"{base}.json")
        self.ids: list[str] = []
        self.hashes: list[str] = []
        self.vecs = np.zeros((0, DIM), dtype=np.float32)
        if self.npy.exists() and self.meta.exists():
            m = json.loads(self.meta.read_text())
            vecs = np.load(self.npy)
            if m.get("model") == MODEL_NAME and len(m["ids"]) == len(vecs):
                self.ids, self.hashes, self.vecs = m["ids"], m["hashes"], vecs

    def sync(self, chunks: list[Chunk], embedder: Embedder) -> dict[str, int]:
        """Make the store match ``chunks``; embed only new/changed ones. Returns counts."""
        have = {(i, h): k for k, (i, h) in enumerate(zip(self.ids, self.hashes))}
        texts = [embed_text(c) for c in chunks]
        hashes = [text_hash(t) for t in texts]
        keep_rows, todo = [], []
        for n, (c, h) in enumerate(zip(chunks, hashes)):
            k = have.get((c.id, h))
            keep_rows.append(k)
            if k is None:
                todo.append(n)
        new = embedder.embed([texts[n] for n in todo])
        vecs = np.zeros((len(chunks), DIM), dtype=np.float32)
        for n, k in enumerate(keep_rows):
            if k is not None:
                vecs[n] = self.vecs[k]
        for j, n in enumerate(todo):
            vecs[n] = new[j]
        removed = len(set(have.values()) - {k for k in keep_rows if k is not None})
        self.ids, self.hashes, self.vecs = [c.id for c in chunks], hashes, vecs
        if todo or removed or not self.npy.exists():
            self.save()
        return {"embedded": len(todo), "kept": len(chunks) - len(todo), "removed": removed}

    def save(self) -> None:
        self.npy.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.npy.with_name(self.npy.stem + ".tmp.npy")
        np.save(tmp, self.vecs)
        os.replace(tmp, self.npy)
        self.meta.write_text(json.dumps({"model": MODEL_NAME, "ids": self.ids, "hashes": self.hashes}))

    def search(self, qvec: np.ndarray, k: int = 10) -> list[tuple[str, float]]:
        """(chunk id, cosine) for the top-k rows."""
        if not len(self.ids):
            return []
        sims = self.vecs @ qvec.reshape(-1)
        top = np.argsort(-sims)[:k]
        return [(self.ids[i], float(sims[i])) for i in top]
