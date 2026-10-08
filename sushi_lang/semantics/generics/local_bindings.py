"""The locals a statement binds, for the two early walks over a body.

The `instantiate` walk and the `monomorphize` walk each keep a map from a local's name
to its type, so that a generic called with a local can be solved. A `foreach` binder and
a `match` payload binding are locals too, and both walks read them here: one rule for a
written body and for a substituted copy (#1155, #1157).
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from sushi_lang.semantics.generics.types import (
    GenericTypeRef, substitute_type_params, type_param_substitution,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type

Bindings = list[tuple[str, "Type"]]
Saved = list[tuple[str, "Type | None"]]


def variant_payload_types(scrutinee_type, variant_name: str, generic_enums) -> tuple:
    """The payload types of one variant, or () when they cannot be read here.

    Two shapes reach this. A monomorphized `EnumType` carries its payload types
    already substituted. A `GenericTypeRef` does not -- the interned instance does not
    exist until the monomorphize pass -- so the generic TEMPLATE is read and its type
    parameters are substituted with the reference's own arguments. That keeps the rule
    general: `Result` and `Maybe` take the same path as a user's generic enum.
    """
    from sushi_lang.semantics.typesys import EnumType

    if isinstance(scrutinee_type, EnumType):
        for variant in scrutinee_type.variants:
            if variant.name == variant_name:
                return tuple(variant.associated_types or ())
        return ()

    if isinstance(scrutinee_type, GenericTypeRef):
        if generic_enums is None:
            return ()
        template = generic_enums.get(scrutinee_type.base_name)
        if template is None:
            return ()
        substitution = type_param_substitution(template, scrutinee_type.type_args)
        if substitution is None:
            return ()
        for variant in template.variants:
            if variant.name == variant_name:
                return tuple(
                    substitute_type_params(payload, substitution)
                    for payload in (variant.associated_types or ())
                )
        return ()

    return ()


def pattern_bindings(pattern, scrutinee_type, generic_enums,
                     resolve: Callable[["Type"], "Type"]) -> Bindings:
    """The names one match arm binds, each with the type of its payload.

    Each payload is resolved before it is bound, exactly as a `let` local's annotation
    is resolved. A template's payload can be a bare name -- an UnknownType("NetError")
    displays as "NetError" while the enum table holds the real EnumType -- and binding
    the unresolved one interns a Result whose element differs from the canonical
    instance, which is the poisoned intern CE0126 reports with two identical spellings.

    A `peek`/`poke` RefBinding carries a ReferenceType rather than the payload's own
    type, and a reference is not a type argument a generic can be called with, so
    nothing is bound for one. A taken (`nom`) payload is the arm's own VALUE, so its
    type is the payload's (borrow-model.md S10b).
    """
    from sushi_lang.semantics.ast import (
        LiteralPattern, Pattern, NomBinding, OrPattern, RangePattern, TuplePattern,
    )
    from sushi_lang.semantics.generics.tuples import is_tuple_type, tuple_elements

    if scrutinee_type is None:
        return []
    if isinstance(pattern, OrPattern):
        # Each alternative binds the same names with the same types (CE2126).
        return pattern_bindings(pattern.alternatives[0], scrutinee_type, generic_enums,
                                resolve)
    if isinstance(pattern, TuplePattern):
        if not is_tuple_type(scrutinee_type):
            return []
        items, payloads = pattern.elements, tuple(tuple_elements(scrutinee_type))
    elif isinstance(pattern, Pattern):
        items = pattern.bindings
        payloads = variant_payload_types(scrutinee_type, pattern.variant_name,
                                         generic_enums)
    else:
        return []

    bound: Bindings = []
    for binding, raw_payload in zip(items, payloads, strict=False):
        payload = resolve(raw_payload)
        if isinstance(binding, str):
            if binding != "_":
                bound.append((binding, payload))
        elif isinstance(binding, NomBinding):
            bound.append((binding.name, payload))
        elif isinstance(binding, (Pattern, TuplePattern, OrPattern)):
            bound.extend(pattern_bindings(binding, payload, generic_enums, resolve))
        elif isinstance(binding, (LiteralPattern, RangePattern)):
            continue                  # a literal and a range bind nothing
    return bound


def foreach_bindings(stmt, infer: Callable[[object], Optional["Type"]],
                     resolve: Callable[["Type"], "Type"]) -> Bindings:
    """The names one `foreach` binds for its body, each with its type.

    The item is the written item type, or the item type of the iterable: an
    `Iterator@(T)` yields T, and a type with `next()` yields the payload of the Maybe
    that `next()` answers (HANDLES.md ruling R21). A `??` binder binds two names: the
    hidden item, and the value the `let` at the top of the body unwraps from it. A
    written type on a `??` binder names that value.
    """
    from sushi_lang.semantics.ast import MethodCall
    from sushi_lang.semantics.typesys import IteratorType

    written = resolve(stmt.item_type) if stmt.item_type is not None else None
    element = None
    if written is None or stmt.item_try_let is not None:
        iterable = infer(stmt.iterable) if stmt.iterable is not None else None
        if isinstance(iterable, IteratorType):
            element = iterable.element_type
        elif iterable is not None:
            next_call = MethodCall(loc=stmt.iterable.loc, receiver=stmt.iterable,
                                   method="next", args=[])
            element = _payload_of(infer(next_call), "Maybe", "Some")
        element = resolve(element) if element is not None else None

    if stmt.item_try_let is None:
        item = written if written is not None else element
        return [(stmt.item_name, item)] if item is not None else []

    bound: Bindings = [(stmt.item_name, element)] if element is not None else []
    value = written if written is not None else _payload_of(element, "Result", "Ok")
    if value is not None:
        bound.append((stmt.item_try_let.name, resolve(value)))
    return bound


def _payload_of(ty, base: str, variant: str):
    """The one payload of `variant` of a Maybe or a Result, or None."""
    from sushi_lang.semantics.type_predicates import is_instance_of
    from sushi_lang.semantics.typesys import EnumType

    if isinstance(ty, GenericTypeRef):
        if ty.base_name != base or not ty.type_args:
            return None
        return ty.type_args[0]
    if not isinstance(ty, EnumType) or not is_instance_of(ty, base):
        return None
    found = ty.get_variant(variant)
    if found is None or len(found.associated_types) != 1:
        return None
    return found.associated_types[0]


def bind_locals(scope: dict, bindings: Bindings) -> Saved:
    """Bind each name, and answer what to put back afterwards.

    A binding lives for its own body only, so the previous value of every name touched
    is returned rather than the scope being cleared -- an arm must not see the arm
    before it, and it must not lose an outer local of the same name.
    """
    saved: Saved = []
    for name, ty in bindings:
        saved.append((name, scope.get(name)))
        scope[name] = ty
    return saved


def unbind_locals(scope: dict, saved: Saved) -> None:
    """Put back what `bind_locals` displaced."""
    for name, previous in reversed(saved):
        if previous is None:
            scope.pop(name, None)
        else:
            scope[name] = previous
