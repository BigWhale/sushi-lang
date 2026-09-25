"""A function type is not an extension target, and the refusal is one diagnostic (#771).

The extension-target tuple (`generics/extension_targets.py:CONCRETE_EXTENSION_TARGETS`)
answers "may an extension name this target" and nothing else. A function type is not in
it, and the collect pass refuses a declaration that names one (CE2110) at the target.
The body is not checked against a `self` that has no type, and a call of the refused
method is not an undefined name.

A method call on a function value is still INFERRED: inference asks the method-family
table for every receiver, so `f.clone()` has the type of `f`.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is absent, so the code
LIST is asserted here, on the analyze path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sushi_lang.semantics.generics.extension_targets import CONCRETE_EXTENSION_TARGETS
from sushi_lang.semantics.typesys import FunctionType

_TESTS = Path(__file__).resolve().parents[1]

_CASES = {
    "extensions/function_target/test_err_function_type_target.sushi": ["CE2110"],
    "extensions/function_target/test_err_function_type_static.sushi": ["CE2110"],
    "closures/function_value_method/test_err_fn_clone_into_i32.sushi": ["CE2002"],
    "closures/function_value_method/test_err_fn_clone_into_string.sushi": ["CE2002"],
}


def _codes(analyze, src: str) -> list[str]:
    return [item.code for item in analyze(src, name="ft").items
            if item.code.startswith("CE")]


@pytest.mark.parametrize("fixture", list(_CASES))
def test_one_fault_is_one_diagnostic(analyze, fixture):
    assert _codes(analyze, (_TESTS / fixture).read_text()) == _CASES[fixture]


def test_a_function_type_is_not_an_extension_target():
    assert not issubclass(FunctionType, CONCRETE_EXTENSION_TARGETS)


_METHOD_GENERIC = """extend fn(i32) -> i32 pick@(U)(U u) i32:
    return 1

fn main() i32:
    return Result.Ok(0)
"""


def test_a_method_generic_on_a_function_type_is_refused(analyze):
    assert _codes(analyze, _METHOD_GENERIC) == ["CE2110"]


def test_control_a_struct_target_is_accepted(analyze):
    src = ("struct Crate:\n    i32 w\n\nextend Crate seven() i32:\n    return 7\n\n"
           "fn main() i32:\n    let Crate c = Crate(1)\n    println(\"{c.seven()}\")\n"
           "    return Result.Ok(0)\n")
    assert _codes(analyze, src) == []
