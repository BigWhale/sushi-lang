"""The SLIR constants (docs/design/ir.md 8.5), and the constant of a literal or of a value.

The constant evaluator makes a constant on the AST (ruling Q5), and lowering carries it
through unchanged. Only the backend turns one into an LLVM constant. Typecheck writes the
value that an integer literal holds, and the value of a constant, as a row (ruling R-W):
lowering reads the row and decides no value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.semantics.const_eval import ConstantValue, ScalarConstant
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.type_predicates import is_float_type
from sushi_lang.semantics.typesys import ArrayType, BuiltinType, EnumType, StructType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.typeck import TypeckResults


@dataclass(frozen=True, slots=True)
class Int:
    value: int
    ty: Type


@dataclass(frozen=True, slots=True)
class Float:
    value: float
    ty: Type


@dataclass(frozen=True, slots=True)
class Bool:
    value: bool


@dataclass(frozen=True, slots=True)
class Blank:
    """`~`."""


@dataclass(frozen=True, slots=True)
class Str:
    """A string literal, as UTF-8 bytes. It owns no heap."""
    value: bytes


@dataclass(frozen=True, slots=True)
class Aggregate:
    """A constant array, struct or enum variant. `variant` names the variant of an enum."""
    ty: Type
    parts: tuple[Constant, ...]
    variant: Optional[str] = None


@dataclass(frozen=True, slots=True)
class FnRef:
    symbol: str


Constant: TypeAlias = Int | Float | Bool | Blank | Str | Aggregate | FnRef


def text(value: str) -> Str:
    """A string literal, as its UTF-8 bytes."""
    return Str(value.encode("utf-8", "surrogatepass"))


def of_literal(lit: e.Lit, typeck: TypeckResults) -> Constant:
    """The constant of a literal. An integer literal takes its value row; any other literal
    is its written value, at the type that typecheck gave it."""
    value = lit.value
    if isinstance(value, e.BoolConst):
        return Bool(value.value)
    if isinstance(value, e.BlankConst):
        return Blank()
    if isinstance(value, e.StrConst):
        return text(value.value)
    if isinstance(value, e.IntConst):
        held = typeck.value_of(lit.shir_id)
        if not isinstance(held, ScalarConstant):
            er.raise_internal_error("CE0000", span=lit.loc,
                                    detail="an integer literal with no value reached SLIR")
        return of_scalar(held)
    ty = typeck.type_of(lit.shir_id)
    if ty is None:
        er.raise_internal_error("CE0000", span=lit.loc, detail="a literal with no type reached SLIR")
    return Float(value.value, ty)


def of_value(value: ConstantValue) -> Constant:
    """The constant of a value that typecheck wrote: a scalar, an enum variant, a struct or
    a `T[N]`, whose payload values, field values and elements are made by the same rule."""
    if isinstance(value, ScalarConstant):
        return of_scalar(value)
    ty = value.semantic_type
    if not (isinstance(ty, (StructType, ArrayType))
            or (isinstance(ty, EnumType) and value.variant is not None)):
        er.raise_internal_error("CE0000", detail=f"a constant value of {display_type(ty)} has no SLIR "
                                                 f"constant")
    return Aggregate(ty, tuple(of_value(part) for part in value.elements), value.variant)


def of_scalar(value: ScalarConstant) -> Constant:
    """The constant of a scalar value that typecheck wrote: a number, a bool or a string. A
    string is a literal, so it owns no heap."""
    ty = value.semantic_type
    if ty == BuiltinType.BOOL:
        return Bool(bool(value.value))
    if ty == BuiltinType.STRING:
        return text(str(value.value))
    if is_float_type(ty):
        return Float(float(value.value), ty)
    return Int(int(value.value), ty)
