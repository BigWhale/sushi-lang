"""An enum cycle is ONE CE2095 per cycle, located, with the chain -- the struct rule (#677).

`EXPECT_ERROR_CODE` is a substring check and cannot pin "one fault, one diagnostic", so
the code SET, the count and the span are pinned here. Before the ruling of 2026-09-14 a
pure enum cycle was CE2052 from the derive pass: a file name and no caret, once per
member, and once more per late intern, because the derive pass re-runs for every
instance a call site solves. The finite-types pass owns every inline cycle now -- a
struct field, a fixed-array element, an enum payload -- and it runs before the derive
pass, once, so the count is one by construction.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sushic_path import SUSHIC, needs_sushic

TWO_ENUMS = """
enum A:
    X(B)

enum B:
    Y(A)

fn main() i32:
    return Result.Ok(0)
"""

SELF_ENUM = """
enum BadList:
    Cons(i32, BadList)
    Nil()

fn main() i32:
    return Result.Ok(0)
"""

FIXED_ARRAY_OF_SELF = """
enum E:
    V(E[2])
    Leaf(i32)

fn main() i32:
    return Result.Ok(0)
"""

THREE_ENUMS = """
enum A:
    X(B)

enum B:
    Y(C)

enum C:
    Z(A)
    Leaf(i32)

fn main() i32:
    return Result.Ok(0)
"""

TWO_SEPARATE_CYCLES = """
enum A:
    X(B)

enum B:
    Y(A)

enum C:
    Z(D)

enum D:
    W(C)

fn main() i32:
    return Result.Ok(0)
"""

STRUCT_THEN_ENUM = """
struct S:
    i32 value
    E e

enum E:
    V(S)

fn main() i32:
    return Result.Ok(0)
"""

ENUM_THEN_STRUCT = """
enum E:
    V(S)

struct S:
    i32 value
    E e

fn main() i32:
    return Result.Ok(0)
"""

ENUM_ARRAY_OF_STRUCT = """
enum E:
    V(S[2])

struct S:
    i32 value
    E e

fn main() i32:
    return Result.Ok(0)
"""

GENERIC_SELF_PAYLOAD = """
enum Tree@(T):
    Leaf(T)
    Node(Tree@(T))

fn main() i32:
    let Tree@(i32) t = Tree.Leaf(1)
    match t:
        Tree.Leaf(v) -> return Result.Ok(v)
        Tree.Node(_) -> return Result.Ok(0)
"""

THROUGH_A_GENERIC = """
enum Wrap@(T):
    W(T)

enum Ring:
    R(Wrap@(Ring))

fn main() i32:
    return Result.Ok(0)
"""

LATE_METHOD_TYPE_PARAM = """
enum Tree@(T):
    Leaf(T)
    Node(Tree@(T))

extend List@(T) wrap@(U)(U x) Tree@(U):
    return Tree.Leaf(x)

fn main() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    match xs.wrap(true):
        Tree.Leaf(_) -> return Result.Ok(0)
        Tree.Node(_) -> return Result.Ok(1)
"""

LATE_INTERN_BESIDE_A_CYCLE = """\
use <collections/iter>

enum A:
    X(B)

enum B:
    Y(A)

fn main() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    let i32 n = xs.map(|i32 x| x > 0).realise(List.new()).len()
    return Result.Ok(n)
"""

OWN_INDIRECTION_IS_FINITE = """
enum IntList:
    Cons(i32, Own@(IntList))
    Nil()

fn main() i32:
    return Result.Ok(0)
"""

DYNAMIC_ARRAY_IS_FINITE = """
enum Tree:
    Node(Tree[])
    Leaf(i32)

fn main() i32:
    return Result.Ok(0)
"""


def _errors(reporter):
    return [d for d in reporter.items if d.kind == "error"]


def _one_ce2095(reporter):
    errors = _errors(reporter)
    assert [d.code for d in errors] == ["CE2095"], [
        (d.code, d.message) for d in errors]
    return errors[0]


def test_two_enums_are_one_diagnostic_at_the_first_member(analyze):
    only = _one_ce2095(analyze(TWO_ENUMS))
    assert "A refers to B refers to A" in only.message
    assert only.span is not None, "the first member's declaration has a location"
    assert (only.span.line, only.span.col) == (2, 6), "the name in `enum A:`"


def test_a_self_holding_enum_names_itself_twice(analyze):
    only = _one_ce2095(analyze(SELF_ENUM))
    assert "BadList refers to BadList" in only.message
    assert only.span is not None and (only.span.line, only.span.col) == (2, 6)


def test_a_fixed_array_of_self_is_a_cycle(analyze):
    """This shape compiled with exit 0 before #677: neither pass followed the array."""
    only = _one_ce2095(analyze(FIXED_ARRAY_OF_SELF))
    assert "E refers to E" in only.message
    assert only.span is not None and (only.span.line, only.span.col) == (2, 6)


def test_a_three_hop_cycle_names_every_hop(analyze):
    only = _one_ce2095(analyze(THREE_ENUMS))
    assert "A refers to B refers to C refers to A" in only.message


def test_two_separate_cycles_are_two_diagnostics(analyze):
    errors = _errors(analyze(TWO_SEPARATE_CYCLES))
    assert [d.code for d in errors] == ["CE2095", "CE2095"]
    assert "A refers to B refers to A" in errors[0].message
    assert "C refers to D refers to C" in errors[1].message


@pytest.mark.parametrize("source, struct_line", [
    (STRUCT_THEN_ENUM, 2),
    (ENUM_THEN_STRUCT, 5),
    (ENUM_ARRAY_OF_STRUCT, 5),
])
def test_a_mixed_cycle_is_one_diagnostic_at_the_struct(analyze, source, struct_line):
    """The walk visits every struct before any enum, so a mixed cycle starts at its
    struct whatever the declaration order -- the shape the struct twin had before.
    """
    only = _one_ce2095(analyze(source))
    assert "S refers to E refers to S" in only.message
    assert only.span is not None and (only.span.line, only.span.col) == (struct_line, 8)


def test_a_generic_instance_in_its_own_payload_is_one_diagnostic(analyze):
    _one_ce2095(analyze(GENERIC_SELF_PAYLOAD))


def test_a_cycle_through_a_generic_wrapper_is_one_diagnostic(analyze):
    only = _one_ce2095(analyze(THROUGH_A_GENERIC))
    assert only.span is not None and (only.span.line, only.span.col) == (5, 6), (
        "Ring is the first DECLARED member of the cycle; the instance came later")


def test_a_late_interned_instance_is_still_one_diagnostic(analyze):
    """`Tree@(bool)` is interned after the whole-program passes ran, so the cycle exists
    only from that moment. The derive pass used to catch it; the finite-types check must.
    """
    _one_ce2095(analyze(LATE_METHOD_TYPE_PARAM))


@pytest.mark.parametrize("source", [OWN_INDIRECTION_IS_FINITE, DYNAMIC_ARRAY_IS_FINITE])
def test_an_indirection_is_not_a_cycle(analyze, source):
    assert _errors(analyze(source)) == []


@needs_sushic
def test_a_late_intern_does_not_repeat_the_diagnostic(tmp_path: Path):
    """End to end: the `List@(bool)` shape printed the enum cycle FOUR times before #677."""
    (tmp_path / "late.sushi").write_text(LATE_INTERN_BESIDE_A_CYCLE, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "late.sushi", "--cache-dir", str(tmp_path / "cache")],
        cwd=tmp_path, capture_output=True, text=True,
        env={"NO_COLOR": "1", "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr.count("[CE2095]") == 1, result.stderr
    assert "CE2052" not in result.stderr, result.stderr
