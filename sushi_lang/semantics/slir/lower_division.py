"""SHIR to SLIR: the run-time checks of an integer `/` and `%` (docs/adr/0002-overflow-mode.md).

A divisor of 0 goes to the trap RE2030. On a signed type, the smallest value over -1 goes to
the trap RE2031. Each check is a compare in a local of the statement and a guard of
`lower_arrays`, before the `Binary`; the backend folds nothing. A constant divisor other than
0 gets no zero check, and a constant divisor other than -1 or a constant dividend other than
the smallest value gets no overflow check. A float operation gets no check.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals.report import Span
from sushi_lang.semantics.integer_width import integer_range
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.lower_arrays import check_local, guard
from sushi_lang.semantics.slir.lower_control import int_of
from sushi_lang.semantics.type_predicates import is_integer_type, is_unsigned_int
from sushi_lang.semantics.typesys import BuiltinType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def check_divisor(low: Lowering, left: p.Operand, right: p.Operand, ty: Type,
                  span: Span) -> None:
    """The checks of `left / right` and `left % right` at the type `ty`."""
    if not is_integer_type(ty):
        return
    divisor, dividend = int_of(right), int_of(left)
    if divisor is None or divisor == 0:
        ok = check_local(low, r.Binary("!=", right, p.Const(k.Int(0, ty))),
                         BuiltinType.BOOL, span)
        guard(low, ok, tm.Trap("RE2030", (), span), span)
    if is_unsigned_int(ty):
        return
    smallest = integer_range(ty)[0]
    if divisor not in (None, -1) or dividend not in (None, smallest):
        return
    not_smallest = check_local(low, r.Binary("!=", left, p.Const(k.Int(smallest, ty))),
                               BuiltinType.BOOL, span)
    not_minus_one = check_local(low, r.Binary("!=", right, p.Const(k.Int(-1, ty))),
                                BuiltinType.BOOL, span)
    ok = check_local(low, r.Binary("|", not_smallest, not_minus_one), BuiltinType.BOOL, span)
    guard(low, ok, tm.Trap("RE2031", (), span), span)
