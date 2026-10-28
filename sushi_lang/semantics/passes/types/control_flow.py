"""The path faults of a SHIR body (the typecheck pass, docs/design/ir.md 7.5).

A statement after one that ends the path is CE0140, and a body that answers a value and
can reach its end with no `return` is CE0107. Neither rule reads a type. The predicate is
the one path predicate of SHIR, `semantics/shir/paths.py`, which the borrow pass reads too.
A match whose arms miss a value (`uncovered`) does not make the statement after it dead.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Collection, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir.body import ShirBody
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.paths import first_dead_statement, terminates
from sushi_lang.semantics.shir.walk import walk
from sushi_lang.semantics.typesys import BuiltinType, Type

if TYPE_CHECKING:
    from . import TypeValidator


def reject_dead_statements(validator: 'TypeValidator', body: ShirBody,
                           uncovered: Collection[ShirId]) -> None:
    """CE0140, once per block of one body (#854)."""
    for _path, value in walk(body.block, "block"):
        if not isinstance(value, ShirBlock):
            continue
        found = first_dead_statement(value, body, uncovered=uncovered)
        if found is None:
            continue
        ender, dead = found
        validator.err.emit_with(er.ERR.CE0140, dead.loc) \
            .note_at("the path ends here", ender.loc) \
            .help("remove the statement, or move it before the one that ends the path") \
            .emit()


def reject_fall_off(validator: 'TypeValidator', block: ShirBlock, ret: Optional[Type],
                    channel: bool, span: Optional[Span]) -> None:
    """CE0107: a body that answers a value or a Result reaches its end with no `return`.

    A bare `~` body answers nothing, so it may end (#824, #845).
    """
    if (ret != BuiltinType.BLANK or channel) and not terminates(block):
        validator.err.emit(er.ERR.CE0107, span, callable=validator.body_name)
