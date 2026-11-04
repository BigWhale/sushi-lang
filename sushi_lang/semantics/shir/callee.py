"""The closed set of callees: what a call resolves to (docs/design/ir.md 7.6).

A `Call` node holds the written `CallTarget`. Which callee it names is an answer of the
`typecheck` pass, a row of `TypeckResults` (ruling S5). Data only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.exprs import ShirExpr
from sushi_lang.semantics.shir.ids import DeclId
from sushi_lang.semantics.typesys import Type


@dataclass(frozen=True, slots=True)
class BuiltinMethod:
    """A method of a row of `METHOD_TYPE_REGISTRY`: the family, and the method name.
    `nom_self` is True for a method that takes its receiver (its row declares `nom self`).
    It has no default, so a maker states the mode.

    `MethodFamily.callee` makes it, so the family is a row of the table (gate:
    `tests/unit/test_dotcall_ladder_is_one.py`)."""
    family: str
    name: str
    nom_self: bool


@dataclass(frozen=True, slots=True)
class Free:
    decl: DeclId


@dataclass(frozen=True, slots=True)
class Method:
    """An extension method or a method of a perk implementation. `decl` names the written
    name `<type>.<method>`, and `receiver` is the convention of the declared `self`."""
    recv: ShirExpr
    decl: DeclId
    receiver: Convention


@dataclass(frozen=True, slots=True)
class Static:
    """A user static: `Vec.at(3, 4)`."""
    ty: Type
    decl: DeclId


@dataclass(frozen=True, slots=True)
class Builtin:
    recv: ShirExpr
    method: BuiltinMethod


@dataclass(frozen=True, slots=True)
class BuiltinStatic:
    """`List.new`, `HashMap.new`, `Iterator.new`."""
    ty: Type
    method: BuiltinMethod


@dataclass(frozen=True, slots=True)
class Conversion:
    """`e as B`, and `??` across two error types."""
    decl: DeclId


@dataclass(frozen=True, slots=True)
class Extern:
    decl: DeclId


@dataclass(frozen=True, slots=True)
class Intrinsic:
    """A function that the language gives: `print`, `println`."""
    name: str


@dataclass(frozen=True, slots=True)
class Indirect:
    """A call through a function value: a closure or a local of a function type."""
    value: ShirExpr


Callee: TypeAlias = (
    Free | Method | Static | Builtin | BuiltinStatic | Conversion | Extern | Intrinsic | Indirect
)
