"""HashMap<K, V> iterator method implementations."""

from typing import Any, TYPE_CHECKING
import llvmlite.ir as ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType
from sushi_lang.backend import gep_utils
from ..types import get_user_entry_type
from sushi_lang.semantics.generics.hashmap import extract_key_value_types, ensure_entry_type_in_struct_table
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    pass


def emit_hashmap_keys(
    codegen: Any,
    call: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.keys() -> Iterator<K>"""
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="keys", expected=0, got=len(call.args))

    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get HashMap fields:
    # struct HashMap<K, V>:
    #     Entry<K, V>[] buckets  (field 0)
    #     i32 size               (field 1)
    #     i32 capacity           (field 2)
    #     i32 tombstones         (field 3)

    buckets_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 0, "buckets_ptr")

    capacity_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 2, "capacity_ptr")
    capacity = codegen.builder.load(capacity_ptr, name="capacity")

    buckets_data_ptr = gep_utils.gep_struct_field(codegen, buckets_ptr, 2, "buckets_data_ptr")
    buckets_data = codegen.builder.load(buckets_data_ptr, name="buckets_data")

    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=key_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="hashmap_keys_iterator")

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    # Set capacity with HashMap keys marker
    # We encode: capacity | 0x80000000 (bit 31 = HashMap flag, bit 30 = 0 for keys)
    # This allows up to 2^31-1 capacity while preserving the marker
    capacity_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "capacity_ptr")
    hashmap_flag = ir.Constant(codegen.types.i32, 0x80000000)  # Bit 31 set = HashMap iterator
    marked_capacity = codegen.builder.or_(capacity, hashmap_flag, name="hashmap_keys_capacity")
    codegen.builder.store(marked_capacity, capacity_ptr_out)

    buckets_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "buckets_ptr_out")
    key_type_llvm = codegen.types.ll_type(key_type)
    buckets_as_keys = codegen.builder.bitcast(buckets_data, ir.PointerType(key_type_llvm), name="buckets_as_keys")
    codegen.builder.store(buckets_as_keys, buckets_ptr_out)

    return codegen.builder.load(iterator_slot, name="keys_iterator")


def emit_hashmap_values(
    codegen: Any,
    call: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.values() -> Iterator<V>"""
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="values", expected=0, got=len(call.args))

    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    buckets_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 0, "buckets_ptr")
    capacity_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 2, "capacity_ptr")
    capacity = codegen.builder.load(capacity_ptr, name="capacity")

    buckets_data_ptr = gep_utils.gep_struct_field(codegen, buckets_ptr, 2, "buckets_data_ptr")
    buckets_data = codegen.builder.load(buckets_data_ptr, name="buckets_data")

    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=value_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="hashmap_values_iterator")

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    capacity_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "capacity_ptr")
    hashmap_values_flag = ir.Constant(codegen.types.i32, 0xC0000000)  # Bits 31+30 set = HashMap values iterator
    marked_capacity = codegen.builder.or_(capacity, hashmap_values_flag, name="hashmap_values_capacity")
    codegen.builder.store(marked_capacity, capacity_ptr_out)

    buckets_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "buckets_ptr_out")
    value_type_llvm = codegen.types.ll_type(value_type)
    buckets_as_values = codegen.builder.bitcast(buckets_data, ir.PointerType(value_type_llvm), name="buckets_as_values")
    codegen.builder.store(buckets_as_values, buckets_ptr_out)

    return codegen.builder.load(iterator_slot, name="values_iterator")


def emit_hashmap_entries(
    codegen: Any,
    call: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.entries() -> Iterator<Entry<K, V>>"""
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="entries", expected=0, got=len(call.args))

    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    entry_struct_type = ensure_entry_type_in_struct_table(codegen.struct_table, key_type, value_type)

    buckets_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 0, "buckets_ptr")
    capacity_ptr = gep_utils.gep_struct_field(codegen, hashmap_value, 2, "capacity_ptr")
    capacity = codegen.builder.load(capacity_ptr, name="capacity")

    buckets_data_ptr = gep_utils.gep_struct_field(codegen, buckets_ptr, 2, "buckets_data_ptr")
    buckets_data = codegen.builder.load(buckets_data_ptr, name="buckets_data")

    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=entry_struct_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="hashmap_entries_iterator")

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    capacity_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "capacity_ptr")
    hashmap_entries_flag = ir.Constant(codegen.types.i32, 0xE0000000)
    marked_capacity = codegen.builder.or_(capacity, hashmap_entries_flag, name="hashmap_entries_capacity")
    codegen.builder.store(marked_capacity, capacity_ptr_out)

    buckets_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "buckets_ptr_out")
    user_entry_llvm = get_user_entry_type(codegen, key_type, value_type)
    buckets_as_entries = codegen.builder.bitcast(
        buckets_data, ir.PointerType(user_entry_llvm), name="buckets_as_entries"
    )
    codegen.builder.store(buckets_as_entries, buckets_ptr_out)

    return codegen.builder.load(iterator_slot, name="entries_iterator")
