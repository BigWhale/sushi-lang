from __future__ import annotations
from enum import Enum
from typing import Optional, Mapping, Union
from dataclasses import dataclass, field

from sushi_lang.semantics.generics.types import TypeParameter, GenericTypeRef


class BorrowMode(Enum):
    """Borrow mode for reference types."""
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
    # The alias the name was written behind, or None for a bare one. It takes no part
    # in identity: under phase 1 of `docs/design/unit-namespaces.md` the qualifier picks
    # WHICH declaration is meant and the table key stays the bare name, so `geo.Vec` and
    # `Vec` are one type. `compare=False` is what says that to the generated hash.
    namespace: Optional[str] = field(default=None, compare=False)

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
    """Represents a user-defined struct type."""
    name: str                          # Struct name (e.g., "Point")
    fields: tuple[tuple[str, "Type"], ...]  # Immutable sequence of (field_name, field_type) tuples
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
        # NOMINAL identity: a named type IS its name, which already encodes
        # (declaration, type arguments), and the table is the sole authority for the fields.
        # See docs/design/type-identity.md.
        #
        # Comparing `fields` too made identity structural while `__hash__` stayed nominal,
        # so two instances at different resolution depths hash-matched and compared UNEQUAL
        # -- a silent dict miss, and the root of both #240 and the CE0126 class.
        return isinstance(other, StructType) and self.name == other.name

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
class IteratorType:
    """Represents an Iterator<T> type for iteration over sequences."""
    element_type: "Type"  # The type of elements yielded by this iterator

    def __str__(self) -> str:
        return f"Iterator<{self.element_type}>"

    def __hash__(self) -> int:
        return hash(("iterator", self.element_type))

    def __eq__(self, other) -> bool:
        return isinstance(other, IteratorType) and self.element_type == other.element_type

@dataclass(frozen=True)
class ReferenceType:
    """Represents a borrowed reference to a value (peek T or poke T)."""
    referenced_type: "Type"  # The type being borrowed (e.g., i32[], MyStruct)
    mutability: BorrowMode = BorrowMode.POKE  # Default to poke for backward compat during migration

    def __str__(self) -> str:
        return f"{self.mutability} {self.referenced_type}"

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


def deref_type(t: Optional["Type"]) -> Optional["Type"]:
    """The type a borrow refers to, or `t` unchanged when it is not a borrow."""
    return t.referenced_type if isinstance(t, ReferenceType) else t

@dataclass(frozen=True)
class PointerType:
    """Represents a pointer to heap-allocated data (T*)."""
    pointee_type: "Type"  # The type being pointed to

    def __str__(self) -> str:
        return f"{self.pointee_type}*"

    def __hash__(self) -> int:
        return hash(("pointer", self.pointee_type))

    def __eq__(self, other) -> bool:
        return isinstance(other, PointerType) and self.pointee_type == other.pointee_type

@dataclass(frozen=True)
class ForeignPtrType:
    """Opaque, unmanaged foreign pointer type (`ptr`) for the FFI boundary."""

    def __str__(self) -> str:
        return "ptr"

    def __hash__(self) -> int:
        return hash("foreign_ptr")

    def __eq__(self, other) -> bool:
        return isinstance(other, ForeignPtrType)

@dataclass(frozen=True)
class FunctionType:
    """Represents a first-class function type (a bare function pointer)."""
    param_types: tuple["Type", ...]
    ok_type: "Type"
    err_type: "Type"
    captures: Optional[tuple] = None
    param_modes: Optional[tuple] = None

    @property
    def modes(self) -> tuple:
        """The normalized parameter modes. Read this, never `param_modes` directly."""
        from sushi_lang.semantics.param_modes import normalize_modes
        return normalize_modes(self.param_types, self.param_modes)

    def __str__(self) -> str:
        params = ", ".join(
            f"{m.marker} {p}" if m.marker and not m.by_pointer else str(p)
            for p, m in zip(self.param_types, self.modes, strict=True)
        )
        base = f"fn({params}) -> {self.ok_type}"
        if str(self.err_type) != "StdError":
            base += f" | {self.err_type}"
        return base

    def __hash__(self) -> int:
        return hash(("function", self.param_types, self.ok_type, self.err_type,
                     self.modes))

    def __eq__(self, other) -> bool:
        return (isinstance(other, FunctionType) and
                self.param_types == other.param_types and
                self.ok_type == other.ok_type and
                self.err_type == other.err_type and
                self.modes == other.modes)


def owns_heap(t: Optional["Type"], _visited: Optional[set] = None,
              resolve=None) -> bool:
    """True if a value of this type owns heap that RAII must free and a sink must transfer."""
    if t is None:
        return False
    if resolve is not None and isinstance(t, UnknownType):
        t = resolve(t) or t

    if isinstance(t, ForeignPtrType):
        return False  # an opaque unmanaged foreign handle; RAII never frees it
    if isinstance(t, ReferenceType):
        return False  # a borrow names storage someone else owns
    if isinstance(t, FunctionType):
        # Tri-state, and the distinction is load-bearing -- see the docstring.
        return t.captures != ()
    if isinstance(t, BuiltinType):
        # A string owns its buffer when the runtime `owned` bit is set; the free is guarded
        # on that bit, so a literal/borrow frees to a no-op. Every other builtin (numerics,
        # bool, I/O handles) is unmanaged.
        return t == BuiltinType.STRING
    if isinstance(t, DynamicArrayType):
        return True
    if isinstance(t, GenericTypeRef) and t.base_name in ('Own', 'List', 'HashMap'):
        return True

    if isinstance(t, UnknownType):
        return False  # unresolved and no resolver given; treat as owning nothing

    if _visited is None:
        _visited = set()

    if isinstance(t, StructType):
        # Own<T> / List<T> / HashMap<K, V> always own a heap allocation, but their only
        # fields are raw pointers or a placeholder, so the field scan below answers False and
        # every recursion gate would skip them. That mismatch was #162 / #181 / #183.
        if t.name.startswith(('Own<', 'List<', 'HashMap<')):
            return True
        if t.name in _visited:
            return False
        _visited.add(t.name)
        return any(owns_heap(ft, _visited, resolve) for _, ft in t.fields)
    if isinstance(t, EnumType):
        if t.name in _visited:
            return False
        _visited.add(t.name)
        return any(owns_heap(at, _visited, resolve)
                   for variant in t.variants for at in variant.associated_types)
    if isinstance(t, ArrayType):
        # A fixed array `T[N]` owns no buffer of its own -- its storage is inline -- but its
        # ELEMENTS can own heap (#185).
        return owns_heap(t.base_type, _visited, resolve)
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
    """Represents a user-defined enum type."""
    name: str                                   # Enum name (e.g., "Option", "Color")
    variants: tuple[EnumVariantInfo, ...]       # Immutable sequence of variants
    generic_base: Optional[str] = None  # Base name before monomorphization (e.g., "Maybe" for "Maybe<i32>")
    generic_args: Optional[tuple["Type", ...]] = None  # Type arguments used (e.g., (BuiltinType.I32,))

    def __str__(self) -> str:
        return self.name

    def __hash__(self) -> int:
        return hash(("enum", self.name))

    def __eq__(self, other) -> bool:
        # NOMINAL identity -- see StructType.__eq__. Hashing on the name while comparing on
        # the variants is the pairing CE0126 describes: a silent cache miss and a duplicate
        # monomorphization rather than a crash.
        return isinstance(other, EnumType) and self.name == other.name

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
    IteratorType, ReferenceType, PointerType, ForeignPtrType,
    FunctionType, TypeParameter, GenericTypeRef
]


TYPE_NODE_NAMES = {
    "i8_t", "i16_t", "i32_t", "i64_t", "u8_t", "u16_t", "u32_t", "u64_t",
    "f32_t", "f64_t", "bool_t", "string_t", "blank_t",
    "array_t", "dynamic_array_t", "reference_t", "file_t",
    "generic_type_t",  # Generic type instantiation (e.g., Result<i32>)
    "fn_type_t",       # First-class function type (e.g., fn(i32) -> i32)
    # A name written behind an alias. Every reader of this set asks one question --
    # "is this child a written type" -- and the answer for a qualified name is yes.
    "qualified_name_t", "qualified_generic_type_t",
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
    """Human-readable type name for diagnostics. Returns enum .value for known types, otherwise a
    reasonable fallback (strip trailing '_t' if present).
    """
    t = NODE_TO_TYPE.get(name)
    if t is not None:
        return t.value
    return name[:-2] if name.endswith("_t") else name
