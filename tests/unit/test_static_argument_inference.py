"""A generic static solves its type arguments from the ARGUMENTS first, and from the
propagation stamp for what no parameter names (#573, MIG.md R5).

Three gates:
  - the one solver answers both passes (the instantiate pass collects what the
    arguments name; the typecheck pass resolves through the same function),
  - a type parameter neither source reaches is CE2060, and the text names BOTH sources
    and the parameter it could not reach -- only that one, when another was solved,
  - the refusal fires once, with no second code beside it.
"""
from __future__ import annotations

from sushi_lang.semantics.statics import solve_target_type_args
from sushi_lang.semantics.typesys import BuiltinType, UnknownType


class _Param:
    def __init__(self, ty):
        self.ty = ty


class _Template:
    def __init__(self, type_params, params):
        self.type_params = type_params
        self.params = params


def _codes(reporter):
    return [item.code for item in reporter.items if item.code.startswith("CE")]


def _ce2060(reporter):
    return [item for item in reporter.items if item.code == "CE2060"]


CAGE = """
struct Cage@(T):
    T[] items

extend Cage@(T) static empty() Cage@(T):
    return Cage(from([]))

fn main() i32:
    println("{Cage.empty().items.len()}")
    return Result.Ok(0)
"""

PAIR = """
struct Pair@(A, B):
    A first
    B[] rest

extend Pair@(A, B) static of_first(A a) Pair@(A, B):
    return Pair(a, from([]))

fn main() i32:
    println("{Pair.of_first(3).first}")
    return Result.Ok(0)
"""


# 1. The solver

def test_an_argument_solves_the_parameter_it_names():
    template = _Template(("T",), [_Param(UnknownType("T"))])
    args, unsolved = solve_target_type_args(template, [BuiltinType.I32], None)
    assert args == (BuiltinType.I32,)
    assert unsolved == ()


def test_the_stamp_answers_what_no_argument_names():
    template = _Template(("A", "B"), [_Param(UnknownType("A"))])
    args, unsolved = solve_target_type_args(
        template, [BuiltinType.I32], (BuiltinType.BOOL, BuiltinType.STRING))
    assert args == (BuiltinType.I32, BuiltinType.STRING)
    assert unsolved == ()


def test_the_argument_wins_over_the_stamp():
    """Arguments FIRST: a stamp that disagrees is not consulted for a solved parameter."""
    template = _Template(("T",), [_Param(UnknownType("T"))])
    args, _ = solve_target_type_args(template, [BuiltinType.I32], (BuiltinType.BOOL,))
    assert args == (BuiltinType.I32,)


def test_a_parameter_neither_source_reaches_is_named():
    template = _Template(("A", "B"), [_Param(UnknownType("A"))])
    args, unsolved = solve_target_type_args(template, [BuiltinType.I32], None)
    assert args is None
    assert unsolved == ("B",)


def test_an_argument_with_no_type_yet_solves_nothing():
    template = _Template(("T",), [_Param(UnknownType("T"))])
    args, unsolved = solve_target_type_args(template, [None], None)
    assert args is None
    assert unsolved == ("T",)


# 2. The refusal

def test_no_source_names_both_sources_and_the_parameter(analyze):
    reporter = analyze(CAGE)
    items = _ce2060(reporter)
    assert len(items) == 1
    message = items[0].message
    assert "'T'" in message
    assert "no argument names" in message
    assert "declares no type" in message


def test_a_solved_parameter_is_not_named(analyze):
    reporter = analyze(PAIR)
    items = _ce2060(reporter)
    assert len(items) == 1
    message = items[0].message
    assert "'B'" in message
    assert "'A'" not in message


def test_the_refusal_is_one_diagnostic(analyze):
    assert _codes(analyze(CAGE)) == ["CE2060"]
    assert _codes(analyze(PAIR)) == ["CE2060"]
