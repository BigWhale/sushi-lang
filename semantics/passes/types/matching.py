# semantics/passes/types/matching.py
"""
Pattern matching validation for type validation.

This module contains validation functions for match statements:
- Match statement validation
- Pattern validation (including nested patterns)
- Exhaustiveness checking
- Pattern binding validation and registration
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Set, Tuple

from internals import errors as er
from semantics.typesys import EnumType, UnknownType, StructType
from semantics.generics.types import GenericTypeRef
from semantics.ast import Match, Pattern, WildcardPattern, OwnPattern, Block, Expr
from semantics.type_resolution import resolve_unknown_type

if TYPE_CHECKING:
    from . import TypeValidator
    from semantics.enums.variants import EnumVariant


def validate_match_statement(validator: 'TypeValidator', stmt: Match) -> None:
    """Validate match statement: check enum type, exhaustiveness, and pattern types."""
    # Validate the scrutinee expression and ensure it's an enum
    scrutinee_type = validate_match_scrutinee(validator, stmt)
    if scrutinee_type is None:
        return

    # Collect and validate all patterns
    covered_variants, has_wildcard = collect_and_validate_patterns(validator, stmt, scrutinee_type)

    # Check exhaustiveness
    check_match_exhaustiveness(validator, stmt, scrutinee_type, covered_variants, has_wildcard)


def validate_match_scrutinee(validator: 'TypeValidator', stmt: Match) -> Optional[EnumType]:
    """Validate match scrutinee is an enum type.

    Args:
        validator: The TypeValidator instance.
        stmt: The match statement to validate.

    Returns:
        The scrutinee's EnumType if valid, None otherwise.
    """
    validator.validate_expression(stmt.scrutinee)
    scrutinee_type = validator.infer_expression_type(stmt.scrutinee)

    # Ensure scrutinee is an EnumType
    if scrutinee_type is None:
        return None  # Error already emitted during expression validation

    if not isinstance(scrutinee_type, EnumType):
        er.emit(validator.reporter, er.ERR.CE2048, stmt.scrutinee.loc, got=str(scrutinee_type))
        return None

    return scrutinee_type


def collect_and_validate_patterns(
    validator: 'TypeValidator', stmt: Match, scrutinee_type: EnumType
) -> Tuple[Set[str], bool]:
    """Collect and validate all match arms, checking pattern validity.

    Args:
        validator: The TypeValidator instance.
        stmt: The match statement to validate.
        scrutinee_type: The validated enum type of the scrutinee.

    Returns:
        Tuple of (covered_variants set, has_wildcard flag).
    """
    covered_variants: Set[str] = set()
    has_wildcard = False

    # Validate each match arm
    for idx, arm in enumerate(stmt.arms):
        pattern = arm.pattern

        # Check for wildcard pattern
        if isinstance(pattern, WildcardPattern):
            has_wildcard = True
            # Wildcard must be the last arm
            if idx != len(stmt.arms) - 1:
                er.emit(validator.reporter, er.ERR.CE2041, pattern.loc,
                       variant="_")  # Reuse duplicate arm error for now

            # CRITICAL: Validate the wildcard arm body!
            # Wildcard patterns don't have bindings to validate, but the body still needs validation
            if isinstance(arm.body, Block):
                validator._validate_block(arm.body)
            elif isinstance(arm.body, Expr):
                validator.validate_expression(arm.body)

            # No need to validate bindings for wildcard, but we've validated the body
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
            # Pattern uses a generic enum base name - check if scrutinee is an instance
            if scrutinee_type.name.startswith(f"{pattern.enum_name}<"):
                enum_names_match = True

        if not enum_names_match:
            er.emit(validator.reporter, er.ERR.CE2048, pattern.enum_name_span or pattern.loc,
                   got=pattern.enum_name)
            continue

        # Look up the variant in the enum
        variant = scrutinee_type.get_variant(pattern.variant_name)
        if variant is None:
            er.emit(validator.reporter, er.ERR.CE2045, pattern.variant_name_span or pattern.loc,
                   variant=pattern.variant_name, enum=scrutinee_type.name)
            continue

        # Check for duplicate match arms (must compare full nested pattern structure)
        pattern_signature = get_pattern_signature(pattern)
        if pattern_signature in covered_variants:
            er.emit(validator.reporter, er.ERR.CE2041, pattern.loc,
                   variant=pattern.variant_name)
            continue

        # Mark variant as covered (using full pattern signature for nested patterns)
        covered_variants.add(pattern_signature)

        # Validate pattern bindings match variant's associated types (supports nested patterns)
        if not validate_pattern_bindings(validator, pattern, variant, scrutinee_type):
            continue

        # Track pattern binding types in variable_types for body validation (recursive)
        saved_vars = validator.variable_types.copy()
        register_pattern_bindings(validator, pattern, variant)

        # Validate the arm body (expression or block)
        if isinstance(arm.body, Block):
            validator._validate_block(arm.body)
        elif isinstance(arm.body, Expr):
            validator.validate_expression(arm.body)

        # Restore variable types after arm
        validator.variable_types = saved_vars

    return covered_variants, has_wildcard


def check_match_exhaustiveness(
    validator: 'TypeValidator', stmt: Match, scrutinee_type: EnumType, covered_variants: Set[str], has_wildcard: bool
) -> None:
    """Check if match statement covers all enum variants.

    Args:
        validator: The TypeValidator instance.
        stmt: The match statement to check.
        scrutinee_type: The enum type being matched on.
        covered_variants: Set of covered pattern signatures.
        has_wildcard: Whether a wildcard pattern is present.
    """
    # Check exhaustiveness: ensure all variants are covered (or wildcard is present)
    # For nested patterns, we need to extract the outer variant name from the signature
    if not has_wildcard:
        all_variants = {variant.name for variant in scrutinee_type.variants}
        # Extract outer variant names from pattern signatures (e.g., "Value(Some)" -> "Value")
        covered_outer_variants = set()
        for sig in covered_variants:
            # Pattern signatures are like "Value" or "Value(nested)"
            # Extract the part before "(" if present
            outer_variant = sig.split("(")[0]
            covered_outer_variants.add(outer_variant)

        missing_variants = all_variants - covered_outer_variants

        if missing_variants:
            # Sort for consistent error messages
            missing_list = ", ".join(sorted(missing_variants))
            er.emit(validator.reporter, er.ERR.CE2040, stmt.loc, variants=missing_list)


def validate_pattern_bindings(validator: 'TypeValidator', pattern: 'Pattern', variant: 'EnumVariant', parent_enum_type: 'EnumType') -> bool:
    """Validate pattern bindings match variant's associated types (supports nested patterns).

    Args:
        validator: The TypeValidator instance.
        pattern: The pattern with bindings to validate.
        variant: The enum variant being matched.
        parent_enum_type: The enum type of the scrutinee (for error messages).

    Returns:
        True if bindings are valid, False otherwise.
    """
    expected_bindings = len(variant.associated_types)
    actual_bindings = len(pattern.bindings)

    if expected_bindings != actual_bindings:
        er.emit(validator.reporter, er.ERR.CE2044, pattern.loc,
               variant=pattern.variant_name,
               expected=expected_bindings,
               got=actual_bindings)
        return False

    # Validate each binding (may be variable name, wildcard, nested pattern, or Own pattern)
    for i, (binding, binding_type) in enumerate(zip(pattern.bindings, variant.associated_types)):
        if isinstance(binding, Pattern):
            # Nested pattern - recursively validate
            # First, resolve the binding_type to an EnumType
            from semantics.typesys import UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

            if not isinstance(resolved_type, EnumType):
                # Nested pattern requires enum type
                er.emit(validator.reporter, er.ERR.CE2048, binding.loc, got=str(resolved_type))
                return False

            # Validate nested pattern's enum name matches
            if binding.enum_name != resolved_type.name:
                # Check for generic enum base name match
                if not (binding.enum_name in validator.generic_enum_table.by_name and
                        resolved_type.name.startswith(f"{binding.enum_name}<")):
                    er.emit(validator.reporter, er.ERR.CE2048, binding.enum_name_span or binding.loc,
                           got=binding.enum_name)
                    return False

            # Validate nested pattern's variant exists
            nested_variant = resolved_type.get_variant(binding.variant_name)
            if nested_variant is None:
                er.emit(validator.reporter, er.ERR.CE2045, binding.variant_name_span or binding.loc,
                       variant=binding.variant_name, enum=resolved_type.name)
                return False

            # Recursively validate nested pattern
            if not validate_pattern_bindings(validator, binding, nested_variant, resolved_type):
                return False
        elif isinstance(binding, OwnPattern):
            # Own pattern - validate that binding_type is Own<T>
            from semantics.typesys import UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

            # Validate binding_type is Own<T>
            # Can be either StructType (monomorphized) or GenericTypeRef (before monomorphization)
            is_own_type = False
            if isinstance(resolved_type, StructType) and resolved_type.name.startswith("Own<"):
                is_own_type = True
            elif isinstance(resolved_type, GenericTypeRef) and resolved_type.base_name == "Own":
                is_own_type = True

            if not is_own_type:
                er.emit(validator.reporter, er.ERR.CE2048, binding.loc,
                       got=f"Own(...) pattern requires Own<T> type, got {resolved_type}")
                return False

            # Validate inner pattern if it's a nested pattern
            if isinstance(binding.inner_pattern, Pattern):
                # Get element type T from Own<T>
                element_type = None
                if isinstance(resolved_type, GenericTypeRef):
                    # Before monomorphization: extract from type_args
                    if len(resolved_type.type_args) == 1:
                        element_type = resolved_type.type_args[0]
                    else:
                        er.emit(validator.reporter, er.ERR.CE2048, binding.loc,
                               got=f"Invalid Own<T> type arguments: {resolved_type}")
                        return False
                elif isinstance(resolved_type, StructType):
                    # After monomorphization: extract from fields
                    from backend.generics import own as own_module
                    try:
                        element_type = own_module.get_own_element_type(resolved_type)
                    except (TypeError, IndexError):
                        er.emit(validator.reporter, er.ERR.CE2048, binding.loc,
                               got=f"Invalid Own<T> type structure: {resolved_type}")
                        return False

                # Resolve element type if it's UnknownType
                if isinstance(element_type, UnknownType):
                    element_type = resolve_unknown_type(element_type, validator.struct_table.by_name, validator.enum_table.by_name)

                # Validate inner pattern is an enum pattern
                if not isinstance(element_type, EnumType):
                    er.emit(validator.reporter, er.ERR.CE2048, binding.inner_pattern.loc,
                           got=f"Nested pattern inside Own(...) requires enum type, got {element_type}")
                    return False

                # Validate inner nested pattern
                inner_variant = element_type.get_variant(binding.inner_pattern.variant_name)
                if inner_variant is None:
                    er.emit(validator.reporter, er.ERR.CE2045,
                           binding.inner_pattern.variant_name_span or binding.inner_pattern.loc,
                           variant=binding.inner_pattern.variant_name, enum=element_type.name)
                    return False

                # Recursively validate inner pattern
                if not validate_pattern_bindings(validator, binding.inner_pattern, inner_variant, element_type):
                    return False
        # Variable names and wildcards are always valid (type-checked later)

    return True


def register_pattern_bindings(validator: 'TypeValidator', pattern: 'Pattern', variant: 'EnumVariant') -> None:
    """Register pattern bindings in variable_types table (recursive for nested and Own patterns).

    Args:
        validator: The TypeValidator instance.
        pattern: The pattern with bindings to register.
        variant: The enum variant being matched.
    """
    for binding, binding_type in zip(pattern.bindings, variant.associated_types):
        if isinstance(binding, str):
            # Simple variable binding or wildcard
            if binding != "_":  # Skip wildcards
                # Resolve UnknownType to StructType/EnumType if needed
                from semantics.typesys import UnknownType
                resolved_type = binding_type
                if isinstance(binding_type, UnknownType):
                    resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

                validator.variable_types[binding] = resolved_type
        elif isinstance(binding, Pattern):
            # Nested pattern - recursively register its bindings
            # First, resolve the binding_type to an EnumType
            from semantics.typesys import UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

            if isinstance(resolved_type, EnumType):
                nested_variant = resolved_type.get_variant(binding.variant_name)
                if nested_variant:
                    # Recurse for nested pattern
                    register_pattern_bindings(validator, binding, nested_variant)
        elif isinstance(binding, OwnPattern):
            # Own pattern - unwrap Own<T> and register the inner pattern with type T
            from semantics.typesys import UnknownType
            resolved_type = binding_type
            if isinstance(binding_type, UnknownType):
                resolved_type = resolve_unknown_type(binding_type, validator.struct_table.by_name, validator.enum_table.by_name)

            # Get element type T from Own<T>
            element_type = None
            if isinstance(resolved_type, GenericTypeRef) and resolved_type.base_name == "Own":
                # Before monomorphization: extract from type_args
                if len(resolved_type.type_args) == 1:
                    element_type = resolved_type.type_args[0]
            elif isinstance(resolved_type, StructType) and resolved_type.name.startswith("Own<"):
                # After monomorphization: extract from fields
                from backend.generics import own as own_module
                try:
                    element_type = own_module.get_own_element_type(resolved_type)
                except (TypeError, IndexError):
                    # Invalid Own<T> structure - skip registration
                    pass

            if element_type is not None:
                # Resolve element type if needed
                if isinstance(element_type, UnknownType):
                    element_type = resolve_unknown_type(element_type, validator.struct_table.by_name, validator.enum_table.by_name)

                # Register inner pattern with unwrapped type
                inner_pattern = binding.inner_pattern
                if isinstance(inner_pattern, str):
                    # Simple variable binding
                    if inner_pattern != "_":
                        validator.variable_types[inner_pattern] = element_type
                elif isinstance(inner_pattern, Pattern):
                    # Nested pattern inside Own(...)
                    if isinstance(element_type, EnumType):
                        inner_variant = element_type.get_variant(inner_pattern.variant_name)
                        if inner_variant:
                            register_pattern_bindings(validator, inner_pattern, inner_variant)


def get_pattern_signature(pattern: 'Pattern') -> str:
    """Generate a unique signature for a pattern including nested and Own patterns.

    This signature is used for duplicate checking in match statements.
    For nested patterns, the signature includes the full nested structure.

    Args:
        pattern: The pattern to generate a signature for.

    Returns:
        A unique string signature for the pattern.

    Examples:
        Simple pattern: "Value" -> "Value"
        Nested pattern: "Value(Some(x))" -> "Value(Some)"
        Nested wildcard: "Value(None)" -> "Value(None)"
        Own pattern: "BinOp(Own(left), Own(right), op)" -> "BinOp(Own,Own,_)"
    """
    # Start with the variant name
    signature = pattern.variant_name

    # If there are bindings, recursively build the signature
    if pattern.bindings:
        binding_signatures = []
        for binding in pattern.bindings:
            if isinstance(binding, str):
                # Variable or wildcard - represented as "_" in signature
                # (we don't care about variable names, just structure)
                binding_signatures.append("_")
            elif isinstance(binding, Pattern):
                # Nested pattern - recurse to get its signature
                nested_sig = get_pattern_signature(binding)
                binding_signatures.append(nested_sig)
            elif isinstance(binding, OwnPattern):
                # Own pattern - represented as "Own" (or "Own(nested)" for nested patterns inside)
                if isinstance(binding.inner_pattern, str):
                    binding_signatures.append("Own")
                elif isinstance(binding.inner_pattern, Pattern):
                    inner_sig = get_pattern_signature(binding.inner_pattern)
                    binding_signatures.append(f"Own({inner_sig})")
        signature += "(" + ",".join(binding_signatures) + ")"

    return signature
