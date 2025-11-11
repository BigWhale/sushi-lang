"""
Struct operations for the Sushi language compiler.

This module handles struct constructor calls, member access, and type inference
for struct expressions. Includes GEP-based field access for dynamic array fields
to enable Rust-style method call syntax on struct fields.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from semantics.ast import Expr, Name, Call, MemberAccess, MethodCall, DotCall, DynamicArrayNew, DynamicArrayFrom
from semantics.typesys import UnknownType, StructType, DynamicArrayType, ReferenceType
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def emit_struct_constructor(codegen: 'LLVMCodegen', expr: Call, to_i1: bool = False) -> ir.Value:
    """Emit struct constructor call.

    Creates a struct value by constructing it with the provided field values.
    Handles dynamic array fields with special initialization logic and performs
    deep-copy for nested structs containing dynamic arrays to prevent double-free.

    Args:
        codegen: The LLVM codegen instance.
        expr: The constructor call expression (treated as Call).
        to_i1: Whether to convert result to i1 (should be False for structs).

    Returns:
        The constructed struct value.

    Raises:
        TypeError: If field count doesn't match or unsupported array constructor type.
    """
    struct_name = expr.callee.id
    struct_type = codegen.struct_table.by_name[struct_name]

    # Get the LLVM struct type
    llvm_struct_type = codegen.types.get_struct_type(struct_type)

    # Emit field values
    field_values = []
    for arg, (field_name, field_type) in zip(expr.args, struct_type.fields):
        # Special handling for dynamic array fields
        if isinstance(field_type, DynamicArrayType):
            # For dynamic arrays, we need to create the struct value directly
            if isinstance(arg, DynamicArrayNew):
                # Create empty dynamic array struct: {0, 0, null}
                element_llvm_type = codegen.types.ll_type(field_type.base_type)
                array_struct_type = ir.LiteralStructType([
                    codegen.types.i32,                     # len
                    codegen.types.i32,                     # cap
                    ir.PointerType(element_llvm_type)           # data*
                ])
                # Create empty array struct
                zero_i32 = ir.Constant(codegen.types.i32, 0)
                null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
                array_struct = ir.Constant(array_struct_type, ir.Undefined)
                array_struct = codegen.builder.insert_value(array_struct, zero_i32, 0)
                array_struct = codegen.builder.insert_value(array_struct, zero_i32, 1)
                array_struct = codegen.builder.insert_value(array_struct, null_ptr, 2)
                field_values.append(array_struct)
            elif isinstance(arg, DynamicArrayFrom):
                # Create initialized dynamic array struct from array literal
                # Emit all elements
                elements = []
                for element_expr in arg.elements.elements:
                    element_value = codegen.expressions.emit_expr(element_expr)
                    elements.append(element_value)

                # Allocate and initialize array
                from backend.types import arrays
                element_llvm_type = codegen.types.ll_type(field_type.base_type)
                array_struct = arrays.create_dynamic_array_from_elements(
                    codegen, field_type.base_type, element_llvm_type, elements
                )
                field_values.append(array_struct)
            else:
                # Move semantics: Transfer ownership of existing dynamic array
                # This handles cases like: Container(existing_array)
                # The array is MOVED into the struct (original becomes invalid)
                arg_value = codegen.expressions.emit_expr(arg)

                # Handle method calls that return pointers (like .clone())
                # If arg_value is a pointer to a dynamic array struct, load the value
                if isinstance(arg_value.type, ir.PointerType):
                    element_llvm_type = codegen.types.ll_type(field_type.base_type)
                    expected_struct_type = ir.LiteralStructType([
                        codegen.types.i32,
                        codegen.types.i32,
                        ir.PointerType(element_llvm_type)
                    ])
                    if arg_value.type.pointee == expected_struct_type:
                        arg_value = codegen.builder.load(arg_value)

                field_values.append(arg_value)

                # Mark the source variable as moved to prevent double-free
                # (but not for method calls like .clone() which create new arrays)
                if isinstance(arg, Name):
                    codegen.dynamic_arrays.mark_as_moved(arg.id)
        else:
            # Regular field - emit normally
            arg_value = codegen.expressions.emit_expr(arg)

            # Resolve UnknownType to StructType if needed
            resolved_field_type = field_type
            if isinstance(field_type, UnknownType):
                if field_type.name in codegen.struct_table.by_name:
                    resolved_field_type = codegen.struct_table.by_name[field_type.name]

            # Deep-copy structs with dynamic arrays to avoid double-free
            # When passing a struct with dynamic arrays to another struct constructor,
            # we must clone the dynamic array memory to avoid shared ownership
            if isinstance(resolved_field_type, StructType):
                if codegen.dynamic_arrays.struct_needs_cleanup(resolved_field_type):
                    from backend.expressions import memory
                    arg_value = memory.deep_copy_struct(codegen, arg_value, resolved_field_type)

            # Cast to the expected field type
            llvm_field_type = codegen.types.ll_type(field_type)
            casted_value = codegen.utils.cast_for_param(arg_value, llvm_field_type)
            field_values.append(casted_value)

    # Construct the struct value
    # Start with an undefined struct value
    struct_value = ir.Constant(llvm_struct_type, ir.Undefined)

    # Insert each field value
    for i, field_value in enumerate(field_values):
        struct_value = codegen.builder.insert_value(struct_value, field_value, i)

    return struct_value


def emit_member_access(codegen: 'LLVMCodegen', expr: MemberAccess, to_i1: bool = False) -> ir.Value:
    """Emit member access expression for struct fields.

    For dynamic array fields, returns a pointer to the field (GEP-based access).
    For other fields, extracts the field value using extractvalue.

    This enables method calls on dynamic array fields within structs (Rust-style).

    Args:
        codegen: The LLVM codegen instance.
        expr: The member access expression.
        to_i1: Whether to convert result to i1 (usually False for struct fields).

    Returns:
        The field value, or pointer to field for dynamic array fields.

    Raises:
        TypeError: If receiver is not a struct or field doesn't exist.
    """
    # Infer the receiver's struct type
    struct_type = infer_struct_type(codegen, expr.receiver)

    # Get the field index and type
    field_index = struct_type.get_field_index(expr.member)
    if field_index is None:
        raise_internal_error("CE0029", struct=struct_type.name, field=expr.member)

    field_type = struct_type.get_field_type(expr.member)

    # Special handling for dynamic array fields: use GEP to get pointer to field
    # This enables method calls like c.numbers.push(10) to work (Rust-style)
    if isinstance(field_type, DynamicArrayType):
        # Try to get struct variable's alloca for GEP-based access
        struct_alloca = try_get_struct_alloca(codegen, expr.receiver)

        if struct_alloca is not None:
            # Use GEP to get pointer to the dynamic array field
            from backend import gep_utils
            field_ptr = gep_utils.gep_struct_field(
                codegen,
                struct_alloca,
                field_index,
                name=f"{expr.member}_ptr"
            )
            return field_ptr

    # Default: emit receiver and extract field value
    # For references, we need to load the struct value first
    if isinstance(expr.receiver, Name):
        from backend.expressions.type_utils import is_reference_parameter
        if is_reference_parameter(codegen, expr.receiver.id):
            # Reference parameter: get the pointer and load the struct
            slot = codegen.memory.find_local_slot(expr.receiver.id)
            struct_ptr = codegen.builder.load(slot, name=f"{expr.receiver.id}_ptr")
            receiver_value = codegen.builder.load(struct_ptr, name=f"{expr.receiver.id}_deref")
        else:
            # Regular variable: emit normally
            receiver_value = codegen.expressions.emit_expr(expr.receiver)
    else:
        # Other expressions: emit normally
        receiver_value = codegen.expressions.emit_expr(expr.receiver)

    field_value = codegen.builder.extract_value(receiver_value, field_index)
    return field_value


def try_get_struct_alloca(codegen: 'LLVMCodegen', receiver_expr: Expr) -> Optional[ir.Value]:
    """Try to get the alloca instruction or pointer for a struct variable.

    This is used for GEP-based field access for dynamic array fields.
    For reference parameters, returns the pointer (not the alloca containing the pointer).

    Args:
        codegen: The LLVM codegen instance.
        receiver_expr: The receiver expression (typically a Name or MemberAccess).

    Returns:
        The alloca instruction or pointer if receiver is accessible, None otherwise.
    """
    if isinstance(receiver_expr, Name):
        # Simple variable access: look up alloca
        try:
            slot = codegen.memory.find_local_slot(receiver_expr.id)

            # Check if this is a reference parameter
            from backend.expressions.type_utils import is_reference_parameter
            if is_reference_parameter(codegen, receiver_expr.id):
                # For reference parameters, the slot contains a pointer to the struct
                # Load the pointer from the slot to get the actual struct pointer
                return codegen.builder.load(slot, name=f"{receiver_expr.id}_ptr")
            else:
                # For regular variables, return the alloca directly
                return slot
        except KeyError:
            return None
    elif isinstance(receiver_expr, MemberAccess):
        # Nested struct access: recursively get base alloca, then GEP through fields
        base_alloca = try_get_struct_alloca(codegen, receiver_expr.receiver)
        if base_alloca is None:
            return None

        # Get the parent struct type and field index
        parent_struct_type = infer_struct_type(codegen, receiver_expr.receiver)
        field_index = parent_struct_type.get_field_index(receiver_expr.member)
        if field_index is None:
            return None

        # GEP to get pointer to the nested struct field
        from backend import gep_utils
        field_ptr = gep_utils.gep_struct_field(
            codegen,
            base_alloca,
            field_index,
            name=f"{receiver_expr.member}_ptr"
        )
        return field_ptr
    else:
        # Other expressions (method calls, etc.) - can't get alloca
        return None


def infer_struct_type(codegen: 'LLVMCodegen', expr: Expr) -> StructType:
    """Infer the struct type of an expression.

    Args:
        codegen: The LLVM codegen instance.
        expr: The expression to infer the type of.

    Returns:
        The StructType of the expression.

    Raises:
        TypeError: If the type cannot be inferred or is not a struct.
    """
    if isinstance(expr, Name):
        # Look up the variable's Sushi type
        var_name = expr.id
        if var_name not in codegen.variable_types:
            raise_internal_error("CE0056", name=var_name)

        var_type = codegen.variable_types[var_name]

        # Unwrap ReferenceType to get the underlying type
        if isinstance(var_type, ReferenceType):
            var_type = var_type.referenced_type

        # Resolve UnknownType to StructType if needed
        if isinstance(var_type, UnknownType):
            if var_type.name not in codegen.struct_table.by_name:
                raise_internal_error("CE0020", type=var_type.name)
            return codegen.struct_table.by_name[var_type.name]
        elif isinstance(var_type, StructType):
            return var_type
        else:
            # Check if this is a GenericTypeRef that resolves to a struct
            from semantics.generics.types import GenericTypeRef
            if isinstance(var_type, GenericTypeRef):
                # Build struct name from generic type ref: Box<i32> -> "Box<i32>"
                type_args_str = ", ".join(str(arg) for arg in var_type.type_args)
                struct_name = f"{var_type.base_name}<{type_args_str}>"
                if struct_name in codegen.struct_table.by_name:
                    return codegen.struct_table.by_name[struct_name]

            raise_internal_error("CE0031", type=str(var_type))

    elif isinstance(expr, MemberAccess):
        # Recursively infer the type of nested member access
        parent_struct_type = infer_struct_type(codegen, expr.receiver)
        field_type = parent_struct_type.get_field_type(expr.member)

        if field_type is None:
            raise_internal_error("CE0029", struct=parent_struct_type.name, field=expr.member)

        # Resolve field type to StructType
        if isinstance(field_type, UnknownType):
            if field_type.name not in codegen.struct_table.by_name:
                raise_internal_error("CE0020", type=field_type.name)
            return codegen.struct_table.by_name[field_type.name]
        elif isinstance(field_type, StructType):
            return field_type
        else:
            # Check if this is a GenericTypeRef that resolves to a struct
            from semantics.generics.types import GenericTypeRef
            if isinstance(field_type, GenericTypeRef):
                # Build struct name from generic type ref: Box<i32> -> "Box<i32>"
                type_args_str = ", ".join(str(arg) for arg in field_type.type_args)
                struct_name = f"{field_type.base_name}<{type_args_str}>"
                if struct_name in codegen.struct_table.by_name:
                    return codegen.struct_table.by_name[struct_name]

            raise_internal_error("CE0044", type=str(field_type))

    elif isinstance(expr, MethodCall):
        # Infer struct type from method call return type
        # For array.get() methods, get receiver type from semantic types
        if expr.method == "get" and isinstance(expr.receiver, Name):
            receiver_name = expr.receiver.id
            receiver_type = codegen.memory.find_semantic_type(receiver_name)

            if receiver_type and isinstance(receiver_type, DynamicArrayType):
                element_type = receiver_type.base_type

                # Resolve UnknownType to StructType
                if isinstance(element_type, UnknownType):
                    if element_type.name not in codegen.struct_table.by_name:
                        raise_internal_error("CE0020", type=element_type.name)
                    return codegen.struct_table.by_name[element_type.name]
                elif isinstance(element_type, StructType):
                    return element_type
                else:
                    raise_internal_error("CE0043", type=str(element_type))

        raise_internal_error("CE0068", method=expr.method)

    elif isinstance(expr, DotCall):
        # DotCall: unified X.Y(args) - handle array.get() method calls
        # For array.get() methods, get receiver type from semantic types
        if expr.method == "get" and isinstance(expr.receiver, Name):
            receiver_name = expr.receiver.id
            receiver_type = codegen.memory.find_semantic_type(receiver_name)

            if receiver_type and isinstance(receiver_type, DynamicArrayType):
                element_type = receiver_type.base_type

                # Resolve UnknownType to StructType
                if isinstance(element_type, UnknownType):
                    if element_type.name not in codegen.struct_table.by_name:
                        raise_internal_error("CE0020", type=element_type.name)
                    return codegen.struct_table.by_name[element_type.name]
                elif isinstance(element_type, StructType):
                    return element_type
                else:
                    raise_internal_error("CE0043", type=str(element_type))

        raise_internal_error("CE0069", method=expr.method)

    else:
        raise_internal_error("CE0067", expr=type(expr).__name__)
