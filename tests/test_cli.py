from deskbrain.__main__ import main


def test_cli_index_then_search(desk, tmp_path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--desk", str(desk), "index", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "added" in out and "PRIVACY" not in out
    assert main(["--desk", str(desk), "index", "--db", db]) == 0
    assert " 0 added, 0 changed, 0 removed" in capsys.readouterr().out

    assert main(["--desk", str(desk), "search", "sourdough starter", "--db", db, "--color", "never"]) == 0
    out = capsys.readouterr().out
    first = out.splitlines()[0]
    assert first.startswith(" 1. 02-cooking/bread.md:")
    assert "**starter**" in out

    assert main(["--desk", str(desk), "search", "zzqqxx", "--db", db]) == 1
