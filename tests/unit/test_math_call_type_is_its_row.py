"""Every `<math>` call has the type of its signature row, bare and behind an alias (#926).

A call with a known type is compared with its position, so an f64 result in an i32
declaration is CE2002 for every function. `EXPECT_ERROR_CODE` is a substring test and
cannot prove that a code is alone, so the code SET is asserted here, on the analyze path.
"""
from __future__ import annotations

import pytest

from sushi_lang.sushi_stdlib.src.math import MATH_FAMILIES, MATH_SIGNATURES


def _codes(analyze, src: str) -> set[str]:
    return {item.code for item in analyze(src, name="mt").items
            if item.code.startswith("CE")}


def _program(use: str, call: str, declared: str) -> str:
    return (f"{use}\n\nfn main() i32:\n    let {declared} a = {call}\n"
            "    println(\"{a}\")\n    return Result.Ok(0)\n")


def _f64_call(name: str) -> str:
    arity = len(MATH_SIGNATURES[name].params)
    return f"{name}({', '.join(['1.0'] * arity)})"


def _family_call(name: str) -> str:
    arity, _types = MATH_FAMILIES[name]
    return f"{name}({', '.join(['1.0'] * arity)})"


@pytest.mark.parametrize("name", sorted(MATH_SIGNATURES))
def test_a_bare_call_has_the_type_of_its_row(analyze, name):
    src = _program("use <math>", _f64_call(name), "i32")
    assert _codes(analyze, src) == {"CE2002"}


@pytest.mark.parametrize("name", sorted(MATH_SIGNATURES))
def test_an_alias_call_has_the_type_of_its_row(analyze, name):
    src = _program("use <math> as m", "m." + _f64_call(name), "i32")
    assert _codes(analyze, src) == {"CE2002"}


@pytest.mark.parametrize("name", sorted(MATH_FAMILIES))
def test_a_family_call_has_the_type_of_its_row(analyze, name):
    src = _program("use <math>", _family_call(name), "i32")
    assert _codes(analyze, src) == {"CE2002"}


@pytest.mark.parametrize("name", sorted(MATH_SIGNATURES))
def test_a_matching_declaration_is_clean(analyze, name):
    """The control: the row's own type is accepted."""
    src = _program("use <math>", _f64_call(name), "f64")
    assert _codes(analyze, src) == set()


@pytest.mark.parametrize("name", ["sin", "abs"])
def test_a_bare_call_under_an_alias_is_one_fault(analyze, name):
    """Under `use <math> as m` the bare name is not in scope: CE2008 and nothing more."""
    call = _f64_call(name) if name in MATH_SIGNATURES else _family_call(name)
    src = _program("use <math> as m", call, "i32")
    assert _codes(analyze, src) == {"CE2008"}
