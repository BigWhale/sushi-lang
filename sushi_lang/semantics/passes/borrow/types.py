"""The type algebra the borrow checker needs, answered from Pass 0's tables alone."""

from __future__ import annotations
from typing import Optional
from types import SimpleNamespace

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.generics.type_strings import resolve_type_from_string
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


def _split_type_args(args: str) -> list[str]:
    """Split an interned type-argument list on its TOP-LEVEL commas."""
    parts, depth, current = [], 0, ""
    for ch in args:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


class TypeQueries:
    """Resolves and classifies types for the checker. Reads `tables`, nothing else."""

    def __init__(self, tables=None) -> None:
        """Hold Pass 0's tables -- the sole authority for a named type's contents."""
        self.tables = tables

    def resolve_named(self, ty: Optional[Type]):
        """Resolve an `UnknownType` against the struct/enum tables; identity otherwise."""
        if not isinstance(ty, UnknownType) or self.tables is None:
            return ty
        structs = getattr(getattr(self.tables, "structs", None), "by_name", None) or {}
        enums = getattr(getattr(self.tables, "enums", None), "by_name", None) or {}
        return structs.get(ty.name) or enums.get(ty.name) or ty

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
        if isinstance(ty, StructType) and ty.name.startswith("Own<") and ty.fields:
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
        if isinstance(ty, GenericTypeRef):
            return ty.base_name in ("List", "HashMap")
        name = getattr(ty, "name", None)
        return isinstance(name, str) and (name.startswith("List<") or name.startswith("HashMap<"))

    def element_type(self, ty: Optional[Type]) -> Optional[Type]:
        """What a `.get()` on a receiver of type `ty` reads out."""
        ty = self.resolve_named(ty)
        if isinstance(ty, ReferenceType):
            ty = self.resolve_named(ty.referenced_type)
        if isinstance(ty, (DynamicArrayType, ArrayType)):
            return ty.base_type
        if isinstance(ty, GenericTypeRef):
            if ty.base_name in ("List", "Own") and ty.type_args:
                return ty.type_args[0]
            if ty.base_name == "HashMap" and len(ty.type_args) == 2:
                return ty.type_args[1]
        # An interned StructType's NAME carries its type arguments and IS its identity
        # (#240), so reading them back out of it is the supported route. The angle
        # brackets are the internal spelling on purpose.
        if isinstance(ty, StructType):
            if ty.name.startswith("List<"):
                return self.type_from_name(ty.name[len("List<"):-1])
            if ty.name.startswith("HashMap<"):
                args = _split_type_args(ty.name[len("HashMap<"):-1])
                return self.type_from_name(args[1]) if len(args) == 2 else None
        return self.own_payload(ty)

    def type_from_name(self, type_str: str) -> Optional[Type]:
        """Resolve one interned type-argument spelling back to a `Type`."""
        if self.tables is None:
            return None
        adapter = SimpleNamespace(
            struct_table=getattr(self.tables, "structs", SimpleNamespace(by_name={})),
            enum_table=getattr(self.tables, "enums", SimpleNamespace(by_name={})),
        )
        try:
            return resolve_type_from_string(type_str, adapter)
        except Exception:
            return None

    def type_class(self, ty: Optional[Type]) -> TypeClass:
        """Classify a type as PLAIN or MOVE, resolving named types first."""
        return type_class_of(ty, self.resolve_named)

    def type_class_of_source(self, state: Optional[BorrowState],
                             ty: Optional[Type]) -> TypeClass:
        """Classify the SOURCE of a consuming use, applying option B."""
        if state is not None and state.owns_no_heap:
            return TypeClass.PLAIN
        return self.type_class(ty)
