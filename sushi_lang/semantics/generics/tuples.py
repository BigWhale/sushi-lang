"""The tuple type: an interned struct whose base name no user can write.

A tuple `(i32, string)` is the `StructType` `$Tuple<i32, string>` with the fields `0` and
`1` (docs/design/tuples.md). `NAME` is `CNAME`, so a `$` cannot start a written name, and a
user `struct Tuple` or `struct _Tuple@(A, B)` is a different type. `intern_tuple` is the ONE
producer of the instance; the elements are resolved RECURSIVELY, so one interned name has
one resolution depth. A tuple has a variable arity, so it never goes through
`monomorphize_struct`, which refuses a count that differs from a declared one.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Tuple

from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import ReferenceType, StructType, Type

TUPLE_BASE = "$Tuple"


def tuple_ref(elements: Iterable[Type]) -> GenericTypeRef:
    """The written form of a tuple type, before the tables can intern it."""
    return GenericTypeRef(base_name=TUPLE_BASE, type_args=tuple(elements))


def is_tuple_type(ty: Optional[Type]) -> bool:
    """Is `ty` a tuple, interned or written? A reference to one counts as its referent."""
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if isinstance(ty, StructType):
        return ty.generic_base == TUPLE_BASE
    return isinstance(ty, GenericTypeRef) and ty.base_name == TUPLE_BASE


def tuple_elements(ty: Type) -> Tuple[Type, ...]:
    """The element types of a tuple, in order."""
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if isinstance(ty, GenericTypeRef):
        return tuple(ty.type_args)
    if isinstance(ty, StructType):
        return tuple(field_type for _, field_type in ty.fields)
    return ()


def element_name(index: int) -> str:
    """The field name of element `index`: `t.0` reads the field `0`."""
    return str(index)


def intern_tuple(struct_table: Any, enum_table: Any, elements: Iterable[Type]) -> StructType:
    """Intern the tuple of `elements` in `struct_table`, or answer the one already there.

    `enum_table` resolves an enum element and carries the compilation's derived-method
    table. An abstract tuple, whose elements still name a template's type parameter, is
    handed back and kept OUT of the table, as `intern_wrapper_enum` keeps an abstract
    `Result` out; so is a tuple whose element names an instance not built yet.
    """
    from sushi_lang.semantics.generics.results import names_an_unbuilt_instance
    from sushi_lang.semantics.passes.derive import derive_for_struct
    from sushi_lang.semantics.type_predicates import is_abstract_type
    from sushi_lang.semantics.type_resolution import resolve_type_recursively

    structs = struct_table.by_name if struct_table is not None else {}
    enums = enum_table.by_name if enum_table is not None else {}
    resolved = tuple(
        resolve_type_recursively(intern_tuples_in(t, struct_table, enum_table), structs, enums)
        for t in elements)
    name = interned_name(TUPLE_BASE, resolved)

    existing = structs.get(name)
    if existing is not None:
        return existing

    interned = StructType(
        name=name,
        fields=tuple((element_name(i), t) for i, t in enumerate(resolved)),
        generic_base=TUPLE_BASE,
        generic_args=resolved,
    )
    if struct_table is None or any(
            is_abstract_type(t, structs, enums, opaque_bound=struct_table.admits_opaque)
            or names_an_unbuilt_instance(t, structs, enums)
            for t in resolved):
        return interned

    structs[name] = interned
    struct_table.order.append(name)
    if enum_table is not None:
        derive_for_struct(interned, enum_table.derived)
    return interned


def display_tuple(elements: Iterable[str]) -> str:
    """`(i32, string)`: the one spelling a user reads."""
    return f"({', '.join(elements)})"


def intern_tuples_in(ty: Type, struct_table: Any, enum_table: Any) -> Type:
    """`ty` with every written tuple in it interned, the innermost first."""
    from sushi_lang.semantics.type_walk import map_named_types

    def intern(held: Type) -> Type:
        if isinstance(held, GenericTypeRef) and held.base_name == TUPLE_BASE:
            return intern_tuple(struct_table, enum_table, held.type_args)
        return held

    mapped = map_named_types(ty, intern)
    return ty if mapped is None else mapped


def holds_written_tuple(ty: Optional[Type]) -> bool:
    """Does `ty` spell a tuple anywhere that is not interned yet?"""
    from sushi_lang.semantics.type_walk import walk_named_types

    return any(isinstance(held, GenericTypeRef) and held.base_name == TUPLE_BASE
               for held in walk_named_types(ty, through_declarations=False))
