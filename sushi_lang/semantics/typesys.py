from __future__ import annotations
from enum import Enum
from typing import AbstractSet, Callable, Optional, Mapping, Union
from dataclasses import dataclass, field

from sushi_lang.semantics.generics.interned import interned_name
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

def element_text(element: "Type", render) -> str:
    """An array element as text. A function element takes parentheses, because
    `fn(i32) -> i32[]` is a function that returns an array (#1128)."""
    text = render(element)
    return f"({text})" if isinstance(element, FunctionType) else text


@dataclass(frozen=True)
class ArrayType:
    base_type: "Type"  # The element type
    size: int          # Array size (compile-time constant)
    # The constant a written size named (`i32[SIZE]`), for the `unused` pass. Not identity.
    size_name: Optional[str] = field(default=None, compare=False)

    def __str__(self) -> str:
        return f"{element_text(self.base_type, str)}[{self.size}]"

    def __hash__(self) -> int:
        return hash((self.base_type, self.size))

    def __eq__(self, other) -> bool:
        return isinstance(other, ArrayType) and self.base_type == other.base_type and self.size == other.size

@dataclass(frozen=True)
class DynamicArrayType:
    base_type: "Type"  # The element type

    def __str__(self) -> str:
        return f"{element_text(self.base_type, str)}[]"

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
        return interned_name("Iterator", (self.element_type,))

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
    """Represents a first-class function type (a bare function pointer).

    `err_type` is the `| E` channel, and None is a BARE function type: a call through
    the value yields `ok_type` itself (docs/design/error-channel.md).
    """
    param_types: tuple["Type", ...]
    ok_type: "Type"
    err_type: Optional["Type"]
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
        if self.err_type is not None:
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


def _container_bases() -> tuple[str, ...]:
    """The base names of the compiler's containers."""
    from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
    return CONTAINER_BASES


# Where each predicate stops. A pointer, an iterator, a fn value and a template hold what
# they name somewhere else, so neither predicate enters them. `owns_resource` also stops
# at a borrow, because a borrow names storage that another value owns.
_HOLDS_STOPS = frozenset({"PointerType", "IteratorType", "FunctionType", "TypePack",
                          "GenericStructType", "GenericEnumType"})
_OWNS_STOPS = _HOLDS_STOPS | {"ReferenceType"}


def owns_resource(t: Optional["Type"], drops: AbstractSet[str],
                  resolve: Optional[Callable[["Type"], Optional["Type"]]] = None) -> bool:
    """True if a value of this type owns something RAII must release and a sink must transfer.

    Two ways to own. Most types own HEAP, and the answer is structural: a `string`, a
    `T[]`, an `Own`, a `List`, a `HashMap`, a capturing closure, or a composite holding
    one. A type may also DECLARE that it owns a resource, by implementing the `Drop`
    perk -- a file or a socket holds one `i32` descriptor, so no field walk can find it
    (HANDLES.md ruling R2).

    `drops` is the set of type names that implement `Drop`. It is REQUIRED and has no
    default: a caller that forgets it would answer False for every handle in the
    program, and a false answer here is a leaked descriptor with no diagnostic
    (ruling R2a).

    One question per type over `type_walk.walk_named_types`. A fixed array owns no buffer
    of its own, but its elements can own heap (#185). A container's fields are raw
    pointers, so its generic base answers (#162, #181, #183).
    """
    from sushi_lang.semantics.type_walk import walk_named_types
    from sushi_lang.semantics.type_predicates import generic_base_of
    bases = _container_bases()

    def owns_here(ty: "Type") -> bool:
        if isinstance(ty, BuiltinType):
            return ty == BuiltinType.STRING
        if isinstance(ty, DynamicArrayType):
            return True
        if isinstance(ty, FunctionType):
            return ty.captures != ()
        if isinstance(ty, GenericTypeRef):
            return ty.base_name in bases
        if isinstance(ty, (StructType, EnumType)):
            return ty.name in drops or (
                isinstance(ty, StructType) and generic_base_of(ty) in bases)
        # An opaque type parameter MAY own, so a value of it moves (#1070, R5).
        return isinstance(ty, TypeParameter) and ty.is_opaque

    return any(owns_here(ty) for ty in walk_named_types(
        t, stop=lambda ty: type(ty).__name__ in _OWNS_STOPS, resolve=resolve))


def holds_declared_resource(t: Optional["Type"], drops: AbstractSet[str],
                            resolve: Optional[Callable[["Type"], Optional["Type"]]] = None,
                            found: Optional[list] = None) -> bool:
    """Does this type declare a resource, or hold one anywhere inside it?

    NARROWER than `owns_resource`, and the difference is the whole point: a `string`
    owns heap and answers True there, and it deep-copies perfectly well. This asks only
    about the DECLARED half -- a `Drop` type, a struct with one in a field, an array or
    a container of them -- and an opaque type parameter that does not promise `Clone`
    (#1070, R5): `Clone` is the promise "holds no resource", so the two are one fact.

    It is what `.clone()` is refused on (ruling R3). A derived clone of a handle copies
    the descriptor number, so two values hold one descriptor and both drop: a double
    close that the copy verb hides. `.share()` is the operation that means a second
    owner, and it says so in its name.

    The walk stops where the type holds nothing by value (`_HOLDS_STOPS`: a function
    value, an iterator, a pointer). A user struct holds what its fields hold; only a
    container keeps its element in its type arguments, so only a container's are read.
    `found`, when given, receives each type that declares a resource: the clone check
    reads it to name the fault (CE4018 for a type parameter, CE2431 for a handle).
    """
    from sushi_lang.semantics.type_walk import walk_named_types
    from sushi_lang.semantics.type_predicates import generic_base_of
    bases = _container_bases()

    def declares_here(ty: "Type") -> bool:
        if isinstance(ty, TypeParameter):
            return ty.is_opaque and not ty.promises("Clone")
        return isinstance(ty, (StructType, EnumType)) and ty.name in drops

    declaring = [ty for ty in walk_named_types(
        t, stop=lambda ty: type(ty).__name__ in _HOLDS_STOPS, resolve=resolve,
        struct_type_args=lambda ty: generic_base_of(ty) in bases) if declares_here(ty)]
    if found is not None:
        found.extend(declaring)
    return bool(declaring)


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
    # The stdlib module a PREDEFINED enum belongs to (#574, Ruling 3). No unit declares
    # `FileMode`, so no declaration record can say who may write the name; the stamp is
    # what the `namespaces` pass reads to gate the bare name behind `use <io/fs>` and to
    # hold it behind the alias. None on every enum a unit declares, and on `StdError`.
    home_module: Optional[str] = None
    # An error type: written `error`, or one of the seven predefined error enums. It is
    # part of the type and NOT of its identity, so `__eq__` and `__hash__` do not read it.
    is_error: bool = False

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

@dataclass(frozen=True)
class ReceiverType:
    """The implementing type, in the contract of a PREDEFINED perk.

    `Eq` and `Ord` compare the receiver with a second value of the same type, and a perk
    has no `Self`. Only the compiler writes this placeholder (`register_predefined_perks`);
    the implementation matcher reads it as the target of the implementation. A user
    perk still cannot name its receiver.
    """

    def __str__(self) -> str:
        return "Self"


# Union type for all possible types
# Includes generic types: TypeParameter, GenericTypeRef
# Note: GenericEnumType is NOT in the Type union - it's a template that produces EnumTypes
Type = Union[
    BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType,
    IteratorType, ReferenceType, PointerType, ForeignPtrType,
    FunctionType, TypeParameter, GenericTypeRef
]


# Every alias the grammar's `?type` and `?atom_type` rules make. The answer to "is this
# child a written type" is yes for each of them, and a name is one of them -- bare or
# behind an alias. `is_type_node` (`ast_builder/utils/tree_navigation.py`) is the reader.
TYPE_NODE_NAMES = {
    "i8_t", "i16_t", "i32_t", "i64_t", "u8_t", "u16_t", "u32_t", "u64_t",
    "f32_t", "f64_t", "bool_t", "string_t", "blank_t",
    "array_t", "dynamic_array_t", "reference_t",
    "generic_type_t",  # Generic type instantiation (e.g., Result<i32>)
    "fn_type_t",       # First-class function type (e.g., fn(i32) -> i32)
    "tuple_t",         # Tuple type (e.g., (i32, string))
    "name_t", "qualified_name_t", "qualified_generic_type_t",
    # The bounded array element `(T: Clone)`: only the top level of an `extend` target
    # reads one (#1070), and every other position is CE6110. The bounded type argument
    # `T: Clone` is no `type` of the grammar; the type-argument readers ask for it.
    "bounded_paren_t",
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
}

def type_from_rule_name(name: str) -> Optional[Type]:
    """Map grammar rule name (e.g., 'int_t') to internal Type, or None if unknown."""
    return NODE_TO_TYPE.get(name)

