"""SHIR to SLIR: a value before lowering knows the place that takes it (docs/design/ir.md 8.9).

An arm of a value answers an rvalue, a call, a short circuit or a view; the store of the
value into its place makes the statement (`Lowering.write`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, TypeAlias, TypeVar

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.integer_width import fits_integer_type
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.ids import LocalId
from sushi_lang.semantics.typesys import ArrayType

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


@dataclass(frozen=True, slots=True)
class CallValue:
    """A call, before lowering knows the place that takes its value."""
    callee: st.SlirCallee
    args: tuple[tuple[p.Operand, Convention], ...]


@dataclass(frozen=True, slots=True)
class ShortCircuit:
    """`and` or `or`, before lowering knows the place that takes its value: the left operand,
    and the right operand that a block of its own evaluates (ir.md 8.9)."""
    op: str
    lhs: p.Operand
    rhs: e.ShirExpr
    span: Span


@dataclass(frozen=True, slots=True)
class ViewValue:
    """A borrowed view, before lowering knows the place that takes it (ir.md 8.2): a `Copy`
    of a place that an owner keeps, or a call whose answer is a view. The place that takes it
    is a view local, in a region over each place of `owners`."""
    value: r.Use | CallValue
    owners: tuple[p.Place, ...]


Value: TypeAlias = r.Rvalue | CallValue | ShortCircuit | ViewValue


@dataclass(frozen=True, slots=True)
class Learned:
    """What a walk found at a join for an earlier point: the locals that get a drop flag,
    and the locals whose state a loop header does not know, by the loop."""
    flags: frozenset[ShirLocalId] = frozenset()
    unknown_at: frozenset[tuple[ShirId, ShirLocalId]] = frozenset()


R = TypeVar("R")


def until_stable(lower_once: Callable[[Learned], tuple[Lowering, R]]) -> tuple[Lowering, R]:
    """Lower a body again with what its walk learned, until the walk learns nothing new.
    `lower_once` makes a lowering from the learned facts and lowers the body."""
    learned = Learned()
    while True:
        lowering, rest = lower_once(learned)
        found = lowering.all_learned()
        if found == learned:
            return lowering, rest
        learned = found


def holds(constant: k.Constant) -> bool:
    """Whether the type of `constant` holds it, at every depth: each integer is in the
    range of its type, and each array has the length of its type."""
    if isinstance(constant, k.Int):
        return fits_integer_type(constant.value, constant.ty)
    if not isinstance(constant, k.Aggregate):
        return True
    if isinstance(constant.ty, ArrayType) and len(constant.parts) != constant.ty.size:
        return False
    return all(holds(part) for part in constant.parts)


def moves_of(value: r.Rvalue | CallValue) -> list[p.Place]:
    """The place of each `move` operand of a value."""
    parts = (tuple(operand for operand, _ in value.args) if isinstance(value, CallValue)
             else r.operands_of(value))
    return [part.place for part in parts if isinstance(part, p.Move)]


def root_of_move(place: p.Place) -> LocalId:
    """The local that a move takes: a local, or a part of a local (the payload of a
    variant, `move ((_3 as Ok).0)`, or a field that a take moves), which moves the whole
    local."""
    root = p.root_of(place)
    if not isinstance(root, p.Local):
        er.raise_internal_error("CE0000", detail=f"SLIR lowering moves out of {place}")
    return root.id
