"""The resolve pass reads the ONE type resolver, and it does no work twice.

"Resolve a written type to its table entry" had two implementations with complementary
blind spots (#678): the pass's own copy had no `FunctionType` arm and rebuilt every array
type unconditionally, so a struct field of function type kept the names the builder wrote
and an array-bearing struct had its frozen `fields` tuple rewritten on every run.
"""
from __future__ import annotations

import inspect

import pytest

from sushi_lang.semantics.passes import resolve as resolve_pass
from sushi_lang.semantics.passes.collect import EnumTable, StructTable
from sushi_lang.semantics.typesys import StructType, UnknownType


def test_a_function_typed_field_resolves_the_names_it_spells(analyze_program):
    """A `fn(Node) -> i32` field names `Node`, and the field must hold the table entry."""
    analysis = analyze_program(
        """
struct Node:
    i32 v

struct Sink:
    fn(Node) -> i32 handler

fn main() i32:
    return Result.Ok(0)
"""
    )
    sink = analysis.analyzer.tables.structs.by_name["Sink"]
    handler = dict(sink.fields)["handler"]

    unresolved = [p for p in handler.param_types if isinstance(p, UnknownType)]
    assert not unresolved, (
        f"a function-typed field kept unresolved parameter names: {unresolved}. "
        "Two spellings of one interned name is the hazard the intern seam documents."
    )
    assert not isinstance(handler.err_type, UnknownType), (
        f"a function-typed field kept an unresolved error arm: {handler.err_type}"
    )


def test_an_array_field_is_not_rebuilt_by_a_second_run(analyze_program):
    """The pass runs again at every late intern; an unchanged field must stay the same object."""
    analysis = analyze_program(
        """
struct Inner:
    i32 v

struct Holder:
    Inner[] many
    Inner[3] fixed
    i32[] plain

fn main() i32:
    return Result.Ok(0)
"""
    )
    analyzer = analysis.analyzer
    before = analyzer.tables.structs.by_name["Holder"].fields

    resolve_pass.resolve_struct_field_types(analyzer.tables.structs, analyzer.tables.enums)

    after = analyzer.tables.structs.by_name["Holder"].fields
    rebuilt = [name for (name, was), (_, now) in zip(before, after, strict=True)
               if was is not now]
    assert not rebuilt, (
        f"a second run rebuilt these field types: {rebuilt}. An array type was rebuilt "
        "unconditionally, so every array-bearing struct was rewritten on every run."
    )
    assert before is after, "the frozen fields tuple was replaced although nothing changed"


class _CountingSig:
    """A constant record that counts how often the pass writes its declared type."""

    def __init__(self, declared):
        self._declared = declared
        self.writes = 0

    @property
    def const_type(self):
        return self._declared

    @const_type.setter
    def const_type(self, value):
        self._declared = value
        self.writes += 1


class _Constants:
    """The two views `ConstantTable` keeps, with nothing else the pass reads."""

    def __init__(self, by_name, by_unit):
        self.by_name = by_name
        self.by_unit = by_unit


def _tables_with_handle():
    structs = StructTable()
    handle = StructType(name="Handle", fields=())
    structs.by_name["Handle"] = handle
    return structs, EnumTable(), handle


def test_a_constant_in_both_views_is_visited_once():
    """`ConstantTable.declare` files ONE record in both views; the pass must read it once."""
    structs, enums, _handle = _tables_with_handle()
    sig = _CountingSig(UnknownType("Handle"))
    constants = _Constants({"OUT": sig}, {"main": {"OUT": sig}})

    resolve_pass.resolve_constant_types(constants, structs, enums)

    assert sig.writes == 1, (
        f"the pass wrote one constant's type {sig.writes} times. It walks `by_name` and "
        "then `by_unit`, and both views hold the same record."
    )


def test_a_constant_only_one_unit_declares_is_still_resolved():
    """`by_name` is FIRST-wins, so a shadowed name lives in `by_unit` alone."""
    structs, enums, handle = _tables_with_handle()
    first = _CountingSig(UnknownType("Handle"))
    shadowed = _CountingSig(UnknownType("Handle"))
    constants = _Constants(
        {"SCRATCH": first},
        {"a": {"SCRATCH": first}, "b": {"SCRATCH": shadowed}},
    )

    resolve_pass.resolve_constant_types(constants, structs, enums)

    assert shadowed.const_type is handle, (
        "a constant that only the second unit declares was never resolved: `by_name` is "
        "not a superset of `by_unit`."
    )


def test_the_pass_holds_no_resolver_of_its_own():
    """One seam. A private copy is what grew the two blind spots."""
    assert not hasattr(resolve_pass, "_resolve_type"), (
        "passes/resolve.py has a private type resolver again"
    )
    source = inspect.getsource(resolve_pass)
    for rebuilt in ("ArrayType(", "DynamicArrayType(", "GenericTypeRef("):
        assert rebuilt not in source, (
            f"passes/resolve.py builds a {rebuilt[:-1]} itself; the shared resolver owns that"
        )


def test_the_backend_keeps_no_homonym_of_the_semantics_resolver():
    """Two `resolve_unknown_type`s with different contracts read alike at a call site."""
    from sushi_lang.backend.types.core import resolution

    assert not hasattr(resolution, "resolve_unknown_type"), (
        "backend/types/core/resolution.py spells `resolve_unknown_type` again. The "
        "semantics one returns the type unchanged on a miss; this one raises CE0020, and "
        "a reader cannot tell them apart by the name."
    )


def test_the_backend_keeps_no_homonym_of_the_semantics_generic_resolver():
    """The second homonym of the pair: one name, two opposite contracts (#717)."""
    from sushi_lang.backend.types.core import resolution

    assert not hasattr(resolution, "resolve_generic_type_ref"), (
        "backend/types/core/resolution.py spells `resolve_generic_type_ref` again. The "
        "semantics one returns the type unchanged on a miss; this one raises CE0045, and "
        "a reader cannot tell them apart by the name."
    )


def test_the_backend_seam_says_a_generic_instance_is_required():
    """The name states the contract: the caller is about to emit, so a miss is a fault."""
    from sushi_lang.backend.types.core.resolution import require_generic_instance
    from sushi_lang.internals.errors import InternalCompilerError
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import BuiltinType

    with pytest.raises(InternalCompilerError):
        require_generic_instance(
            GenericTypeRef(base_name="Box", type_args=(BuiltinType.I32,)), {}, {})

    assert require_generic_instance(UnknownType("Node"), {}, {}) is None, (
        "a type that is not a generic reference is not this seam's business"
    )
