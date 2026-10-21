"""The SLIR statements and callees (docs/design/ir.md 8.6).

Sushi does not unwind (ruling R3), so a call needs no unwind edge, and `Call` is a
statement. Every statement carries the span of the construct that caused it (G9).

A `Builtin` callee names an emitter family: a row of `METHOD_TYPE_REGISTRY` (the SHIR
`BuiltinMethod`), or a function that the language gives (the SHIR `Intrinsic`: `println`,
`print`, `interp`). The two levels share these two shapes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import BuiltinMethod, Intrinsic
from sushi_lang.semantics.slir.ids import BorrowId, LocalId
from sushi_lang.semantics.slir.places import Operand, Place
from sushi_lang.semantics.slir.rvalues import Rvalue
from sushi_lang.semantics.typesys import BorrowMode, Type

BuiltinName: TypeAlias = BuiltinMethod | Intrinsic


@dataclass(frozen=True, slots=True)
class Direct:
    """A free function, a method, an extern or a conversion: its symbol, and its source name."""
    symbol: str
    name: str


@dataclass(frozen=True, slots=True)
class Builtin:
    """An emitter family; `types` holds the type of each operand of an intrinsic."""
    method: BuiltinName
    types: tuple[Type, ...]


@dataclass(frozen=True, slots=True)
class Indirect:
    """A call through the `fn_ptr` of a closure."""
    operand: Operand


SlirCallee: TypeAlias = Direct | Builtin | Indirect


@dataclass(frozen=True, slots=True)
class Assign:
    place: Place
    rvalue: Rvalue
    span: Span


@dataclass(frozen=True, slots=True)
class Call:
    dest: Place
    callee: SlirCallee
    args: tuple[tuple[Operand, Convention], ...]
    span: Span


@dataclass(frozen=True, slots=True)
class Drop:
    """An unconditional drop (RAII)."""
    place: Place
    span: Span


@dataclass(frozen=True, slots=True)
class DropIfSet:
    """The drop of a conditionally moved local; `flag` is a `bool` local."""
    place: Place
    flag: LocalId
    span: Span


@dataclass(frozen=True, slots=True)
class BeginBorrow:
    id: BorrowId
    place: Place
    mode: BorrowMode
    span: Span


@dataclass(frozen=True, slots=True)
class EndBorrow:
    id: BorrowId
    span: Span


@dataclass(frozen=True, slots=True)
class StorageLive:
    local: LocalId
    span: Span


@dataclass(frozen=True, slots=True)
class StorageDead:
    local: LocalId
    span: Span


SlirStatement: TypeAlias = (
    Assign | Call | Drop | DropIfSet | BeginBorrow | EndBorrow | StorageLive | StorageDead
)
