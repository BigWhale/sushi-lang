"""The entry unit of a compilation is picked by ONE rule (#736).

The rule is "the first unit with an AST that says `is_entry`, else the first unit with
an AST", and the analyzer spelled it twice: once to answer the AST a monomorphized
extension's lifted lambdas belong to, and once to answer the unit a generic-target perk
implementation goes home to. Both cite the same reason -- `generics/synthesis.py` names
the entry unit, so a lifted or a synthesized body belongs to the unit the compiler was
pointed at, and not to whichever unit the compilation order puts first.

The two copies agreed, so nothing was wrong. This gate holds both of them, in the
spelling they had before they merged, and asks the one rule to answer what each answered.
A change to the rule that moves one caller and not the other fails here.

`generics/synthesis.py` is the THIRD reader and it is NOT this rule. It tries the home
unit of the instance first, and where the fallback of this rule scans for the first unit
with an AST, that one reads `units[0]` alone. The difference is deliberate there and is
recorded at that function.
"""
from __future__ import annotations

from pathlib import Path

from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.units import Unit


def _unit(name: str, has_ast: bool, is_entry: bool = False) -> Unit:
    """A unit that carries nothing but what the rule reads."""
    ast = None
    if has_ast:
        from sushi_lang.semantics.ast import Program
        from sushi_lang.internals.report import Span
        ast = Program(loc=Span(line=1, col=1, end_line=1, end_col=1),
                      uses=[], constants=[], structs=[], enums=[], perks=[],
                      functions=[], extensions=[], generic_extensions=[],
                      perk_impls=[])
    return Unit(name=name, file_path=Path(f"{name}.sushi"), ast=ast,
                dependencies=[], public_symbols={}, is_entry=is_entry)


def _old_lift_target(compilation_order):
    """`_lift_target`, as it stood before the merge: the entry unit's AST."""
    lift_target = next(
        (u.ast for u in compilation_order
         if u.ast is not None and getattr(u, "is_entry", False)),
        None)
    if lift_target is None:
        lift_target = next(
            (u.ast for u in compilation_order if u.ast is not None), None)
    return lift_target


def _old_entry(compilation_order):
    """The copy inside `_monomorphize_generic_perk_impls`: the entry unit itself."""
    entry = next((u for u in compilation_order
                  if u.ast is not None and getattr(u, "is_entry", False)), None)
    if entry is None:
        entry = next((u for u in compilation_order if u.ast is not None), None)
    return entry


# Every shape a compilation order can have at these two call sites. The name says what
# a failure means.
ORDERS: list[tuple[str, list[Unit]]] = [
    ("the entry unit is not first", [_unit("dep", True), _unit("main", True, True)]),
    ("the entry unit is first", [_unit("main", True, True), _unit("dep", True)]),
    ("no unit says it is the entry", [_unit("dep", True), _unit("other", True)]),
    ("the first unit has no AST", [_unit("stub", False), _unit("main", True, True)]),
    ("no AST, and no entry either", [_unit("stub", False), _unit("dep", True)]),
    ("no unit has an AST", [_unit("stub", False), _unit("other", False)]),
    ("nothing to compile", []),
]


def test_one_rule_answers_what_both_copies_answered():
    """The merged rule answers the unit one copy answered and the AST the other did."""
    for what, order in ORDERS:
        answered = SemanticAnalyzer._entry_unit(order)

        assert answered is _old_entry(order), what
        ast = answered.ast if answered is not None else None
        assert ast is _old_lift_target(order), what


def test_the_entry_flag_beats_the_order():
    """The reason the rule exists: a position in the order is not a home."""
    order = [_unit("dep", True), _unit("main", True, True)]

    answered = SemanticAnalyzer._entry_unit(order)

    assert answered is not None
    assert answered.name == "main"


def test_a_unit_with_no_ast_is_never_the_answer():
    """Every caller reads `.ast` off the answer, so a unit without one is no home."""
    order = [_unit("stub", False), _unit("dep", True)]

    answered = SemanticAnalyzer._entry_unit(order)

    assert answered is not None
    assert answered.name == "dep"
