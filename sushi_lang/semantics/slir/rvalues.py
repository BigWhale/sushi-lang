"""The SLIR rvalues and the kinds of an aggregate (docs/design/ir.md 8.7).

A `Binary` never carries `and` or `or`: a short circuit is control flow (ir.md 8.9). A
closure and an iterator have one layout, and the kind of the aggregate keeps them apart,
because the backend reads the semantic type and never the layout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.semantics.operators import BinOp, UnOp
from sushi_lang.semantics.slir.places import Operand, Place
from sushi_lang.semantics.typesys import BorrowMode, Type


@dataclass(frozen=True, slots=True)
class AggStruct:
    ty: Type


@dataclass(frozen=True, slots=True)
class AggTuple:
    ty: Type


@dataclass(frozen=True, slots=True)
class AggVariant:
    ty: Type
    variant: str


@dataclass(frozen=True, slots=True)
class RunOne:
    """One slot: one part, its value."""


@dataclass(frozen=True, slots=True)
class RunRepeat:
    """Two parts: the value, then the count of its slots."""


@dataclass(frozen=True, slots=True)
class RunSequence:
    """Two parts: the first value, then the count; slot k holds `first + step * k`."""
    step: int


ArrayRun: TypeAlias = RunOne | RunRepeat | RunSequence


def run_width(run: ArrayRun) -> int:
    """The count of the parts of `run`."""
    return 1 if isinstance(run, RunOne) else 2


@dataclass(frozen=True, slots=True)
class AggArray:
    """A `T[N]` or a `T[]` of the runs `runs`; the parts are the parts of each run, in
    order (docs/design/ir.md 8.7)."""
    ty: Type
    runs: tuple[ArrayRun, ...]


@dataclass(frozen=True, slots=True)
class AggClosure:
    ty: Type


@dataclass(frozen=True, slots=True)
class AggIterator:
    ty: Type


AggKind: TypeAlias = AggStruct | AggTuple | AggVariant | AggArray | AggClosure | AggIterator


@dataclass(frozen=True, slots=True)
class Use:
    operand: Operand


@dataclass(frozen=True, slots=True)
class Binary:
    op: BinOp
    left: Operand
    right: Operand


@dataclass(frozen=True, slots=True)
class Unary:
    op: UnOp
    operand: Operand


@dataclass(frozen=True, slots=True)
class Cast:
    operand: Operand
    target: Type


@dataclass(frozen=True, slots=True)
class Aggregate:
    kind: AggKind
    parts: tuple[Operand, ...]


@dataclass(frozen=True, slots=True)
class Discriminant:
    """The tag of the enum in `place`, as an `i32`."""
    place: Place


@dataclass(frozen=True, slots=True)
class Clone:
    """The one deep copy."""
    operand: Operand


@dataclass(frozen=True, slots=True)
class Len:
    place: Place


@dataclass(frozen=True, slots=True)
class Slice:
    """A borrowed `T[]` view of `place`, from `start` to `end`."""
    place: Place
    start: Operand
    end: Operand


@dataclass(frozen=True, slots=True)
class Ref:
    place: Place
    mode: BorrowMode


Rvalue: TypeAlias = (
    Use | Binary | Unary | Cast | Aggregate | Discriminant | Clone | Len | Slice | Ref
)


def operands_of(value: Rvalue) -> tuple[Operand | Place, ...]:
    """The operands and the places that `value` reads, in order. An rvalue class that this
    function does not know is an internal error."""
    if isinstance(value, (Use, Unary, Cast, Clone)):
        return (value.operand,)
    if isinstance(value, Binary):
        return (value.left, value.right)
    if isinstance(value, Aggregate):
        return value.parts
    if isinstance(value, (Discriminant, Len, Ref)):
        return (value.place,)
    if isinstance(value, Slice):
        return (value.place, value.start, value.end)
    er.raise_internal_error("CE0000", detail=f"no operands for the SLIR rvalue "
                                             f"{type(value).__name__}")
