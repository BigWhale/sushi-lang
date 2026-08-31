"""Type Converters"""

import llvmlite.ir as ir
from sushi_lang.semantics.typesys import Type, BuiltinType, StructType, EnumType, ArrayType, DynamicArrayType


def semantic_type_to_llvm(sem_type: Type) -> ir.Type:
    """Convert a semantic type to an LLVM IR type (standalone version)."""
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
    elif sem_type == BuiltinType.F32:
        return ir.FloatType()
    elif sem_type == BuiltinType.F64:
        return ir.DoubleType()
    elif sem_type == BuiltinType.BOOL:
        return ir.IntType(8)
    elif sem_type == BuiltinType.STRING:
        return ir.IntType(8).as_pointer()
    elif sem_type == BuiltinType.BLANK:
        return ir.IntType(32)  # Represented as i32 (dummy value)
    else:
        raise TypeError(f"Unsupported semantic type in standalone mode: {sem_type}")


def mangle_generic_name(base_name: str, type_params: list[Type]) -> str:
    """Generate a mangled name for a generic type instantiation."""
    mangled = base_name.lower()
    for param in type_params:
        mangled += "_" + _type_to_mangled_string(param)
    return mangled


def _type_to_mangled_string(t: Type) -> str:
    """Convert a type to a string suitable for name mangling."""
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
    elif isinstance(t, StructType):
        return t.name.lower()
    elif isinstance(t, EnumType):
        return t.name.lower()
    elif isinstance(t, ArrayType):
        return f"array_{t.size}_{_type_to_mangled_string(t.base_type)}"
    elif isinstance(t, DynamicArrayType):
        return f"dynarray_{_type_to_mangled_string(t.base_type)}"
    else:
        return str(t).replace("<", "_").replace(">", "_").replace("[", "_").replace("]", "_").lower()
