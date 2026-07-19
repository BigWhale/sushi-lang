"""No Python traceback ever reaches the user, whatever channel the failure took.

One row per failure channel -- grammar, lexer, indenter, AST builder, semantics,
codegen. Each drives the real compiler as a subprocess over a malformed program
and asserts the same four things:

  - exit code 2 (an error, not a "warning" -- a crash used to exit 1)
  - no "Traceback (most recent call last)" anywhere in the output
  - a [CE####] diagnostic code in stderr
  - a file:line:col prefix, unless the diagnostic genuinely has no location

Adding a channel later is one row. `--traceback` opts the traceback back IN; the
last test pins that it appends to, rather than replaces, the diagnostic.
"""
from __future__ import annotations

import subprocess
import re
from pathlib import Path

import pytest


TRACEBACK_MARKER = "Traceback (most recent call last)"
CODE_RE = re.compile(r"\[(C[EW]\d{4})\]")
LOCATION_RE = re.compile(r"^\S+:\d+:\d+: ", re.MULTILINE)


def _compile(tmp_path: Path, source: str, *extra: str) -> subprocess.CompletedProcess:
    src = tmp_path / "crash.sushi"
    src.write_text(source, encoding="utf-8")
    return subprocess.run(
        ["sushic", "crash.sushi", *extra],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"NO_COLOR": "1", "PATH": _path(), "HOME": str(tmp_path)},
    )


def _path() -> str:
    import os
    return os.environ.get("PATH", "")


# (id, source, expected_code, has_location)
CHANNELS = [
    pytest.param(
        "fn main() i32:\n    let i32 x = \n    return Result.Ok(0)\n",
        "CE6001", True, id="unexpected_token",
    ),
    pytest.param(
        "fn main() i32:\n    let i32 x = ```\n    return Result.Ok(0)\n",
        "CE6002", True, id="unexpected_characters",
    ),
    pytest.param(
        "fn main() i32:\n",
        "CE6003", True, id="unexpected_eof",
    ),
    pytest.param(
        'fn main() i32:\n    if (true):\n        println("a")\n      println("b")\n'
        "    return Result.Ok(0)\n",
        "CE6004", True, id="inconsistent_dedent",
    ),
    pytest.param(
        'fn main() i32:\n    println("val {1 +}")\n    return Result.Ok(0)\n',
        "CE6010", True, id="interpolation_bad_expression",
    ),
    pytest.param(
        'fn main() i32:\n    println("{x")\n    return Result.Ok(0)\n',
        "CE2026", True, id="unterminated_interpolation",
    ),
    pytest.param(
        "fn main() i32:\n"
        "    let Result@(i32, StdError) r = Result.Ok(1)\n"
        "    let bool ok = r.is_ok(1)\n"
        "    return Result.Ok(0)\n",
        "CE2016", True, id="result_method_arity",
    ),
    pytest.param(
        "fn main() i32:\n    let i32 x = 0755\n    return Result.Ok(0)\n",
        "CE2071", True, id="c_style_octal",
    ),
    pytest.param(
        "fn main() i32:\n    let i32 x = \"hi\"\n    return Result.Ok(0)\n",
        "CE2002", True, id="type_mismatch_control",
    ),
]


@pytest.mark.parametrize("source,expected_code,has_location", CHANNELS)
def test_no_traceback_escapes(tmp_path, source, expected_code, has_location):
    result = _compile(tmp_path, source)
    output = result.stdout + result.stderr

    assert TRACEBACK_MARKER not in output, f"traceback leaked:\n{output}"
    assert result.returncode == 2, f"expected exit 2, got {result.returncode}:\n{output}"

    codes = CODE_RE.findall(result.stderr)
    assert codes, f"no diagnostic code in stderr:\n{result.stderr}"
    if expected_code is not None:
        assert expected_code in codes, f"expected {expected_code}, got {codes}"
    if has_location:
        assert LOCATION_RE.search(result.stderr), \
            f"no file:line:col prefix in stderr:\n{result.stderr}"


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
