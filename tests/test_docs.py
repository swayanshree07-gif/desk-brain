"""Day 29: docs and the one-command check script stay in place."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_has_core_sections():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in ("## Install", "## Run", "## Privacy rules", "## Architecture", "## Checks", "## Status"):
        assert heading in text, heading
    assert "scripts/check.sh" in text and "127.0.0.1:8765" in text


def test_check_script_is_valid_bash_and_uses_scratch_data():
    script = ROOT / "scripts" / "check.sh"
    assert script.stat().st_mode & 0o111, "check.sh must be executable"
    subprocess.run(["bash", "-n", str(script)], check=True)
    body = script.read_text()
    assert "DESKBRAIN_DATA" in body  # fixture runs must never overwrite the real data/ index
    assert "timeout" in body


def test_every_module_has_a_docstring():
    for f in sorted((ROOT / "deskbrain").glob("*.py")):
        assert f.read_text(encoding="utf-8").lstrip().startswith('"""'), f.name
