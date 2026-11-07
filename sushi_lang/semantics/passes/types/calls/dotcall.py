"""The ladders of a method call: what does `X.Y(args)` name? (#753)

The question has one answer and one arm order. Two ladders drifted apart before: they
copied a different number of stamps back from the temporary node they resolved the callee
on, and every stamp in the gap has a miscompile behind it -- a lost `poke self` receiver
(#326, #327), a `nom` parameter made inert, a call bound to the copy cut for another type.

The typecheck pass reads SHIR, where lowering already answered a namespace call, a static
and an enum constructor. `resolve_dotted` is its ladder of a method call: a built-in
family, a perk implementation, an extension method (docs/design/ir.md 7.3). It writes no
stamp: the pass writes the answer as rows of `TypeckResults`.

`resolve_dotcall` is the AST ladder of the inference of the early passes, which reports
nothing, until those passes stop inferring.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import ParamMode
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, Type
from .methods import declaration_was_refused, explain_unpromised_method
from ..call_facts import TypecheckCallFacts
from ..method_registry import METHOD_TYPE_REGISTRY, MethodFamily

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import DotCall
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
    if TypecheckCallFacts(validator).declaring_units(name):
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


class DotCallKind(Enum):
    """Which rung of the ladder answered the node."""

    NAMESPACE = auto()
    PRIMITIVE_STATIC = auto()
    STATIC = auto()
    ENUM = auto()
    FN_FIELD = auto()
    METHOD = auto()


@dataclass(frozen=True)
class DotCallTarget:
    """What `X.Y(args)` names, and what the answering rung already knows about it."""

    kind: DotCallKind
    type: Optional[Type] = None
    fn_type: Optional[Type] = None
    method_call: Optional[MethodCall] = None
    receiver_type: Optional[Type] = None


def resolve_dotcall(validator: 'TypeValidator', node: 'DotCall') -> DotCallTarget:
    """Walk the ladder once and answer which rung the node belongs to."""
    from sushi_lang.semantics.passes.types.calls.namespaced import (
        fold_namespaced_enum, fold_namespaced_static, infer_namespaced_call)
    from sushi_lang.semantics.passes.types.calls.statics import infer_static_call

    # `geo.Sign.Plus(1)`: the qualifier folds away and what is left is the bare
    # constructor every rule below already knows (unit-namespaces.md section 5).
    # `hm.HashMap.new()` folds the same way (#506).
    fold_namespaced_enum(validator, node)
    fold_namespaced_static(validator, node)

    # A namespace answers first, and it answers for every producer: an FFI block, a unit
    # behind a `use ... as`, a registry stdlib module. Local-wins is inside
    # `namespace_of`, so a variable of the same name never reaches here.
    if validator.namespace_of(node.receiver) is not None:
        return DotCallTarget(DotCallKind.NAMESPACE,
                             type=infer_namespaced_call(validator, node))

    # A static on a primitive type NAME: `f64.from_bits(bits)`, `f32.from_bits(bits)`,
    # `string.from_bytes(nom b)`. The receiver is a type, not a value, so it answers
    # before the receiver is measured as an expression.
    static = PRIMITIVE_STATICS.get(_primitive_static_key(node))
    if static is not None:
        node.inferred_return_type = static.returns
        # The one parameter has its mode in the row, and the `borrow` pass checks the
        # marker by the rule of every callee (#1173).
        node.callee_param_modes = (ParamMode.NOM if static.consumes else ParamMode.BORROW,)
        node.callee_param_names = [static.param_name]
        return DotCallTarget(DotCallKind.PRIMITIVE_STATIC, type=static.returns)

    # The receiver is deliberately NOT validated here: every path either has no receiver
    # (an enum constructor's is a type NAME) or validates it itself. Doing it here as well
    # walked the receiver twice and reported every diagnostic in it twice (#201).

    # A name behind a type's dot is a MEMBER of that type (#542, ruling Q1): a static
    # method, or -- on an enum -- a variant. The static is asked first because the variant
    # path OWNS the enum's refusal (CE2045), and it steps aside for a name that is neither.
    static_type = infer_static_call(validator, node)
    if static_type is not None:
        return DotCallTarget(DotCallKind.STATIC, type=static_type)

    if isinstance(node.receiver, Name):
        enum_target = _enum_receiver(validator, node)
        if enum_target is not None:
            return enum_target

    # The two rungs that are left both read the receiver's type. It is inferred ONE time
    # here, and the METHOD rung carries it on: the receiver of a chain is the whole chain
    # before the call, so a second inference per call doubles the work at each call.
    receiver_type = validator.infer_expression_type(node.receiver)

    # obj.handler(): an indirect call through a fn-typed struct field (a same-named method
    # wins over the field -- see `resolve_fn_field_call`). The backend reads
    # `callee_fn_type` to emit the fat-pointer indirect call.
    fn_type = _fn_field_type(validator, node, receiver_type)
    if fn_type is not None:
        node.callee_fn_type = fn_type
        return DotCallTarget(DotCallKind.FN_FIELD, fn_type=fn_type)

    return DotCallTarget(DotCallKind.METHOD,
                         method_call=_method_call_view(node),
                         receiver_type=receiver_type)


def _names_an_enum(validator: 'TypeValidator', name: str) -> bool:
    """Whether a bare receiver name reaches an enum declaration rather than a local."""
    if name in validator.variable_types:
        return False
    return (name in validator.enum_table.by_name
            or name in validator.generic_enum_table.by_name)


def _enum_receiver(validator: 'TypeValidator', node: 'DotCall') -> Optional[DotCallTarget]:
    """The enum rung: `Enum.Variant(args)`, or None when the receiver names no enum."""
    name = node.receiver.id
    if not _names_an_enum(validator, name):
        return None
    enum_type = validator.enum_table.by_name.get(name)
    if enum_type is not None:
        node.inferred_return_type = enum_type
        return DotCallTarget(DotCallKind.ENUM, type=enum_type)
    # A GENERIC enum constructor: the position's stamp, or else the instance its payload
    # gives (#1005). Neither is None, and validation reports why.
    instance = getattr(node, 'resolved_enum_type', None)
    if instance is None:
        from sushi_lang.semantics.passes.types.calls.enums import (
            declared_position_shape, untyped_constructor_instance)
        instance = untyped_constructor_instance(validator, name, node.method, node.args)
        if instance is None:
            return DotCallTarget(DotCallKind.ENUM, type=declared_position_shape(
                validator, node, name, node.method, node.args))
        node.resolved_enum_type = instance
    node.inferred_return_type = instance
    return DotCallTarget(DotCallKind.ENUM, type=instance)


def _fn_field_type(validator: 'TypeValidator', node: 'DotCall',
                   receiver_type: Optional[Type]) -> Optional[Type]:
    """The FunctionType of a fn-typed field this call goes through, or None."""
    from sushi_lang.semantics.passes.types.visit.helpers import resolve_fn_field_call
    return resolve_fn_field_call(validator, node, receiver_type)


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


def _primitive_static_key(node: 'DotCall') -> Optional[tuple[str, str]]:
    if not isinstance(node.receiver, Name):
        return None
    return (node.receiver.id, node.method)


def _method_call_view(node: 'DotCall') -> MethodCall:
    """The temporary MethodCall the method rung resolves the callee on.

    The inference half carries no stamp of the DotCall into it: handing it one would let
    it resolve a callee that it could not resolve before -- a change of answer, not a move.
    """
    return MethodCall(receiver=node.receiver, method=node.method, args=node.args,
                      loc=node.loc)
