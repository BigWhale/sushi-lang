"""One WRITTEN declaration is yielded once, however many times it is instantiated.

A monomorphized generic FUNCTION goes home to the unit that declared its template, and
so does a monomorphized generic PERK IMPLEMENTATION. `ast_walk` is the one walk over a
unit's declarations, so it is the one place that can tell a copy from the declaration a
person wrote; without that, a rule over the walk reports one source line once for each
instantiation (#657).

The copies are NOT a `.sushi` fixture's to catch. A copy carries its template's spans, so
every diagnostic it raises is byte-identical to the template's and the report channel
collapses the repeats. The fault is therefore pinned here, at the seam.
"""
from __future__ import annotations

from sushi_lang.semantics.ast_walk import bodied, declarations

# One perk implementation and one generic function, each instantiated two times.
SRC = """perk Show:
    fn show() string

struct Box@(T):
    T item

extend Box@(T) with Show:
    fn show() string:
        return "box"

fn twice@(T)(nom T v) string:
    return Result.Ok("x")

fn main() i32:
    let Box@(i32) a = Box(1)
    let Box@(string) b = Box("s")
    println(a.show())
    println(b.show())
    println(twice(nom 1).realise("?"))
    println(twice(nom "s").realise("?"))
    return Result.Ok(0)
"""


def _analyzed(analyze_program):
    result = analyze_program(SRC)
    program = result.program
    # The gate is vacuous unless the copies are really there.
    assert len(program.perk_impls) == 2, "the two instantiations must be monomorphized"
    return program


def test_the_walk_yields_one_written_perk_implementation(analyze_program):
    program = _analyzed(analyze_program)
    impls = [node for kind, node in declarations(program)
             if kind == "perk implementation"]
    assert len(impls) == 1


def test_the_walk_yields_one_written_perk_method(analyze_program):
    program = _analyzed(analyze_program)
    # One from the contract, one from the implementation the source wrote.
    methods = [node for kind, node in declarations(program) if kind == "perk method"]
    assert len(methods) == 2


def test_the_walk_yields_one_written_generic_function(analyze_program):
    program = _analyzed(analyze_program)
    names = [node.name for kind, node in declarations(program) if kind == "function"]
    assert names == ["twice", "main"]


def test_the_bodied_walk_yields_one_body_for_each_written_declaration(analyze_program):
    program = _analyzed(analyze_program)
    assert [node.name for node in bodied(program)] == ["twice", "main", "show"]


def test_the_predicate_tells_a_copy_from_the_declaration(analyze_program):
    from sushi_lang.semantics.ast_walk import is_written

    program = _analyzed(analyze_program)
    assert all(not is_written(impl) for impl in program.perk_impls)
    assert all(is_written(impl) for impl in program.generic_perk_impls)
