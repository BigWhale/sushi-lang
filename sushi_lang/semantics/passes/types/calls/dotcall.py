"""One ladder for `X.Y(args)`: what does a DotCall name? (#753)

The question has one answer and one arm order. The validation half and the inference half
both walk it through `resolve_dotcall`, which reports the rung that answered as a
`DotCallTarget`; each half then does its own work with that answer. Two ladders drifted
apart before: they copied a different number of stamps back from the temporary node they
resolved the callee on, and every stamp in the gap has a miscompile behind it -- a lost
`poke self` receiver (#326, #327), a `nom` parameter made inert, a call bound to the copy
cut for another type. `copy_callee_stamps` is now the one stamp set both halves read.

`report` is the validation half's True and the inference half's False, the same discipline
`resolve_method` and `resolve_static` already use: only one half may emit a diagnostic.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.param_modes import ParamMode
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, Type
from .enums import validate_enum_constructor
from ..propagation import holds_declared_type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import DotCall
    from sushi_lang.semantics.passes.types import TypeValidator


#: The stamps a resolved callee leaves on the temporary node, in one place. The borrow
#: pass and the backend read every one of them off the DotCall itself. The inference half
#: writes `inferred_return_type` on the DotCall itself, so it is not copied (#769).
CALLEE_STAMPS = ("resolved_enum_type", "callee_self_mode", "callee_builtin_family",
                 "callee_param_modes", "callee_param_names", "callee_param_types",
                 "callee_method_type_args", "callee_variadic_at")


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


def resolve_dotcall(validator: 'TypeValidator', node: 'DotCall', *,
                    report: bool) -> DotCallTarget:
    """Walk the ladder once and answer which rung the node belongs to."""
    from sushi_lang.semantics.passes.types.calls.namespaced import (
        fold_namespaced_enum, fold_namespaced_static, infer_namespaced_call,
        validate_namespaced_call)
    from sushi_lang.semantics.passes.types.calls.statics import (
        infer_static_call, validate_static_call)

    # `geo.Sign.Plus(1)`: the qualifier folds away and what is left is the bare
    # constructor every rule below already knows (unit-namespaces.md section 5).
    # `hm.HashMap.new()` folds the same way (#506).
    fold_namespaced_enum(validator, node)
    fold_namespaced_static(validator, node)

    # A namespace answers first, and it answers for every producer: an FFI block, a unit
    # behind a `use ... as`, a registry stdlib module. Local-wins is inside
    # `namespace_of`, so a variable of the same name never reaches here.
    if validator.namespace_of(node.receiver) is not None:
        if report:
            validate_namespaced_call(validator, node)
            return DotCallTarget(DotCallKind.NAMESPACE)
        return DotCallTarget(DotCallKind.NAMESPACE,
                             type=infer_namespaced_call(validator, node))

    if report:
        _reject_call_site_type_args(validator, node)

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
        if report:
            _validate_primitive_static_args(validator, node, static)
        return DotCallTarget(DotCallKind.PRIMITIVE_STATIC, type=static.returns)

    # The receiver is deliberately NOT validated here: every path either has no receiver
    # (an enum constructor's is a type NAME) or validates it itself. Doing it here as well
    # walked the receiver twice and reported every diagnostic in it twice (#201).

    # A name behind a type's dot is a MEMBER of that type (#542, ruling Q1): a static
    # method, or -- on an enum -- a variant. The static is asked first because the variant
    # path OWNS the enum's refusal (CE2045), and it steps aside for a name that is neither.
    if report:
        if validate_static_call(validator, node):
            return DotCallTarget(DotCallKind.STATIC)
    else:
        static_type = infer_static_call(validator, node)
        if static_type is not None:
            return DotCallTarget(DotCallKind.STATIC, type=static_type)

    if isinstance(node.receiver, Name):
        enum_target = _enum_receiver(validator, node, report=report)
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
                         method_call=_method_call_view(node, carry_stamps=report),
                         receiver_type=receiver_type)


def copy_callee_stamps(node: 'DotCall', resolved: MethodCall) -> None:
    """Copy what the resolved callee stamped on the temporary node back onto the DotCall.

    ONE stamp set. The borrow pass and the backend read every one of these off the DotCall,
    so a stamp dropped here is a silent miscompile: a `poke self` receiver passed by value
    and its write lost (#326, #327), a `nom` parameter made inert, or a call bound to
    `List__i32_mapv` where `List__i32_mapv__bool` is defined.
    """
    for stamp in CALLEE_STAMPS:
        value = getattr(resolved, stamp, None)
        if value is not None:
            setattr(node, stamp, value)


def validate_variant_spelling(validator: 'TypeValidator', node, variant_name: str,
                              args: list) -> bool:
    """Validate `Enum.Variant(...)` or the bare `Enum.Variant` as the constructor it is.

    Both parse as an operation on a type NAME -- a call, or a field read -- and both
    construct the same value, so one path checks them. The bare spelling used to take
    neither path: an undeclared variant compiled, and a GENERIC enum's variant carried no
    stamp for the borrow pass and the backend to read (#545).

    Local-wins (#296): a local named after the enum shadows it, so the node is a method
    call or a field read on the local, never a variant construction.
    """
    from sushi_lang.semantics.ast import EnumConstructor

    receiver_name = node.receiver.id
    if not _names_an_enum(validator, receiver_name):
        return False

    constructor = EnumConstructor(
        enum_name=receiver_name,
        variant_name=variant_name,
        args=args,
        enum_name_span=node.receiver.loc,
        variant_name_span=getattr(node, 'member_span', None),
        loc=node.loc,
    )
    constructor.resolved_enum_type = getattr(node, 'resolved_enum_type', None)
    validate_enum_constructor(validator, constructor,
                              declared=holds_declared_type(validator, node))
    if constructor.resolved_enum_type is not None:
        node.resolved_enum_type = constructor.resolved_enum_type
    return True


def _names_an_enum(validator: 'TypeValidator', name: str) -> bool:
    """Whether a bare receiver name reaches an enum declaration rather than a local."""
    if name in validator.variable_types:
        return False
    return (name in validator.enum_table.by_name
            or name in validator.generic_enum_table.by_name)


def _enum_receiver(validator: 'TypeValidator', node: 'DotCall', *,
                   report: bool) -> Optional[DotCallTarget]:
    """The enum rung: `Enum.Variant(args)`, or None when the receiver names no enum."""
    if report:
        if validate_variant_spelling(validator, node, node.method, node.args):
            return DotCallTarget(DotCallKind.ENUM)
        return None

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
    from sushi_lang.semantics.passes.types.visitor import resolve_fn_field_call
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


def _validate_primitive_static_args(validator: 'TypeValidator', node: 'DotCall',
                                    static: PrimitiveStatic) -> None:
    """Measure the one argument of a primitive static against its parameter."""
    from sushi_lang.semantics.passes.types.arguments import check_arguments

    check_arguments(validator, f"{node.receiver.id}.{node.method}", [static.param],
                    node.args, node.loc,
                    mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                    stop_on_arity=True)


def _reject_call_site_type_args(validator: 'TypeValidator', node: 'DotCall') -> None:
    """CE6102: a `@(...)` list rides a direct call to a named free function, nothing else.

    The rule reads the RECEIVER, not the parse shape: every receiver that reaches this line
    is a value. It used to live in the AST builder, which cannot know whether a receiver
    names a bound alias (`docs/design/unit-namespaces.md` section 5.1).
    """
    if not node.type_args:
        return
    er.emit_with(validator.reporter, er.ERR.CE6102,
                 node.type_args_loc or node.loc) \
        .help("call the generic function directly, e.g. foo@(i32)(x)") \
        .emit()


def _method_call_view(node: 'DotCall', *, carry_stamps: bool) -> MethodCall:
    """The temporary MethodCall the method rung resolves the callee on.

    `carry_stamps` is the validation half's True. The propagation stamp travels with it
    there: the `HashMap.new()` key gate reads the concrete HashMap type off the call node
    (#272), and the namespace stamp is what says the receiver arrived QUALIFIED (#506,
    A-strict). The inference half has never carried either, and handing it one here would
    let it resolve a callee it could not resolve before -- a change of answer, not a move.
    """
    view = MethodCall(receiver=node.receiver, method=node.method, args=node.args,
                      loc=node.loc)
    if carry_stamps:
        view.resolved_struct_type = getattr(node, 'resolved_struct_type', None)
        view.namespace_ref = getattr(node, 'namespace_ref', None)
    return view
