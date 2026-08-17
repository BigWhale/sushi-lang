"""A name under a borrow is classified ONCE, by the pass that owns names."""
from __future__ import annotations

import pytest


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def _borrowing(setup: str, borrowed: str) -> str:
    return (
        f"{setup}"
        "fn read_value(peek i32 x) i32:\n"
        "    return Result.Ok(x)\n"
        "\n"
        "fn main() i32:\n"
        f"    let i32 y = read_value(peek {borrowed}).realise(0)\n"
        "    println(\"{y}\")\n"
        "    return Result.Ok(0)\n"
    )


def test_undeclared_name_reports_exactly_one_diagnostic(analyze):
    """One token, one diagnostic. CE2400 no longer piles on."""
    codes = _codes(analyze(_borrowing("", "nope")))
    assert codes.count("CE1001") == 1
    assert "CE2400" not in codes


def test_undeclared_member_base_reports_exactly_one_diagnostic(analyze):
    """`peek nope.x` double-reported through the member-access arm as well."""
    src = (
        "struct P:\n"
        "    i32 x\n"
        "\n"
        "fn read_value(peek i32 v) i32:\n"
        "    return Result.Ok(v)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 y = read_value(peek nope.x).realise(0)\n"
        "    println(\"{y}\")\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert codes.count("CE1001") == 1
    assert "CE2400" not in codes


# The names that EXIST but are not storage a frame owns. Each used to be reported as an
# undeclared identifier, which is plainly false, and then reported a second time.

NON_LOCALS = {
    "constant":      ("const i32 LIMIT = 10\n\n", "LIMIT"),
    "function":      ("fn helper() i32:\n    return Result.Ok(1)\n\n", "helper"),
}


@pytest.mark.parametrize("kind", sorted(NON_LOCALS))
def test_borrowing_a_non_local_is_CE2400_only(analyze, kind):
    setup, borrowed = NON_LOCALS[kind]
    codes = _codes(analyze(_borrowing(setup, borrowed)))
    assert codes.count("CE2400") == 1, f"{kind}: got {codes}"
    assert "CE1001" not in codes, f"{kind}: got {codes}"


def test_borrowing_a_local_is_clean(analyze):
    """The green mirror: a real local borrows with no diagnostic at all."""
    src = (
        "fn read_value(peek i32 x) i32:\n"
        "    return Result.Ok(x)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 n = 42\n"
        "    let i32 y = read_value(peek n).realise(0)\n"
        "    println(\"{y}\")\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE1001" not in codes
    assert "CE2400" not in codes
