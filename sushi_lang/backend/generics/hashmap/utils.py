"""
Utility functions for HashMap<K, V> implementation.

This module provides helper functions for key equality checking and entry insertion,
which are shared across multiple HashMap methods.
"""

from typing import Any
from sushi_lang.semantics.typesys import Type, StructType, EnumType, BuiltinType, ArrayType, DynamicArrayType
import llvmlite.ir as ir
from .types import ENTRY_OCCUPIED
from sushi_lang.backend import enum_utils
from sushi_lang.backend.constants.llvm_values import ZERO_I32, TRUE_I1, make_i32_const
from sushi_lang.backend.constants import ENTRY_KEY_INDICES, ENTRY_VALUE_INDICES, ENTRY_STATE_INDICES


def emit_key_equality_check(codegen: Any, key_type: Type, key1: ir.Value, key2: ir.Value) -> ir.Value:
    """Emit LLVM IR to check if two keys are equal.

    Supports:
    - Primitive types (i32, bool, f64, etc.)
    - Strings (strcmp)
    - Structs (field-by-field comparison)
    - Enums (tag comparison, then data comparison if tags match)
    - Fixed arrays (element-by-element comparison)
    - Dynamic arrays (length check, then element comparison)

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

    # An unresolved Result<T, E> annotation (GenericTypeRef) - intern it to its EnumType
    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(key_type, GenericTypeRef) and key_type.base_name == "Result":
        # Convert GenericTypeRef("Result", [T, E]) to Result enum
        if len(key_type.type_args) >= 2:
            from sushi_lang.semantics.generics.results import ensure_result_type_in_table
            ok_type = key_type.type_args[0]
            err_type = key_type.type_args[1]
            result_enum = ensure_result_type_in_table(codegen.enum_table, ok_type, err_type, struct_table=codegen.struct_table.by_name)
            if result_enum is not None:
                key_type = result_enum

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
            from sushi_lang.sushi_stdlib.src.collections.strings_inline import emit_strcmp_intrinsic_inline
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

    # For fixed array types, compare element-by-element
    elif isinstance(key_type, ArrayType):
        return emit_fixed_array_equality(codegen, key_type, key1, key2)

    # For dynamic array types, compare length then elements
    elif isinstance(key_type, DynamicArrayType):
        return emit_dynamic_array_equality(codegen, key_type, key1, key2)

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

    # For enums with data, compare the matching variant's payload. The data field
    # is a raw [N x i8] union, so its LLVM type carries no field structure; switch
    # on the (equal) tag and unpack each data-carrying variant's fields with their
    # real semantic types, then compare field-by-field.
    i1_ty = ir.IntType(1)
    data_eq_slot = builder.alloca(i1_ty, name="enum_data_eq")
    builder.store(TRUE_I1, data_eq_slot)

    enum_llvm_type = codegen.types.get_enum_type(enum_type)
    enum1_ptr = builder.alloca(enum_llvm_type, name="enum1_tmp")
    enum2_ptr = builder.alloca(enum_llvm_type, name="enum2_tmp")
    builder.store(enum1, enum1_ptr)
    builder.store(enum2, enum2_ptr)
    data1_ptr = enum_utils.get_data_ptr(codegen, enum1_ptr, name="enum1_data")
    data2_ptr = enum_utils.get_data_ptr(codegen, enum2_ptr, name="enum2_data")

    switch_end = builder.append_basic_block("enum_eq_end")
    # Default (unit variants): tag equality is sufficient, data stays equal.
    switch_instr = builder.switch(tag1, switch_end)
    for variant_index, variant in enumerate(enum_type.variants):
        if not variant.associated_types:
            continue
        case_block = builder.append_basic_block(f"enum_eq_v{variant_index}")
        switch_instr.add_case(make_i32_const(variant_index), case_block)
        builder.position_at_end(case_block)

        field_types = list(variant.associated_types)
        vals1 = enum_utils.unpack_all_variant_fields(codegen, data1_ptr, field_types, "k1")
        vals2 = enum_utils.unpack_all_variant_fields(codegen, data2_ptr, field_types, "k2")

        variant_eq = TRUE_I1
        for field_type, v1, v2 in zip(field_types, vals1, vals2):
            field_eq = emit_key_equality_check(codegen, field_type, v1, v2)
            variant_eq = builder.and_(variant_eq, field_eq, name="variant_field_eq")
        builder.store(variant_eq, data_eq_slot)
        builder.branch(switch_end)

    builder.position_at_end(switch_end)
    data_equal = builder.load(data_eq_slot, name="enum_data_equal")

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

    # Set key
    key_ptr = builder.gep(entry_ptr, ENTRY_KEY_INDICES, name="entry_key_ptr")
    builder.store(key, key_ptr)

    # Set value
    value_ptr = builder.gep(entry_ptr, ENTRY_VALUE_INDICES, name="entry_value_ptr")
    builder.store(value, value_ptr)

    # Set state = OCCUPIED
    state_ptr = builder.gep(entry_ptr, ENTRY_STATE_INDICES, name="entry_state_ptr")
    builder.store(ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), state_ptr)


def emit_fixed_array_equality(codegen: Any, array_type: ArrayType, arr1: ir.Value, arr2: ir.Value) -> ir.Value:
    """Emit element-by-element equality check for fixed arrays.

    Uses gep_fixed_array_element() utility for correct LLVM GEP construction.

    Args:
        codegen: LLVM codegen instance.
        array_type: The fixed array type.
        arr1: First array value.
        arr2: Second array value.

    Returns:
        i1 boolean result of arr1 == arr2.
    """
    from sushi_lang.backend import gep_utils

    builder = codegen.builder
    element_type = array_type.base_type
    size = array_type.size

    # Fixed arrays are value types in LLVM. Store to alloca to get pointers for GEP
    arr1_llvm_type = codegen.types.ll_type(array_type)
    arr1_ptr = builder.alloca(arr1_llvm_type, name="arr1_ptr")
    builder.store(arr1, arr1_ptr)
    arr2_ptr = builder.alloca(arr1_llvm_type, name="arr2_ptr")
    builder.store(arr2, arr2_ptr)

    # Start with result = true
    result = builder.alloca(codegen.types.i1, name="arrays_equal")
    builder.store(TRUE_I1, result)

    # Loop through elements
    i_ptr = builder.alloca(codegen.types.i32, name="i_ptr")
    builder.store(ZERO_I32, i_ptr)

    loop_cond_bb = builder.append_basic_block(name="array_eq_loop_cond")
    loop_body_bb = builder.append_basic_block(name="array_eq_loop_body")
    loop_end_bb = builder.append_basic_block(name="array_eq_loop_end")

    builder.branch(loop_cond_bb)

    # Loop condition: i < size
    builder.position_at_end(loop_cond_bb)
    i_val = builder.load(i_ptr, name="i_val")
    size_const = ir.Constant(codegen.types.i32, size)
    cond = builder.icmp_signed("<", i_val, size_const, name="loop_cond")
    builder.cbranch(cond, loop_body_bb, loop_end_bb)

    # Loop body: compare elements
    builder.position_at_end(loop_body_bb)
    # Get arr1[i] using gep_fixed_array_element utility
    elem1_ptr = gep_utils.gep_fixed_array_element(codegen, arr1_ptr, i_val, "elem1_ptr")
    elem1 = builder.load(elem1_ptr, name="elem1")
    # Get arr2[i] using gep_fixed_array_element utility
    elem2_ptr = gep_utils.gep_fixed_array_element(codegen, arr2_ptr, i_val, "elem2_ptr")
    elem2 = builder.load(elem2_ptr, name="elem2")
    # Compare elements (recursively for complex types)
    elem_equal = emit_key_equality_check(codegen, element_type, elem1, elem2)
    # If not equal, set result = false
    result_val = builder.load(result, name="result_val")
    new_result = builder.and_(result_val, elem_equal, name="new_result")
    builder.store(new_result, result)
    # Increment i
    i_next = builder.add(i_val, ir.Constant(codegen.types.i32, 1), name="i_next")
    builder.store(i_next, i_ptr)
    builder.branch(loop_cond_bb)

    # After loop
    builder.position_at_end(loop_end_bb)
    return builder.load(result, name="arrays_equal")


def emit_dynamic_array_equality(codegen: Any, array_type: DynamicArrayType, arr1: ir.Value, arr2: ir.Value) -> ir.Value:
    """Emit length check + element-by-element equality for dynamic arrays.

    Dynamic arrays are passed as struct VALUES: {i32 len, i32 cap, T* data}.
    Uses extractvalue to access struct fields directly (no GEP needed).

    Args:
        codegen: LLVM codegen instance.
        array_type: The dynamic array type.
        arr1: First array value (struct {i32, i32, T*}).
        arr2: Second array value (struct {i32, i32, T*}).

    Returns:
        i1 boolean result of arr1 == arr2.
    """
    from sushi_lang.backend import gep_utils

    builder = codegen.builder
    element_type = array_type.base_type

    # Extract lengths (field 0) using extractvalue for struct values
    len1 = builder.extract_value(arr1, 0, name="len1")
    len2 = builder.extract_value(arr2, 0, name="len2")

    # Check if lengths are equal
    lens_equal = builder.icmp_signed("==", len1, len2, name="lens_equal")

    # Short-circuit if lengths differ
    check_elements_bb = builder.append_basic_block(name="check_array_elements")
    done_bb = builder.append_basic_block(name="arrays_eq_done")

    builder.cbranch(lens_equal, check_elements_bb, done_bb)

    # Check elements (if lengths match)
    builder.position_at_end(check_elements_bb)
    # Extract data pointers (field 2) using extractvalue
    data1_ptr = builder.extract_value(arr1, 2, name="data1_ptr")
    data2_ptr = builder.extract_value(arr2, 2, name="data2_ptr")

    # Loop through elements
    result = builder.alloca(codegen.types.i1, name="elements_equal")
    builder.store(TRUE_I1, result)

    i_ptr = builder.alloca(codegen.types.i32, name="i_ptr")
    builder.store(ZERO_I32, i_ptr)

    loop_cond_bb = builder.append_basic_block(name="dyn_array_loop_cond")
    loop_body_bb = builder.append_basic_block(name="dyn_array_loop_body")
    loop_end_bb = builder.append_basic_block(name="dyn_array_loop_end")

    builder.branch(loop_cond_bb)

    # Loop condition: i < len
    builder.position_at_end(loop_cond_bb)
    i_val = builder.load(i_ptr, name="i_val")
    cond = builder.icmp_signed("<", i_val, len1, name="loop_cond")
    builder.cbranch(cond, loop_body_bb, loop_end_bb)

    # Loop body
    builder.position_at_end(loop_body_bb)
    # Use gep_array_element for data pointer indexing
    elem1_ptr = gep_utils.gep_array_element(codegen, data1_ptr, i_val, "elem1_ptr")
    elem1 = builder.load(elem1_ptr, name="elem1")
    elem2_ptr = gep_utils.gep_array_element(codegen, data2_ptr, i_val, "elem2_ptr")
    elem2 = builder.load(elem2_ptr, name="elem2")
    elem_equal = emit_key_equality_check(codegen, element_type, elem1, elem2)
    result_val = builder.load(result, name="result_val")
    new_result = builder.and_(result_val, elem_equal, name="new_result")
    builder.store(new_result, result)
    i_next = builder.add(i_val, ir.Constant(codegen.types.i32, 1), name="i_next")
    builder.store(i_next, i_ptr)
    builder.branch(loop_cond_bb)

    # After element loop
    builder.position_at_end(loop_end_bb)
    elements_equal = builder.load(result, name="elements_equal")
    builder.branch(done_bb)

    # Final result
    builder.position_at_end(done_bb)
    result_phi = builder.phi(codegen.types.i1, name="arrays_equal")
    result_phi.add_incoming(ir.Constant(codegen.types.i1, 0), lens_equal.parent)  # False if lengths differ
    result_phi.add_incoming(elements_equal, loop_end_bb)  # Result from element comparison
    return result_phi


def emit_entry_state_check(codegen: Any, entry_ptr: ir.Value, state: int, name: str) -> ir.Value:
    """Emit `entry.state == <state>` for one of the ENTRY_* constants."""
    builder = codegen.builder
    state_ptr = builder.gep(entry_ptr, ENTRY_STATE_INDICES, name="state_ptr")
    loaded = builder.load(state_ptr, name="state")
    return builder.icmp_unsigned("==", loaded, ir.Constant(codegen.types.i8, state), name=name)


def emit_destroy_all_entries(
    codegen: Any,
    buckets_data: ir.Value,
    capacity: ir.Value,
    key_type: Type,
    value_type: Type,
    *,
    null_guard: bool = False,
) -> None:
    """Recursively destroy the key and value of every occupied bucket.

    Args:
        codegen: LLVM codegen instance.
        buckets_data: Pointer to the bucket storage (Entry<K, V>*).
        capacity: Number of buckets to walk.
        key_type: The key type K.
        value_type: The value type V.
        null_guard: Skip the walk entirely if the bucket storage is null.
    """
    from sushi_lang.backend.destructors import emit_value_destructor
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    builder = codegen.builder

    def occupied(entry_ptr: ir.Value, _index: ir.Value) -> ir.Value:
        return emit_entry_state_check(codegen, entry_ptr, ENTRY_OCCUPIED, "is_occupied")

    def destroy(entry_ptr: ir.Value, _index: ir.Value) -> None:
        key_ptr = builder.gep(entry_ptr, ENTRY_KEY_INDICES, name="key_ptr")
        value_ptr = builder.gep(entry_ptr, ENTRY_VALUE_INDICES, name="value_ptr")
        emit_value_destructor(codegen, builder, key_ptr, key_type)
        emit_value_destructor(codegen, builder, value_ptr, value_type)

    emit_container_walk(
        codegen, buckets_data, capacity, destroy,
        should_visit=occupied, null_guard=null_guard, prefix="destroy",
    )


def emit_init_buckets_empty(codegen: Any, buckets_data: ir.Value, capacity: ir.Value) -> None:
    """Mark every bucket EMPTY. Fresh malloc'd storage holds garbage, not zeroes."""
    from sushi_lang.backend.generics.container_walk import emit_container_walk
    from .types import ENTRY_EMPTY

    builder = codegen.builder

    def set_empty(entry_ptr: ir.Value, _index: ir.Value) -> None:
        state_ptr = builder.gep(entry_ptr, ENTRY_STATE_INDICES, name="state_ptr")
        builder.store(ir.Constant(codegen.types.i8, ENTRY_EMPTY), state_ptr)

    emit_container_walk(codegen, buckets_data, capacity, set_empty, prefix="init")
