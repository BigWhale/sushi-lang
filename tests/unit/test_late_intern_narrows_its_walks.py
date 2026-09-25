"""A late intern resolves and derives what it interned, not the whole table.

The late-interning seam runs INSIDE a fixpoint loop: a call site that solves a
method-level type argument can name an instantiation nothing else names, and the
instance has to be resolved, size-checked and derived after those passes are over.
Re-running the two resolve entry points and the four derive entry points over both
whole tables costs O(all types) for each instance, and the passes already did that work
once (#676).

`_check_monomorphized_extensions(..., only=None)` is the shape: `None` means the whole
table, and a name list means those names alone.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.passes import derive as derive_pass
from sushi_lang.semantics.passes import resolve as resolve_pass
from sushi_lang.semantics.passes.collect import EnumTable, StructTable
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.typesys import (
    BuiltinType, EnumType, EnumVariantInfo, StructType, UnknownType)


def _tables():
    """Two structs and two enums, each naming the other kind by an unresolved name."""
    structs, enums = StructTable(), EnumTable()
    for name in ("Old", "New"):
        structs.by_name[name] = StructType(
            name=name, fields=(("tag", UnknownType(name=f"{name}Tag")),))
        structs.order.append(name)
    for name in ("OldTag", "NewTag"):
        enums.by_name[name] = EnumType(
            name=name,
            variants=(EnumVariantInfo(name="One",
                                      associated_types=(UnknownType(name="Old"),)),))
        enums.order.append(name)
    return structs, enums


def test_resolve_of_struct_fields_takes_only():
    structs, enums = _tables()
    resolve_pass.resolve_struct_field_types(structs, enums, only=["New"])
    assert isinstance(structs.by_name["New"].fields[0][1], EnumType)
    assert isinstance(structs.by_name["Old"].fields[0][1], UnknownType)


def test_resolve_of_enum_variants_takes_only():
    structs, enums = _tables()
    resolve_pass.resolve_enum_variant_types(structs, enums, only=["NewTag"])
    assert isinstance(enums.by_name["NewTag"].variants[0].associated_types[0],
                      StructType)
    assert isinstance(enums.by_name["OldTag"].variants[0].associated_types[0],
                      UnknownType)


def _plain_tables():
    """Two hashable structs and two hashable enums, nothing unresolved."""
    structs, enums = StructTable(), EnumTable()
    for name in ("Old", "New"):
        structs.by_name[name] = StructType(name=name,
                                           fields=(("n", BuiltinType.I32),))
        structs.order.append(name)
        enums.by_name[f"{name}Tag"] = EnumType(
            name=f"{name}Tag",
            variants=(EnumVariantInfo(name="One", associated_types=()),))
        enums.order.append(f"{name}Tag")
    return structs, enums


@pytest.mark.parametrize("register, kept, skipped", [
    ("register_all_struct_hashes", "New", "Old"),
    ("register_all_clones", "New", "Old"),
])
def test_derive_over_structs_takes_only(register, kept, skipped):
    structs, enums = _plain_tables()
    derived = DerivedMethodTable()
    entry = getattr(derive_pass, register)
    if register == "register_all_clones":
        entry(structs, enums, derived, only=[kept])
    else:
        entry(structs, derived, only=[kept])
    method = "clone" if register == "register_all_clones" else "hash"
    assert derived.get_method(structs.by_name[kept], method) is not None
    assert derived.get_method(structs.by_name[skipped], method) is None


def test_derive_over_enums_takes_only():
    structs, enums = _plain_tables()
    derived = DerivedMethodTable()
    derive_pass.register_all_enum_hashes(enums, derived, only=["NewTag"])
    assert derived.get_method(enums.by_name["NewTag"], "hash") is not None
    assert derived.get_method(enums.by_name["OldTag"], "hash") is None


def test_the_array_hash_collector_takes_only():
    structs, enums = _plain_tables()
    assert derive_pass.collect_array_types(structs, enums, only=["New"]) == set()


# Two solved method-level type arguments, so `List<bool>` and `List<string>` are named
# nowhere else and are interned after `resolve`, `finite-types` and `derive` are over.
LATE = """extend List@(T) mapv@(U)(fn(T) -> U f) List@(U) | StdError:
    let List@(U) out = List.new()
    foreach(x in self.iter()):
        out.push(f(x)??)
    return Result.Ok(out)

fn to_bool(i32 x) bool:
    return Result.Ok(x > 1)

fn to_str(i32 x) string:
    return Result.Ok("n{x}")

fn main() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    match xs.mapv(to_bool):
        Result.Ok(bs) -> println("{bs.len()}")
        Result.Err(_) -> println("err")
    match xs.mapv(to_str):
        Result.Ok(ss) -> println("{ss.len()}")
        Result.Err(_) -> println("err")
    return Result.Ok(0)
"""

# Every entry point the late interner re-runs, with the table each one walks.
WALKS = (
    (resolve_pass, "resolve_struct_field_types", 0),
    (resolve_pass, "resolve_enum_variant_types", 1),
    (derive_pass, "register_all_struct_hashes", 0),
    (derive_pass, "register_all_enum_hashes", 0),
    (derive_pass, "register_all_array_hashes", 0),
    (derive_pass, "register_all_clones", 0),
)


def test_a_late_intern_walks_only_what_it_interned(analyze_program, monkeypatch):
    """Every resolve and derive run a late intern makes names what it interned.

    The whole-program `resolve` and `derive` passes still walk both tables whole; only
    the runs INSIDE the late-interning seam are measured here.
    """
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer

    seen: list[tuple[str, object]] = []
    inside = False

    def spy(module, name):
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            if inside:
                seen.append((name, kwargs.get("only")))
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    for module, name, _table in WALKS:
        spy(module, name)

    late = SemanticAnalyzer._intern_generic_type_refs

    def watched(self, monomorphizer, types):
        nonlocal inside
        inside = True
        try:
            return late(self, monomorphizer, types)
        finally:
            inside = False

    monkeypatch.setattr(SemanticAnalyzer, "_intern_generic_type_refs", watched)

    analyze_program(LATE)

    assert seen, "the source must drive at least one late intern"
    whole_table = [name for name, only in seen if only is None]
    assert not whole_table, (
        f"a late intern re-ran {sorted(set(whole_table))} over the whole table")
