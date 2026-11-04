"""`Type.name(args)` as a static method call (#542).

One namespace behind a type's dot (ruling Q1). This module is the only place that
answers "which type does this receiver name, and does it declare that static": the
validation half and the inference half both read it, so a third answer cannot drift in.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.type_predicates import generic_base_of
from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Name
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.statics import (builtin_type_named, is_builtin_static,
                                          solve_target_type_args, static_template)
from sushi_lang.semantics.typesys import Type

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


def static_template_for(validator: 'TypeValidator', base: str, method: str):
    """The static template of `<base>.<method>` that the calling unit reads, or None.

    Its own unit's first, then one that it may call (`extension_reach`).
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.passes.types.calls.methods import in_reach_order
    target = GenericTypeRef(base_name=base, type_args=())
    return static_template(validator.generic_extension_table, base, method,
                           order=lambda templates: in_reach_order(validator, templates, target))


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
    template = static_template_for(validator, base, call.method)
    if template is None:
        return stamped

    type_args, unsolved = _solve_static(validator, call, template, stamped)
    if type_args is None:
        return None
    return _interned_static_target(validator, call, template, base, type_args)


def _solve_static(validator: 'TypeValidator', call, template, stamped):
    """Run the shared solver over this call's argument types and its stamp."""
    from sushi_lang.semantics.generics.pack_inference import infer_call_arg_type
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    arg_types = []
    for arg in call.args:
        arg_type = infer_call_arg_type(validator, arg)
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
    arguments. When it could not -- an argument the typecheck pass types and the
    collector does not -- the instantiation is interned here (risk 1 of the UFCS epic,
    the same seam). The first call of the static enters its signature and queues its
    copy, so the call has a symbol to bind to (#1196).
    """
    from sushi_lang.semantics.generics.extension_targets import instantiation_key
    from sushi_lang.semantics.generics.extensions import bounds_hold_for
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

    # An instance that fails a target bound gets no copy (#1070); the call is refused by
    # the last rung of the method ladder.
    if (interner is not None and validator.queues_late_copies
            and not any(record.is_static and record.unit_name == template.unit_name
                        for record in validator.extension_table.declarations(
                            target, call.method))
            and bounds_hold_for(template, tuple(type_args), validator.tables)):
        _add_late_static_copy(validator, template, target, type_args, call.loc)
    return target


def _add_late_static_copy(validator: 'TypeValidator', template, target, type_args,
                          site) -> None:
    """Enter the substituted signature and queue the body copy for the fixpoint round."""
    from sushi_lang.semantics.passes.types.calls.methods import (
        _queue_extension_instantiation, target_copy_signature)

    validator.extension_table.add_method(target_copy_signature(validator, template, target))
    _queue_extension_instantiation(validator, template, target, tuple(type_args), (), site)


def _unentered_static(validator: 'TypeValidator', call, target):
    """The signature an early inference reads where no copy is entered yet (#1153).

    An inference before the typecheck pass enters no copy, so the extension table can
    hold no row for an instance it reaches. It reads the substituted template instead.
    """
    from sushi_lang.semantics.generics.extensions import target_copy_args
    from sushi_lang.semantics.passes.types.calls.methods import target_copy_signature
    base = generic_base_of(target)
    if base is None:
        return None
    template = static_template_for(validator, base, call.method)
    if template is None or target_copy_args(template, target) is None:
        return None
    return target_copy_signature(validator, template, target)


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
    if resolved is None and not validator.queues_late_copies:
        return _unentered_static(validator, call, target)
    if resolved is None or resolved is RESOLUTION_REPORTED:
        return None
    return resolved.method


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
    declarations = validator.generic_extension_table.declarations(base, call.method)
    if not any(getattr(d, "is_static", False) for d in declarations):
        return False
    # The declared type of this position was refused (CE2001): that is the one fault.
    from sushi_lang.semantics.passes.types.propagation import holds_declared_type
    if holds_declared_type(validator, call):
        return True

    unsolved = _unreached_type_params(validator, call, base)
    spelled = spell_names(unsolved)
    reason = f"no argument names {spelled}, and this position declares no type"
    diag = er.emit_with(validator.reporter, er.ERR.CE2060, call.loc,
                        name=f"{base}.{call.method}", reason=reason)
    if _returns_the_target(declarations, base):
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
    template = static_template_for(validator, base, call.method)
    if template is not None:
        _args, unsolved = _solve_static(validator, call, template,
                                        _stamped_instantiation(call, base))
        if unsolved:
            return unsolved
    generic = (validator.generic_struct_table.by_name.get(base)
               or validator.generic_enum_table.by_name.get(base))
    params = getattr(generic, "type_params", None) or ()
    return tuple(p.name if hasattr(p, "name") else str(p) for p in params) or ("T",)


def spell_names(names) -> str:
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
        if generic_base_of(ret) == base:
            return True
    return False


def reject_missing_static(validator: 'TypeValidator', target: Type, method: str,
                          loc: Span) -> None:
    """CE2102 for a struct or a primitive whose dot holds no static `method`.

    An enum has its own refusal: its dot also holds variants, and CE2045 names both. A
    help says when `method` is an instance method of the type, else how to declare the
    static.
    """
    from sushi_lang.semantics.passes.types.calls.methods import resolve_method

    diag = er.emit_with(validator.reporter, er.ERR.CE2102, loc,
                        type=display_type(target), method=method)
    if resolve_method(validator, target, method) is not None:
        diag.help(f"'{method}' is an instance method here: call it on a value "
                  f"of '{display_type(target)}', not on the type name")
    else:
        diag.help(f"declare it as 'extend {display_type(target)} static "
                  f"{method}(...)'")
    diag.emit()
