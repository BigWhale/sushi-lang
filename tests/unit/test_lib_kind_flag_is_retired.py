"""The binary library kind is retired, and `--lib-kind` with it: the command line refuses it.

An argparse error has no diagnostic code, so a `test_err_` fixture cannot name one. This
test reads the argument parser of `sushi_lang/compiler/cli.py` alone; it does not start
the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.compiler.cli import _parse_args


@pytest.mark.parametrize("kind", ["source", "binary"])
def test_the_lib_kind_flag_is_refused(kind, capsys):
    with pytest.raises(SystemExit) as excinfo:
        _parse_args(["x.sushi", "--lib", "--lib-version", "1.0.0", "--lib-kind", kind])
    assert excinfo.value.code == 2
    assert f"unrecognized arguments: --lib-kind {kind}" in capsys.readouterr().err


def test_a_library_build_needs_no_kind():
    args = _parse_args(["--lib", "--lib-version", "1.0.0", "x.sushi"])
    assert args.lib
    assert not hasattr(args, "lib_kind")
