"""A name written behind a dot: `geo.twice(42)`, `std_math.sin(0.0)`, `geo.MAX_DEPTH`.

The seam that says WHICH namespace a receiver names is `semantics/namespaces.py`. This
module is the typecheck pass's half: it takes the binding the seam returns and measures
the call by exactly the rules the bare form is measured by
(`docs/design/unit-namespaces.md` sections 4 and 5).

Visibility is the SECOND seam and it runs here, not in the lookup: a namespace holds a
unit's declarations whatever their visibility, so a private one is refused where it is
written (`CE3005`) instead of being reported as a name that does not exist.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import DotCall, MemberAccess
    from sushi_lang.semantics.namespaces import Binding
    from sushi_lang.semantics.passes.types import TypeValidator


def validate_namespaced_call(validator: 'TypeValidator', node: 'DotCall') -> None:
    """Validate `<namespace>.<name>(args)`. The caller has established the namespace."""
    binding = validator.resolve_namespaced(node.receiver, node.method)
    if binding is None:
        for arg in node.args:
            validator.validate_expression(arg)
        _reject_unknown_member(validator, node.receiver, node.method, node.loc)
        return

    producer = binding.provider.namespace_kind

    if producer == "extern":
        validator._resolve_external_call(node)
        for arg in node.args:
            validator.validate_expression(arg)
        validator._validate_external_call_args(node)
        return

    if binding.kind == "struct":
        _validate_struct_construction(validator, node, binding)
        return

    if binding.kind == "generic function":
        _validate_generic_call(validator, node, binding)
        return

    if binding.kind != "function":
        # A member that exists and is not callable: a constant, an enum, or a perk.
        for arg in node.args:
            validator.validate_expression(arg)
        _reject_unknown_member(validator, node.receiver, node.method, node.loc)
        return

    if producer == "stdlib":
        from .user_defined import validate_stdlib_function
        _stamp(node, binding)
        validate_stdlib_function(validator, node, (binding.provider.origin,
                                                   binding.record))
        return

    from sushi_lang.semantics.passes.types.visibility import reject_private_call
    if reject_private_call(validator, "function", binding.record, node.loc):
        return

    _stamp(node, binding)
    _stamp_param_modes(node, binding.record)
    from .user_defined import validate_call_arguments
    validate_call_arguments(validator, _written(node.receiver, node.method),
                            binding.record, node.args, node.loc)


def _validate_struct_construction(validator: 'TypeValidator', node: 'DotCall',
                                  binding: 'Binding') -> None:
    """A struct constructor behind a dot: `geo.Vec(1, 2)`.

    Read through a stand-in `Call`, the device every other kind behind the dot uses,
    so construction is measured by exactly the rules the bare form is measured by. A
    struct is one declaration per program under phase 1 (Ruling 6), so the stamp tells
    the back end WHAT to emit and the name is the whole address.
    """
    from sushi_lang.semantics.ast import Call, Name
    from sushi_lang.semantics.passes.types.visibility import reject_private_type
    from .structs import validate_struct_constructor

    if reject_private_type(validator, binding.name, node.loc):
        return

    stand_in = Call(callee=Name(id=binding.name, loc=node.loc), args=node.args,
                    field_names=node.field_names, loc=node.loc)
    validate_struct_constructor(validator, stand_in)
    # A named construction is put in declaration order on the stand-in, as the bare
    # form is on its own node. The node is what every later reader emits from, so
    # the order comes back with it, and the names are spent.
    node.args = stand_in.args
    node.field_names = stand_in.field_names
    node.resolved_struct_type = validator.struct_table.by_name.get(binding.name)
    _stamp(node, binding)


def _validate_generic_call(validator: 'TypeValidator', node: 'DotCall',
                           binding: 'Binding') -> None:
    """A generic function behind a dot.

    The generic rules are read through a stand-in `Call`, the same device the enum
    arm of `visit_dotcall` uses: `validate_generic_function_call` rewrites its callee
    to the monomorphized name, and that name is what the alias then points at. The
    DECLARATION comes from the alias's provider, so two units' generics of one name
    cannot cross here (#495).
    """
    from sushi_lang.semantics.ast import Call, Name
    from .generics import validate_generic_function_call

    name = binding.name
    stand_in = Call(callee=Name(id=name, loc=node.loc), args=node.args,
                    type_args=node.type_args, type_args_loc=node.type_args_loc,
                    loc=node.loc)
    validate_generic_function_call(validator, stand_in, name,
                                   generic_func=binding.record)
    if stand_in.callee.id != name:
        _stamp(node, binding, name=stand_in.callee.id)


def infer_namespaced_call(validator: 'TypeValidator',
                          node: 'DotCall') -> Optional[Type]:
    """The type `<namespace>.<name>(args)` yields."""
    binding = validator.resolve_namespaced(node.receiver, node.method)
    if binding is None:
        return None

    producer = binding.provider.namespace_kind
    if producer == "extern":
        node.inferred_return_type = binding.record.ret_type
        return binding.record.ret_type
    if binding.kind == "generic function":
        return _infer_generic_call(validator, node)
    if binding.kind != "function":
        return None
    if producer == "stdlib":
        return _stdlib_return_type(validator, node, binding)

    inferred = validator.type_inference_visitor.result_type_of(binding.record)
    node.inferred_return_type = inferred
    return inferred


def infer_namespaced_member(validator: 'TypeValidator',
                            node: 'MemberAccess') -> Optional[Type]:
    """The type of `<namespace>.<name>` read as a value -- a constant."""
    binding = validator.resolve_namespaced(node.receiver, node.member)
    if binding is None or binding.kind != "constant":
        _reject_unknown_member(validator, node.receiver, node.member,
                               getattr(node, "loc", None))
        return None

    if binding.provider.namespace_kind == "stdlib":
        _stamp(node, binding)
        return _materialize(validator, binding.record.get_return_type())

    from sushi_lang.semantics.passes.types.visibility import reject_private_name
    if reject_private_name(validator, "constant", binding.record,
                           getattr(node, "loc", None)):
        return None

    _stamp(node, binding)
    return binding.record.const_type


def fold_namespaced_enum(validator: 'TypeValidator', node) -> bool:
    """`geo.Sign.Plus` becomes `Sign.Plus`, in place. True when a qualifier was folded.

    Section 5's rule is a fold: a leading `NAME .` that names a namespace is stripped
    and attached to the name after it. An enum is one declaration per program under
    phase 1, so once the qualifier is gone the node IS the bare form and every reader
    after this one -- inference, the borrow pass, the back end -- needs to know nothing
    about namespaces. Both shapes fold here: `MemberAccess` for a variant with no
    payload, `DotCall` for one with a payload.

    A private enum is reported and folded all the same. The type exists, so leaving the
    node half-resolved would report the same mistake again at every rule below.
    """
    from sushi_lang.semantics.ast import MemberAccess, Name
    from sushi_lang.semantics.passes.types.visibility import reject_private_type

    receiver = node.receiver
    if not isinstance(receiver, MemberAccess):
        return False
    binding = validator.resolve_namespaced(receiver.receiver, receiver.member)
    if binding is None or binding.kind != "enum":
        return False

    reject_private_type(validator, binding.name, receiver.loc)
    node.receiver = Name(id=binding.name, loc=receiver.loc)
    return True


def fold_namespaced_static(validator: 'TypeValidator', node) -> bool:
    """`hm.HashMap.new()` becomes `HashMap.new()`, in place (#506, decision A-strict).

    The receiver-is-a-type position, section 5's last row: a leading `NAME .` that
    names a namespace holding the TYPE folds away, and what is left is the bare
    static call every rule below already measures. The type is one per program
    (Ruling 6), so the name is the whole address, exactly as the enum fold above.

    A USER struct or enum folds through it unchanged (#542): the fold was never
    per-type, and the only reason it read `"type"` alone is that a built-in generic was
    the only thing a static could be declared on. The stamp is normalized to `"type"`,
    because what this position knows is that the name is a TYPE -- the declaration's
    own kind would send the back end to the struct-CONSTRUCTION arm instead.
    """
    from sushi_lang.semantics.ast import MemberAccess, Name

    receiver = node.receiver
    if not isinstance(receiver, MemberAccess):
        return False
    binding = validator.resolve_namespaced(receiver.receiver, receiver.member)
    if binding is None or binding.kind not in ("type", "struct", "enum"):
        return False

    node.receiver = Name(id=binding.name, loc=receiver.loc)
    # The stamp is what tells the scope gate the name arrived QUALIFIED: the
    # folded node is otherwise the bare shape the gate refuses (A-strict).
    _stamp(node, binding, kind="type")
    return True


def _stamp(node, binding: 'Binding', *, name: Optional[str] = None,
           kind: Optional[str] = None) -> None:
    """Record what the qualified name resolved to. The back end reads this."""
    node.namespace_ref = binding.ref(name=name, kind=kind)


def _stamp_param_modes(node, func_sig) -> None:
    """The callee's declared modes, for the borrow pass. A FUNCTION, not a method."""
    from sushi_lang.semantics.param_modes import CalleeKind, modes_for
    params = getattr(func_sig, "params", None) or ()
    node.callee_param_modes = modes_for(params, CalleeKind.FUNCTION)
    node.callee_param_names = tuple(p.name for p in params)
    node.callee_param_types = tuple(p.ty for p in params)


def _written(receiver, name: str) -> str:
    """The qualified name as the user wrote it, for a diagnostic to quote."""
    return f"{getattr(receiver, 'id', '?')}.{name}"


def _reject_unknown_member(validator: 'TypeValidator', receiver, name: str,
                           loc) -> None:
    """The namespace does not hold this name, or does not hold it as a callable.

    Once the receiver is known to name a namespace, this rule owns the diagnostic:
    letting the receiver fall through to the ordinary expression rules would report an
    undeclared variable, which names the wrong thing.
    """
    # Reported once. The validator walks a call and the inference visitor follows, and
    # only the validating pass may speak.
    if getattr(validator, "_namespaced_reported", None) is None:
        validator._namespaced_reported = set()
    if id(loc) in validator._namespaced_reported:
        return
    validator._namespaced_reported.add(id(loc))
    from sushi_lang.semantics.namespaces import suggest_member
    written = _written(receiver, name)
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2008, loc, name=written)
    ns = validator.namespace_of(receiver)
    closest = suggest_member(validator.namespaces.members(ns), name) if ns else None
    # A suggestion equal to what the user wrote helps nobody: the member exists
    # and is not what this position takes.
    if closest is not None and closest != name:
        diagnostic = diagnostic.help(f"did you mean '{ns}.{closest}'?")
    diagnostic.emit()


def _stdlib_return_type(validator: 'TypeValidator', node: 'DotCall',
                        binding: 'Binding') -> Optional[Type]:
    """A registry stdlib function's return type, by the rules its module declares."""
    from sushi_lang.semantics.passes.types.visitor import _REGISTRY_TYPED_STDLIB_MODULES
    module_path = binding.provider.origin
    stdlib_func = binding.record

    if module_path in _REGISTRY_TYPED_STDLIB_MODULES:
        inferred = _materialize(validator, stdlib_func.get_return_type())
        node.inferred_return_type = inferred
        return inferred

    # math: the return depends on the arguments, so the same rule the bare form uses.
    from sushi_lang.sushi_stdlib.src import math as math_module
    if not math_module.is_builtin_math_function(binding.name):
        return None
    if binding.name in {"abs", "min", "max"}:
        for arg in node.args:
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None:
                node.inferred_return_type = arg_type
                return arg_type
        return None
    from sushi_lang.semantics.typesys import BuiltinType
    node.inferred_return_type = BuiltinType.F64
    return BuiltinType.F64


def _materialize(validator: 'TypeValidator', declared) -> Optional[Type]:
    """Resolve a registry module's declared return type against the program's tables."""
    return validator.type_inference_visitor._materialize_stdlib_return_type(declared)


def _infer_generic_call(validator: 'TypeValidator',
                        node: 'DotCall') -> Optional[Type]:
    """The type a monomorphized instance yields, once validation has named it.

    Validation runs first -- `validate_expression` visits before it infers -- so the
    stamp is there. Before it is, the answer is None, exactly as the bare form's is
    before its own callee is rewritten.
    """
    ref = getattr(node, "namespace_ref", None)
    if ref is None:
        return None
    func_sig = validator.func_table.by_name.get(ref.name)
    if func_sig is None:
        return None
    inferred = validator.type_inference_visitor.result_type_of(func_sig)
    node.inferred_return_type = inferred
    return inferred
