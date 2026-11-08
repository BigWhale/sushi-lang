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
    from sushi_lang.semantics.ast import DotCall
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

    if binding.kind == "generic function":
        er.raise_internal_error(
            "CE0000", span=node.loc,
            detail="a generic call behind a dot is a call on SHIR (ir.md 7.3)")

    if binding.kind == "struct":
        er.raise_internal_error(
            "CE0000", span=node.loc,
            detail="a struct construction behind a dot is a StructInit on SHIR (ir.md 7.3)")

    if binding.kind != "function":
        # A member that exists and is not callable: a constant, an enum, or a perk.
        for arg in node.args:
            validator.validate_expression(arg)
        _reject_unknown_member(validator, node.receiver, node.method, node.loc)
        return

    from sushi_lang.semantics.passes.types.visibility import reject_private_call
    if reject_private_call(validator, "function", binding.record, node.loc):
        return

    _stamp(node, binding)
    _stamp_param_modes(node, binding.record, validator)
    from .user_defined import validate_call_arguments
    validate_call_arguments(validator, _written(node.receiver, node.method),
                            binding.record, node.args, node.loc)


def _constructed_struct(validator: 'TypeValidator', node: 'DotCall',
                        binding: 'Binding') -> Optional[Type]:
    """The struct a constructor behind a dot builds: the one its name declares (Ruling 6).

    Both halves read it, so `sh.Pt(0)` has the type `Pt(0)` has under a flat import in
    every pass that infers it, the passes before the typecheck pass included (#1147).
    A generic struct answers the instance the declared type stamped, or else the one
    its arguments solve (#1150, #1151).
    """
    if binding.name not in validator.generic_struct_table.by_name:
        return validator.struct_table.by_name.get(binding.name)
    stamped = getattr(node, "resolved_struct_type", None)
    if stamped is not None:
        return stamped
    from .structs import untyped_struct_instance
    return untyped_struct_instance(validator, binding.name, node.args, node.field_names)


def _stamp(node, binding: 'Binding', *, name: Optional[str] = None,
           kind: Optional[str] = None) -> None:
    """Record what the qualified name resolved to. The back end reads this."""
    node.namespace_ref = binding.ref(name=name, kind=kind)


def _stamp_param_modes(node, func_sig, validator=None) -> None:
    """The callee's declared modes and its `...T` slot, for the borrow pass.

    A FUNCTION, not a method: the same answers the flat call reads from the resolver.
    """
    from sushi_lang.semantics.param_modes import CalleeKind, modes_for
    params = getattr(func_sig, "params", None) or ()
    node.callee_param_modes = modes_for(params, CalleeKind.FUNCTION)
    node.callee_param_types = tuple(p.ty for p in params)
    if func_sig is not None and validator is not None:
        from ..borrowed_returns import stamp_call_sources
        stamp_call_sources(validator, node, func_sig)


def _written(receiver, name: str) -> str:
    """The qualified name as the user wrote it, for a diagnostic to quote."""
    return f"{getattr(receiver, 'id', '?')}.{name}"


def _first_report(validator: 'TypeValidator', loc) -> bool:
    """True the first time a qualified name at `loc` is judged, False after that.

    The validator walks an expression and the inference visitor follows, and only one
    of them may speak.
    """
    if getattr(validator, "_namespaced_reported", None) is None:
        validator._namespaced_reported = set()
    if id(loc) in validator._namespaced_reported:
        return False
    validator._namespaced_reported.add(id(loc))
    return True


def _reject_unknown_member(validator: 'TypeValidator', receiver, name: str,
                           loc) -> None:
    """The namespace does not hold this name, or does not hold it as a callable.

    Once the receiver is known to name a namespace, this rule owns the diagnostic:
    letting the receiver fall through to the ordinary expression rules would report an
    undeclared variable, which names the wrong thing.
    """
    if not _first_report(validator, loc):
        return
    reject_unknown_namespace_call(validator, getattr(receiver, 'id', '?'),
                                  validator.namespace_of(receiver), name, loc)


def reject_unknown_namespace_call(validator: 'TypeValidator', written: str,
                                  namespace: Optional[str], name: str, loc) -> None:
    """CE2008 for `written.name(...)`, a call of a name that the namespace does not hold,
    with the closest name that it holds as a help."""
    from sushi_lang.semantics.namespaces import suggest_member
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2008, loc, name=f"{written}.{name}")
    closest = (suggest_member(validator.namespaces.members(namespace), name)
               if namespace else None)
    # A suggestion equal to what the user wrote helps nobody: the member exists
    # and is not what this position takes.
    if closest is not None and closest != name:
        diagnostic = diagnostic.help(f"did you mean '{namespace}.{closest}'?")
    diagnostic.emit()

