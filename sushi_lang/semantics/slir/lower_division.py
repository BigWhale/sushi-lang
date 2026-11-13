"""SHIR to SLIR: the run-time checks of an integer `/` and `%` (docs/adr/0002-overflow-mode.md).

The checks come before the `Binary`, and the backend folds nothing. A switch on the divisor
goes to the trap RE2030 for 0. On a signed type, it goes for -1 to a switch on the dividend,
which goes to the trap RE2031 for the smallest value of the type. Each other value goes on.
The switches read the operands of the `Binary`, so the check makes no local: a deep nest of
divisions keeps the dataflow of the verifier small. A constant divisor other than 0 gets no
zero check, and a constant divisor other than -1 or a constant dividend other than the
smallest value gets no overflow check. A float operation gets no check.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals.report import Span
from sushi_lang.semantics.integer_width import integer_range
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.ids import BlockId
from sushi_lang.semantics.slir.lower_control import int_of
from sushi_lang.semantics.type_predicates import is_unsigned_int
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def check_divisor(low: Lowering, left: p.Operand, right: p.Operand, ty: Type,
                  span: Span) -> None:
    """The checks of `left / right` and `left % right` at the type `ty`."""
    limits = integer_range(ty)
    if limits is None:
        return
    divisor, dividend = int_of(right), int_of(left)
    cases: list[tuple[int, BlockId]] = []
    if divisor is None or divisor == 0:
        cases.append((0, _trap(low, tm.Trap("RE2030", (), span))))
    smallest = limits[0]
    overflow = (not is_unsigned_int(ty) and divisor in (None, -1)
                and dividend in (None, smallest))
    if not cases and not overflow:
        return
    minus_one = low.new_block() if overflow else None
    if minus_one is not None:
        cases.append((-1, minus_one.id))
        trap = _trap(low, tm.Trap("RE2031", (), span))
    next_ = low.new_block()
    if minus_one is not None:
        minus_one.terminator = tm.SwitchInt(left, ((smallest, trap),), next_.id, span)
    low.jump(tm.SwitchInt(right, tuple(cases), next_.id, span))
    low.block = next_


def _trap(low: Lowering, trap: tm.Trap) -> BlockId:
    block = low.new_block()
    block.terminator = trap
    return block.id
