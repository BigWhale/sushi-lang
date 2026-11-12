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
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.statics import (static_template)
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def static_template_for(validator: 'TypeValidator', base: str, method: str):
    """The static template of `<base>.<method>` that the calling unit reads, or None.

    Its own unit's first, then one that it may call (`extension_reach`).
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.passes.types.calls.methods import in_reach_order
    target = GenericTypeRef(base_name=base, type_args=())
    return static_template(validator.generic_extension_table, base, method,
                           order=lambda templates: in_reach_order(validator, templates, target))


def _interned_static_target(validator: 'TypeValidator', method: str, site: Optional[Span],
                            template, base: str, type_args) -> Optional[Type]:
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
    # the last rung of the method ladder. A method-generic static has no copy for the
    # instance alone: the method-generic rung cuts it when the arguments solve its
    # method-level parameters (#1276).
    if (interner is not None and validator.queues_late_copies
            and not template.method_type_params
            and not any(record.is_static and record.unit_name == template.unit_name
                        for record in validator.extension_table.declarations(
                            target, method))
            and bounds_hold_for(template, tuple(type_args), validator.tables)):
        _add_late_static_copy(validator, template, target, type_args, site)
    return target


def _add_late_static_copy(validator: 'TypeValidator', template, target, type_args,
                          site) -> None:
    """Enter the substituted signature and queue the body copy for the fixpoint round."""
    from sushi_lang.semantics.passes.types.calls.methods import (
        _queue_extension_instantiation, target_copy_signature)

    validator.extension_table.add_method(target_copy_signature(validator, template, target))
    _queue_extension_instantiation(validator, template, target, tuple(type_args), (), site)


def emit_no_source(validator: 'TypeValidator', loc: Optional[Span], name: str, reason: str,
                   help_text: str) -> None:
    """CE2060: the static `name` reads no type argument from its source."""
    er.emit_with(validator.reporter, er.ERR.CE2060, loc, name=name, reason=reason) \
        .help(help_text).emit()


def reject_unreached_type_params(validator: 'TypeValidator', loc: Optional[Span], base: str,
                                 method: str, unsolved, declarations) -> None:
    """CE2060: a GENERIC static with a type parameter neither source reaches (#542, #573).

    Two sources, in order: an argument whose parameter names the type parameter, then the
    declared type of the position. The text names both sources and the parameters that it
    could not reach, only those.
    """
    spelled = spell_names(unsolved)
    if _returns_the_target(declarations, base):
        help_text = (f"bind the result to a declared type "
                     f"('let {base}@(...) x = {base}.{method}(...)'), or name "
                     f"{spelled} in a parameter so the argument solves it")
    else:
        # The return does not name the target, so no binding can give the type, and a
        # method has no call-site `@(...)` slot (Known Limitation 7).
        help_text = (f"a static whose return does not name '{base}' has no declared type "
                     f"to read from -- name {spelled} in a parameter, or make it a "
                     f"generic free function ('fn {method}@(T)(...)'), which takes "
                     f"explicit type arguments")
    emit_no_source(validator, loc, f"{base}.{method}",
                   f"no argument names {spelled}, and this position declares no type",
                   help_text)


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
