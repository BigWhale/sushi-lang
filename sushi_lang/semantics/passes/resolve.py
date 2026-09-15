"""The resolve pass: every struct field, enum variant and constant type made concrete."""

from typing import Callable, Iterable, Optional, Set

from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.passes.collect.utils import types_to_walk
from sushi_lang.semantics.type_resolution import resolve_type_recursively
from sushi_lang.semantics.typesys import StructType, EnumType, Type


def table_resolver(struct_table: StructTable,
                   enum_table: EnumTable) -> Callable[[Type], Type]:
    """One resolver over the tables, for a reader that runs before this pass.

    The `Hashable` constraint check in the monomorphize pass walks a struct whose
    fields still spell their types (#696). It reads the two tables LIVE, because
    monomorphization publishes instances while it runs and a copy taken earlier
    would miss them.
    """
    structs, enums = struct_table.by_name, enum_table.by_name
    return lambda ty: resolve_type_recursively(ty, structs, enums)


def resolve_struct_field_types(
    struct_table: StructTable,
    enum_table: EnumTable,
    only: Optional[Iterable[str]] = None
) -> None:
    """Resolve UnknownType references in struct fields to concrete types.

    `only` narrows the run to the named entries; `None` is the whole table. The pass
    itself walks it whole, and the late-interning seam names what it interned (#676).
    """
    structs, enums = struct_table.by_name, enum_table.by_name

    for struct_type in types_to_walk(struct_table, only):
        if not isinstance(struct_type, StructType):
            continue  # Skip if not a regular StructType

        resolved_fields = []
        needs_update = False
        for field_name, field_type in struct_type.fields:
            resolved_type = resolve_type_recursively(field_type, structs, enums)
            resolved_fields.append((field_name, resolved_type))
            if resolved_type is not field_type:
                needs_update = True

        if needs_update:
            object.__setattr__(struct_type, 'fields', tuple(resolved_fields))


def resolve_enum_variant_types(
    struct_table: StructTable,
    enum_table: EnumTable,
    only: Optional[Iterable[str]] = None
) -> None:
    """Resolve UnknownType references in enum variant associated types to concrete types.

    `only` narrows the run the way `resolve_struct_field_types` does.
    """
    structs, enums = struct_table.by_name, enum_table.by_name

    for enum_type in types_to_walk(enum_table, only):
        if not isinstance(enum_type, EnumType):
            continue  # Skip if not a regular EnumType

        needs_update = False
        resolved_variants = []
        for variant in enum_type.variants:
            resolved_assoc_types = []
            variant_needs_update = False
            for assoc_type in variant.associated_types:
                resolved_type = resolve_type_recursively(assoc_type, structs, enums)
                resolved_assoc_types.append(resolved_type)
                if resolved_type is not assoc_type:
                    variant_needs_update = True
                    needs_update = True

            if variant_needs_update:
                from sushi_lang.semantics.typesys import EnumVariantInfo
                resolved_variant = EnumVariantInfo(
                    name=variant.name,
                    associated_types=tuple(resolved_assoc_types)
                )
                resolved_variants.append(resolved_variant)
            else:
                resolved_variants.append(variant)

        if needs_update:
            object.__setattr__(enum_type, 'variants', tuple(resolved_variants))


def resolve_constant_types(constants, struct_table: StructTable,
                           enum_table: EnumTable) -> None:
    """Resolve a constant's DECLARED type on its RECORD, so every reader sees the table entry.

    A constant's type is written by the AST builder before any table exists, so
    `const Handle OUT = ...` collects as `UnknownType("Handle")`. That was invisible
    while every constant was a scalar; a constant of struct type made it visible at
    once, because an extension method resolves on the receiver's type and an
    `UnknownType` matches nothing.

    The DECLARATION keeps the type as written. `sh.Shape` is a qualified name and the
    typecheck pass has to see the qualifier to rule on it (`validate_type_name`); the
    resolved type is written back there, the way a `let`'s is, once the name has been
    checked in the unit that wrote it (#561). Every reader after that pass -- the
    borrow pass, the back end -- sees the resolved type on both.

    Both views are walked, and each RECORD is read once. `ConstantTable.declare` files
    one record in both views, so a plain walk of each resolves most constants twice;
    but `by_name` is FIRST-wins, so the second record of a name that two units both
    declare lives in `by_unit` alone and a walk of `by_name` would miss it.
    """
    if constants is None:
        return

    structs, enums = struct_table.by_name, enum_table.by_name
    seen: Set[int] = set()

    def resolve_one(sig) -> None:
        if id(sig) in seen:
            return
        seen.add(id(sig))
        if sig.const_type is not None:
            sig.const_type = resolve_type_recursively(sig.const_type, structs, enums)

    for sig in constants.by_name.values():
        resolve_one(sig)
    for unit_sigs in constants.by_unit.values():
        for sig in unit_sigs.values():
            resolve_one(sig)
