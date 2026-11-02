"""Walk the desk and list Markdown pages that are allowed to be indexed.

Read-only: this module only lists and opens files for reading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import EXCLUDED_ANY_DIRS, EXCLUDED_TOP_DIRS


@dataclass(frozen=True)
class PageFile:
    rel: str          # desk-relative POSIX path, e.g. "04-ai-ml/nlp.md"
    path: Path        # absolute path
    mtime: float
    size: int


def is_allowed(rel: str) -> bool:
    """True if a desk-relative path may be indexed. Used as a guard on every read."""
    parts = PurePosixPath(rel).parts
    if not parts or rel.startswith("/") or ".." in parts:
        return False
    if parts[0] in EXCLUDED_TOP_DIRS:
        return False
    for p in parts[:-1]:
        if p.startswith(".") or p in EXCLUDED_ANY_DIRS:
            return False
    name = parts[-1]
    return name.lower().endswith(".md") and not name.startswith(".")


def scan(root: Path) -> list[PageFile]:
    """All allowed Markdown pages under root, sorted by relative path.

    Symlinks are not followed, so nothing outside the desk is reached.
    """
    root = Path(root).resolve()
    pages: list[PageFile] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        depth0 = rel_dir == "."
        # Prune excluded directories in place so we never descend into them.
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".")
            and d not in EXCLUDED_ANY_DIRS
            and not (depth0 and d in EXCLUDED_TOP_DIRS)
        )
        for fn in sorted(filenames):
            rel = fn if depth0 else f"{rel_dir}/{fn}"
            if not is_allowed(rel):
                continue
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            st = p.stat()
            pages.append(PageFile(rel=rel, path=p, mtime=st.st_mtime, size=st.st_size))
    pages.sort(key=lambda f: f.rel)
    return pages


def read_page(root: Path, rel: str) -> str:
    """Read one allowed page as text. Raises PermissionError for excluded paths."""
    if not is_allowed(rel):
        raise PermissionError(f"not allowed: {rel}")
    root = Path(root).resolve()
    p = (root / rel).resolve()
    if root not in p.parents:
        raise PermissionError(f"outside desk: {rel}")
    return p.read_text(encoding="utf-8", errors="replace")
