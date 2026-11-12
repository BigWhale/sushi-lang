"""Enum constructor validation.

A variant of a generic enum that no position and no payload solves is CE2112
(`refuse_untyped_variant`).
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Mapping, Optional

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.typesys import BuiltinType, EnumType, EnumVariantInfo, Type
from ..arguments import NOTHING_TYPED, check_arguments
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from .. import TypeValidator
    from sushi_lang.semantics.shir import exprs as e


def intern_constructed_instance(validator: 'TypeValidator', base: str,
                                type_args: tuple) -> Optional[Type]:
    """The instance `base@(type_args)` that a generic constructor builds.

    One seam for a struct constructor and a variant (#1150, #1152). The instance is
    interned through the seam that owns it. The `instantiate` pass runs before the
    interner exists, so there the answer is the instance's SHAPE, a `GenericTypeRef`:
    the leading solver unifies it as it unifies an instance, and the pass collects it.
    The typecheck pass always has the interner, so it never sees a shape.
    """
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
    from sushi_lang.semantics.generics.interned import interned_name
    from sushi_lang.semantics.generics.types import GenericTypeRef

    structs = validator.struct_table.by_name
    if base == "Result":
        return ensure_result_type_in_table(validator.enum_table, *type_args, structs)
    if base == "Maybe":
        return ensure_maybe_type_in_table(validator.enum_table, type_args[0], structs)
    key = interned_name(base, type_args)
    instance = structs.get(key) or validator.enum_table.by_name.get(key)
    if instance is None:
        shape = GenericTypeRef(base_name=base, type_args=tuple(type_args))
        interner = getattr(validator.tables, "intern_generic_ref", None)
        if interner is None:
            return shape
        interner(shape)
        instance = structs.get(key) or validator.enum_table.by_name.get(key)
    return instance


def _partial_shape(template, names: list, type_args: tuple):
    """`Base@(solved..., T...)`: the instance with each unsolved parameter left named."""
    from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter
    return GenericTypeRef(base_name=template.name, type_args=tuple(
        TypeParameter(name) if t is None else t
        for name, t in zip(names, type_args, strict=True)))


def refuse_untyped_variant(validator: 'TypeValidator', node: 'e.EnumInit', template,
                           name: str, type_args: tuple) -> None:
    """CE2112: no position and no payload gives a type parameter of a generic enum (#1005)."""
    names = [tp.name for tp in template.type_params]
    missing = [each for each, ty in zip(names, type_args, strict=True) if ty is None]
    spelled = f"{name}.{node.variant}" + ("(...)" if node.payload else "()")
    example = display_type(_partial_shape(template, names, type_args))
    er.emit_with(validator.reporter, er.ERR.CE2112, node.loc, constructor=spelled,
                 params=", ".join(f"'{each}'" for each in missing)) \
        .help(f"declare the type first, e.g. 'let {example} v = {spelled}', "
              "then use 'v'").emit()


def declared_position_answer(template, type_args: tuple):
    """The type that a variant with no instance answers where a declared type does not fit
    it. The mismatch check of the position reads it (`got Maybe@(T)`) and reports the one
    diagnostic. It is never an instance: the program does not reach the backend."""
    return _partial_shape(template, [tp.name for tp in template.type_params], type_args)


def reject_missing_variant(validator: 'TypeValidator', loc: Optional[Span], variant: str,
                           enum: str) -> None:
    """CE2045: the dot of the enum `enum` holds no member `variant`.

    One namespace stands behind a type's dot (#542, ruling Q1): the name could have been a
    variant or a static method, so the help names both.
    """
    er.emit_with(validator.reporter, er.ERR.CE2045, loc, variant=variant, enum=enum) \
        .help("a name behind an enum's dot is a variant or a static method: "
              f"add the variant, or declare 'extend {enum} static {variant}(...)'").emit()


def validate_constructor_arguments(
    validator: 'TypeValidator', init: 'e.EnumInit', variant: EnumVariantInfo, enum_type: EnumType,
    typed: Mapping[int, Optional[Type]] = NOTHING_TYPED
) -> None:
    """The payload of a variant construction against the types of the variant.

    `Result.Ok()` of a `~` value is CE2036, which names the fix. Every other payload goes
    through the one argument check, with the codes of a variant (CE2049, CE2050).
    """
    expected_types = list(variant.associated_types)
    if (is_instance_of(enum_type, "Result") and init.variant == "Ok"
            and not init.payload and expected_types == [BuiltinType.BLANK]):
        er.emit(validator.reporter, er.ERR.CE2036, init.loc)
        return

    check_arguments(validator, init.variant, expected_types, init.payload, init.loc,
                    mismatch_code=er.ERR.CE2049, arity_code=er.ERR.CE2050, typed=typed)
