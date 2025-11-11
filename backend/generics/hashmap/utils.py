"""
Utility functions for HashMap<K, V> implementation.

This module provides helper functions for key equality checking and entry insertion,
which are shared across multiple HashMap methods.
"""

from typing import Any
from semantics.typesys import Type, StructType, EnumType, BuiltinType
import llvmlite.ir as ir
from .types import ENTRY_OCCUPIED
from backend import enum_utils
from backend.llvm_constants import ZERO_I32, TRUE_I1, make_i32_const


def emit_key_equality_check(codegen: Any, key_type: Type, key1: ir.Value, key2: ir.Value) -> ir.Value:
    """Emit LLVM IR to check if two keys are equal.

    Supports:
    - Primitive types (i32, bool, f64, etc.)
    - Strings (strcmp)
    - Structs (field-by-field comparison)
    - Enums (tag comparison, then data comparison if tags match)

    Args:
        codegen: LLVM codegen instance.
        key_type: The type of the keys.
        key1: First key value.
        key2: Second key value.

    Returns:
        i1 boolean result of key1 == key2.
    """
    builder = codegen.builder
    zero_i32 = ZERO_I32

    # For primitive types, use direct comparison
    if key_type in (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64):
        return builder.icmp_signed("==", key1, key2, name="keys_equal")
    elif key_type in (BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64):
        return builder.icmp_unsigned("==", key1, key2, name="keys_equal")
    elif key_type == BuiltinType.BOOL:
        return builder.icmp_unsigned("==", key1, key2, name="keys_equal")
    elif key_type in (BuiltinType.F32, BuiltinType.F64):
        return builder.fcmp_ordered("==", key1, key2, name="keys_equal")
    elif key_type == BuiltinType.STRING:
        # String comparison: use llvm_strcmp intrinsic with fat pointers
        # Emit inline if not already present (for HashMap<string, V>)
        if "llvm_strcmp" not in codegen.module.globals:
            from stdlib.src.collections.strings_inline import emit_strcmp_intrinsic_inline
            strcmp_fn = emit_strcmp_intrinsic_inline(codegen.module)
        else:
            strcmp_fn = codegen.module.globals["llvm_strcmp"]
        # Call llvm_strcmp with full fat pointer structs
        result = builder.call(strcmp_fn, [key1, key2], name="strcmp_result")
        return builder.icmp_signed("==", result, zero_i32, name="keys_equal")

    # For struct types, compare all fields
    elif isinstance(key_type, StructType):
        return emit_struct_equality(codegen, key_type, key1, key2)

    # For enum types, compare tag first, then data
    elif isinstance(key_type, EnumType):
        return emit_enum_equality(codegen, key_type, key1, key2)

    else:
        raise NotImplementedError(f"Equality check not yet implemented for key type: {key_type}")


def emit_struct_equality(codegen: Any, struct_type: StructType, struct1: ir.Value, struct2: ir.Value) -> ir.Value:
    """Emit field-by-field equality check for structs.

    Args:
        codegen: LLVM codegen instance.
        struct_type: The struct type.
        struct1: First struct value (by value).
        struct2: Second struct value (by value).

    Returns:
        i1 boolean result (true if all fields are equal).
    """
    builder = codegen.builder
    true_i1 = TRUE_I1

    # Start with result = true
    result = true_i1

    # Compare each field
    for field_idx, (field_name, field_type) in enumerate(struct_type.fields):
        # Extract field from both structs (by value, use extractvalue)
        field1 = builder.extract_value(struct1, field_idx, name=f"{field_name}1")
        field2 = builder.extract_value(struct2, field_idx, name=f"{field_name}2")

        # Recursively compare fields (handles nested structs/enums)
        field_equal = emit_key_equality_check(codegen, field_type, field1, field2)

        # AND with accumulated result
        result = builder.and_(result, field_equal, name=f"result_with_{field_name}")

    return result


def emit_enum_equality(codegen: Any, enum_type: EnumType, enum1: ir.Value, enum2: ir.Value) -> ir.Value:
    """Emit equality check for enums (tag comparison, then data if tags match).

    Args:
        codegen: LLVM codegen instance.
        enum_type: The enum type.
        enum1: First enum value (by value).
        enum2: Second enum value (by value).

    Returns:
        i1 boolean result (true if tags and data are equal).
    """
    builder = codegen.builder

    # Enum layout: {i32 tag, <data union>}
    # Extract tags (field 0) using extractvalue
    tag1 = enum_utils.extract_enum_tag(codegen, enum1, name="tag1")
    tag2 = enum_utils.extract_enum_tag(codegen, enum2, name="tag2")

    # Compare tags
    tags_equal = enum_utils.compare_enum_tags(codegen, tag1, tag2, signed=True, name="tags_equal")

    # If enum has no data (all unit variants), tags being equal is sufficient
    if not any(variant.associated_types for variant in enum_type.variants):
        return tags_equal

    # For enums with data, we need to compare data fields when tags match
    # Extract data (field 1) using extractvalue
    data1 = enum_utils.extract_enum_data(codegen, enum1, name="data1")
    data2 = enum_utils.extract_enum_data(codegen, enum2, name="data2")

    # Get the LLVM type of the enum to find data type
    enum_llvm_type = codegen.types.get_enum_type(enum_type)
    if len(enum_llvm_type.elements) > 1:
        data_llvm_type = enum_llvm_type.elements[1]

        # Compare based on data type
        zero_i32 = ZERO_I32

        if isinstance(data_llvm_type, ir.IntType):
            data_equal = builder.icmp_signed("==", data1, data2, name="data_equal")
        elif isinstance(data_llvm_type, ir.DoubleType) or isinstance(data_llvm_type, ir.FloatType):
            data_equal = builder.fcmp_ordered("==", data1, data2, name="data_equal")
        elif isinstance(data_llvm_type, ir.LiteralStructType):
            # For struct types (including fat pointer strings {i8*, i32})
            # Check if this is a string type by looking at the struct elements
            if (len(data_llvm_type.elements) == 2 and
                isinstance(data_llvm_type.elements[0], ir.PointerType) and
                data_llvm_type.elements[0].pointee == ir.IntType(8) and
                data_llvm_type.elements[1] == ir.IntType(32)):
                # This is a fat pointer string {i8*, i32}
                # Use llvm_strcmp intrinsic (emit inline if needed)
                if "llvm_strcmp" not in codegen.module.globals:
                    from stdlib.src.collections.strings_inline import emit_strcmp_intrinsic_inline
                    strcmp_fn = emit_strcmp_intrinsic_inline(codegen.module)
                else:
                    strcmp_fn = codegen.module.globals["llvm_strcmp"]
                # Call llvm_strcmp with full fat pointer structs
                cmp_result = builder.call(strcmp_fn, [data1, data2], name="strcmp_result")
                data_equal = builder.icmp_signed("==", cmp_result, zero_i32, name="data_equal")
            else:
                # For other struct types, assume equal if tags match (conservative)
                data_equal = TRUE_I1
        elif isinstance(data_llvm_type, ir.PointerType):
            # For pointers (legacy case, shouldn't happen with fat pointers)
            # Generic pointer comparison
            data_equal = builder.icmp_signed("==", data1, data2, name="data_equal")
        else:
            # For complex types (structs), recursively compare
            # This handles nested enums with struct data
            data_equal = TRUE_I1  # Conservative: assume equal if tags match
    else:
        # No data field, tags being equal is enough
        data_equal = TRUE_I1

    # Combine: tags must be equal AND data must be equal
    result = builder.and_(tags_equal, data_equal, name="enum_equal")
    return result


def emit_insert_entry(codegen: Any, entry_ptr: ir.Value, key: ir.Value, value: ir.Value, entry_type: ir.Type) -> None:
    """Emit LLVM IR to insert a key-value pair into an entry slot.

    Args:
        codegen: LLVM codegen instance.
        entry_ptr: Pointer to the entry slot.
        key: Key value to insert.
        value: Value to insert.
        entry_type: LLVM type of the Entry struct.
    """
    builder = codegen.builder
    zero_i32 = ZERO_I32
    one_i32 = make_i32_const(1)
    two_i32 = make_i32_const(2)

    # Set key
    key_ptr = builder.gep(entry_ptr, [zero_i32, zero_i32], name="entry_key_ptr")
    builder.store(key, key_ptr)

    # Set value
    value_ptr = builder.gep(entry_ptr, [zero_i32, one_i32], name="entry_value_ptr")
    builder.store(value, value_ptr)

    # Set state = OCCUPIED
    state_ptr = builder.gep(entry_ptr, [zero_i32, two_i32], name="entry_state_ptr")
    builder.store(ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), state_ptr)
