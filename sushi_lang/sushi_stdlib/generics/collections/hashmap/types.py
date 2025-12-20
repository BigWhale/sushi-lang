"""
LLVM type helpers and constants for HashMap<K, V>.

This module provides functions to create LLVM struct types for HashMap<K, V>
and Entry<K, V>, along with constants for entry states and prime capacity tables.
"""

from typing import Any, Optional
from sushi_lang.semantics.typesys import Type, StructType, BuiltinType, EnumType, ArrayType, DynamicArrayType
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
import re


# ==============================================================================
# Entry State Constants
# ==============================================================================

ENTRY_EMPTY = 0      # Slot is empty (never used)
ENTRY_OCCUPIED = 1   # Slot contains valid key-value pair
ENTRY_TOMBSTONE = 2  # Slot was deleted (marks probe chain)


# ==============================================================================
# Power-of-Two Capacity Table for Resize
# ==============================================================================

# Power-of-two capacities for HashMap (fast bitwise AND indexing)
# With strong hash functions (FxHash, FNV-1a), power-of-two provides:
# - 3-10x faster indexing (AND vs modulo)
# - Excellent distribution (hash quality matters, not capacity)
# - Simpler implementation
POWER_OF_TWO_CAPACITIES = [
    16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
    16384, 32768, 65536, 131072, 262144, 524288, 1048576,
    2097152, 4194304, 8388608, 16777216
]


def get_next_capacity(current: int) -> int:
    """Get the next power-of-two capacity >= 2 * current.

    Args:
        current: Current capacity.

    Returns:
        Next power-of-two >= 2 * current.
    """
    target = current * 2
    for capacity in POWER_OF_TWO_CAPACITIES:
        if capacity >= target:
            return capacity
    # If we exceed our table, just double (still power-of-two)
    return target


# ==============================================================================
# LLVM Type Constructors
# ==============================================================================


def get_entry_type(codegen: Any, key_type: Type, value_type: Type) -> ir.Type:
    """Get LLVM struct type for Entry<K, V>.

    Structure:
        struct Entry<K, V>:
            K key
            V value
            u8 state  # 0=Empty, 1=Occupied, 2=Tombstone

    Args:
        codegen: LLVM codegen instance.
        key_type: The key type K.
        value_type: The value type V.

    Returns:
        LLVM literal struct type for Entry<K, V>.
    """
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)
    state_llvm = codegen.types.i8

    return ir.LiteralStructType([key_llvm, value_llvm, state_llvm])


def get_hashmap_llvm_type(codegen: Any, key_type: Type, value_type: Type) -> ir.Type:
    """Get LLVM struct type for HashMap<K, V>.

    Structure:
        struct HashMap<K, V>:
            Entry<K, V>[] buckets  # Dynamic array: {i32 len, i32 cap, Entry* data}
            i32 size
            i32 capacity
            i32 tombstones

    Args:
        codegen: LLVM codegen instance.
        key_type: The key type K.
        value_type: The value type V.

    Returns:
        LLVM literal struct type for HashMap<K, V>.
    """
    entry_type = get_entry_type(codegen, key_type, value_type)

    # Dynamic array of Entry<K, V>: {i32 len, i32 cap, Entry* data}
    buckets_type = ir.LiteralStructType([
        codegen.types.i32,                    # len
        codegen.types.i32,                    # cap
        ir.PointerType(entry_type)            # data (Entry<K, V>*)
    ])

    # HashMap struct: {buckets, size, capacity, tombstones}
    return ir.LiteralStructType([
        buckets_type,         # Entry<K, V>[] buckets
        codegen.types.i32,    # i32 size
        codegen.types.i32,    # i32 capacity
        codegen.types.i32,    # i32 tombstones
    ])


# ==============================================================================
# Type String Parsing
# ==============================================================================


def get_key_hash_method(key_type: Type) -> Optional[Any]:
    """Get the hash method for a HashMap key type, registering it on-demand if needed.

    This function handles on-demand hash registration for array types used as HashMap keys.
    Array types may not be registered in Pass 1.8 if they only appear as HashMap type
    parameters (not in struct/enum fields).

    Args:
        key_type: The key type (must have a hash() method).

    Returns:
        The BuiltinMethod for hash(), or None if the type cannot be hashed.
    """
    from sushi_lang.sushi_stdlib.src.common import get_builtin_method

    # Try to get existing hash method
    hash_method = get_builtin_method(key_type, "hash")
    if hash_method is not None:
        return hash_method

    # For array types, try to register on-demand
    if isinstance(key_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.backend.types.arrays.methods.hashing import register_array_hash_method, can_array_be_hashed
        can_hash, reason = can_array_be_hashed(key_type)
        if can_hash:
            register_array_hash_method(key_type)
            return get_builtin_method(key_type, "hash")

    return None


def split_type_arguments(type_args_str: str) -> list[str]:
    """Split comma-separated type arguments while respecting angle brackets.

    Handles nested generics like "Box<i32>, string" -> ["Box<i32>", "string"]

    Args:
        type_args_str: Comma-separated type arguments string.

    Returns:
        List of type argument strings.
    """
    parts = []
    current = []
    depth = 0

    for char in type_args_str:
        if char == '<':
            depth += 1
            current.append(char)
        elif char == '>':
            depth -= 1
            current.append(char)
        elif char == ',' and depth == 0:
            # Top-level comma - split here
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(char)

    # Add the last part
    if current:
        parts.append(''.join(current).strip())

    return parts


def resolve_type_from_string(type_str: str, codegen: Any) -> Type:
    """Resolve a type from its string representation.

    Handles:
    - Builtin types (i32, string, bool, etc.)
    - Struct types (Point, Person, etc.)
    - Enum types (Color, FileError, etc.)
    - Generic types (Maybe<i32>, Box<string>, etc.)
    - Fixed arrays (i32[10], string[3], etc.)
    - Dynamic arrays (i32[], string[], etc.)

    Args:
        type_str: Type name string (e.g., "i32", "Point", "Maybe<i32>", "string[3]").
        codegen: LLVM codegen instance with struct_table and enum_table.

    Returns:
        Resolved Type object.

    Raises:
        ValueError: If type cannot be resolved.
    """
    # Check for array types first (fixed: "type[N]" or dynamic: "type[]")
    if '[' in type_str and type_str.endswith(']'):
        # Extract base type and size
        match = re.match(r'^(.+)\[(\d*)\]$', type_str)
        if match:
            base_type_str = match.group(1)
            size_str = match.group(2)

            # Recursively resolve base type
            base_type = resolve_type_from_string(base_type_str, codegen)

            if size_str:
                # Fixed array: "type[N]"
                size = int(size_str)
                return ArrayType(base_type=base_type, size=size)
            else:
                # Dynamic array: "type[]"
                return DynamicArrayType(base_type=base_type)

    # Builtin type mapping
    builtin_map = {
        "i8": BuiltinType.I8,
        "i16": BuiltinType.I16,
        "i32": BuiltinType.I32,
        "i64": BuiltinType.I64,
        "u8": BuiltinType.U8,
        "u16": BuiltinType.U16,
        "u32": BuiltinType.U32,
        "u64": BuiltinType.U64,
        "f32": BuiltinType.F32,
        "f64": BuiltinType.F64,
        "bool": BuiltinType.BOOL,
        "string": BuiltinType.STRING,
    }

    # Try builtin type first
    if type_str in builtin_map:
        return builtin_map[type_str]

    # Check for generic type (contains angle brackets)
    if '<' in type_str and type_str.endswith('>'):
        # This is a generic type like "Maybe<i32>" or "Box<Point>"
        # For HashMap purposes, we need to look it up in the tables
        # The monomorphized type should already exist in struct_table or enum_table

        # Try to find it in enum_table first (Maybe, Result, etc.)
        if type_str in codegen.enum_table.by_name:
            return codegen.enum_table.by_name[type_str]

        # Try struct_table (Box, Own, etc.)
        if type_str in codegen.struct_table.by_name:
            return codegen.struct_table.by_name[type_str]

        raise_internal_error("CE0045", type=type_str)

    # Try to find it in struct_table (user-defined struct)
    if type_str in codegen.struct_table.by_name:
        return codegen.struct_table.by_name[type_str]

    # Try to find it in enum_table (user-defined enum)
    if type_str in codegen.enum_table.by_name:
        return codegen.enum_table.by_name[type_str]

    raise_internal_error("CE0022", type=type_str)


def extract_key_value_types(hashmap_type: StructType, codegen: Any) -> tuple[Type, Type]:
    """Extract K and V types from HashMap<K, V>.

    Parses the struct name "HashMap<K, V>" to extract the concrete key and value types.
    This works because monomorphized generic structs have names like "HashMap<string, i32>".

    Now supports:
    - Builtin types (i32, string, bool, etc.)
    - User-defined structs (Point, Person, etc.)
    - User-defined enums (Color, FileError, etc.)
    - Nested generics (HashMap<Maybe<i32>, Box<string>>)

    Args:
        hashmap_type: The HashMap<K, V> struct type (after monomorphization).
        codegen: LLVM codegen instance with struct_table and enum_table.

    Returns:
        Tuple of (key_type, value_type).

    Raises:
        ValueError: If cannot parse HashMap type name.
    """
    name = hashmap_type.name

    # Expected format: "HashMap<K, V>" where K and V are type names
    if not name.startswith("HashMap<") or not name.endswith(">"):
        raise_internal_error("CE0087", type=name)

    # Extract the type arguments string: "K, V"
    type_args_str = name[len("HashMap<"):-1]

    # Split by comma while respecting nested angle brackets
    parts = split_type_arguments(type_args_str)
    if len(parts) != 2:
        raise_internal_error("CE0050", generic="HashMap", expected=2, got=len(parts))

    key_type_str, value_type_str = parts[0].strip(), parts[1].strip()

    # Resolve each type using the codegen tables
    key_type = resolve_type_from_string(key_type_str, codegen)
    value_type = resolve_type_from_string(value_type_str, codegen)

    return (key_type, value_type)
