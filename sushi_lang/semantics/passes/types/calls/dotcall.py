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
from sushi_lang.semantics.typesys import BuiltinType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import DotCall
    from sushi_lang.semantics.passes.types import TypeValidator


#: The stamps a resolved callee leaves on the temporary node, in one place. The borrow
#: pass and the backend read every one of them off the DotCall itself.
CALLEE_STAMPS = ("inferred_return_type", "resolved_enum_type", "callee_self_mode",
                 "callee_param_modes", "callee_param_names", "callee_param_types",
                 "callee_method_type_args")


class DotCallKind(Enum):
    """Which rung of the ladder answered the node."""

    NAMESPACE = auto()
    FROM_BITS = auto()
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

    # f64.from_bits(bits) / f32.from_bits(bits): the static bit-reinterpret constructor.
    # The receiver is a primitive float type NAME, not a value, so it answers before the
    # receiver is measured as an expression.
    float_type = _from_bits_type(node)
    if float_type is not None:
        node.inferred_return_type = float_type
        if report:
            _validate_from_bits_args(validator, node, float_type)
        return DotCallTarget(DotCallKind.FROM_BITS, type=float_type)

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

    # obj.handler(): an indirect call through a fn-typed struct field (a same-named method
    # wins over the field -- see `resolve_fn_field_call`). The backend reads
    # `callee_fn_type` to emit the fat-pointer indirect call.
    fn_type = _fn_field_type(validator, node)
    if fn_type is not None:
        node.callee_fn_type = fn_type
        return DotCallTarget(DotCallKind.FN_FIELD, fn_type=fn_type)

    return DotCallTarget(DotCallKind.METHOD,
                         method_call=_method_call_view(node, carry_stamps=report))


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
        loc=node.loc,
    )
    constructor.resolved_enum_type = getattr(node, 'resolved_enum_type', None)
    validator._validate_enum_constructor(constructor)
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
    # A GENERIC enum constructor (`Result.Ok()`): the instantiation comes from the
    # position that binds it, so this rung answers the node with no type of its own.
    return DotCallTarget(DotCallKind.ENUM)


def _fn_field_type(validator: 'TypeValidator', node: 'DotCall') -> Optional[Type]:
    """The FunctionType of a fn-typed field this call goes through, or None."""
    from sushi_lang.semantics.passes.types.visitor import resolve_fn_field_call
    return resolve_fn_field_call(validator, node)


def _from_bits_type(node: 'DotCall') -> Optional[Type]:
    """The float type `f64.from_bits` / `f32.from_bits` yields, or None for anything else."""
    if not (isinstance(node.receiver, Name) and node.method == "from_bits"):
        return None
    if node.receiver.id == "f64":
        return BuiltinType.F64
    if node.receiver.id == "f32":
        return BuiltinType.F32
    return None


def _validate_from_bits_args(validator: 'TypeValidator', node: 'DotCall',
                             float_type: Type) -> None:
    """Measure `f64.from_bits(u64)` / `f32.from_bits(u32)` against the width it reads."""
    from sushi_lang.semantics.passes.types.arguments import check_arguments

    expected = BuiltinType.U64 if float_type == BuiltinType.F64 else BuiltinType.U32
    check_arguments(validator, f"{node.receiver.id}.from_bits", [expected],
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
