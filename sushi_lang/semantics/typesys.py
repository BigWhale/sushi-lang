from __future__ import annotations
from enum import Enum
from typing import Optional, Mapping, Union, Literal
from dataclasses import dataclass

# Import generic types
from sushi_lang.semantics.generics.types import TypeParameter, GenericEnumType, GenericTypeRef


class BorrowMode(Enum):
    """Borrow mode for reference types.

    PEEK: Read-only borrow (multiple allowed)
    POKE: Read-write borrow (exclusive access)
    """
    PEEK = "peek"  # Read-only
    POKE = "poke"  # Read-write

    def __str__(self) -> str:
        return self.value


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
    """Represents a Result<T, E> type for function returns.

    All functions implicitly return Result<T, E> where T is their declared return type
    and E is the error type. This type is transparent to users but used internally for
    type checking.
    """
    ok_type: "Type"   # The type wrapped in Ok(value)
    err_type: "Type"  # The type wrapped in Err(error)

    def __str__(self) -> str:
        return f"Result<{self.ok_type}, {self.err_type}>"

    def __hash__(self) -> int:
        return hash(("result", self.ok_type, self.err_type))

    def __eq__(self, other) -> bool:
        return (isinstance(other, ResultType) and
                self.ok_type == other.ok_type and
                self.err_type == other.err_type)

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
    """Represents a borrowed reference to a value (&peek T or &poke T).

    References allow temporary access to data without transferring ownership.
    Two borrow modes:
    - &peek T: Read-only borrow (multiple allowed)
    - &poke T: Read-write borrow (exclusive access)

    Borrow Rules (enforced at compile time):
    - Multiple &peek borrows allowed (read-only)
    - Only one &poke borrow at a time (exclusive)
    - Cannot have &peek and &poke borrows simultaneously
    - Can't move, rebind, or destroy a variable while it's borrowed
    - Borrows are function-scoped (end at function return)

    Type Coercion:
    - &poke T can be passed where &peek T is expected (safe downgrade)
    - &peek T cannot be passed where &poke T is expected

    Usage:
    - Read-only params: fn read(&peek i32[] arr) i32
    - Mutable params: fn modify(&poke i32 x) ~
    - Zero-cost: compiles to LLVM pointers
    """
    referenced_type: "Type"  # The type being borrowed (e.g., i32[], MyStruct)
    mutability: BorrowMode = BorrowMode.POKE  # Default to poke for backward compat during migration

    def __str__(self) -> str:
        return f"&{self.mutability} {self.referenced_type}"

    def __hash__(self) -> int:
        return hash(("reference", self.referenced_type, self.mutability))

    def __eq__(self, other) -> bool:
        return (isinstance(other, ReferenceType) and
                self.referenced_type == other.referenced_type and
                self.mutability == other.mutability)

    def is_peek(self) -> bool:
        """Returns True if this is a read-only borrow."""
        return self.mutability == BorrowMode.PEEK

    def is_poke(self) -> bool:
        """Returns True if this is a read-write borrow."""
        return self.mutability == BorrowMode.POKE

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
class ForeignPtrType:
    """Opaque, unmanaged foreign pointer type (`ptr`) for the FFI boundary.

    Maps to LLVM `i8*`. Unlike PointerType (which carries Own<T> RAII semantics)
    or ReferenceType (zero-cost borrowing), a ForeignPtrType is:
    - Exempt from borrow checking (aliasing is not tracked).
    - Exempt from RAII (no destructor; the matching C free must be called by hand).
    - Without null/bounds guarantees (may be null; dereferencing is unchecked).

    It is the single C-traffic handle type introduced by `unsafe external` blocks.
    """

    def __str__(self) -> str:
        return "ptr"

    def __hash__(self) -> int:
        return hash("foreign_ptr")

    def __eq__(self, other) -> bool:
        return isinstance(other, ForeignPtrType)

@dataclass(frozen=True)
class FunctionType:
    """Represents a first-class function type (a bare function pointer).

    Syntax: fn(P0, P1, ...) -> T [| E]
    - param_types: the declared parameter types (no `self`, no variadics in v1).
    - ok_type:     the declared return type T (the value wrapped in Result.Ok).
    - err_type:    the error type E. When the surface syntax omits `| E` this is
                   UnknownType("StdError") at parse time and is resolved to the StdError
                   enum by the normal type-resolution pass.

    A function value lowers to a 3-word fat pointer {fn_ptr, env_ptr, drop_ptr}. Calling
    through it yields the same Result<ok_type, err_type> a direct call would.

    `captures` is an OPTIONAL descriptor of a capturing lambda's environment (list of
    (name, Type) captured from the enclosing scope). It is metadata only: it is deliberately
    EXCLUDED from __eq__/__hash__ so type identity stays capture-agnostic — `fn(i32) -> i32`
    names both a plain fn and any closure of that arity/ok/err (compatibility is invariant on
    arity + each param + ok + err, never on capture).
    """
    param_types: tuple["Type", ...]
    ok_type: "Type"
    err_type: "Type"
    captures: Optional[tuple] = None

    def __str__(self) -> str:
        params = ", ".join(str(p) for p in self.param_types)
        base = f"fn({params}) -> {self.ok_type}"
        # Hide the implicit StdError to match the surface syntax in diagnostics.
        if str(self.err_type) != "StdError":
            base += f" | {self.err_type}"
        return base

    def __hash__(self) -> int:
        return hash(("function", self.param_types, self.ok_type, self.err_type))

    def __eq__(self, other) -> bool:
        return (isinstance(other, FunctionType) and
                self.param_types == other.param_types and
                self.ok_type == other.ok_type and
                self.err_type == other.err_type)


def is_owning_type(t: Optional["Type"]) -> bool:
    """True if a value of this type carries heap ownership.

    An owning value is MOVED on rebind/return/capture and RAII-freed at scope exit
    (its outer binding is consumed). This is the single ownership predicate shared by
    the borrow checker (move semantics) and the backend (deep-copy + destructor
    dispatch) so they never disagree.

    Owning: dynamic arrays, `List<T>`, `Own<T>`, and a CAPTURING function value (a
    closure with a non-empty `captures` descriptor). A non-capturing function value
    stays copyable (preserves v1 first-class-function ergonomics). Capture is metadata
    excluded from type identity, so closure ownership is resolved off `captures` here,
    while the backend additionally guards every function-value free at runtime by the
    `drop_ptr` (a null drop makes a conservative free a no-op).
    """
    if t is None:
        return False
    if isinstance(t, DynamicArrayType):
        return True
    if isinstance(t, GenericTypeRef) and t.base_name in ('Own', 'List'):
        return True
    if isinstance(t, FunctionType) and t.captures:
        return True
    name = getattr(t, 'name', None)
    if isinstance(name, str) and (name.startswith('Own<') or name.startswith('List<')):
        return True
    return False


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
    ResultType, IteratorType, ReferenceType, PointerType, ForeignPtrType,
    FunctionType, TypeParameter, GenericTypeRef
]


TYPE_NODE_NAMES = {
    "i8_t", "i16_t", "i32_t", "i64_t", "u8_t", "u16_t", "u32_t", "u64_t",
    "f32_t", "f64_t", "bool_t", "string_t", "blank_t",
    "array_t", "dynamic_array_t", "reference_t", "file_t",
    "generic_type_t",  # Generic type instantiation (e.g., Result<i32>)
    "fn_type_t"        # First-class function type (e.g., fn(i32) -> i32)
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
