"""No Python traceback ever reaches the user, whatever channel the failure took."""
from __future__ import annotations




TRACEBACK_MARKER = "Traceback (most recent call last)"






# (id, source, expected_code, has_location)




def test_internal_compiler_error_is_reported_not_dumped(tmp_path, monkeypatch):
    """An unexpected exception anywhere becomes a CE0000 diagnostic, not a dump."""
    from sushi_lang.compiler import cli

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "_run", _boom)

    src = tmp_path / "ok.sushi"
    src.write_text("fn main() i32:\n    return Result.Ok(0)\n", encoding="utf-8")

    rc = cli.main([str(src)])
    assert rc == 2


def test_traceback_flag_appends_to_the_diagnostic(tmp_path, capsys, monkeypatch):
    """--traceback adds the Python trace; it does not replace the diagnostic."""
    from sushi_lang.compiler import cli

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "_run", _boom)
    monkeypatch.setenv("NO_COLOR", "1")

    src = tmp_path / "ok.sushi"
    src.write_text("fn main() i32:\n    return Result.Ok(0)\n", encoding="utf-8")

    rc = cli.main(["--traceback", str(src)])
    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert rc == 2
    assert "CE0000" in output
    assert TRACEBACK_MARKER in output
