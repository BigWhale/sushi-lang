"""`--lib-kind` accepts `source` and `binary` only; the hybrid kind is removed.

An argparse error has no diagnostic code, so a `test_err_` fixture cannot name one. This
test reads the argument parser of `sushi_lang/compiler/cli.py` alone; it does not start
the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.compiler.cli import _parse_args


@pytest.mark.parametrize("kind", ["source", "binary"])
def test_a_kind_is_accepted(kind):
    args = _parse_args(["--lib", "--lib-version", "1.0.0", "--lib-kind", kind, "x.sushi"])
    assert args.lib_kind == kind


def test_the_hybrid_kind_is_refused(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _parse_args(["--lib", "--lib-version", "1.0.0", "--lib-kind", "hybrid", "x.sushi"])
    assert excinfo.value.code == 2
    assert "argument --lib-kind: invalid choice: 'hybrid'" in capsys.readouterr().err
