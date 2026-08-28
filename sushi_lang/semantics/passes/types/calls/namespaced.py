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

    if binding.kind == "generic function":
        _validate_generic_call(validator, node, binding)
        return

    if binding.kind != "function":
        # A member that exists and is not callable: a constant, or a kind the
        # qualified grammar of section 5 has yet to reach.
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


def _validate_generic_call(validator: 'TypeValidator', node: 'DotCall',
                           binding: 'Binding') -> None:
    """A generic function behind a dot. The instance it names is program-wide.

    The generic rules are read through a stand-in `Call`, the same device the enum
    arm of `visit_dotcall` uses: `validate_generic_function_call` rewrites its callee
    to the monomorphized name, and that name is what the alias then points at. A
    generic carries no per-unit view of its own (#495), so the instance resolves
    flat -- which is what `lookup` falls back to.
    """
    from sushi_lang.semantics.ast import Call, Name
    from .generics import validate_generic_function_call

    name = binding.name
    stand_in = Call(callee=Name(id=name, loc=node.loc), args=node.args, loc=node.loc)
    validate_generic_function_call(validator, stand_in, name)
    if stand_in.callee.id != name:
        node.namespace_ref = (binding.provider.namespace_kind,
                              binding.provider.origin, stand_in.callee.id)


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


def _stamp(node, binding: 'Binding') -> None:
    """Record which producer answered, and what it named. The back end reads this."""
    node.namespace_ref = (binding.provider.namespace_kind,
                          binding.provider.origin, binding.name)


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
    er.emit(validator.reporter, er.ERR.CE2008, loc, name=_written(receiver, name))


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
    func_sig = validator.func_table.by_name.get(ref[2])
    if func_sig is None:
        return None
    inferred = validator.type_inference_visitor.result_type_of(func_sig)
    node.inferred_return_type = inferred
    return inferred
