"""The derive pass: auto-derived hash() and clone() for every struct and enum."""

from typing import Iterable, Optional, Set

from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.passes.collect.utils import types_to_walk
from sushi_lang.semantics.generics.hashing import register_hash_if_hashable
from sushi_lang.semantics.generics.cloning import (
    register_struct_clone_method, register_enum_clone_method)
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.typesys import (
    ArrayType, DynamicArrayType, EnumType, StructType, Type)


def derive_for_struct(struct_type: StructType, derived: DerivedMethodTable) -> None:
    """The derived pair for ONE struct, for a seam that interns after this pass ran.

    The pass walks both tables once, before typecheck. A producer that mints a type
    behind it calls this, so the pair arrives with the type and not from a later
    whole-table walk that a program may never drive (#720, #730).
    """
    register_hash_if_hashable(struct_type, derived)
    register_struct_clone_method(struct_type, derived)


def derive_for_enum(enum_type: EnumType, derived: DerivedMethodTable) -> None:
    """The derived pair for ONE enum, for a seam that interns after this pass ran.

    The `Result` and `Maybe` intern seams are the callers. A type with no variants YET
    stays outside: the gate below reads the variants, and an empty shell has none (#730).
    """
    register_hash_if_hashable(enum_type, derived)
    register_enum_clone_method(enum_type, derived)


def register_all_struct_hashes(struct_table: StructTable,
                               derived: DerivedMethodTable,
                               only: Optional[Iterable[str]] = None) -> None:
    """Register hash() for every struct a derived hash can read.

    `only` narrows the run to the named entries; `None` is the whole table. The pass
    walks it whole once, and the late-interning seam names what it interned (#676).
    """
    for struct_type in types_to_walk(struct_table, only):
        register_hash_if_hashable(struct_type, derived)


def register_all_enum_hashes(enum_table: EnumTable, derived: DerivedMethodTable,
                             only: Optional[Iterable[str]] = None) -> None:
    """Register hash() for every enum a derived hash can read."""
    for enum_type in types_to_walk(enum_table, only):
        register_hash_if_hashable(enum_type, derived)


def collect_array_types(struct_table: StructTable, enum_table: EnumTable,
                        only: Optional[Iterable[str]] = None) -> Set[Type]:
    """Every array type a struct field or an enum payload names."""
    array_types: Set[Type] = set()

    def note(ty: Type) -> None:
        if isinstance(ty, (ArrayType, DynamicArrayType)):
            array_types.add(ty)

    for struct_type in types_to_walk(struct_table, only):
        for _field_name, field_type in struct_type.fields:
            note(field_type)

    for enum_type in types_to_walk(enum_table, only):
        for variant in enum_type.variants:
            for assoc_type in variant.associated_types:
                note(assoc_type)

    return array_types


def register_all_array_hashes(struct_table: StructTable, enum_table: EnumTable,
                              derived: DerivedMethodTable,
                              only: Optional[Iterable[str]] = None) -> None:
    """Register hash() for every array type a derived hash can read."""
    for array_type in collect_array_types(struct_table, enum_table, only):
        register_hash_if_hashable(array_type, derived)


def register_all_clones(struct_table: StructTable, enum_table: EnumTable,
                        derived: DerivedMethodTable,
                        only: Optional[Iterable[str]] = None) -> None:
    """Auto-derive clone() for every struct and enum (#134).

    No ordering constraint -- the clone emitter resolves nested and recursive types at
    emission time -- so a flat walk over both tables is enough.
    """
    for struct_type in types_to_walk(struct_table, only):
        register_struct_clone_method(struct_type, derived)

    for enum_type in types_to_walk(enum_table, only):
        register_enum_clone_method(enum_type, derived)
