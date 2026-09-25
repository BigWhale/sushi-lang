"""The type algebra the borrow checker needs, answered from the collect pass's tables alone."""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.drop_set import drop_type_names
from sushi_lang.semantics.ownership import TypeClass, type_class_of
from sushi_lang.semantics.typesys import (
    ArrayType,
    DynamicArrayType,
    EnumType,
    PointerType,
    ReferenceType,
    StructType,
    Type,
    UnknownType,
)

from .state import BorrowState

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables


# The containers a `push` / `insert` fills and a `.get()` reads an element out of.
# An `Own@(T)` holds one value and is not one of them.
_ELEMENT_CONTAINERS = frozenset(CONTAINER_BASES) - {"Own"}


def _generic_parts(ty: Optional[Type]) -> tuple[Optional[str], tuple]:
    """The base name and the type arguments of a generic instance, written or interned."""
    if isinstance(ty, GenericTypeRef):
        return ty.base_name, ty.type_args
    if isinstance(ty, StructType):
        return ty.generic_base, ty.generic_args or ()
    return None, ()


class TypeQueries:
    """Resolves and classifies types for the checker. Reads `tables`, nothing else."""

    def __init__(self, tables: SymbolTables) -> None:
        """Hold the collect pass's tables -- the sole authority for a named type's contents."""
        self.tables = tables

    def resolve_named(self, ty: Optional[Type]):
        """Resolve an `UnknownType` or a `GenericTypeRef` against the struct/enum tables.

        A `GenericTypeRef` spells its own interned name, so the monomorphized type IS
        reachable by lookup -- and it has to be. A `let Result@(i32[], StdError) r`
        keeps the ref as its declared type, and an unresolved ref answers PLAIN: this
        pass would then classify a Result holding a dynamic array as owning nothing,
        while the backend, which resolves it, classifies it MOVE. That disagreement is
        a use after move with no diagnostic in front of it.
        """
        if not isinstance(ty, (UnknownType, GenericTypeRef)):
            return ty
        name = ty.name if isinstance(ty, UnknownType) else str(ty)
        return (self.tables.structs.by_name.get(name)
                or self.tables.enums.by_name.get(name)
                or ty)

    def variant_payload_types(self, enum_type: Optional[Type],
                             variant_name: str) -> tuple:
        """The associated types of `variant_name`, or () when the enum is not resolved."""
        resolved = self.resolve_named(enum_type)
        if not isinstance(resolved, EnumType):
            return ()
        variant = resolved.get_variant(variant_name)
        return tuple(variant.associated_types) if variant is not None else ()

    def own_payload(self, ty: Optional[Type]) -> Optional[Type]:
        """The `T` inside an `Own@(T)`, for an OwnPattern's inner binding."""
        ty = self.resolve_named(ty)
        if isinstance(ty, GenericTypeRef) and ty.base_name == "Own" and ty.type_args:
            return ty.type_args[0]
        if isinstance(ty, StructType) and is_instance_of(ty, "Own") and ty.fields:
            value_field = ty.fields[0][1]
            if isinstance(value_field, PointerType):
                return value_field.pointee_type
        return None

    def is_container(self, ty: Optional[Type]) -> bool:
        """Is `ty` a `List@(T)`, `HashMap@(K, V)` or a dynamic array `T[]`?"""
        ty = self.resolve_named(ty)
        if isinstance(ty, ReferenceType):
            ty = ty.referenced_type
        if isinstance(ty, DynamicArrayType):
            return True
        return _generic_parts(ty)[0] in _ELEMENT_CONTAINERS

    def element_type(self, ty: Optional[Type]) -> Optional[Type]:
        """What a `.get()` on a receiver of type `ty` reads out."""
        ty = self.resolve_named(ty)
        if isinstance(ty, ReferenceType):
            ty = self.resolve_named(ty.referenced_type)
        if isinstance(ty, (DynamicArrayType, ArrayType)):
            return ty.base_type
        base, args = _generic_parts(ty)
        if base == "List" and args:
            return args[0]
        if base == "HashMap" and len(args) == 2:
            return args[1]
        return self.own_payload(ty)

    @property
    def drops(self) -> frozenset:
        """The type names that implement `Drop`, read from the perk table."""
        return drop_type_names(self.tables.perk_impls)

    def type_class(self, ty: Optional[Type]) -> TypeClass:
        """Classify a type as PLAIN or MOVE, resolving named types first."""
        return type_class_of(ty, self.drops, self.resolve_named)

    def type_class_of_source(self, state: Optional[BorrowState],
                             ty: Optional[Type]) -> TypeClass:
        """Classify the SOURCE of a consuming use, applying option B."""
        if state is not None and state.owns_no_heap:
            return TypeClass.PLAIN
        return self.type_class(ty)
