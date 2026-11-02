"""Configuration: where the desk lives, where data goes, what is never indexed."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DESKBRAIN_DATA", PROJECT_ROOT / "data"))

# Top-level folders that must never be indexed or shown (privacy rules in CLAUDE.md).
EXCLUDED_TOP_DIRS = frozenset({"activity", "Personal", "tools", "_templates"})
# Folder names skipped at any depth (page templates).
EXCLUDED_ANY_DIRS = frozenset({"_template", "_templates", "node_modules", "__pycache__"})


def desk_path() -> Path:
    """The desk root. Override with the DESK_PATH environment variable."""
    return Path(os.environ.get("DESK_PATH", Path.home() / "Personal-Desk")).expanduser().resolve()
