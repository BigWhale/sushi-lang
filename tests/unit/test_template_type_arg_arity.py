"""A wrong type-argument count inside a template reads one located CE2062 (#807).

A position inside a generic declaration is a WRITTEN position: the count does not depend
on the type argument, so it is checked with no instance. When an instance is made too, the
instantiation must not report the same fault a second time without a location.

`EXPECT_ERROR_CODE` matches a SUBSTRING, so no fixture can count. This module counts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parent.parent / "generics" / "template_type_arg_arity"

_EXPECTED_LINE = {
    "test_err_template_arity_fn_param": 9,
    "test_err_template_arity_fn_return": 9,
    "test_err_template_arity_struct_field": 10,
    "test_err_template_arity_enum_payload": 10,
    "test_err_template_arity_with_an_instance": 10,
}

_BOX = "struct Box@(T, U):\n    T a\n    U b\n\n"
_MAIN = "fn main() i32:\n    return Result.Ok(0)\n"


def _errors(reporter):
    return [item for item in reporter.items if item.code.startswith("CE")]


@pytest.mark.parametrize("stem", sorted(_EXPECTED_LINE))
def test_a_template_position_reads_one_located_ce2062(analyze, stem):
    source = (_FIXTURES / f"{stem}.sushi").read_text()
    errors = _errors(analyze(source, name="m"))
    assert [e.code for e in errors] == ["CE2062"], [(e.code, e.message) for e in errors]
    (error,) = errors
    assert error.span is not None, error.message
    assert error.span.line == _EXPECTED_LINE[stem], error.span
    assert any(sub.span is not None for sub in error.sub), error.sub


def test_two_positions_in_one_template_read_one_each(analyze):
    source = (_BOX + "struct Wrap@(T):\n    Box@(T) a\n    Box@(T, T, T) b\n\n"
              "fn main() i32:\n    let Wrap@(i32) w = Wrap(Box(1, 2), Box(1, 2))\n"
              "    return Result.Ok(0)\n")
    errors = _errors(analyze(source, name="m"))
    assert [e.code for e in errors] == ["CE2062", "CE2062"], [
        (e.code, e.message) for e in errors]
    assert sorted(e.span.line for e in errors) == [6, 7]


def test_a_nested_position_in_a_template_is_checked(analyze):
    source = _BOX + "fn f@(T)(Maybe@(Box@(T)) m) i32:\n    return Result.Ok(0)\n\n" + _MAIN
    errors = _errors(analyze(source, name="m"))
    assert [e.code for e in errors] == ["CE2062"], [(e.code, e.message) for e in errors]
    assert errors[0].span is not None


def test_a_right_count_in_a_template_reads_nothing(analyze):
    """The control: the rule must not fire on a correct count in a template."""
    source = (_BOX + "struct Wrap@(T):\n    Box@(T, T) b\n\n"
              "enum Slot@(T):\n    Full(Box@(T, i32))\n    Empty\n\n"
              "fn f@(T)(Box@(T, T) b) Maybe@(Box@(T, T)):\n    return Result.Ok(Maybe.Some(b))\n\n"
              "fn main() i32:\n    let Wrap@(i32) w = Wrap(Box(1, 2))\n"
              "    return Result.Ok(0)\n")
    assert _errors(analyze(source, name="m")) == []


def test_a_generic_target_extension_parameter_is_a_template_position(analyze):
    source = (_BOX + "extend Box@(T, U) swap(Box@(U) other) i32:\n    return 1\n\n"
              "fn main() i32:\n    let Box@(i32, i32) b = Box(1, 2)\n"
              "    return Result.Ok(b.a)\n")
    errors = _errors(analyze(source, name="m"))
    assert [e.code for e in errors] == ["CE2062"], [(e.code, e.message) for e in errors]
    assert errors[0].span is not None and errors[0].span.line == 5
