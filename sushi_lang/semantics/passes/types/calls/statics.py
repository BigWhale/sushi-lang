"""`Type.name(args)` as a static method call (#542).

One namespace behind a type's dot (ruling Q1). This module is the only place that
answers "which type does this receiver name, and does it declare that static": the
validation half and the inference half both read it, so a third answer cannot drift in.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import Name
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import CalleeKind, modes_for
from sushi_lang.semantics.statics import builtin_type_named, is_builtin_static
from sushi_lang.semantics.typesys import EnumType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def static_target_type(validator: 'TypeValidator', call) -> Optional[Type]:
    """The TYPE a receiver names, or None when it names something else.

    Local-wins first (#296). A CONCRETE struct or enum answers itself. A GENERIC one
    answers the instantiation the propagation stamp carries, exactly as `List.new()`
    reads its element type off the binding site (#272) -- a static's type argument has
    no other source, because there is no receiver to read it from. A primitive answers
    its builtin (ruling R2).
    """
    receiver = call.receiver
    if not isinstance(receiver, Name):
        return None
    name = receiver.id
    if name in validator.variable_types:
        return None
    # A BUILT-IN static is emitted by its container's own handler (ruling R3), which no
    # user declaration reaches. This path steps aside for one rather than refusing it.
    if is_builtin_static(name, call.method):
        return None

    concrete = (validator.struct_table.by_name.get(name)
                or validator.enum_table.by_name.get(name))
    if concrete is not None:
        return concrete

    if (name in validator.generic_struct_table.by_name
            or name in validator.generic_enum_table.by_name):
        stamped = (getattr(call, "resolved_struct_type", None)
                   or getattr(call, "resolved_enum_type", None))
        if stamped is not None and getattr(stamped, "generic_base", None) == name:
            return stamped
        return None

    return builtin_type_named(name)


def resolve_static(validator: 'TypeValidator', call, report: bool = False):
    """The static method a `Type.name(...)` call resolves to, or None.

    `report` is the inference half's False and the validation half's True: only one of
    them may let a template rung emit its own CE2063.
    """
    from sushi_lang.semantics.passes.types.calls.methods import (
        RESOLUTION_REPORTED, resolve_method)

    target = static_target_type(validator, call)
    if target is None:
        return None
    resolved = resolve_method(validator, target, call.method, call=call,
                              report=report, static=True)
    if resolved is None or resolved is RESOLUTION_REPORTED:
        return None
    return resolved.method


def validate_static_call(validator: 'TypeValidator', call) -> bool:
    """Validate `Type.name(args)`. True when this path answered the node.

    It answers the whole type-name receiver position for a struct and a primitive,
    refusal included (CE2102). On an ENUM it steps aside when no static answered: a
    name behind an enum's dot may still be a variant, and that path owns CE2045.
    """
    from sushi_lang.semantics.passes.types.calls.methods import (
        RESOLUTION_REPORTED, extension_call_result_type, resolve_method)
    from sushi_lang.semantics.passes.types.compatibility import types_compatible
    from sushi_lang.semantics.passes.types.propagation import (
        propagate_declared_type_to_value)

    target = static_target_type(validator, call)
    if target is None:
        return _refuse_unstamped_generic(validator, call)

    resolved = resolve_method(validator, target, call.method, call=call,
                              report=True, static=True)
    if resolved is RESOLUTION_REPORTED:
        return True

    if resolved is None:
        return _refuse_missing_static(validator, call, target)

    method = resolved.method
    params = getattr(method, "params", None) or ()
    # A static declares no receiver, so it asks a different mode question than a
    # method does -- which is why `CalleeKind` names it separately (ruling R4).
    call.callee_param_modes = modes_for(params, CalleeKind.STATIC_METHOD)
    call.callee_param_names = tuple(p.name for p in params)
    call.callee_param_types = tuple(p.ty for p in params)
    call.callee_is_static = True
    call.callee_static_target = target

    if len(call.args) != len(params):
        er.emit(validator.reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target)}.{call.method}",
                expected=len(params), got=len(call.args))

    for index, (arg, param) in enumerate(zip(call.args, params, strict=False)):
        # PROPAGATE before validating, exactly as the instance arm does (#387).
        expected_ty = propagate_declared_type_to_value(validator, arg, param.ty)
        validator.validate_expression(arg)
        if expected_ty is None:
            continue
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, expected_ty):
            er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                    index=index + 1, expected=display_type(expected_ty),
                    got=display_type(arg_type))

    for extra in call.args[len(params):]:
        validator.validate_expression(extra)

    call.inferred_return_type = extension_call_result_type(validator, method)
    return True


def infer_static_call(validator: 'TypeValidator', call) -> Optional[Type]:
    """What a static call yields, or None when the node is not one."""
    from sushi_lang.semantics.passes.types.calls.methods import (
        extension_call_result_type)

    method = resolve_static(validator, call)
    if method is None:
        return None
    inferred = extension_call_result_type(validator, method)
    if inferred is not None:
        call.inferred_return_type = inferred
    return inferred


def _refuse_unstamped_generic(validator: 'TypeValidator', call) -> bool:
    """CE2060: a GENERIC static with no instantiation to read (#542).

    A static's type argument comes from the propagation stamp at the binding site,
    because there is no receiver to read it from. In a position that stamps nothing --
    a bare `println(Box.describing(9))` -- there is no answer, and saying so is the
    documented rule for a generic whose arguments have no source.

    The test is deliberately narrow: it fires only when the base name declares a
    static of THIS name. Otherwise the node is a variant construction whose stamp the
    surrounding statement supplies (`Result.Ok(0)` in a return), or a plain unknown,
    and both belong to the paths below.
    """
    receiver = call.receiver
    if not isinstance(receiver, Name):
        return False
    base = receiver.id
    if (base not in validator.generic_struct_table.by_name
            and base not in validator.generic_enum_table.by_name):
        return False
    declarations = validator.generic_extension_table.declarations(base, call.method)
    if not any(getattr(d, "is_static", False) for d in declarations):
        return False

    diag = er.emit_with(validator.reporter, er.ERR.CE2060, call.loc,
                        name=f"{base}.{call.method}",
                        reason="a static reads its type arguments from the declared "
                               "type at the call site, and this position declares none")
    if _returns_the_target(declarations, base):
        diag.help(f"bind the result to a declared type: "
                  f"'let {base}@(...) x = {base}.{call.method}(...)'")
    else:
        # The return does not name the target, so no binding could stamp it and a
        # method has no call-site `@(...)` slot at all (Known Limitation 7). The
        # signature is what has to change.
        diag.help(f"a static whose return does not name '{base}' has no position to "
                  f"read its type arguments from -- make it a generic free function "
                  f"('fn {call.method}@(T)(...)'), which takes explicit type "
                  f"arguments")
    diag.emit()
    return True


def _returns_the_target(declarations, base: str) -> bool:
    """Whether any declaration of this static returns the type it is declared on."""
    for declaration in declarations:
        ret = getattr(declaration, "ret_type", None)
        if ret is None:
            continue
        if getattr(ret, "base_name", None) == base or getattr(ret, "name", None) == base:
            return True
        name = getattr(ret, "name", None)
        if isinstance(name, str) and name.startswith(f"{base}<"):
            return True
    return False


def _refuse_missing_static(validator: 'TypeValidator', call, target: Type) -> bool:
    """CE2102 for a type whose dot holds no such member. True when it was emitted.

    An enum is the one target that steps aside: its dot also holds variants, and the
    variant path's CE2045 already names the type and the member and reads correctly.
    """
    if isinstance(target, EnumType) or _is_generic_enum(validator, call):
        return False

    from sushi_lang.semantics.passes.types.calls.methods import resolve_method

    diag = er.emit_with(validator.reporter, er.ERR.CE2102, call.loc,
                        type=display_type(target), method=call.method)
    if resolve_method(validator, target, call.method) is not None:
        diag.help(f"'{call.method}' is an instance method here: call it on a value "
                  f"of '{display_type(target)}', not on the type name")
    else:
        diag.help(f"declare it as 'extend {display_type(target)} static "
                  f"{call.method}(...)'")
    diag.emit()
    return True


def _is_generic_enum(validator: 'TypeValidator', call) -> bool:
    """Whether the receiver names a generic ENUM whose instantiation is unstamped."""
    receiver = call.receiver
    return (isinstance(receiver, Name)
            and receiver.id in validator.generic_enum_table.by_name)
