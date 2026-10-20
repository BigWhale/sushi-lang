"""The integer operator kernel: the one home of the integer operator semantics.

Pure functions over an operator, its operand values and the integer type of the
operation. The AST constant evaluator calls them (`const_eval.py`), and docs/design/ir.md
7.4 rules that the fold of a readable SHIR value calls them too. The module reads no AST
node.
`docs/design/compile-time-evaluation.md` is normative for which operator gets which rule:

- An OVERFLOW-CHECKED operator (`+ - * / %` and the negation) gives the exact result and
  whether it left the type (CE2077 is the caller's to report).
- A WIDTH-DEFINED operator (`~ & | ^ << >>`) and a cast keep the bits that the type
  holds, and never leave the type.

A value that the kernel takes is in the range of its type. The gate is
`tests/unit/test_integer_operator_semantics_agree.py`.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Union

from sushi_lang.internals import errors as er
from sushi_lang.semantics.integer_width import (
    fits_integer_type, integer_bit_width, wrap_to_integer_type)
from sushi_lang.semantics.typesys import Type


@dataclass(frozen=True)
class Folded:
    """The value of an operation, and whether it left the type.

    When `overflows` is true, `value` is the exact result that the type cannot hold. For
    `%` it is the quotient, because the machine divides first.
    """
    value: int
    overflows: bool = False


class Refusal(Enum):
    """An operation that has no value."""
    DIVISION_BY_ZERO = "division by zero"
    NEGATIVE_SHIFT = "shift by negative amount"


Answer = Union[Folded, Refusal]

_EXACT: Mapping[str, Callable[[int, int], int]] = {
    "+": operator.add, "-": operator.sub, "*": operator.mul,
}

_BITWISE: Mapping[str, Callable[[int, int], int]] = {
    "&": operator.and_, "|": operator.or_, "^": operator.xor,
}

#: The overflow-checked binary operators and the width-defined ones.
OVERFLOW_CHECKED = frozenset({*_EXACT, "/", "%"})
WIDTH_DEFINED = frozenset({*_BITWISE, "<<", ">>"})

_COMPARISONS: Mapping[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq, "!=": operator.ne,
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}

#: The comparison operators, and the ones of them that ORDER their operands.
COMPARISONS = frozenset(_COMPARISONS)
ORDERINGS = frozenset({"<", "<=", ">", ">="})


def binary(op: str, left: int, right: int, ty: Type) -> Answer:
    """`left op right` at the integer type `ty`, the type of the left operand."""
    width = _width(ty)
    if op in _EXACT:
        return _checked(_EXACT[op](left, right), ty)
    if op in ("/", "%"):
        return _divide(op, left, right, ty)
    if op in _BITWISE:
        return Folded(wrap_to_integer_type(_BITWISE[op](left, right), ty))
    if op in ("<<", ">>"):
        return _shift(op, left, right, ty, width)
    er.raise_internal_error("CE0000", detail=f"no integer operator {op!r}")


def negate(value: int, ty: Type) -> Folded:
    """`-value`, overflow-checked: the negation of the smallest signed value leaves the type."""
    _width(ty)
    return _checked(-value, ty)


def complement(value: int, ty: Type) -> int:
    """`~value`, with the bits that `ty` holds: `~255` is 0 as a `u8` and -256 as an `i64`."""
    _width(ty)
    return wrap_to_integer_type(~value, ty)


def cast(value: int, ty: Type) -> int:
    """`value as ty`: the bit pattern, truncated. A cast is the escape from the overflow rule."""
    _width(ty)
    return wrap_to_integer_type(value, ty)


def compare(op: str, left: Any, right: Any) -> bool:
    """`left op right` for a comparison operator. A held value is in its range, so the order
    of the numbers is the order of the machine, at every width."""
    return _COMPARISONS[op](left, right)


def _width(ty: Type) -> int:
    width = integer_bit_width(ty)
    if width is None:
        er.raise_internal_error("CE0000", detail=f"an integer operator at the type {ty}")
    return width


def _checked(value: int, ty: Type) -> Folded:
    return Folded(value, overflows=not fits_integer_type(value, ty))


def _divide(op: str, left: int, right: int, ty: Type) -> Answer:
    """`/` and `%` truncate toward zero, as sdiv and srem do. Python floors (#441).

    The remainder overflows exactly where the quotient does: the smallest signed value
    with -1. LLVM calls that srem undefined and x86 traps on it, so the quotient is what
    is checked (compile-time-evaluation.md, Ruling 1).
    """
    if right == 0:
        return Refusal.DIVISION_BY_ZERO
    magnitude = abs(left) // abs(right)
    quotient = -magnitude if (left < 0) != (right < 0) else magnitude
    if op == "/" or not fits_integer_type(quotient, ty):
        return _checked(quotient, ty)
    remainder = abs(left) % abs(right)
    return Folded(-remainder if left < 0 else remainder)


def _shift(op: str, value: int, count: int, ty: Type, width: int) -> Answer:
    """A count past the width is defined and not checked (Go's rule): 0, or the sign bit of
    a signed `>>`. The count is clamped to the width, because a Python shift by a count of
    millions builds the number that it names.

    A held value is in its own range, so Python's `>>` is the machine's: it fills from the
    sign bit of a negative value, and a value of an unsigned type has none.
    """
    if count < 0:
        return Refusal.NEGATIVE_SHIFT
    if op == ">>":
        return Folded(value >> min(count, width))
    return Folded(0 if count >= width else wrap_to_integer_type(value << count, ty))
