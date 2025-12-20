# semantics/passes/types/statements.py
"""
Statement validation for type validation.

This module contains validation functions for various statement types:
- Let statements (variable declarations)
- Return statements
- Rebind statements (variable reassignment)
- Control flow statements (if, while, foreach)

Architecture Overview
--------------------

Statement validation follows a three-phase pattern:

1. **Type Resolution** (resolution.py)
   - Converts declared types to concrete types
   - Handles GenericTypeRef → EnumType/StructType/ResultType
   - Handles UnknownType → resolved enum/struct types
   - Validates special cases (HashMap key types, Result<T, E>)

2. **Type Propagation** (propagation.py)
   - MUST happen BEFORE validation
   - Propagates expected types to constructors for type inference
   - Sets resolved_enum_type/resolved_struct_type on AST nodes
   - Enables generic type inference (Result<T>, Maybe<T>, Own<T>, etc.)
   - Handles nested generics (Result<Maybe<T>>, HashMap<K, List<V>>)

3. **Validation** (result_validation.py, compatibility.py, expressions.py)
   - Validates expressions after type information is propagated
   - Checks type compatibility and Result patterns
   - Emits appropriate error codes

Critical Ordering Requirement
-----------------------------

Type propagation MUST occur BEFORE expression validation. This ordering enables:
- Generic type inference without explicit type arguments
- Nested generic type resolution
- Proper monomorphization of generic constructors

Example flow for `let Maybe<i32> x = Maybe.Some(42)`:
1. Resolution: Maybe<i32> → Maybe<i32> (concrete enum type)
2. Propagation: Set Maybe.Some.resolved_enum_type = Maybe<i32>
3. Validation: Check compatibility of 42 with i32

AST Annotation Mechanism
------------------------

The propagation phase annotates AST nodes with resolved types:
- resolved_enum_type: Set on EnumConstructor/DotCall for generic enums
- resolved_struct_type: Set on DotCall for generic structs
- callee.id update: For Call nodes, updates to concrete type name

These annotations are used by the backend for:
- Code generation of monomorphized generic types
- LLVM IR emission with correct type information
- Runtime type dispatch for generic functions

Error Codes
-----------

Statement validation can emit:
- CE2007: Missing type annotation (let statement)
- CE2030: Return must use Result.Ok() or Result.Err()
- CE2031: Result.Ok() value type mismatch
- CE2039: Result.Err() error type mismatch
- CE2032: Blank type cannot be used for variables
- CE2058: HashMap key type cannot be dynamic array
- CE2505: Unused Result warning (assigning Result without handling)
- CW2511: Warning for ?? operator in main() function
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, ArrayType, StructType, EnumType, UnknownType, IteratorType, ResultType
from sushi_lang.semantics.ast import Let, Return, Rebind, If, While, Foreach, EnumConstructor, DotCall, MethodCall, Name, MemberAccess
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from .utils import validate_type_name
from .compatibility import validate_assignment_compatibility, types_compatible
from .expressions import validate_boolean_condition

if TYPE_CHECKING:
    from . import TypeValidator


def validate_let_statement(validator: 'TypeValidator', stmt: Let) -> None:
    """Validate let statement type annotations."""
    # Check if type annotation is missing (CE2007)
    if stmt.ty is None:
        er.emit(validator.reporter, er.ERR.CE2007, stmt.name_span, name=stmt.name)
        return  # Cannot continue without type info

    # Validate the declared type
    validate_type_name(validator, stmt.ty, stmt.type_span)

    # Blank type cannot be used for variables
    if stmt.ty == BuiltinType.BLANK:
        er.emit(validator.reporter, er.ERR.CE2032, stmt.type_span)
        return

    # Resolve variable type (handles UnknownType, GenericTypeRef, Result<T, E>, HashMap<K, V>, etc.)
    from .resolution import resolve_variable_type
    from sushi_lang.semantics.generics.types import GenericTypeRef

    resolved_type = resolve_variable_type(validator, stmt.ty, stmt.type_span)

    # Store resolved type in variable table
    validator.variable_types[stmt.name] = resolved_type

    # Update AST node for backend (but keep GenericTypeRef for Result<T, E>)
    if not (isinstance(stmt.ty, GenericTypeRef) and stmt.ty.base_name == "Result"):
        if resolved_type != stmt.ty:
            stmt.ty = resolved_type

    # Propagate expected type to constructors BEFORE validation
    # This is CRITICAL for generic type inference (Result<T>, Maybe<T>, Own<T>, etc.)
    if stmt.value:
        from .propagation import propagate_types_to_value
        propagate_types_to_value(validator, stmt.value, resolved_type)

    # Validate assignment compatibility (CE2002)
    if stmt.value:
        validate_assignment_compatibility(validator, stmt.ty, stmt.value, stmt.type_span, stmt.value.loc)

    # Phase 4.2: Validate Result<T> handling
    # If RHS is a function call that returns Result<T>, LHS must also be Result<T>
    # (unless RHS is already .realise() or other handling method)
    if stmt.value:
        rhs_type = validator.infer_expression_type(stmt.value)

        # Check if RHS is Result<T> but LHS is not
        if (rhs_type is not None and
            isinstance(rhs_type, EnumType) and
            rhs_type.name.startswith("Result<") and
            not (isinstance(stmt.ty, EnumType) and stmt.ty.name.startswith("Result<"))):

            # Allow if RHS is already a method call (like .realise() or .clone())
            # because those methods return the unwrapped type
            if not isinstance(stmt.value, MethodCall):
                # Error: assigning Result<T> to non-Result variable without handling
                er.emit(validator.reporter, er.ERR.CE2505, stmt.value.loc)


def validate_return_statement(validator: 'TypeValidator', stmt: Return) -> None:
    """Validate return statement type compatibility.

    All return statements must now use Ok(value) or Err().
    """
    if not validator.current_function:
        return  # Should not happen, but defensive programming

    expected_type = validator.current_function.ret
    if expected_type is None:
        return  # Functions without return type (shouldn't happen after CE0103)

    # Resolve return type to ResultType (handles explicit Result<T, E> and implicit T | E)
    from .resolution import resolve_return_type_to_result
    expected_type = resolve_return_type_to_result(
        validator,
        expected_type,
        validator.current_function.err_type
    )

    if stmt.value:
        # Propagate expected type to constructors BEFORE validation
        # This is CRITICAL for generic type inference (Result<T>, Maybe<T>, Own<T>, etc.)
        from .propagation import propagate_types_to_value
        propagate_types_to_value(validator, stmt.value, expected_type)

        # Validate the return expression after type propagation
        validator.validate_expression(stmt.value)

        # Validate Result.Ok() or Result.Err() pattern using extracted utilities
        from .result_validation import validate_result_pattern

        if not validate_result_pattern(validator, stmt.value, expected_type):
            # Return statement must use Result.Ok() or Result.Err()
            er.emit(validator.reporter, er.ERR.CE2030, stmt.value.loc)

        # Check for ?? in main() warning (CW2511)
        if validator.current_function.name == "main":
            from .expressions import check_propagation_in_expression
            if check_propagation_in_expression(stmt.value):
                er.emit(validator.reporter, er.ERR.CW2511, stmt.value.loc)
    else:
        # Bare "return" is no longer allowed - must use Ok() or Err()
        er.emit(validator.reporter, er.ERR.CE2030, stmt.loc)


def validate_rebind_statement(validator: 'TypeValidator', stmt: Rebind) -> None:
    """Validate rebind statement type compatibility (CE2002)."""
    from sushi_lang.semantics.ast import Name, MemberAccess

    # Determine the target type based on whether we're rebinding a variable or a field
    actual_type = None

    if isinstance(stmt.target, Name):
        # Simple variable rebind (x := value)
        var_name = stmt.target.id
        if var_name not in validator.variable_types:
            # Variable not found - this should have been caught in scope pass
            # but we'll validate the expression anyway
            validator.validate_expression(stmt.value)
            return

        # Get variable type
        var_type = validator.variable_types[var_name]

        # Unwrap reference types for validation
        # When rebinding through a reference parameter, we check compatibility
        # with the referenced type, not the reference wrapper
        from sushi_lang.semantics.typesys import ReferenceType
        actual_type = var_type
        if isinstance(var_type, ReferenceType):
            actual_type = var_type.referenced_type

    elif isinstance(stmt.target, MemberAccess):
        # Field rebind (obj.field := value)
        # First, validate the target expression to ensure it's valid
        validator.validate_expression(stmt.target)

        # Infer the type of the field being rebound
        actual_type = validator.infer_expression_type(stmt.target)
        if actual_type is None:
            # Can't infer field type - validation already failed
            validator.validate_expression(stmt.value)
            return

    else:
        # Unknown target type - should not happen
        validator.validate_expression(stmt.target)
        validator.validate_expression(stmt.value)
        return

    # Propagate expected type to constructors BEFORE validation
    # This is critical for generic type inference (user-defined generic enums, etc.)
    if stmt.value:
        from .propagation import propagate_types_to_value
        propagate_types_to_value(validator, stmt.value, actual_type)

    # Validate the expression after propagating type information
    validator.validate_expression(stmt.value)

    expr_type = validator.infer_expression_type(stmt.value)

    if expr_type is None:
        # Can't infer expression type - validation already failed elsewhere
        return

    # Check type compatibility with the actual type (unwrapped for references)
    if actual_type != expr_type:
        # Type mismatch in rebind statement
        er.emit(validator.reporter, er.ERR.CE2002, stmt.loc,
               expected=str(actual_type), got=str(expr_type))


def validate_if_statement(validator: 'TypeValidator', stmt: If) -> None:
    """Validate if statement conditions and branches."""
    # Validate all condition-block arms
    for cond, block in stmt.arms:
        # Validate condition is boolean (CE2005)
        validate_boolean_condition(validator, cond, "if")
        # Validate block
        validator._validate_block(block)

    # Validate else branch if present
    if stmt.else_block:
        validator._validate_block(stmt.else_block)


def validate_while_statement(validator: 'TypeValidator', stmt: While) -> None:
    """Validate while statement condition and body."""
    # Validate condition is boolean (CE2005)
    validate_boolean_condition(validator, stmt.cond, "while")

    # Validate body
    validator._validate_block(stmt.body)


def validate_foreach_statement(validator: 'TypeValidator', stmt: Foreach) -> None:
    """Validate foreach statement: check iterator type and item variable."""
    # Validate the iterable expression
    validator.validate_expression(stmt.iterable)
    iterable_type = validator.infer_expression_type(stmt.iterable)

    # Ensure iterable is an IteratorType
    if iterable_type is None:
        return  # Error already emitted during expression validation

    if not isinstance(iterable_type, IteratorType):
        er.emit(validator.reporter, er.ERR.CE2033, stmt.iterable.loc, got=str(iterable_type))
        return

    # Get the element type from the iterator
    element_type = iterable_type.element_type

    # If item type is explicitly declared, validate compatibility
    if stmt.item_type is not None:
        # Validate the declared type
        validate_type_name(validator, stmt.item_type, stmt.item_type_span)

        # Resolve UnknownType to StructType if needed
        declared_type = stmt.item_type
        from sushi_lang.semantics.typesys import UnknownType
        if isinstance(stmt.item_type, UnknownType):
            resolved_type = resolve_unknown_type(stmt.item_type, validator.struct_table.by_name, validator.enum_table.by_name)
            if resolved_type != stmt.item_type:
                declared_type = resolved_type

        # Check type compatibility
        if not types_compatible(validator, declared_type, element_type):
            er.emit(validator.reporter, er.ERR.CE2034, stmt.item_type_span,
                   expected=str(element_type), got=str(declared_type))
            return

        # Use declared type
        stmt.item_type = declared_type
    else:
        # Infer item type from iterator's element type
        stmt.item_type = element_type

    # Track the item variable type
    validator.variable_types[stmt.item_name] = stmt.item_type

    # Validate the body block
    validator._validate_block(stmt.body)
