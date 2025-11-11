"""
Enum constructor operations for the Sushi language compiler.

This module handles enum variant construction, including generic enums like Result<T>.
Creates tagged union structs with discriminant tags and associated data packing.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Union

from llvmlite import ir
from semantics.ast import EnumConstructor, DotCall, Name
from internals.errors import raise_internal_error
from backend import enum_utils

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.typesys import EnumType


def emit_enum_constructor(codegen: 'LLVMCodegen', expr: Union[EnumConstructor, DotCall], is_dotcall: bool = False) -> ir.Value:
    """Emit enum variant constructor (e.g., Result.Ok(42) or Color.Red()).

    For generic enums like Result<T> and Maybe<T>, the concrete type MUST be resolved by the type checker
    and stored in expr.resolved_enum_type. For non-generic enums, we look up by name directly.

    Args:
        codegen: The LLVM codegen instance.
        expr: The EnumConstructor or DotCall expression.
        is_dotcall: If True, extract fields from DotCall node.

    Returns:
        The constructed enum value.

    Raises:
        ValueError: If enum type not found or resolved_enum_type not set for generic enums.
    """
    # Extract fields based on node type
    if is_dotcall:
        # DotCall: extract enum_name, variant_name, args from DotCall fields
        assert isinstance(expr.receiver, Name), "DotCall receiver must be a Name for enum constructors"
        enum_name = expr.receiver.id
        variant_name = expr.method
        args = expr.args
        resolved_enum_type = getattr(expr, 'resolved_enum_type', None)
    else:
        # EnumConstructor: use existing fields
        enum_name = expr.enum_name
        variant_name = expr.variant_name
        args = expr.args
        resolved_enum_type = expr.resolved_enum_type

    # Priority 1: Use resolved enum type from type checker (for ALL generic enums like Result<T> and Maybe<T>)
    if resolved_enum_type is not None:
        return emit_enum_constructor_from_method_call(
            codegen, resolved_enum_type, variant_name, args
        )

    # Priority 2: For non-generic enums, look up directly by name
    if enum_name not in codegen.enum_table.by_name:
        raise_internal_error("CE0033", name=enum_name)

    enum_type = codegen.enum_table.by_name[enum_name]

    # Delegate to existing enum constructor emission
    return emit_enum_constructor_from_method_call(codegen, enum_type, variant_name, args)


def emit_enum_constructor_from_method_call(
    codegen: 'LLVMCodegen',
    enum_type: 'EnumType',
    variant_name: str,
    args: list
) -> ir.Value:
    """Emit enum constructor for method call syntax (e.g., Color.Red()).

    Creates a tagged union struct: {i32 tag, [N x i8] data}

    Args:
        codegen: The LLVM codegen instance.
        enum_type: The enum type being constructed.
        variant_name: The variant name (e.g., "Red").
        args: List of argument expressions for the variant.

    Returns:
        The constructed enum value.

    Raises:
        ValueError: If variant not found or argument count mismatch.
    """
    from semantics.typesys import EnumType

    # Find the variant and get its index (discriminant/tag)
    variant_index = enum_type.get_variant_index(variant_name)
    if variant_index is None:
        raise_internal_error("CE0034", variant=variant_name, enum=enum_type.name)

    variant = enum_type.get_variant(variant_name)

    # Check argument count
    if len(args) != len(variant.associated_types):
        raise_internal_error("CE0096", operation="Variant {enum_type.name}.{variant_name} expects {len(variant.associated_types)} arguments, got {len(args)}"
        )

    # Get the LLVM type for this enum: {i32 tag, [N x i8] data}
    llvm_enum_type = codegen.types.get_enum_type(enum_type)

    # Create enum value with tag set
    enum_value = enum_utils.construct_enum_variant(
        codegen, llvm_enum_type, variant_index,
        data=None, name_prefix=f"{enum_type.name}_{variant_name}"
    )

    # If there are associated values, pack them into the data field
    if args:
        # Allocate temporary storage for the data
        data_array_type = llvm_enum_type.elements[1]  # [N x i8] array
        temp_alloca = codegen.builder.alloca(data_array_type, name=f"enum_data_temp")

        # Cast to i8* for bitcasting
        data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

        # Pack each argument into the data field
        offset = 0
        for i, (arg_expr, arg_type) in enumerate(zip(args, variant.associated_types)):
            # Special handling for dynamic arrays to ensure proper ownership
            # Similar to how struct constructors handle dynamic arrays
            from semantics.ast import DynamicArrayFrom
            from semantics.typesys import DynamicArrayType

            if isinstance(arg_type, DynamicArrayType) and isinstance(arg_expr, DynamicArrayFrom):
                # Create a fresh dynamic array with its own heap allocation
                # This prevents dangling pointers when the original array is destroyed
                elements = []
                for element_expr in arg_expr.elements.elements:
                    element_value = codegen.expressions.emit_expr(element_expr)
                    elements.append(element_value)

                # Allocate and initialize array
                from backend.types import arrays
                element_llvm_type = codegen.types.ll_type(arg_type.base_type)
                arg_value = arrays.create_dynamic_array_from_elements(
                    codegen, arg_type.base_type, element_llvm_type, elements
                )
            else:
                # Regular argument - emit normally
                arg_value = codegen.expressions.emit_expr(arg_expr)

            # Calculate size of this argument
            from backend.expressions import memory
            arg_llvm_type = arg_value.type
            arg_size = memory.get_type_size(arg_llvm_type)

            # Store the argument at the current offset
            arg_ptr_i8 = codegen.builder.gep(data_ptr, [ir.Constant(codegen.types.i32, offset)], name=f"arg{i}_ptr")
            arg_ptr_typed = codegen.builder.bitcast(arg_ptr_i8, ir.PointerType(arg_llvm_type), name=f"arg{i}_ptr_typed")
            codegen.builder.store(arg_value, arg_ptr_typed)

            offset += arg_size

        # Load the packed data array
        packed_data = codegen.builder.load(temp_alloca, name="packed_data")

        # Insert the data into the enum struct
        enum_value = enum_utils.set_enum_data(
            codegen, enum_value, packed_data,
            name=f"{enum_type.name}_{variant_name}_data"
        )

    return enum_value
