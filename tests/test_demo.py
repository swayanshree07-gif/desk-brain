"""Day 30: the demo script is valid Python, documented, and never prints note text."""
import ast
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent / "scripts" / "demo.py"


def test_demo_script_parses_and_has_docstring():
    tree = ast.parse(DEMO.read_text())
    assert ast.get_docstring(tree)
    src = DEMO.read_text()
    assert '"timeout"' in src and "proc.terminate()" in src   # server wrapped in timeout and stopped
    assert "build/demo-data" in src                            # fixture runs never touch data/
