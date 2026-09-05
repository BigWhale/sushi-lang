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
from sushi_lang.semantics.statics import (builtin_type_named, is_builtin_static,
                                          solve_target_type_args, static_template)
from sushi_lang.semantics.typesys import EnumType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def static_target_type(validator: 'TypeValidator', call) -> Optional[Type]:
    """The TYPE a receiver names, or None when it names something else.

    Local-wins first (#296). A CONCRETE struct or enum answers itself. A GENERIC one
    answers the instantiation its static's ARGUMENTS name, and the propagation stamp
    at the binding site for every type parameter no parameter names (#573) -- the
    stamp alone was the rule from #542, and it left a `| E` static unwritable, because
    a Result-valued call is never stamped. A primitive answers its builtin (ruling R2).
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
        return _generic_static_target(validator, call, name)

    return builtin_type_named(name)


def _stamped_instantiation(call, base: str):
    """The instantiation of `base` the propagation stamp carries on this call, or None."""
    stamped = (getattr(call, "resolved_struct_type", None)
               or getattr(call, "resolved_enum_type", None))
    if stamped is not None and getattr(stamped, "generic_base", None) == base:
        return stamped
    return None


def _generic_static_target(validator: 'TypeValidator', call, base: str):
    """The instantiation a `<generic>.name(...)` call resolves against, or None.

    Without a static TEMPLATE of this name the node is a variant construction or a
    concrete-target static, and the stamp is the whole answer, as before. With one, the
    arguments are solved first and the stamp fills what they left (`solve_target_type_args`
    is the one solver; the instantiate pass collects through it too). The solved
    instantiation is normally interned already -- the instantiate pass saw the same
    arguments -- and is interned late otherwise, through the seam the method-generic rung
    uses, with the static's own copy queued for the fixpoint round.
    """
    stamped = _stamped_instantiation(call, base)
    template = static_template(validator.generic_extension_table, base, call.method)
    if template is None:
        return stamped

    type_args, unsolved = _solve_static(validator, call, template, stamped)
    if type_args is None:
        return None
    return _interned_static_target(validator, call, template, base, type_args)


def _solve_static(validator: 'TypeValidator', call, template, stamped):
    """Run the shared solver over this call's argument types and its stamp."""
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    arg_types = []
    for arg in call.args:
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None:
            arg_type = resolve_unknown_type(arg_type, validator.struct_table.by_name,
                                            validator.enum_table.by_name)
        arg_types.append(arg_type)
    stamped_args = getattr(stamped, "generic_args", None) if stamped is not None else None
    type_args, unsolved = solve_target_type_args(template, arg_types, stamped_args)
    if type_args is None:
        return None, unsolved
    resolved = tuple(resolve_unknown_type(t, validator.struct_table.by_name,
                                          validator.enum_table.by_name)
                     for t in type_args)
    return resolved, ()


def _interned_static_target(validator: 'TypeValidator', call, template, base: str,
                            type_args) -> Optional[Type]:
    """The interned instantiation for the solved type arguments, with its static's copy.

    The instantiate pass collected the instantiation when it could infer the same
    arguments, and the monomorphize pass then cut every extension copy for it. When it
    could not -- an argument the typecheck pass types and the collector does not -- the
    instantiation is interned here (risk 1 of the UFCS epic, the same seam) and the
    static's copy is queued, so the call still has a symbol to bind to.
    """
    from sushi_lang.semantics.generics.extension_targets import instantiation_key
    from sushi_lang.semantics.generics.types import GenericTypeRef

    key = instantiation_key(base, type_args)

    def lookup():
        return (validator.struct_table.by_name.get(key)
                or validator.enum_table.by_name.get(key))

    target = lookup()
    interner = getattr(validator.tables, "intern_generic_ref", None)
    if target is None:
        if interner is None:
            return None
        interner(GenericTypeRef(base_name=base, type_args=tuple(type_args)))
        target = lookup()
        if target is None:
            return None

    if (interner is not None
            and validator.extension_table.get_method(target, call.method) is None):
        _add_late_static_copy(validator, template, target, type_args)
    return target


def _add_late_static_copy(validator: 'TypeValidator', template, target, type_args) -> None:
    """Enter the substituted signature and queue the body copy for the fixpoint round."""
    from sushi_lang.semantics.generics.types import substitute_type_params
    from sushi_lang.semantics.passes.collect.functions import ExtensionMethod, Param
    from sushi_lang.semantics.passes.types.calls.methods import (
        _queue_extension_instantiation)

    names = [p.name if hasattr(p, "name") else str(p) for p in template.type_params]
    substitution = dict(zip(names, type_args, strict=True))

    def subst(ty):
        return substitute_type_params(ty, substitution) if ty is not None else None

    params = [Param(
        name=p.name, ty=subst(p.ty), name_span=p.name_span, type_span=p.type_span,
        index=p.index, is_variadic=getattr(p, "is_variadic", False),
        is_nom=getattr(p, "is_nom", False),
    ) for p in template.params]
    concrete = ExtensionMethod(
        target_type=target, name=template.name, params=params,
        ret_type=subst(template.ret_type), loc=template.loc,
        target_type_span=template.target_type_span, name_span=template.name_span,
        ret_span=template.ret_span, self_mode=template.self_mode,
        filename=template.filename, unit_name=template.unit_name,
        err_type=subst(getattr(template, "err_type", None)),
        err_span=getattr(template, "err_span", None), is_static=True)
    validator.extension_table.add_method(concrete)
    _queue_extension_instantiation(validator, template, target, tuple(type_args), ())


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
    """CE2060: a GENERIC static with a type parameter neither source reaches (#542, #573).

    Two sources, in order: an argument whose parameter names the type parameter, then
    the propagation stamp at the binding site. A position that has neither -- a bare
    `println(Box.empty().len())` -- has no answer, and the text names both sources and
    the parameters it could not reach, only those.

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
    # A BUILT-IN static (`List.new()`, `HashMap.new()`, `Own.alloc(v)`) is the same
    # fault in the same position (#570): its narrow emitter reads the stamp alone, so an
    # unstamped one reached the backend as a bare `List` name and answered CE0055, an
    # internal error blaming the compiler for the program's mistake. `static_target_type`
    # steps aside for one before it reads the stamp, so the stamp is read here.
    builtin = is_builtin_static(base, call.method)
    if builtin:
        if _stamped_instantiation(call, base) is not None:
            return False
        declarations = ()
    else:
        declarations = validator.generic_extension_table.declarations(base, call.method)
        if not any(getattr(d, "is_static", False) for d in declarations):
            return False

    unsolved = _unreached_type_params(validator, call, base)
    spelled = _spell_names(unsolved)
    reason = ("the built-in static reads its type from the binding alone, and this "
              "position declares no type"
              if builtin else
              f"no argument names {spelled}, and this position declares no type")
    diag = er.emit_with(validator.reporter, er.ERR.CE2060, call.loc,
                        name=f"{base}.{call.method}", reason=reason)
    if builtin or _returns_the_target(declarations, base):
        diag.help(f"bind the result to a declared type "
                  f"('let {base}@(...) x = {base}.{call.method}(...)'), or name "
                  f"{spelled} in a parameter so the argument solves it")
    else:
        # The return does not name the target, so no binding could stamp it and a
        # method has no call-site `@(...)` slot at all (Known Limitation 7). A
        # parameter that names the type parameter, or the signature, is what has to
        # change.
        diag.help(f"a static whose return does not name '{base}' has no declared type "
                  f"to read from -- name {spelled} in a parameter, or make it a "
                  f"generic free function ('fn {call.method}@(T)(...)'), which takes "
                  f"explicit type arguments")
    diag.emit()
    return True


def _unreached_type_params(validator: 'TypeValidator', call, base: str) -> tuple:
    """The target type parameters neither the arguments nor the stamp reached."""
    template = static_template(validator.generic_extension_table, base, call.method)
    if template is not None:
        _args, unsolved = _solve_static(validator, call, template,
                                        _stamped_instantiation(call, base))
        if unsolved:
            return unsolved
    generic = (validator.generic_struct_table.by_name.get(base)
               or validator.generic_enum_table.by_name.get(base))
    params = getattr(generic, "type_params", None) or ()
    return tuple(p.name if hasattr(p, "name") else str(p) for p in params) or ("T",)


def _spell_names(names) -> str:
    """`'T'`, `'A' and 'B'`, `'A', 'B' and 'C'` -- for the diagnostic's text."""
    quoted = [f"'{n}'" for n in names]
    if len(quoted) <= 1:
        return "".join(quoted) or "the type parameter"
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


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
