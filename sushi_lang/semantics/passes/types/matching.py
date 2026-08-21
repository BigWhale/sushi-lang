"""Pattern matching validation for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Set, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, EnumType, UnknownType, StructType
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import (
    Match, Pattern, LiteralPattern, WildcardPattern, OwnPattern, Block, Expr,
)
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.generics.type_display import display_type

# The scrutinee types an integer literal match accepts (#415).
_INTEGER_SCRUTINEES = {
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
}

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.semantics.ast import EnumVariant


def validate_match_statement(validator: 'TypeValidator', stmt: Match) -> None:
    """Validate match statement: check enum type, exhaustiveness, and pattern types."""
    scrutinee_type = validate_match_scrutinee(validator, stmt)
    if scrutinee_type is None:
        return

    if not isinstance(scrutinee_type, EnumType):
        # An integer scrutinee dispatches on literal arms (#415).
        validate_integer_match(validator, stmt, scrutinee_type)
        return

    # Stash the resolved concrete enum type on the node so the backend does not
    # have to re-derive it from the scrutinee expression (which it cannot always
    # do; a miss there silently drops pattern bindings and surfaces as CE0055).
    stmt.resolved_scrutinee_type = scrutinee_type

    covered_variants, has_wildcard = collect_and_validate_patterns(validator, stmt, scrutinee_type)

    check_match_exhaustiveness(validator, stmt, scrutinee_type, covered_variants, has_wildcard)


def validate_match_scrutinee(validator: 'TypeValidator', stmt: Match) -> Optional[EnumType | BuiltinType]:
    """Validate the scrutinee is matchable: an enum, or an integer (#415)."""
    validator.validate_expression(stmt.scrutinee)
    scrutinee_type = validator.infer_expression_type(stmt.scrutinee)

    if scrutinee_type is None:
        return None  # Error already emitted during expression validation

    # An unresolved generic scrutinee (e.g. an indexed element of a Maybe<i32>[]
    # array, or a method returning Maybe<T>) infers to a GenericTypeRef/UnknownType.
    # Resolve it to its concrete monomorphized enum
    # so pattern matching sees a real EnumType instead of rejecting it (CE2048).
    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(scrutinee_type, (GenericTypeRef, UnknownType)):
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        scrutinee_type = resolve_unknown_type(
            scrutinee_type,
            validator.struct_table.by_name,
            validator.enum_table.by_name
        )

    if isinstance(scrutinee_type, EnumType) or scrutinee_type in _INTEGER_SCRUTINEES:
        return scrutinee_type

    er.emit(validator.reporter, er.ERR.CE2048, stmt.scrutinee.loc, got=display_type(scrutinee_type))
    return None


def validate_integer_match(validator: 'TypeValidator', stmt: Match,
                           scrutinee_type: BuiltinType) -> None:
    """Validate a match on an integer scrutinee: literal arms + a trailing `_` (#415).

    A literal takes the scrutinee's type under the same fit rule as any
    context-typed literal (a non-decimal literal is a bit pattern); duplicates
    are duplicates by VALUE; the wildcard is required because integer values
    cannot be enumerated.
    """
    from sushi_lang.semantics.passes.types.inference import int_literal_fits

    # The backend dispatches on this stamp; EnumType matches use
    # `resolved_scrutinee_type` instead.
    stmt.integer_match_type = scrutinee_type

    seen: dict[int, str] = {}
    has_wildcard = False
    for idx, arm in enumerate(stmt.arms):
        pattern = arm.pattern

        if isinstance(pattern, WildcardPattern):
            has_wildcard = True
            if idx != len(stmt.arms) - 1:
                er.emit(validator.reporter, er.ERR.CE2041, pattern.loc, variant="_")
        elif isinstance(pattern, LiteralPattern):
            if not int_literal_fits(pattern.value, pattern.radix, scrutinee_type):
                er.emit(validator.reporter, er.ERR.CE2073, pattern.loc,
                        literal=pattern.display, type=scrutinee_type.value)
            elif pattern.value in seen:
                er.emit(validator.reporter, er.ERR.CE2075, pattern.loc,
                        value=pattern.value, first=seen[pattern.value])
            else:
                seen[pattern.value] = pattern.display
        else:
            er.emit(validator.reporter, er.ERR.CE2076, pattern.loc,
                    arm_kind="enum-pattern", scrutinee_type=scrutinee_type.value)

        if isinstance(arm.body, Block):
            validator._validate_block(arm.body)
        elif isinstance(arm.body, Expr):
            validator.validate_expression(arm.body)

    if not has_wildcard:
        er.emit(validator.reporter, er.ERR.CE2074, stmt.loc)


def collect_and_validate_patterns(
    validator: 'TypeValidator', stmt: Match, scrutinee_type: EnumType
) -> Tuple[Set[str], bool]:
    """Collect and validate all match arms, checking pattern validity."""
    covered_variants: Set[str] = set()
    has_wildcard = False

    for idx, arm in enumerate(stmt.arms):
        pattern = arm.pattern

        if isinstance(pattern, WildcardPattern):
            has_wildcard = True
            if idx != len(stmt.arms) - 1:
                er.emit(validator.reporter, er.ERR.CE2041, pattern.loc,
                       variant="_")  # Reuse duplicate arm error for now

            if isinstance(arm.body, Block):
                validator._validate_block(arm.body)
            elif isinstance(arm.body, Expr):
                validator.validate_expression(arm.body)

            continue

        if isinstance(pattern, LiteralPattern):
            # A literal arm needs an integer scrutinee (#415).
            er.emit(validator.reporter, er.ERR.CE2076, pattern.loc,
                    arm_kind="literal", scrutinee_type=display_type(scrutinee_type))
            continue

        if not isinstance(pattern, Pattern):
            continue

        # Validate that the enum name matches the scrutinee's enum type
        # For generic enums, the pattern uses the base name (e.g., "Maybe")
        # but the scrutinee type includes type args (e.g., "Maybe<i32>")
        enum_names_match = False
        if pattern.enum_name == scrutinee_type.name:
            enum_names_match = True
        elif pattern.enum_name in validator.generic_enum_table.by_name:
            if scrutinee_type.name.startswith(f"{pattern.enum_name}<"):
                enum_names_match = True

        if not enum_names_match:
            er.emit(validator.reporter, er.ERR.CE2048, pattern.enum_name_span or pattern.loc,
                   got=pattern.enum_name)
            continue

        variant = scrutinee_type.get_variant(pattern.variant_name)
        if variant is None:
            er.emit(validator.reporter, er.ERR.CE2045, pattern.variant_name_span or pattern.loc,
                   variant=pattern.variant_name, enum=scrutinee_type.name)
            continue

        pattern_signature = get_pattern_signature(pattern)
        if pattern_signature in covered_variants:
            er.emit(validator.reporter, er.ERR.CE2041, pattern.loc,
                   variant=pattern.variant_name)
            continue

        covered_variants.add(pattern_signature)

        if not validate_pattern_bindings(validator, pattern, variant, scrutinee_type):
            continue

        saved_vars = validator.variable_types.copy()
        register_pattern_bindings(validator, pattern, variant)

        if isinstance(arm.body, Block):
            validator._validate_block(arm.body)
        elif isinstance(arm.body, Expr):
            validator.validate_expression(arm.body)

        validator.variable_types = saved_vars

    return covered_variants, has_wildcard


def check_match_exhaustiveness(
    validator: 'TypeValidator', stmt: Match, scrutinee_type: EnumType, covered_variants: Set[str], has_wildcard: bool
) -> None:
    """Check if match statement covers all enum variants."""
    if not has_wildcard:
        all_variants = {variant.name for variant in scrutinee_type.variants}
        covered_outer_variants = set()
        for sig in covered_variants:
            outer_variant = sig.split("(")[0]
            covered_outer_variants.add(outer_variant)

        missing_variants = all_variants - covered_outer_variants

        if missing_variants:
            missing_list = ", ".join(sorted(missing_variants))
            er.emit(validator.reporter, er.ERR.CE2040, stmt.loc, variants=missing_list)


def validate_pattern_bindings(validator: 'TypeValidator', pattern: 'Pattern', variant: 'EnumVariant', parent_enum_type: 'EnumType') -> bool:
    """Validate pattern bindings match variant's associated types (supports nested patterns)."""
    expected_bindings = len(variant.associated_types)
    actual_bindings = len(pattern.bindings)

    if expected_bindings != actual_bindings:
        er.emit(validator.reporter, er.ERR.CE2044, pattern.loc,
               variant=pattern.variant_name,
               expected=expected_bindings,
               got=actual_bindings)
        return False

    for _i, (binding, binding_type) in enumerate(zip(pattern.bindings, variant.associated_types, strict=False)):
        if isinstance(binding, Pattern):
            from sushi_lang.semantics.typesys import UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

            if not isinstance(resolved_type, EnumType):
                er.emit(validator.reporter, er.ERR.CE2048, binding.loc, got=display_type(resolved_type))
                return False

            if binding.enum_name != resolved_type.name:
                if not (binding.enum_name in validator.generic_enum_table.by_name and
                        resolved_type.name.startswith(f"{binding.enum_name}<")):
                    er.emit(validator.reporter, er.ERR.CE2048, binding.enum_name_span or binding.loc,
                           got=binding.enum_name)
                    return False

            nested_variant = resolved_type.get_variant(binding.variant_name)
            if nested_variant is None:
                er.emit(validator.reporter, er.ERR.CE2045, binding.variant_name_span or binding.loc,
                       variant=binding.variant_name, enum=resolved_type.name)
                return False

            if not validate_pattern_bindings(validator, binding, nested_variant, resolved_type):
                return False
        elif isinstance(binding, OwnPattern):
            from sushi_lang.semantics.typesys import UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

            is_own_type = False
            if isinstance(resolved_type, StructType) and resolved_type.name.startswith("Own<"):
                is_own_type = True
            elif isinstance(resolved_type, GenericTypeRef) and resolved_type.base_name == "Own":
                is_own_type = True

            if not is_own_type:
                er.emit(validator.reporter, er.ERR.CE2048, binding.loc,
                       got=f"Own(...) pattern requires Own@(T) type, got {display_type(resolved_type)}")
                return False

            if isinstance(binding.inner_pattern, Pattern):
                element_type = None
                if isinstance(resolved_type, GenericTypeRef):
                    if len(resolved_type.type_args) == 1:
                        element_type = resolved_type.type_args[0]
                    else:
                        er.emit(validator.reporter, er.ERR.CE2048, binding.loc,
                               got=f"Invalid Own@(T) type arguments: {display_type(resolved_type)}")
                        return False
                elif isinstance(resolved_type, StructType):
                    from sushi_lang.semantics.generics import own as own_module
                    try:
                        element_type = own_module.get_own_element_type(resolved_type)
                    except (TypeError, IndexError):
                        er.emit(validator.reporter, er.ERR.CE2048, binding.loc,
                               got=f"Invalid Own@(T) type structure: {display_type(resolved_type)}")
                        return False

                if isinstance(element_type, UnknownType):
                    element_type = resolve_unknown_type(element_type, validator.struct_table.by_name, validator.enum_table.by_name)

                if not isinstance(element_type, EnumType):
                    er.emit(validator.reporter, er.ERR.CE2048, binding.inner_pattern.loc,
                           got=f"Nested pattern inside Own(...) requires enum type, got {display_type(element_type)}")
                    return False

                inner_variant = element_type.get_variant(binding.inner_pattern.variant_name)
                if inner_variant is None:
                    er.emit(validator.reporter, er.ERR.CE2045,
                           binding.inner_pattern.variant_name_span or binding.inner_pattern.loc,
                           variant=binding.inner_pattern.variant_name, enum=element_type.name)
                    return False

                if not validate_pattern_bindings(validator, binding.inner_pattern, inner_variant, element_type):
                    return False

    return True


def register_pattern_bindings(validator: 'TypeValidator', pattern: 'Pattern', variant: 'EnumVariant') -> None:
    """Register pattern bindings in variable_types table (recursive for nested and Own patterns).
    """
    from sushi_lang.semantics.ast import RefBinding
    for binding, binding_type in zip(pattern.bindings, variant.associated_types, strict=False):
        if isinstance(binding, str):
            if binding != "_":  # Skip wildcards
                from sushi_lang.semantics.typesys import UnknownType
                resolved_type = binding_type
                if isinstance(binding_type, UnknownType):
                    resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

                validator.variable_types[binding] = resolved_type
        elif isinstance(binding, RefBinding):
            # `Variant(poke x)` (#300 phase 3): the binding IS a reference into the
            # scrutinee's payload storage, so register the reference type -- every
            # consumer that asks "is this name a borrow?" answers truthfully, and
            # inference auto-derefs the name.
            from sushi_lang.semantics.typesys import BorrowMode, ReferenceType, UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)
            mode = BorrowMode.POKE if binding.mode == "poke" else BorrowMode.PEEK
            validator.variable_types[binding.name] = ReferenceType(resolved_type, mode)
        elif isinstance(binding, Pattern):
            from sushi_lang.semantics.typesys import UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

            if isinstance(resolved_type, EnumType):
                nested_variant = resolved_type.get_variant(binding.variant_name)
                if nested_variant:
                    register_pattern_bindings(validator, binding, nested_variant)
        elif isinstance(binding, OwnPattern):
            from sushi_lang.semantics.typesys import UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

            element_type = None
            if isinstance(resolved_type, GenericTypeRef) and resolved_type.base_name == "Own":
                if len(resolved_type.type_args) == 1:
                    element_type = resolved_type.type_args[0]
            elif isinstance(resolved_type, StructType) and resolved_type.name.startswith("Own<"):
                from sushi_lang.semantics.generics import own as own_module
                try:
                    element_type = own_module.get_own_element_type(resolved_type)
                except (TypeError, IndexError):
                    pass

            if element_type is not None:
                if isinstance(element_type, UnknownType):
                    element_type = resolve_unknown_type(element_type, validator.struct_table.by_name, validator.enum_table.by_name)

                inner_pattern = binding.inner_pattern
                if isinstance(inner_pattern, str):
                    if inner_pattern != "_":
                        if binding.inner_borrow is not None:
                            # `Own(poke x)` (#300 phase 1): the binding IS a reference
                            # to the pointee, so register the reference type -- every
                            # consumer that asks "is this name a borrow?" then answers
                            # truthfully, and inference auto-derefs the name.
                            from sushi_lang.semantics.typesys import BorrowMode, ReferenceType
                            mode = (BorrowMode.POKE if binding.inner_borrow == "poke"
                                    else BorrowMode.PEEK)
                            validator.variable_types[inner_pattern] = ReferenceType(
                                element_type, mode)
                        else:
                            validator.variable_types[inner_pattern] = element_type
                elif isinstance(inner_pattern, Pattern):
                    if isinstance(element_type, EnumType):
                        inner_variant = element_type.get_variant(inner_pattern.variant_name)
                        if inner_variant:
                            register_pattern_bindings(validator, inner_pattern, inner_variant)


def get_pattern_signature(pattern: 'Pattern') -> str:
    """Generate a unique signature for a pattern including nested and Own patterns."""
    signature = pattern.variant_name

    if pattern.bindings:
        binding_signatures = []
        for binding in pattern.bindings:
            if isinstance(binding, str):
                binding_signatures.append("_")
            elif isinstance(binding, Pattern):
                nested_sig = get_pattern_signature(binding)
                binding_signatures.append(nested_sig)
            elif isinstance(binding, OwnPattern):
                if isinstance(binding.inner_pattern, str):
                    binding_signatures.append("Own")
                elif isinstance(binding.inner_pattern, Pattern):
                    inner_sig = get_pattern_signature(binding.inner_pattern)
                    binding_signatures.append(f"Own({inner_sig})")
            else:
                # A RefBinding (#300 phase 3) binds like a plain name: the marker changes
                # how the binding is materialized, not which values the arm matches, so
                # `Poly(p)` and `Poly(poke p)` are duplicates of each other.
                binding_signatures.append("_")
        signature += "(" + ",".join(binding_signatures) + ")"

    return signature
