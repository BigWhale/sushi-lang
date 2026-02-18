"""
HashMap<K, V> iterator method implementations.

This module contains the keys() and values() methods for creating iterators from HashMap<K, V>.
"""

from typing import Any, TYPE_CHECKING
import llvmlite.ir as ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType
from sushi_lang.backend import gep_utils
from ..types import extract_key_value_types, get_user_entry_type, ensure_entry_type_in_struct_table, ENTRY_OCCUPIED
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_hashmap_keys(
    codegen: Any,
    call: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.keys() -> Iterator<K>

    Creates an iterator that yields keys from the hashmap.

    Iterator structure: {i32 current_index, i32 capacity, Entry<K,V>* buckets_ptr}
    - current_index: starts at 0
    - capacity: number of buckets in the hashmap
    - buckets_ptr: pointer to the buckets array

    The iterator skips Empty and Tombstone entries, only yielding Occupied keys.

    Args:
        codegen: LLVM codegen instance.
        call: The method call AST node.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Iterator<K> struct value.

    Raises:
        ValueError: If keys() is called with arguments.
    """
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="keys", expected=0, got=len(call.args))

    # Extract key and value types from HashMap<K, V>
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get HashMap fields:
    # struct HashMap<K, V>:
    #     Entry<K, V>[] buckets  (field 0)
    #     i32 size               (field 1)
    #     i32 capacity           (field 2)
    #     i32 tombstones         (field 3)

    # Get buckets array (field 0)
    buckets_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 0, "buckets_ptr")

    # Get capacity (field 2)
    capacity_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 2, "capacity_ptr")
    capacity = codegen.builder.load(capacity_ptr, name="capacity")

    # Get buckets data pointer from the dynamic array
    # buckets array structure: {i32 len, i32 cap, Entry* data}
    buckets_data_ptr = gep_utils.gep_struct_field(codegen, buckets_ptr, 2, "buckets_data_ptr")
    buckets_data = codegen.builder.load(buckets_data_ptr, name="buckets_data")

    # Create iterator struct: {i32 current_index, i32 capacity, Entry<K,V>* buckets_ptr}
    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=key_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    # Allocate and initialize iterator struct
    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="hashmap_keys_iterator")

    # Set current_index = 0
    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    # Set capacity with HashMap keys marker
    # We encode: capacity | 0x80000000 (bit 31 = HashMap flag, bit 30 = 0 for keys)
    # This allows up to 2^31-1 capacity while preserving the marker
    capacity_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "capacity_ptr")
    hashmap_flag = ir.Constant(codegen.types.i32, 0x80000000)  # Bit 31 set = HashMap iterator
    marked_capacity = codegen.builder.or_(capacity, hashmap_flag, name="hashmap_keys_capacity")
    codegen.builder.store(marked_capacity, capacity_ptr_out)

    # Set buckets_ptr (cast Entry<K,V>* to K* for type safety)
    # The foreach loop will detect the -2 marker and handle HashMap iteration specially
    buckets_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "buckets_ptr_out")
    # Store the buckets pointer as-is (Entry<K,V>*), cast to K* for type compatibility
    key_type_llvm = codegen.types.ll_type(key_type)
    buckets_as_keys = codegen.builder.bitcast(buckets_data, ir.PointerType(key_type_llvm), name="buckets_as_keys")
    codegen.builder.store(buckets_as_keys, buckets_ptr_out)

    # Load and return the iterator struct
    return codegen.builder.load(iterator_slot, name="keys_iterator")


def emit_hashmap_values(
    codegen: Any,
    call: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.values() -> Iterator<V>

    Creates an iterator that yields values from the hashmap.

    Iterator structure: {i32 current_index, i32 capacity, Entry<K,V>* buckets_ptr}
    - current_index: starts at 0
    - capacity: number of buckets in the hashmap
    - buckets_ptr: pointer to the buckets array

    The iterator skips Empty and Tombstone entries, only yielding Occupied values.

    Args:
        codegen: LLVM codegen instance.
        call: The method call AST node.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Iterator<V> struct value.

    Raises:
        ValueError: If values() is called with arguments.
    """
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="values", expected=0, got=len(call.args))

    # Extract key and value types from HashMap<K, V>
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get HashMap fields (same as keys())
    buckets_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 0, "buckets_ptr")
    capacity_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 2, "capacity_ptr")
    capacity = codegen.builder.load(capacity_ptr, name="capacity")

    # Get buckets data pointer
    buckets_data_ptr = gep_utils.gep_struct_field(codegen, buckets_ptr, 2, "buckets_data_ptr")
    buckets_data = codegen.builder.load(buckets_data_ptr, name="buckets_data")

    # Create iterator struct: {i32 current_index, i32 capacity, Entry<K,V>* buckets_ptr}
    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=value_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    # Allocate and initialize iterator struct
    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="hashmap_values_iterator")

    # Set current_index = 0
    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    # Set capacity with HashMap values marker
    # We encode: capacity | 0xC0000000 (bit 31 = HashMap flag, bit 30 = 1 for values)
    capacity_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "capacity_ptr")
    hashmap_values_flag = ir.Constant(codegen.types.i32, 0xC0000000)  # Bits 31+30 set = HashMap values iterator
    marked_capacity = codegen.builder.or_(capacity, hashmap_values_flag, name="hashmap_values_capacity")
    codegen.builder.store(marked_capacity, capacity_ptr_out)

    # Set buckets_ptr (cast Entry<K,V>* to V* for type safety)
    buckets_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "buckets_ptr_out")
    value_type_llvm = codegen.types.ll_type(value_type)
    buckets_as_values = codegen.builder.bitcast(buckets_data, ir.PointerType(value_type_llvm), name="buckets_as_values")
    codegen.builder.store(buckets_as_values, buckets_ptr_out)

    # Load and return the iterator struct
    return codegen.builder.load(iterator_slot, name="values_iterator")


def emit_hashmap_entries(
    codegen: Any,
    call: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.entries() -> Iterator<Entry<K, V>>

    Creates an iterator that yields Entry<K, V> structs (key + value) from the hashmap.

    The iterator uses the same high-bit marker encoding as keys()/values():
    - capacity | 0xE0000000 (bits 31+30+29 set = HashMap entries iterator)

    The data pointer stores Entry<K,V>* (internal 3-field) cast to user Entry<K,V>*
    (2-field). The foreach loop reconstructs the 2-field entry from the 3-field one.

    Args:
        codegen: LLVM codegen instance.
        call: The method call AST node.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Iterator<Entry<K, V>> struct value.
    """
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="entries", expected=0, got=len(call.args))

    # Extract key and value types from HashMap<K, V>
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Ensure Entry<K, V> struct type is registered
    entry_struct_type = ensure_entry_type_in_struct_table(codegen.struct_table, key_type, value_type)

    # Get HashMap fields
    buckets_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 0, "buckets_ptr")
    capacity_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 2, "capacity_ptr")
    capacity = codegen.builder.load(capacity_ptr, name="capacity")

    # Get buckets data pointer
    buckets_data_ptr = gep_utils.gep_struct_field(codegen, buckets_ptr, 2, "buckets_data_ptr")
    buckets_data = codegen.builder.load(buckets_data_ptr, name="buckets_data")

    # Create iterator struct: {i32 current_index, i32 capacity, Entry<K,V>* buckets_ptr}
    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=entry_struct_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    # Allocate and initialize iterator struct
    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="hashmap_entries_iterator")

    # Set current_index = 0
    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    # Set capacity with HashMap entries marker
    # We encode: capacity | 0xE0000000 (bits 31+30+29 set = HashMap entries iterator)
    capacity_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "capacity_ptr")
    hashmap_entries_flag = ir.Constant(codegen.types.i32, 0xE0000000)
    marked_capacity = codegen.builder.or_(capacity, hashmap_entries_flag, name="hashmap_entries_capacity")
    codegen.builder.store(marked_capacity, capacity_ptr_out)

    # Set buckets_ptr (cast internal Entry<K,V,state>* to user Entry<K,V>* for type compat)
    buckets_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "buckets_ptr_out")
    user_entry_llvm = get_user_entry_type(codegen, key_type, value_type)
    buckets_as_entries = codegen.builder.bitcast(
        buckets_data, ir.PointerType(user_entry_llvm), name="buckets_as_entries"
    )
    codegen.builder.store(buckets_as_entries, buckets_ptr_out)

    # Load and return the iterator struct
    return codegen.builder.load(iterator_slot, name="entries_iterator")
