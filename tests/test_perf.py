import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("perf", ROOT / "scripts" / "perf.py")
perf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(perf)


def test_perf_report_on_fixture():
    r = perf.run(ROOT / "tests" / "fixtures" / "desk", semantic=False)
    assert r["pages"] > 0 and r["noop_changed"] == 0
    assert set(r["query"]) == {"keyword"} and r["semantic"] is False
    assert r["problems"] == []
    assert r["temp_index_mb"] < 5
