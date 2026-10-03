"""Enum constructor validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple, Optional, Sequence

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.internals import errors as er
from ..visibility import name_is_contested
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type
from sushi_lang.semantics.ast import EnumConstructor, DotCall, Name
from ..arguments import check_arguments
from ..propagation import holds_declared_type
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from .. import TypeValidator
    from sushi_lang.semantics.ast import EnumVariant, Expr


def validate_enum_constructor(validator: 'TypeValidator', constructor: EnumConstructor, *,
                              declared: Optional[bool] = None) -> None:
    """Validate enum variant constructor - variant exists, argument count and types.

    `declared` says whether a concrete declared type was propagated to the constructor;
    a caller that validates a temporary node passes the answer for the written one.
    """
    if declared is None:
        declared = holds_declared_type(validator, constructor)
    enum_type = resolve_enum_type(validator, constructor, declared=declared)
    if enum_type is None:
        return

    variant = validate_variant_exists(validator, enum_type, constructor)
    if variant is None:
        return

    propagate_generic_types_to_nested_constructors(validator, constructor, variant)

    validate_constructor_arguments(validator, constructor, variant, enum_type)


def resolve_enum_type(validator: 'TypeValidator', constructor: EnumConstructor, *,
                      declared: bool = False) -> Optional[EnumType]:
    """Resolve constructor to concrete or generic enum type."""
    if hasattr(constructor, 'resolved_enum_type') and constructor.resolved_enum_type is not None:
        return constructor.resolved_enum_type

    enum_name = constructor.enum_name

    if enum_name in validator.enum_table.by_name:
        return validator.enum_table.by_name[enum_name]

    if enum_name in validator.generic_enum_table.by_name:
        # No position stamped the constructor: the payload gives the instance, or the
        # constructor is refused (#1005). There is no default type argument.
        instance = untyped_constructor_instance(
            validator, enum_name, constructor.variant_name, constructor.args)
        if instance is not None:
            constructor.resolved_enum_type = instance
            return instance
        _refuse_untyped_constructor(validator, constructor, declared=declared)
        return None

    er.emit(validator.reporter, er.ERR.CE2001, constructor.enum_name_span or constructor.loc,
           name=enum_name)
    return None


class PayloadSolution(NamedTuple):
    """What the arguments of an unstamped generic enum constructor give (#1005)."""
    type_args: tuple
    has_untyped_argument: bool


def solve_constructor_payload(validator: 'TypeValidator', enum_name: str, variant_name: str,
                              args: Sequence['Expr']) -> Optional[PayloadSolution]:
    """Solve a generic enum's type parameters from a constructor's arguments. Silent.

    `type_args` holds one entry per type parameter, None where no argument gives it. The
    answer is None when the enum is not generic, the variant does not exist, or the count
    is wrong: validation reports those.
    """
    from sushi_lang.semantics.generics.pack_inference import (
        infer_call_arg_type, solve_leading_type_args)
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    template = validator.generic_enum_table.by_name.get(enum_name)
    if template is None:
        return None
    variant = next((v for v in template.variants if v.name == variant_name), None)
    if variant is None or len(variant.associated_types) != len(args):
        return None
    names = [tp.name for tp in template.type_params]
    arg_types = [infer_call_arg_type(validator, arg) for arg in args]
    structs, enums = validator.struct_table.by_name, validator.enum_table.by_name
    solved, _unsolved = solve_leading_type_args(
        None, arg_types, structs, enums, partial=True,
        param_types=list(variant.associated_types), type_param_names=names)
    type_args = tuple(resolve_unknown_type(solved[name], structs, enums)
                      if name in solved else None for name in names)
    return PayloadSolution(type_args, any(t is None for t in arg_types))


def untyped_constructor_instance(validator: 'TypeValidator', enum_name: str,
                                 variant_name: str, args: Sequence['Expr']) -> Optional[Type]:
    """The instance an unstamped generic enum constructor builds, from its payload alone.

    The validating and the inferring half both read this one answer (#1005). None when an
    argument does not give a type parameter. Before the `monomorphize` pass the answer
    is the instance's shape (`intern_constructed_instance`).
    """
    solution = solve_constructor_payload(validator, enum_name, variant_name, args)
    if solution is None or any(t is None for t in solution.type_args):
        return None
    return intern_constructed_instance(validator, enum_name, solution.type_args)


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


def declared_position_shape(validator: 'TypeValidator', node: 'Expr', enum_name: str,
                            variant_name: str, args: Sequence['Expr']):
    """The type an unsolved constructor answers where a declared type does not fit it.

    The position's mismatch check reads it (`got Maybe@(T)`) and reports the one
    diagnostic. Everywhere else the answer is None and CE2112 is the diagnostic. It is
    never a stamp: the program does not reach the backend.
    """
    if not holds_declared_type(validator, node):
        return None
    solution = solve_constructor_payload(validator, enum_name, variant_name, args)
    if solution is None:
        return None
    template = validator.generic_enum_table.by_name[enum_name]
    return _partial_shape(template, [tp.name for tp in template.type_params],
                          solution.type_args)


def _refuse_untyped_constructor(validator: 'TypeValidator', constructor: EnumConstructor, *,
                                declared: bool) -> None:
    """Report why an unstamped generic enum constructor has no instance (#1005).

    With a declared type that does not fit, the position's mismatch check is the one
    diagnostic, so CE2112 is not reported there.
    """
    template = validator.generic_enum_table.by_name[constructor.enum_name]
    variant = next((v for v in template.variants if v.name == constructor.variant_name), None)
    if variant is None:
        er.emit_with(validator.reporter, er.ERR.CE2045,
                     constructor.variant_name_span or constructor.loc,
                     variant=constructor.variant_name, enum=template.name) \
            .help("a name behind an enum's dot is a variant or a static method: "
                  f"add the variant, or declare 'extend {template.name} "
                  f"static {constructor.variant_name}(...)'").emit()
        for arg in constructor.args:
            validator.validate_expression(arg)
        return

    if not check_arguments(validator, constructor.variant_name,
                           [None] * len(variant.associated_types), constructor.args,
                           constructor.loc, mismatch_code=er.ERR.CE2049,
                           arity_code=er.ERR.CE2050, stop_on_arity=True):
        return

    if declared:
        return
    solution = solve_constructor_payload(validator, constructor.enum_name,
                                         constructor.variant_name, constructor.args)
    if solution is None or solution.has_untyped_argument:
        return
    names = [tp.name for tp in template.type_params]
    missing = [name for name, t in zip(names, solution.type_args, strict=True) if t is None]
    if not missing:
        return
    spelled = f"{constructor.enum_name}.{constructor.variant_name}"
    spelled += "(...)" if constructor.args else "()"
    example = display_type(_partial_shape(template, names, solution.type_args))
    er.emit_with(validator.reporter, er.ERR.CE2112, constructor.loc,
                 constructor=spelled,
                 params=", ".join(f"'{name}'" for name in missing)) \
        .help(f"declare the type first, e.g. 'let {example} v = {spelled}', "
              "then use 'v'").emit()


def validate_variant_exists(
    validator: 'TypeValidator', enum_type: EnumType, constructor: EnumConstructor
) -> Optional['EnumVariant']:
    """Check variant exists in enum and return it."""
    variant_name = constructor.variant_name
    variant = enum_type.get_variant(variant_name)

    if variant is None:
        # A contested name is the winner's enum, not this unit's (D2). CE2045 would name
        # a variant the user did write, against an enum they did not.
        if not name_is_contested(validator, "enum", enum_type.name):
            # One namespace behind a type's dot (#542, ruling Q1): the name could have
            # been either member, so the help names both.
            er.emit_with(validator.reporter, er.ERR.CE2045,
                         constructor.variant_name_span or constructor.loc,
                         variant=variant_name, enum=display_type(enum_type)) \
                .help("a name behind an enum's dot is a variant or a static method: "
                      f"add the variant, or declare 'extend {display_type(enum_type)} "
                      f"static {variant_name}(...)'").emit()
        return None

    return variant


def propagate_generic_types_to_nested_constructors(
    validator: 'TypeValidator', constructor: EnumConstructor, variant: 'EnumVariant'
) -> None:
    """Set resolved_enum_type for nested generic enum constructors."""
    from sushi_lang.semantics.generics.types import GenericTypeRef

    expected_types = list(variant.associated_types)
    actual_args = constructor.args

    for _i, (arg, expected_type) in enumerate(zip(actual_args, expected_types, strict=False)):
        resolved_type = expected_type
        if isinstance(expected_type, GenericTypeRef):
            concrete_name = str(expected_type)
            if concrete_name in validator.enum_table.by_name:
                resolved_type = validator.enum_table.by_name[concrete_name]
            else:
                continue

        if not isinstance(resolved_type, EnumType):
            continue

        if isinstance(arg, EnumConstructor):
            if arg.enum_name in validator.generic_enum_table.by_name:
                arg.resolved_enum_type = resolved_type
        elif isinstance(arg, DotCall):
            if isinstance(arg.receiver, Name):
                receiver_name = arg.receiver.id
                if receiver_name in validator.generic_enum_table.by_name:
                    arg.resolved_enum_type = resolved_type


def validate_constructor_arguments(
    validator: 'TypeValidator', constructor: EnumConstructor, variant: 'EnumVariant', enum_type: EnumType
) -> None:
    """Validate argument count and types for enum constructor."""
    variant_name = constructor.variant_name
    expected_types = list(variant.associated_types)
    actual_args = constructor.args

    # Special check for Result.Ok() with zero arguments (CE2036)
    # This provides a more helpful error message than the generic "wrong argument count"
    if (is_instance_of(enum_type, "Result") and variant_name == "Ok" and
        len(actual_args) == 0 and len(expected_types) == 1):
        expected_type = expected_types[0]
        if expected_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2036, constructor.loc)
            return

    check_arguments(validator, variant_name, expected_types, actual_args,
                    constructor.loc,
                    mismatch_code=er.ERR.CE2049, arity_code=er.ERR.CE2050)
