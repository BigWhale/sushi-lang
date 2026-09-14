"""`Hashable` is predefined, and every type with a derived hash satisfies it (#696).

The fixtures under tests/perks/hashable/ pin the exit codes. A directive reads a code as
a SUBSTRING, so this file pins the code SET per program, and the shape of the perk the
compiler predefines beside `Drop`.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.passes.collect import PerkImplementationTable
from sushi_lang.semantics.passes.collect.perks import PerkCollector
from sushi_lang.semantics.typesys import BuiltinType

MAIN = """
fn fingerprint@(T: Hashable)(T value) u64:
    return Result.Ok(value.hash())

fn main() i32:
    {setup}let u64 h = fingerprint({value}).realise(0 as u64)
    println(h)
    return Result.Ok(0)
"""

FFI = """
unsafe external "C" as libc because "a ptr field is the one field the compiler cannot hash":
    fn strlen(string s) i64 = "strlen"
"""


def _codes(reporter) -> list[str]:
    return sorted({item.code for item in reporter.items})


def _program(decls: str, value: str, setup: str = "") -> str:
    return decls + MAIN.format(setup=setup, value=value)


def test_hashable_is_predefined_beside_drop(analyze_program):
    analysis = analyze_program("fn main() i32:\n    return Result.Ok(0)\n")
    perks = analysis.analyzer.perks
    for name in (PerkCollector.DROP_PERK, PerkCollector.HASHABLE_PERK):
        assert perks.get(name) is not None, f"'{name}' is not predefined"
        assert perks.files.get(name) is None, "a predefined perk has no declaring file"
    hashable = perks.get(PerkCollector.HASHABLE_PERK)
    assert hashable.is_public
    (method,) = hashable.methods
    assert method.name == "hash"
    assert method.params == []
    assert method.ret == BuiltinType.U64
    assert method.self_mode is None
    assert method.err_type is None


@pytest.mark.parametrize("decl", [
    "perk Hashable:\n    fn hash() u64\n",
    "perk Drop:\n    fn drop(poke self) ~\n",
])
def test_a_user_declaration_collides_with_the_predefined_perk(analyze, decl):
    """One answer for both predefined perks: the duplicate-definition code."""
    reporter = analyze(decl + "\nfn main() i32:\n    return Result.Ok(0)\n")
    assert _codes(reporter) == ["CE4001"]


@pytest.mark.parametrize("decls, value", [
    ("", "42"),
    ("", '"Mostly Harmless"'),
    ("struct Point:\n    i32 x\n    i32 y\n", "Point(1, 2)"),
    ("enum Shape:\n    Circle(i32)\n    Rect(i32, i32)\n", "Shape.Circle(3)"),
    # A field that names a struct is a WRITTEN name until the resolve pass, and the
    # constraint is checked before that pass runs.
    ("struct Point:\n    i32 x\n\nstruct Segment:\n    Point a\n    Point b\n",
     "Segment(Point(1), Point(2))"),
    ("struct Bag:\n    List@(i32) items\n", "Bag(List.new())"),
])
def test_a_type_with_a_derived_hash_satisfies_the_constraint(analyze, decls, value):
    reporter = analyze(_program(decls, value))
    assert _codes(reporter) == [], "\n".join(str(item) for item in reporter.items)


def test_a_generic_instantiation_with_a_derived_hash_satisfies_the_constraint(analyze):
    """Bound first: a generic call fed an inferred construction is CE2060 on its own."""
    reporter = analyze(_program("struct Box@(T):\n    T item\n", "b",
                                setup="let Box@(i32) b = Box(9)\n    "))
    assert _codes(reporter) == [], "\n".join(str(item) for item in reporter.items)


@pytest.mark.parametrize("decls, value", [
    ("use <collections/hashmap>\n\nstruct Index:\n    HashMap@(i32, string) slots\n",
     "Index(HashMap.new())"),
    ("struct Hook:\n    fn(i32) -> i32 f\n", "Hook(|i32 a| a + 1)"),
    (FFI + "struct Handle:\n    ptr[] slots\n", "Handle(from([]))"),
])
def test_a_type_the_derive_pass_refuses_does_not(analyze, decls, value):
    reporter = analyze(_program(decls, value))
    assert _codes(reporter) == ["CE4006"]


def test_an_explicit_implementation_is_the_override(analyze):
    """A type the derive pass refuses satisfies the constraint through `extend ... with`."""
    decls = FFI + (
        "struct Handle:\n    ptr[] slots\n\n"
        "extend Handle with Hashable:\n    fn hash() u64:\n        return 7 as u64\n"
    )
    reporter = analyze(_program(decls, "Handle(from([]))"))
    assert _codes(reporter) == []


def test_the_magic_list_is_gone():
    """The derive pass's predicate is the one answer; no table of twelve names beside it."""
    assert not hasattr(PerkCollector, "register_synthetic_impls")
    assert not hasattr(PerkImplementationTable, "register_synthetic")
