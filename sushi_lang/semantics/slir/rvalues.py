"""The SLIR rvalues and the kinds of an aggregate (docs/design/ir.md 8.7).

A `Binary` never carries `and` or `or`: a short circuit is control flow (ir.md 8.9). A
closure and an iterator have one layout, and the kind of the aggregate keeps them apart,
because the backend reads the semantic type and never the layout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.semantics.operators import BinOp, UnOp
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir.places import Operand, Place
from sushi_lang.semantics.typesys import BorrowMode, StructType, Type


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
    """`env` is the type of the environment of a lambda, and None for a named function."""
    ty: Type
    env: Optional[StructType] = None


@dataclass(frozen=True, slots=True)
class IterElements:
    """The cursor over `T[]`, `T[N]` or `List@(T)`: one part, the container, as a read of its
    place when `lent`, else as its move."""
    container: Type
    lent: bool


@dataclass(frozen=True, slots=True)
class IterMapView:
    """The cursor over one view of a `HashMap`, with the part of `IterElements`."""
    container: Type
    view: str
    lent: bool


@dataclass(frozen=True, slots=True)
class IterRange:
    """Two parts: the first value and the count; item k is `first + step * k`."""
    step: int


@dataclass(frozen=True, slots=True)
class IterNew:
    """`Iterator.new(nom x)`: one part, the move of `x`, and the `next()` that the state
    calls, by the convention of its `self`."""
    value: Type
    next_symbol: str
    receiver: Convention


IterSource: TypeAlias = IterElements | IterMapView | IterRange | IterNew


@dataclass(frozen=True, slots=True)
class AggIterator:
    """`source` is one row of the closed table of producers and adapters (ir.md 8.7), and
    `heap` is the heap decision of `borrow`: the kind carries it, and the emitter decides
    nothing."""
    ty: Type
    source: IterSource
    heap: bool


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
