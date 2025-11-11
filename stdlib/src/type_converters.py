"""
Type Converters

Utilities for converting between semantic types and LLVM IR types.

Design: Single Responsibility - only type conversion logic.
"""

import llvmlite.ir as ir
from semantics.typesys import Type, BuiltinType, StructType, EnumType, ArrayType, DynamicArrayType


# ==============================================================================
# Semantic Type to LLVM Type Conversion
# ==============================================================================

def semantic_type_to_llvm(sem_type: Type) -> ir.Type:
    """Convert a semantic type to an LLVM IR type (standalone version).

    This is a simplified version that handles basic types needed for stdlib.
    Does NOT handle complex types (structs, enums, generics) - those require
    the full compiler infrastructure.

    Args:
        sem_type: The semantic type to convert.

    Returns:
        The corresponding LLVM IR type.

    Raises:
        TypeError: If the type is not supported in standalone mode.
    """
    # Basic integer types
    if sem_type == BuiltinType.I8:
        return ir.IntType(8)
    elif sem_type == BuiltinType.I16:
        return ir.IntType(16)
    elif sem_type == BuiltinType.I32:
        return ir.IntType(32)
    elif sem_type == BuiltinType.I64:
        return ir.IntType(64)
    elif sem_type == BuiltinType.U8:
        return ir.IntType(8)
    elif sem_type == BuiltinType.U16:
        return ir.IntType(16)
    elif sem_type == BuiltinType.U32:
        return ir.IntType(32)
    elif sem_type == BuiltinType.U64:
        return ir.IntType(64)
    # Floating-point types
    elif sem_type == BuiltinType.F32:
        return ir.FloatType()
    elif sem_type == BuiltinType.F64:
        return ir.DoubleType()
    # Boolean and string
    elif sem_type == BuiltinType.BOOL:
        return ir.IntType(8)
    elif sem_type == BuiltinType.STRING:
        return ir.IntType(8).as_pointer()
    # Blank type
    elif sem_type == BuiltinType.BLANK:
        return ir.IntType(32)  # Represented as i32 (dummy value)
    # I/O handles
    elif sem_type in (BuiltinType.STDIN, BuiltinType.STDOUT, BuiltinType.STDERR, BuiltinType.FILE):
        return ir.IntType(8).as_pointer()  # FILE* as opaque pointer
    else:
        raise TypeError(f"Unsupported semantic type in standalone mode: {sem_type}")


# ==============================================================================
# Name Mangling for Generic Types
# ==============================================================================

def mangle_generic_name(base_name: str, type_params: list[Type]) -> str:
    """Generate a mangled name for a generic type instantiation.

    Examples:
        Result<i32> -> "result_i32"
        Maybe<string> -> "maybe_string"
        Pair<i32, f64> -> "pair_i32_f64"

    Args:
        base_name: The base name of the generic type (e.g., "Result", "Maybe").
        type_params: List of type parameters.

    Returns:
        The mangled name suitable for use in LLVM function names.
    """
    mangled = base_name.lower()
    for param in type_params:
        mangled += "_" + _type_to_mangled_string(param)
    return mangled


def _type_to_mangled_string(t: Type) -> str:
    """Convert a type to a string suitable for name mangling.

    Args:
        t: The type to convert.

    Returns:
        A mangled string representation of the type.
    """
    # Basic types
    if t == BuiltinType.I8:
        return "i8"
    elif t == BuiltinType.I16:
        return "i16"
    elif t == BuiltinType.I32:
        return "i32"
    elif t == BuiltinType.I64:
        return "i64"
    elif t == BuiltinType.U8:
        return "u8"
    elif t == BuiltinType.U16:
        return "u16"
    elif t == BuiltinType.U32:
        return "u32"
    elif t == BuiltinType.U64:
        return "u64"
    elif t == BuiltinType.F32:
        return "f32"
    elif t == BuiltinType.F64:
        return "f64"
    elif t == BuiltinType.BOOL:
        return "bool"
    elif t == BuiltinType.STRING:
        return "string"
    elif t == BuiltinType.BLANK:
        return "blank"
    # Complex types (simplified - expand as needed)
    elif isinstance(t, StructType):
        return t.name.lower()
    elif isinstance(t, EnumType):
        return t.name.lower()
    elif isinstance(t, ArrayType):
        return f"array_{t.size}_{_type_to_mangled_string(t.base_type)}"
    elif isinstance(t, DynamicArrayType):
        return f"dynarray_{_type_to_mangled_string(t.base_type)}"
    else:
        # Fallback: use string representation
        return str(t).replace("<", "_").replace(">", "_").replace("[", "_").replace("]", "_").lower()
