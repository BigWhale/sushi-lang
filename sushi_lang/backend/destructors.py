"""
Unified value destruction logic for all Sushi types.

This module provides the canonical implementation of recursive cleanup for:
- Dynamic arrays (with recursive element cleanup)
- Structs (with recursive field cleanup)
- Enums (with variant-based cleanup)
- Own<T> (heap-allocated owned values)

This code was previously duplicated in both memory_manager.py and llvm_memory.py.
Now both modules delegate to these functions for consistency.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import llvmlite.ir as ir

from sushi_lang.semantics.typesys import Type, BuiltinType, DynamicArrayType, StructType, EnumType
from sushi_lang.backend.constants import INT8_BIT_WIDTH, DA_DATA_INDEX
from sushi_lang.backend.llvm_constants import ZERO_I32, ONE_I32, make_i32_const

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_value_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: Type
) -> None:
    """Recursively destroy a value of any type.

    This is the central cleanup mechanism for all Sushi types:
    - Primitives (i8-i64, u8-u64, f32, f64, bool): no-op
    - Strings: no-op (string literals or static data)
    - Dynamic arrays: free data pointer, recursively destroy elements if needed
    - Structs: recursively destroy each field
    - Enums: switch on discriminant tag, destroy variant data
    - Own<T>: free owned pointer, recursively destroy owned value

    Args:
        codegen: The main codegen instance (for accessing types, free, etc.)
        builder: The LLVM IR builder to emit code with
        value_ptr: Pointer to the value to destroy (not the value itself)
        value_type: The Sushi type of the value
    """
    # Primitives and strings: no cleanup needed
    if isinstance(value_type, BuiltinType):
        if value_type in (BuiltinType.STRING, BuiltinType.STDIN, BuiltinType.STDOUT,
                          BuiltinType.STDERR, BuiltinType.FILE):
            # Strings and I/O types don't need cleanup
            return
        # All numeric types and bool: no cleanup
        return

    # Dynamic arrays: free the data pointer
    elif isinstance(value_type, DynamicArrayType):
        _emit_dynamic_array_destructor(codegen, builder, value_ptr, value_type)

    # Structs: recursively destroy each field
    elif isinstance(value_type, StructType):
        _emit_struct_destructor(codegen, builder, value_ptr, value_type)

    # Enums: switch on tag and destroy variant data
    elif isinstance(value_type, EnumType):
        _emit_enum_destructor(codegen, builder, value_ptr, value_type)


def _emit_dynamic_array_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: DynamicArrayType
) -> None:
    """Emit destructor code for a dynamic array.

    Frees the data pointer and recursively destroys elements if needed.
    """
    # Load the dynamic array struct
    data_ptr_ptr = builder.gep(value_ptr, [
        ZERO_I32,
        make_i32_const(DA_DATA_INDEX)
    ], name="array_data_ptr")
    data_ptr = builder.load(data_ptr_ptr, name="array_data")

    # Check if data is not null before freeing
    is_not_null = builder.icmp_unsigned(
        "!=", data_ptr,
        ir.Constant(data_ptr.type, None)
    )

    with builder.if_then(is_not_null):
        # Check if element type needs cleanup
        if needs_cleanup(value_type.base_type):
            # Load array length
            len_ptr = builder.gep(value_ptr, [
                ZERO_I32,
                ZERO_I32  # len is first field
            ], name="array_len_ptr")
            array_len = builder.load(len_ptr, name="array_len")

            # Iterate through array elements and destroy each one
            loop_i = builder.alloca(ZERO_I32.type, name="cleanup_i")
            builder.store(ZERO_I32, loop_i)

            loop_cond_bb = builder.append_basic_block(name="array_cleanup_cond")
            loop_body_bb = builder.append_basic_block(name="array_cleanup_body")
            loop_end_bb = builder.append_basic_block(name="array_cleanup_end")

            builder.branch(loop_cond_bb)

            # Loop condition: i < len
            builder.position_at_end(loop_cond_bb)
            i_val = builder.load(loop_i, name="i_val")
            cond = builder.icmp_unsigned("<", i_val, array_len, name="cleanup_cond")
            builder.cbranch(cond, loop_body_bb, loop_end_bb)

            # Loop body: destroy element[i]
            builder.position_at_end(loop_body_bb)
            i_val = builder.load(loop_i, name="i_val")
            element_ptr = builder.gep(data_ptr, [i_val], name="element_ptr")

            # Recursively destroy this element
            emit_value_destructor(codegen, builder, element_ptr, value_type.base_type)

            # Increment loop counter
            i_next = builder.add(i_val, ONE_I32, name="i_next")
            builder.store(i_next, loop_i)
            builder.branch(loop_cond_bb)

            # After loop, free the array data
            builder.position_at_end(loop_end_bb)

        # Free the array data pointer
        void_ptr = builder.bitcast(data_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
        free_func = codegen.get_free_func()
        builder.call(free_func, [void_ptr])


def _emit_struct_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: StructType
) -> None:
    """Emit destructor code for a struct.

    Handles Own<T> specially, otherwise recursively destroys each field.
    """
    # Check if this is Own<T> which needs special handling
    if value_type.name.startswith("Own<"):
        # Own<T> has a single pointer field - free it and destroy the value
        ptr_field_ptr = builder.gep(value_ptr, [
            ZERO_I32,
            ZERO_I32
        ], name="own_ptr_field")
        owned_ptr = builder.load(ptr_field_ptr, name="owned_ptr")

        # Check if not null
        is_not_null = builder.icmp_unsigned(
            "!=", owned_ptr,
            ir.Constant(owned_ptr.type, None)
        )

        with builder.if_then(is_not_null):
            # Get the owned type (T from Own<T>)
            if value_type.fields:
                owned_type = value_type.fields[0][1]
                # Recursively destroy the owned value
                emit_value_destructor(codegen, builder, owned_ptr, owned_type)

            # Free the pointer itself
            void_ptr = builder.bitcast(owned_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
            free_func = codegen.get_free_func()
            builder.call(free_func, [void_ptr])
    else:
        # Regular struct: recursively destroy each field
        for i, (field_name, field_type) in enumerate(value_type.fields):
            # Check if field needs cleanup
            if needs_cleanup(field_type):
                field_ptr = builder.gep(value_ptr, [
                    ZERO_I32,
                    make_i32_const(i)
                ], name=f"field_{field_name}_ptr")
                emit_value_destructor(codegen, builder, field_ptr, field_type)


def _emit_enum_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: EnumType
) -> None:
    """Emit destructor code for an enum.

    Creates a switch statement to handle cleanup for each variant with associated data.
    """
    # Load discriminant tag (first field of enum struct)
    tag_ptr = builder.gep(value_ptr, [ZERO_I32, ZERO_I32], name="enum_tag_ptr")
    tag = builder.load(tag_ptr, name="enum_tag")

    # Get data field pointer (second field: [N x i8] byte array)
    data_ptr = builder.gep(value_ptr, [ZERO_I32, ONE_I32], name="enum_data_ptr")

    # Create switch statement for each variant
    # We need to check which variants have associated data that needs cleanup
    variants_needing_cleanup = []
    for i, variant in enumerate(value_type.variants):
        if variant.associated_types:
            # Check if any associated type needs cleanup
            if any(needs_cleanup(assoc_type) for assoc_type in variant.associated_types):
                variants_needing_cleanup.append((i, variant))

    if variants_needing_cleanup:
        # Create basic blocks for each variant that needs cleanup
        cleanup_blocks = {}
        for tag_val, variant in variants_needing_cleanup:
            cleanup_blocks[tag_val] = builder.append_basic_block(name=f"cleanup_variant_{variant.name}")

        end_block = builder.append_basic_block(name="enum_cleanup_end")

        # Create switch instruction
        switch = builder.switch(tag, end_block)

        # Add cases for each variant that needs cleanup
        for tag_val, variant in variants_needing_cleanup:
            tag_const = make_i32_const(tag_val)
            switch.add_case(tag_const, cleanup_blocks[tag_val])

        # Emit cleanup code for each variant
        for tag_val, variant in variants_needing_cleanup:
            builder.position_at_end(cleanup_blocks[tag_val])

            # Calculate offset into data array for each associated value
            offset = 0
            for j, assoc_type in enumerate(variant.associated_types):
                if needs_cleanup(assoc_type):
                    # Get pointer to this field within the data array
                    # Cast the [N x i8]* to i8* first
                    data_i8_ptr = builder.bitcast(data_ptr, ir.PointerType(ir.IntType(8)), name=f"data_i8_ptr_{j}")

                    # Add offset to get to this field
                    offset_const = make_i32_const(offset)
                    field_i8_ptr = builder.gep(data_i8_ptr, [offset_const], name=f"field_{j}_i8_ptr")

                    # Cast to the actual field type pointer
                    field_llvm_type = codegen.types.ll_type(assoc_type)
                    field_ptr = builder.bitcast(field_i8_ptr, ir.PointerType(field_llvm_type), name=f"field_{j}_ptr")

                    # Recursively destroy this field
                    emit_value_destructor(codegen, builder, field_ptr, assoc_type)

                # Update offset for next field
                offset += codegen.types.get_type_size_bytes(assoc_type)

            builder.branch(end_block)

        # Position at end block for continuation
        builder.position_at_end(end_block)


def needs_cleanup(value_type: Type) -> bool:
    """Check if a type needs cleanup (has resources to free).

    Args:
        value_type: The type to check

    Returns:
        True if the type needs cleanup, False otherwise
    """
    if isinstance(value_type, BuiltinType):
        return False  # Primitives don't need cleanup
    elif isinstance(value_type, DynamicArrayType):
        return True  # Dynamic arrays need cleanup
    elif isinstance(value_type, StructType):
        # Structs need cleanup if any field needs cleanup
        return any(needs_cleanup(field_type) for _, field_type in value_type.fields)
    elif isinstance(value_type, EnumType):
        # Enums need cleanup if any variant has associated data that needs cleanup
        for variant in value_type.variants:
            if variant.associated_types:
                if any(needs_cleanup(assoc_type) for assoc_type in variant.associated_types):
                    return True
        return False
    return False
