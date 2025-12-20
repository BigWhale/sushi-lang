# semantics/passes/types/calls/structs.py
"""
Struct constructor validation.

Handles validation for:
- Positional struct construction: Point(10, 20)
- Named struct construction: Point(x: 10, y: 20)
- Generic struct construction: Box<i32>(value: 42)
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.semantics.ast import Call
from ..compatibility import types_compatible
from ..utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall

if TYPE_CHECKING:
    from .. import TypeValidator


def validate_struct_constructor(validator: 'TypeValidator', call: Call) -> None:
    """Validate struct constructor call - field count, field names, and types.

    Handles:
    - Positional construction: Point(10, 20)
    - Named construction: Point(x: 10, y: 20)
    - Generic structs: Box<i32>(value: 42)
    """
    struct_name = call.callee.id

    # Check if struct exists in the struct table
    if struct_name not in validator.struct_table.by_name:
        from sushi_lang.semantics.generics.types import GenericTypeRef
        er.emit(validator.reporter, er.ERR.CE2001, call.callee.loc, name=struct_name)
        # Still validate arguments to propagate type information
        for arg in call.args:
            validator.validate_expression(arg)
        return

    struct_type = validator.struct_table.by_name[struct_name]

    # Get expected fields
    expected_fields = list(struct_type.fields)

    # Check if this is named or positional construction
    field_names = getattr(call, 'field_names', None)

    if field_names is not None:
        # NAMED CONSTRUCTION
        _validate_named_struct_constructor(validator, call, struct_type, expected_fields, field_names)
    else:
        # POSITIONAL CONSTRUCTION (existing logic)
        _validate_positional_struct_constructor(validator, call, struct_type, expected_fields)


def _validate_named_struct_constructor(
    validator: 'TypeValidator',
    call: Call,
    struct_type: StructType,
    expected_fields: List[Tuple[str, Type]],
    field_names: List[str]
) -> None:
    """Validate named struct constructor and reorder arguments.

    Args:
        validator: Type validator instance
        call: Call AST node
        struct_type: Struct type being constructed
        expected_fields: List of (field_name, field_type) from struct definition
        field_names: Field names from call site (in source order)
    """
    from ..field_matcher import validate_and_reorder_named_args

    actual_args = call.args

    # Validate and reorder arguments to match field declaration order
    reordered_args = validate_and_reorder_named_args(
        struct_type,
        actual_args,
        field_names,
        validator.reporter,
        call.callee.loc
    )

    if reordered_args is None:
        # Validation failed - errors already emitted
        # Still validate argument expressions for type propagation
        for arg in actual_args:
            validator.validate_expression(arg)
        return

    # MUTATE: Replace call.args with reordered arguments
    # This ensures codegen receives arguments in field declaration order
    call.args = reordered_args

    # Clear field_names since args are now in positional order
    call.field_names = None

    # Validate each argument type against corresponding field type
    # (Same logic as positional, but args are now reordered)
    for i, (arg, (field_name, field_type)) in enumerate(zip(reordered_args, expected_fields)):
        # Resolve GenericTypeRef to concrete type if needed
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.typesys import StructType as StructTypeClass, ResultType
        resolved_field_type = field_type
        if isinstance(field_type, GenericTypeRef):
            # Special handling for Result<T, E>
            if field_type.base_name == "Result" and len(field_type.type_args) == 2:
                from sushi_lang.backend.generics.results import ensure_result_type_in_table
                from sushi_lang.semantics.type_resolution import resolve_unknown_type

                # Resolve type arguments
                ok_type = resolve_unknown_type(
                    field_type.type_args[0],
                    validator.struct_table.by_name,
                    validator.enum_table.by_name
                )
                err_type = resolve_unknown_type(
                    field_type.type_args[1],
                    validator.struct_table.by_name,
                    validator.enum_table.by_name
                )

                # Ensure Result<T, E> exists in enum table
                result_enum = ensure_result_type_in_table(validator.enum_table, ok_type, err_type)
                if result_enum is not None:
                    resolved_field_type = result_enum
            else:
                # Generic struct/enum lookup
                type_args_str = ", ".join(str(arg_type) for arg_type in field_type.type_args)
                concrete_name = f"{field_type.base_name}<{type_args_str}>"

                if concrete_name in validator.struct_table.by_name:
                    resolved_field_type = validator.struct_table.by_name[concrete_name]
                elif concrete_name in validator.enum_table.by_name:
                    resolved_field_type = validator.enum_table.by_name[concrete_name]

        # Propagate expected type to DotCall nodes for generic enums
        propagate_enum_type_to_dotcall(validator, arg, resolved_field_type)

        # Propagate expected type to DotCall nodes for generic structs
        propagate_struct_type_to_dotcall(validator, arg, resolved_field_type)

        # Propagate expected type to Call nodes for generic struct constructors
        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(resolved_field_type, StructTypeClass):
            arg_struct_name = arg.callee.id
            if arg_struct_name in validator.generic_struct_table.by_name:
                arg.callee.id = resolved_field_type.name

        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, resolved_field_type):
            er.emit(validator.reporter, er.ERR.CE2083, arg.loc,
                   field=field_name, expected=str(resolved_field_type), got=str(arg_type))


def _validate_positional_struct_constructor(
    validator: 'TypeValidator',
    call: Call,
    struct_type: StructType,
    expected_fields: List[Tuple[str, Type]]
) -> None:
    """Validate positional struct constructor (existing logic).

    Args:
        validator: Type validator instance
        call: Call AST node
        struct_type: Struct type being constructed
        expected_fields: List of (field_name, field_type) from struct definition
    """
    actual_args = call.args

    # Check field count
    if len(actual_args) != len(expected_fields):
        er.emit(validator.reporter, er.ERR.CE2027, call.callee.loc,
               name=struct_type.name, expected=len(expected_fields), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding field type
    for i, (arg, (field_name, field_type)) in enumerate(zip(actual_args, expected_fields)):
        # Resolve GenericTypeRef to concrete type if needed
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.typesys import StructType as StructTypeClass, ResultType
        resolved_field_type = field_type
        if isinstance(field_type, GenericTypeRef):
            # Special handling for Result<T, E>
            if field_type.base_name == "Result" and len(field_type.type_args) == 2:
                from sushi_lang.backend.generics.results import ensure_result_type_in_table
                from sushi_lang.semantics.type_resolution import resolve_unknown_type

                # Resolve type arguments
                ok_type = resolve_unknown_type(
                    field_type.type_args[0],
                    validator.struct_table.by_name,
                    validator.enum_table.by_name
                )
                err_type = resolve_unknown_type(
                    field_type.type_args[1],
                    validator.struct_table.by_name,
                    validator.enum_table.by_name
                )

                # Ensure Result<T, E> exists in enum table
                result_enum = ensure_result_type_in_table(validator.enum_table, ok_type, err_type)
                if result_enum is not None:
                    resolved_field_type = result_enum
            else:
                # Generic struct/enum lookup
                type_args_str = ", ".join(str(arg_type) for arg_type in field_type.type_args)
                concrete_name = f"{field_type.base_name}<{type_args_str}>"

                if concrete_name in validator.struct_table.by_name:
                    resolved_field_type = validator.struct_table.by_name[concrete_name]
                elif concrete_name in validator.enum_table.by_name:
                    resolved_field_type = validator.enum_table.by_name[concrete_name]

        # Propagate expected type to DotCall nodes for generic enums
        propagate_enum_type_to_dotcall(validator, arg, resolved_field_type)

        # Propagate expected type to DotCall nodes for generic structs
        propagate_struct_type_to_dotcall(validator, arg, resolved_field_type)

        # Propagate expected type to Call nodes for generic struct constructors
        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(resolved_field_type, StructTypeClass):
            arg_struct_name = arg.callee.id
            if arg_struct_name in validator.generic_struct_table.by_name:
                arg.callee.id = resolved_field_type.name

        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, resolved_field_type):
            er.emit(validator.reporter, er.ERR.CE2028, arg.loc,
                   field_name=field_name, expected=str(resolved_field_type), got=str(arg_type))

    # Validate any excess arguments (if more args than fields)
    for i in range(len(expected_fields), len(actual_args)):
        validator.validate_expression(actual_args[i])
