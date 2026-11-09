"""The ladders of a method call: what does `X.Y(args)` name? (#753)

The question has one answer and one arm order. Two ladders drifted apart before: they
copied a different number of stamps back from the temporary node they resolved the callee
on, and every stamp in the gap has a miscompile behind it -- a lost `poke self` receiver
(#326, #327), a `nom` parameter made inert, a call bound to the copy cut for another type.

The typecheck pass reads SHIR, where lowering already answered a namespace call, a static
and an enum constructor. `resolve_dotted` is its ladder of a method call: a built-in
family, a perk implementation, an extension method (docs/design/ir.md 7.3). It writes no
stamp: the pass writes the answer as rows of `TypeckResults`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, Type
from .methods import declaration_was_refused, explain_unpromised_method
from ..call_facts import declaring_units
from ..method_registry import METHOD_TYPE_REGISTRY, MethodFamily

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


class MethodRung(Enum):
    """Which rung of the SHIR ladder answered a method call."""

    FAMILY = auto()
    PERK = auto()
    EXTENSION = auto()
    UNANSWERED = auto()


@dataclass(frozen=True)
class MethodAnswer:
    """The rung that answered a method call, and the family when a family answered."""

    rung: MethodRung
    family: Optional[MethodFamily] = None


def resolve_dotted(validator: 'TypeValidator', receiver_type: Type,
                   name: str) -> MethodAnswer:
    """The rung that answers the method `name` on a receiver of `receiver_type`.

    The family of the table first: its claim puts a perk implementation before a family
    that yields to it (`MethodFamily.claims`). Then a perk implementation, then an
    extension method of the name in any unit, and else nothing.
    """
    family = METHOD_TYPE_REGISTRY.claim(receiver_type, name, validator)
    if family is not None:
        return MethodAnswer(MethodRung.FAMILY, family)
    if validator.perk_impl_table.get_method(receiver_type, name) is not None:
        return MethodAnswer(MethodRung.PERK)
    if declaring_units(validator, name):
        return MethodAnswer(MethodRung.EXTENSION)
    return MethodAnswer(MethodRung.UNANSWERED)


def reject_unknown_method(validator: 'TypeValidator', receiver_type: Type, name: str,
                          loc: Span) -> None:
    """CE2008 for a method that no rung answers. When the collect pass refused an extension
    or a perk implementation of this name on this type, it reported that fault: the call
    reports nothing, and the body holds that error (`refused_before`)."""
    if declaration_was_refused(validator, receiver_type, name):
        validator.refused_before = True
        return
    diag = er.emit_with(validator.reporter, er.ERR.CE2008, loc,
                        name=f"{display_type(receiver_type)}.{name}")
    explain_unpromised_method(validator, diag, receiver_type, name)
    diag.emit()


def _names_an_enum(validator: 'TypeValidator', name: str) -> bool:
    """Whether a bare receiver name reaches an enum declaration rather than a local."""
    if name in validator.variable_types:
        return False
    return (name in validator.enum_table.by_name
            or name in validator.generic_enum_table.by_name)


@dataclass(frozen=True)
class PrimitiveStatic:
    """A static on a primitive type name: its one parameter, and what it answers."""
    param: Type
    param_name: str
    returns: Type
    consumes: bool = False


#: The statics a primitive type name holds, by (type name, method). The backend emits
#: each in `intrinsics.try_emit_primitive_static`.
PRIMITIVE_STATICS: dict[tuple[str, str], PrimitiveStatic] = {
    ("f64", "from_bits"): PrimitiveStatic(BuiltinType.U64, "bits", BuiltinType.F64),
    ("f32", "from_bits"): PrimitiveStatic(BuiltinType.U32, "bits", BuiltinType.F32),
    # The array's buffer BECOMES the string's, with no copy (#1091).
    ("string", "from_bytes"): PrimitiveStatic(DynamicArrayType(BuiltinType.U8), "bytes",
                                              BuiltinType.STRING, consumes=True),
}

