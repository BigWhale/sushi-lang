from __future__ import annotations
from enum import Enum
from typing import Optional, Mapping, Union
from dataclasses import dataclass

# Import generic types
from semantics.generics.types import TypeParameter, GenericEnumType, GenericTypeRef

class BuiltinType(Enum):
    I8 = "i8"
    I16 = "i16"
    I32 = "i32"
    I64 = "i64"
    U8 = "u8"
    U16 = "u16"
    U32 = "u32"
    U64 = "u64"
    F32 = "f32"
    F64 = "f64"
    BOOL = "bool"
    STRING = "string"
    BLANK = "~"
    STDIN = "stdin"
    STDOUT = "stdout"
    STDERR = "stderr"
    FILE = "file"

    def __str__(self) -> str:
        return self.value

@dataclass(frozen=True)
class UnknownType:
    name: str

    def __str__(self) -> str:
        return self.name

@dataclass(frozen=True)
class ArrayType:
    base_type: "Type"  # The element type
    size: int          # Array size (compile-time constant)

    def __str__(self) -> str:
        return f"{self.base_type}[{self.size}]"

    def __hash__(self) -> int:
        return hash((self.base_type, self.size))

    def __eq__(self, other) -> bool:
        return isinstance(other, ArrayType) and self.base_type == other.base_type and self.size == other.size

@dataclass(frozen=True)
class DynamicArrayType:
    base_type: "Type"  # The element type

    def __str__(self) -> str:
        return f"{self.base_type}[]"

    def __hash__(self) -> int:
        return hash(("dynamic_array", self.base_type))

    def __eq__(self, other) -> bool:
        return isinstance(other, DynamicArrayType) and self.base_type == other.base_type

@dataclass(frozen=True)
class StructType:
    """Represents a user-defined struct type.

    The fields dictionary maps field names to their types.
    Field order is preserved through insertion order in the dict (Python 3.7+).
    """
    name: str                          # Struct name (e.g., "Point")
    fields: tuple[tuple[str, "Type"], ...]  # Immutable sequence of (field_name, field_type) tuples
    # Generic metadata for monomorphized types
    generic_base: Optional[str] = None  # Base name before monomorphization (e.g., "Container" for "Container<Point>")
    generic_args: Optional[tuple["Type", ...]] = None  # Type arguments used (e.g., (StructType("Point"),))

    def __str__(self) -> str:
        return self.name

    def __hash__(self) -> int:
        # Hash based on name only since struct names must be unique
        # For generic structs, each instantiation gets a unique name (e.g., "Box<i32>", "Box<string>")
        # This avoids issues with unhashable field types like UnknownType
        return hash(("struct", self.name))

    def __eq__(self, other) -> bool:
        return isinstance(other, StructType) and self.name == other.name and self.fields == other.fields

    def get_field_type(self, field_name: str) -> Optional["Type"]:
        """Get the type of a field by name, or None if field doesn't exist."""
        for name, ty in self.fields:
            if name == field_name:
                return ty
        return None

    def get_field_index(self, field_name: str) -> Optional[int]:
        """Get the index of a field by name, or None if field doesn't exist."""
        for i, (name, _) in enumerate(self.fields):
            if name == field_name:
                return i
        return None

@dataclass(frozen=True)
class ResultType:
    """Represents a Result<T> type for function returns.

    All functions implicitly return Result<T> where T is their declared return type.
    This type is transparent to users but used internally for type checking.
    """
    ok_type: "Type"  # The type wrapped in Ok(value)

    def __str__(self) -> str:
        return f"Result<{self.ok_type}>"

    def __hash__(self) -> int:
        return hash(("result", self.ok_type))

    def __eq__(self, other) -> bool:
        return isinstance(other, ResultType) and self.ok_type == other.ok_type

@dataclass(frozen=True)
class IteratorType:
    """Represents an Iterator<T> type for iteration over sequences.

    Iterators are created by calling .iter() on arrays and other iterable types.
    They cannot be directly constructed by users - they are opaque values.
    """
    element_type: "Type"  # The type of elements yielded by this iterator

    def __str__(self) -> str:
        return f"Iterator<{self.element_type}>"

    def __hash__(self) -> int:
        return hash(("iterator", self.element_type))

    def __eq__(self, other) -> bool:
        return isinstance(other, IteratorType) and self.element_type == other.element_type

@dataclass(frozen=True)
class ReferenceType:
    """Represents a borrowed reference to a value (&T).

    References allow temporary access to data without transferring ownership.
    All references are mutable in Sushi (unlike Rust's &/&mut distinction).

    Borrow Rules (enforced at compile time):
    - Only one active borrow per variable at a time
    - Can't move, rebind, or destroy a variable while it's borrowed
    - Borrows are function-scoped (end at function return)

    Usage:
    - Function parameters: fn process(arr: &i32[]) ~
    - Function returns: fn get_ref() &MyStruct
    - Zero-cost: compiles to LLVM pointers
    """
    referenced_type: "Type"  # The type being borrowed (e.g., i32[], MyStruct)

    def __str__(self) -> str:
        return f"&{self.referenced_type}"

    def __hash__(self) -> int:
        return hash(("reference", self.referenced_type))

    def __eq__(self, other) -> bool:
        return isinstance(other, ReferenceType) and self.referenced_type == other.referenced_type

@dataclass(frozen=True)
class PointerType:
    """Represents a pointer to heap-allocated data (T*).

    Pointers are used for:
    - Own<T> implementation (owned heap allocation)
    - Breaking recursive type cycles
    - Future features (raw pointers, custom allocators)

    Unlike ReferenceType (&T) which is zero-cost borrowing,
    PointerType represents actual heap-allocated memory that
    must be explicitly freed.

    Syntax: T* (e.g., i32*, Expr*)
    Note: Pointers are internal to Own<T> and not directly exposed to users.
    """
    pointee_type: "Type"  # The type being pointed to

    def __str__(self) -> str:
        return f"{self.pointee_type}*"

    def __hash__(self) -> int:
        return hash(("pointer", self.pointee_type))

    def __eq__(self, other) -> bool:
        return isinstance(other, PointerType) and self.pointee_type == other.pointee_type

@dataclass(frozen=True)
class EnumVariantInfo:
    """Information about a single enum variant."""
    name: str                           # Variant name (e.g., "Some", "None")
    associated_types: tuple["Type", ...]  # Types of associated data (empty for unit variants)

    def __hash__(self) -> int:
        return hash((self.name, self.associated_types))

    def __eq__(self, other) -> bool:
        return isinstance(other, EnumVariantInfo) and self.name == other.name and self.associated_types == other.associated_types

@dataclass(frozen=True)
class EnumType:
    """Represents a user-defined enum type.

    The variants tuple contains all valid variants for this enum.
    Variant order is preserved for exhaustiveness checking.
    """
    name: str                                   # Enum name (e.g., "Option", "Color")
    variants: tuple[EnumVariantInfo, ...]       # Immutable sequence of variants
    # Generic metadata for monomorphized types
    generic_base: Optional[str] = None  # Base name before monomorphization (e.g., "Maybe" for "Maybe<i32>")
    generic_args: Optional[tuple["Type", ...]] = None  # Type arguments used (e.g., (BuiltinType.I32,))

    def __str__(self) -> str:
        return self.name

    def __hash__(self) -> int:
        # Hash based on name only since enum names must be unique
        return hash(("enum", self.name))

    def __eq__(self, other) -> bool:
        return isinstance(other, EnumType) and self.name == other.name and self.variants == other.variants

    def get_variant(self, variant_name: str) -> Optional[EnumVariantInfo]:
        """Get variant info by name, or None if variant doesn't exist."""
        for variant in self.variants:
            if variant.name == variant_name:
                return variant
        return None

    def get_variant_index(self, variant_name: str) -> Optional[int]:
        """Get the index (tag) of a variant by name, or None if variant doesn't exist."""
        for i, variant in enumerate(self.variants):
            if variant.name == variant_name:
                return i
        return None

# Union type for all possible types
# Includes generic types: TypeParameter, GenericTypeRef
# Note: GenericEnumType is NOT in the Type union - it's a template that produces EnumTypes
Type = Union[
    BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType,
    ResultType, IteratorType, ReferenceType, PointerType,
    TypeParameter, GenericTypeRef
]


TYPE_NODE_NAMES = {
    "i8_t", "i16_t", "i32_t", "i64_t", "u8_t", "u16_t", "u32_t", "u64_t",
    "f32_t", "f64_t", "bool_t", "string_t", "blank_t",
    "array_t", "dynamic_array_t", "reference_t", "file_t",
    "generic_type_t"  # Added for generic type instantiation (e.g., Result<i32>)
}

NODE_TO_TYPE: Mapping[str, BuiltinType] = {
    "i8_t": BuiltinType.I8,
    "i16_t": BuiltinType.I16,
    "i32_t": BuiltinType.I32,
    "i64_t": BuiltinType.I64,
    "u8_t": BuiltinType.U8,
    "u16_t": BuiltinType.U16,
    "u32_t": BuiltinType.U32,
    "u64_t": BuiltinType.U64,
    "f32_t": BuiltinType.F32,
    "f64_t": BuiltinType.F64,
    "bool_t": BuiltinType.BOOL,
    "string_t": BuiltinType.STRING,
    "blank_t": BuiltinType.BLANK,
    "file_t": BuiltinType.FILE,
}

def type_from_rule_name(name: str) -> Optional[Type]:
    """Map grammar rule name (e.g., 'int_t') to internal Type, or None if unknown."""
    return NODE_TO_TYPE.get(name)

def type_string_from_rule_name(name: str) -> str:
    """
    Human-readable type name for diagnostics. Returns enum .value for known types,
    otherwise a reasonable fallback (strip trailing '_t' if present).
    """
    t = NODE_TO_TYPE.get(name)
    if t is not None:
        return t.value
    # Fallback keeps messages nice for unknown types like 'string_t' → 'string'
    return name[:-2] if name.endswith("_t") else name
