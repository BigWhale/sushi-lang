"""SHIR to SLIR: the run-time checks of an integer `/` and `%` (docs/design/ir.md 8.5)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals.report import Span
from sushi_lang.semantics.overflow_mode import OverflowMode
from sushi_lang.semantics.integer_width import integer_range
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.ids import BlockId
from sushi_lang.semantics.slir.lower_control import int_of
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def check_division(low: Lowering, left: p.Operand, right: p.Operand, ty: Type,
                   span: Span) -> None:
    """The checks of `left / right` and `left % right` at the type `ty`. A divisor of 0
    traps in each overflow mode; the smallest signed value over -1 traps in `trap` mode
    alone, and in `wrap` mode the backend gives its wrapped answer."""
    limits = integer_range(ty)
    if limits is None:
        return
    smallest = limits[0]
    divisor, dividend = int_of(right), int_of(left)
    cases: list[tuple[int, BlockId]] = []
    if divisor is None or divisor == 0:
        cases.append((0, _trap(low, tm.Trap("RE2030", (), span))))
    overflow = (low.overflow is OverflowMode.TRAP and smallest < 0
                and divisor in (None, -1) and dividend in (None, smallest))
    if not cases and not overflow:
        return
    minus_one = low.new_block() if overflow and divisor is None else None
    if minus_one is not None:
        cases.append((-1, minus_one.id))
    trap = _trap(low, tm.Trap("RE2031", (), span)) if overflow else None
    next_ = low.new_block()
    if trap is not None:
        on_dividend = tm.SwitchInt(left, ((smallest, trap),), next_.id, span)
        if minus_one is None:
            low.jump(on_dividend)
        else:
            minus_one.terminator = on_dividend
    if cases:
        low.jump(tm.SwitchInt(right, tuple(cases), next_.id, span))
    low.block = next_


def _trap(low: Lowering, trap: tm.Trap) -> BlockId:
    block = low.new_block()
    block.terminator = trap
    return block.id
