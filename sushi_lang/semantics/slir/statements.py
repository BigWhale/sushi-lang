"""The SLIR statements and callees (docs/design/ir.md 8.6).

Sushi does not unwind (ruling R3), so a call needs no unwind edge, and `Call` is a
statement. Every statement carries the span of the construct that caused it (G9).

A `Builtin` callee names an emitter family: a row of `METHOD_TYPE_REGISTRY` (the SHIR
`BuiltinMethod`), or a function that the language gives (the SHIR `Intrinsic`: `println`,
`print`, `interp`). The two levels share these two shapes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import BuiltinMethod, Intrinsic
from sushi_lang.semantics.slir.ids import BorrowId, LocalId
from sushi_lang.semantics.slir.places import Operand, Place
from sushi_lang.semantics.slir.rvalues import Rvalue
from sushi_lang.semantics.slir.rvalues import operands_of as rvalue_operands
from sushi_lang.semantics.typesys import BorrowMode, Type

BuiltinName: TypeAlias = BuiltinMethod | Intrinsic


@dataclass(frozen=True, slots=True)
class Direct:
    """A free function, a method, an extern, a conversion or a function of a registry
    stdlib module: its symbol, and its source name. `module` is the `use` path of the
    stdlib module, and None for every other callee."""
    symbol: str
    name: str
    module: Optional[str] = None


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
    """The begin of the region `id` over `place`. `view` is the view local that the region
    lends to, and `None` for a region with no view local (ir.md 8.6)."""
    id: BorrowId
    place: Place
    mode: BorrowMode
    view: Optional[LocalId]
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


def operands_of(stmt: SlirStatement) -> tuple[Operand | Place, ...]:
    """The operands and the places that `stmt` reads as values, in order: the rvalue of an
    `Assign`, and the arguments and the indirect callee of a `Call`. Any other statement
    reads none."""
    if isinstance(stmt, Assign):
        return rvalue_operands(stmt.rvalue)
    if isinstance(stmt, Call):
        return call_operands(stmt.callee, stmt.args)
    return ()


def call_operands(callee: SlirCallee,
                  args: tuple[tuple[Operand, Convention], ...]) -> tuple[Operand, ...]:
    """The operands of a call, in order: each argument, then the indirect callee. A `Call`
    statement and a call value of lowering both read them here."""
    indirect = (callee.operand,) if isinstance(callee, Indirect) else ()
    return (*(operand for operand, _ in args), *indirect)
