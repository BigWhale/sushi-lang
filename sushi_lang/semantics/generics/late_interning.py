"""The one late interner: a generic instance that the typecheck pass names first.

Two table sets use it: the program's, through the analyzer, and the overlay of a
template check (`semantics/template_scope.py`), whose instances hold an opaque type
parameter and must never reach a program table (#1070). One seam, two table sets.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.generics.monomorphize import Monomorphizer
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.typesys import Type


def intern_generic_type_refs(tables: 'SymbolTables', monomorphizer: 'Monomorphizer',
                             reporter: 'Reporter', types: Iterable['Type']) -> None:
    """Monomorphize, resolve, size-check and derive every NEW instantiation `types` name.

    The one late-interning seam: the typecheck pass reaches it through
    `tables.intern_generic_ref` when a call site solves a method-level type
    argument, and the drain reaches it for the copies' bodies.

    Every run here names the instances that are NEW to it. The passes walked both
    tables whole once already, and this seam sits inside a fixpoint loop, so a
    whole-table re-run cost O(all types) for each instance and changed nothing but
    the new names (#676). `names_since` is the one answer to what a table gained.

    ONE window serves resolve, derive and the finite-types walk: this round's own
    instances. A `Result` or a `Maybe` the typecheck pass interns between two rounds
    derives its hash AND its clone at the intern itself (#720), so no later walk has
    to repair it, and an older cycle stopped the analysis already (#677).
    """
    from sushi_lang.semantics.generics.extension_targets import instantiation_key
    from sushi_lang.semantics.generics.tuples import TUPLE_BASE, intern_tuple
    from sushi_lang.semantics.generics.types import GenericTypeRef

    struct_insts: set = set()
    enum_insts: set = set()

    def resolve_ref(ty):
        """A concrete Type for `ty`, queueing what is not interned yet."""
        if not isinstance(ty, GenericTypeRef):
            return ty
        args = tuple(resolve_ref(a) for a in ty.type_args)
        if any(a is None or isinstance(a, GenericTypeRef) for a in args):
            return None
        key = instantiation_key(ty.base_name, args)
        interned = tables.structs.by_name.get(key) or tables.enums.by_name.get(key)
        if interned is not None:
            return interned
        if ty.base_name == TUPLE_BASE:
            return intern_tuple(tables.structs, tables.enums, args)
        if ty.base_name in tables.generic_structs.by_name:
            struct_insts.add((ty.base_name, args))
        elif ty.base_name in tables.generic_enums.by_name:
            enum_insts.add((ty.base_name, args))
        return None

    for ty in types:
        if ty is not None:
            resolve_ref(ty)

    if not struct_insts and not enum_insts:
        return

    from sushi_lang.semantics.passes.finite_types import table_marks
    marks = table_marks(tables.structs, tables.enums)
    monomorphizer.monomorphize_all(tables.generic_enums.by_name, enum_insts)
    monomorphizer.monomorphize_all_structs(tables.generic_structs.by_name, struct_insts)
    settle_new_instances(tables, reporter, marks)


def settle_new_instances(tables: 'SymbolTables', reporter: 'Reporter', marks) -> None:
    """Resolve, size-check and derive every instance the tables gained after `marks`.

    Three callers: the late interner above, the late function request, whose copy can
    name a type for the first time, and the template check, whose cut names instances
    over an opaque type parameter in its overlay.
    """
    from sushi_lang.semantics.passes.finite_types import (
        check_infinite_size_types, names_since)

    # One list for both tables: a type name is one per program, so each table takes
    # the names it holds.
    new_structs, new_enums = names_since(tables.structs, tables.enums, marks)
    interned = [*new_structs, *new_enums]

    from sushi_lang.semantics.passes.resolve import (
        resolve_enum_variant_types, resolve_struct_field_types)
    resolve_struct_field_types(tables.structs, tables.enums, only=interned)
    resolve_enum_variant_types(tables.structs, tables.enums, only=interned)

    # A late-solved instance can hold itself by value, and the whole-program run of
    # finite-types is over: walk it again from the new names alone (#677).
    check_infinite_size_types(tables.structs, tables.enums, reporter, since=marks)

    from sushi_lang.semantics.passes.derive import (
        register_all_array_hashes, register_all_clones,
        register_all_enum_hashes, register_all_struct_hashes)
    register_all_struct_hashes(tables.structs, tables.derived_methods, only=interned)
    register_all_enum_hashes(tables.enums, tables.derived_methods, only=interned)
    register_all_array_hashes(tables.structs, tables.enums, tables.derived_methods,
                              only=interned)
    register_all_clones(tables.structs, tables.enums, tables.derived_methods,
                        only=interned)


def resolved_instantiations(tables: 'SymbolTables', type_instantiations) -> tuple[set, set]:
    """Split collected type instantiations into enums and structs, arguments resolved.

    Type arguments are resolved FIRST. `str(UnknownType("Point"))` and
    `str(StructType("Point"))` are both "Point", so the two spellings mangle to one enum
    name while carrying different payloads -- and EnumType hashes on the name but
    compares on the variants, so the unresolved one hash-matches and compares unequal.
    Resolving here keeps the monomorphized instance and the on-demand intern
    byte-identical.
    """
    from sushi_lang.semantics.generics.tuples import TUPLE_BASE
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    def resolve_args(type_args):
        return tuple(resolve_unknown_type(arg, tables.structs.by_name, tables.enums.by_name)
                     for arg in type_args)

    enum_instantiations = set()
    struct_instantiations = set()
    for base_name, type_args in type_instantiations:
        if base_name in tables.generic_enums.by_name:
            enum_instantiations.add((base_name, resolve_args(type_args)))
        elif base_name in tables.generic_structs.by_name or base_name == TUPLE_BASE:
            struct_instantiations.add((base_name, resolve_args(type_args)))
    return enum_instantiations, struct_instantiations


def copy_signature_types(extend_defs) -> list:
    """The types an extension copy names: its signature and its `let` annotations."""
    from sushi_lang.semantics.generics.monomorphize.functions import let_annotations

    types: list = []
    for extend_def in extend_defs:
        types.append(extend_def.ret)
        types.append(getattr(extend_def, "err_type", None))
        for param in extend_def.params:
            types.append(param.ty)
        types.extend(let_annotations(extend_def.body))
    return [ty for ty in types if ty is not None]


def intern_copy_signatures(tables: 'SymbolTables', monomorphizer: 'Monomorphizer',
                           extend_defs) -> None:
    """Monomorphize every type instantiation an extension copy names (#1146, #1070).

    Two table sets use it. The analyzer's: the instantiate pass collects the signature
    of an EARLY instantiation's copy, and a late instantiation exists only after that
    pass ended, so its copy names types nothing collected -- `Maybe@(Result@(string,
    Bad))` in the `next()` of a `Feed@(string)` that only a generic body reaches. And the
    overlay of a template check, whose check copy names instances over an opaque type
    parameter. The collection's own walk reads a nested instance in one pass. The caller
    settles what this interns (`settle_new_instances`), or `resolve` and `derive` do.
    """
    from sushi_lang.semantics.generics.instantiate.type_collection import (
        collect_type_instantiations)
    from sushi_lang.semantics.type_resolution import TypeResolver

    resolver = TypeResolver(tables.structs.by_name, tables.enums.by_name)
    instantiations: set = set()
    for ty in copy_signature_types(extend_defs):
        collect_type_instantiations(ty, resolver, instantiations)
    if not instantiations:
        return
    enum_insts, struct_insts = resolved_instantiations(tables, instantiations)
    monomorphizer.monomorphize_all(tables.generic_enums.by_name, enum_insts)
    monomorphizer.monomorphize_all_structs(tables.generic_structs.by_name, struct_insts)
