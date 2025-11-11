# semantics/passes/types/statements.py
"""
Statement validation for type validation.

This module contains validation functions for various statement types:
- Let statements (variable declarations)
- Return statements
- Rebind statements (variable reassignment)
- Control flow statements (if, while, foreach)
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from internals import errors as er
from semantics.typesys import BuiltinType, DynamicArrayType, ArrayType, StructType, EnumType, UnknownType, IteratorType
from semantics.ast import Let, Return, Rebind, If, While, Foreach, EnumConstructor, DotCall, MethodCall, Name
from semantics.type_resolution import resolve_unknown_type
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

    # Add variable to type table (resolve UnknownType/GenericTypeRef to StructType/EnumType if needed)
    from semantics.typesys import UnknownType, StructType
    from semantics.generics.types import GenericTypeRef

    resolved_type = stmt.ty  # Track the resolved type for propagation

    if isinstance(stmt.ty, (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)):
        validator.variable_types[stmt.name] = stmt.ty
        resolved_type = stmt.ty
    elif isinstance(stmt.ty, UnknownType):
        # Resolve UnknownType to StructType or EnumType
        resolved_type = resolve_unknown_type(stmt.ty, validator.struct_table.by_name, validator.enum_table.by_name)
        if resolved_type != stmt.ty:
            validator.variable_types[stmt.name] = resolved_type
            stmt.ty = resolved_type  # Update AST node for backend
    elif isinstance(stmt.ty, GenericTypeRef):
        # Resolve GenericTypeRef to monomorphized EnumType or StructType
        # Build type name: Result<i32> -> "Result<i32>", Box<i32> -> "Box<i32>"
        type_args_str = ", ".join(str(arg) for arg in stmt.ty.type_args)
        concrete_name = f"{stmt.ty.base_name}<{type_args_str}>"

        # Try enum table first
        if concrete_name in validator.enum_table.by_name:
            resolved_type = validator.enum_table.by_name[concrete_name]
            validator.variable_types[stmt.name] = resolved_type
            stmt.ty = resolved_type  # Update AST node for backend
        # Try struct table second
        elif concrete_name in validator.struct_table.by_name:
            resolved_type = validator.struct_table.by_name[concrete_name]
            validator.variable_types[stmt.name] = resolved_type
            stmt.ty = resolved_type  # Update AST node for backend

    # If RHS is an enum constructor for a generic enum, propagate the expected type
    if stmt.value and isinstance(stmt.value, (EnumConstructor, DotCall)):
        # Check if this is a generic enum constructor (e.g., Maybe.Some(42))
        enum_name = None
        if isinstance(stmt.value, EnumConstructor):
            enum_name = stmt.value.enum_name
        elif isinstance(stmt.value, DotCall) and isinstance(stmt.value.receiver, Name):
            enum_name = stmt.value.receiver.id

        if (enum_name and enum_name in validator.generic_enum_table.by_name and
            isinstance(resolved_type, EnumType)):
            # Store the resolved enum type in the AST node for backend
            stmt.value.resolved_enum_type = resolved_type

    # If RHS is a struct constructor for a generic struct, propagate the expected type
    # This allows `let Own<i32> ptr = Own.alloc(42)` to resolve correctly
    if stmt.value and isinstance(stmt.value, DotCall) and isinstance(stmt.value.receiver, Name):
        struct_name = stmt.value.receiver.id

        # Check if this is a generic struct constructor (e.g., Own.alloc(42))
        if (struct_name in validator.generic_struct_table.by_name and
            isinstance(resolved_type, StructType)):
            # Store the resolved struct type in the AST node for backend
            stmt.value.resolved_struct_type = resolved_type

    # If RHS is a struct constructor (Call node), propagate the expected type for generic structs
    # This allows `let Box<i32> b = Box(42)` to resolve correctly
    from semantics.ast import Call
    if stmt.value and isinstance(stmt.value, Call) and hasattr(stmt.value.callee, 'id'):
        struct_name = stmt.value.callee.id

        # Check if this is a generic struct constructor
        if (struct_name in validator.generic_struct_table.by_name and
            isinstance(resolved_type, StructType)):
            # Update the Call node's callee id to use the concrete type name
            # This allows validate_struct_constructor to find the right struct
            # e.g., Box -> Box<i32>
            stmt.value.callee.id = resolved_type.name

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

    # Resolve GenericTypeRef to concrete EnumType if needed
    from semantics.generics.types import GenericTypeRef
    if isinstance(expected_type, GenericTypeRef):
        type_args_str = ", ".join(str(arg) for arg in expected_type.type_args)
        enum_name = f"{expected_type.base_name}<{type_args_str}>"
        if enum_name in validator.enum_table.by_name:
            expected_type = validator.enum_table.by_name[enum_name]

    if stmt.value:
        # Propagate expected type to Result.Ok() and Result.Err() BEFORE validation
        # This is CRITICAL: Result<T> is a generic enum, so Result.Ok needs resolved_enum_type set
        if isinstance(stmt.value, (EnumConstructor, DotCall)):
            # Check if this is Result.Ok() or Result.Err()
            is_result_enum = False
            variant_name = None

            if isinstance(stmt.value, EnumConstructor):
                is_result_enum = (stmt.value.enum_name == "Result")
                variant_name = stmt.value.variant_name
            elif isinstance(stmt.value, DotCall) and isinstance(stmt.value.receiver, Name):
                is_result_enum = (stmt.value.receiver.id == "Result")
                variant_name = stmt.value.method

            if is_result_enum:
                # Build the Result<T> type name from the function's return type
                result_enum_name = f"Result<{expected_type}>"
                if result_enum_name in validator.enum_table.by_name:
                    # Set resolved_enum_type to Result<T>
                    stmt.value.resolved_enum_type = validator.enum_table.by_name[result_enum_name]

                # Also propagate type to nested generic enum constructors (e.g., Maybe.Some inside Result.Ok)
                if variant_name == "Ok":
                    if stmt.value.args and isinstance(stmt.value.args[0], (EnumConstructor, DotCall)):
                        arg_constructor = stmt.value.args[0]
                        # Check if nested constructor is for a generic enum
                        nested_enum_name = None
                        if isinstance(arg_constructor, EnumConstructor):
                            nested_enum_name = arg_constructor.enum_name
                        elif isinstance(arg_constructor, DotCall) and isinstance(arg_constructor.receiver, Name):
                            nested_enum_name = arg_constructor.receiver.id

                        if (nested_enum_name and nested_enum_name in validator.generic_enum_table.by_name and
                            isinstance(expected_type, EnumType)):
                            # Propagate the function's return type to the nested constructor
                            arg_constructor.resolved_enum_type = expected_type

        # First, validate the return expression to ensure it's properly formed
        validator.validate_expression(stmt.value)

        # Check for Result.Ok() or Result.Err() patterns
        # These can be EnumConstructor, DotCall, or MethodCall nodes
        is_result_ok_or_err = False

        if isinstance(stmt.value, EnumConstructor):
            # Old-style enum constructor parsing
            if stmt.value.enum_name == "Result":
                is_result_ok_or_err = True
                if stmt.value.variant_name == "Ok":
                    if stmt.value.args:
                        value_type = validator.infer_expression_type(stmt.value.args[0])
                        if value_type and expected_type and not types_compatible(validator, value_type, expected_type):
                            er.emit(validator.reporter, er.ERR.CE2031, stmt.value.loc,
                                   expected=str(expected_type), got=str(value_type))
                # Err() is always valid - no type checking needed
        elif isinstance(stmt.value, DotCall):
            # DotCall: unified X.Y(args) node
            if isinstance(stmt.value.receiver, Name) and stmt.value.receiver.id == "Result":
                is_result_ok_or_err = True
                if stmt.value.method == "Ok":
                    if stmt.value.args:
                        value_type = validator.infer_expression_type(stmt.value.args[0])
                        if value_type and expected_type and not types_compatible(validator, value_type, expected_type):
                            er.emit(validator.reporter, er.ERR.CE2031, stmt.value.loc,
                                   expected=str(expected_type), got=str(value_type))
                # Err() is always valid - no type checking needed
        elif isinstance(stmt.value, MethodCall):
            # Old parsing: Result.Ok() was parsed as MethodCall (legacy support)
            # Check if this is actually an enum constructor (Result.Ok/Result.Err)
            if isinstance(stmt.value.receiver, Name) and (
                stmt.value.receiver.id in validator.enum_table.by_name or
                stmt.value.receiver.id == "Result" or
                stmt.value.receiver.id in validator.generic_enum_table.by_name
            ):
                # This is an enum constructor like Result.Ok() or FileMode.Read()
                if stmt.value.receiver.id == "Result":
                    is_result_ok_or_err = True
                    if stmt.value.method == "Ok":
                        if stmt.value.args:
                            value_type = validator.infer_expression_type(stmt.value.args[0])
                            if value_type and expected_type and not types_compatible(validator, value_type, expected_type):
                                er.emit(validator.reporter, er.ERR.CE2031, stmt.value.loc,
                                       expected=str(expected_type), got=str(value_type))
                    # Err() is always valid - no type checking needed

        if not is_result_ok_or_err:
            # Return statement must use Result.Ok() or Result.Err()
            er.emit(validator.reporter, er.ERR.CE2030, stmt.value.loc)
    else:
        # Bare "return" is no longer allowed - must use Ok() or Err()
        er.emit(validator.reporter, er.ERR.CE2030, stmt.loc)


def validate_rebind_statement(validator: 'TypeValidator', stmt: Rebind) -> None:
    """Validate rebind statement type compatibility (CE2002)."""
    from semantics.ast import Name, MemberAccess

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
        from semantics.typesys import ReferenceType
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

    # If RHS is an enum constructor for a generic enum, propagate the expected type
    # This is critical for user-defined generic enums (e.g., Either<T, U>)
    if stmt.value and isinstance(stmt.value, (EnumConstructor, DotCall)):
        # Check if this is a generic enum constructor (e.g., Either.Right("test"))
        enum_name = None
        if isinstance(stmt.value, EnumConstructor):
            enum_name = stmt.value.enum_name
        elif isinstance(stmt.value, DotCall) and isinstance(stmt.value.receiver, Name):
            enum_name = stmt.value.receiver.id

        if (enum_name and enum_name in validator.generic_enum_table.by_name and
            isinstance(actual_type, EnumType)):
            # Store the resolved enum type in the AST node for backend
            stmt.value.resolved_enum_type = actual_type

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
        from semantics.typesys import UnknownType
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
