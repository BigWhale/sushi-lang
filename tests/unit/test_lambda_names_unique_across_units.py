"""Lifted lambda names must be unique across units (#402).

One LambdaLifter is constructed per unit and its counter starts at zero,
while the function and struct tables are global. Two units with one lambda
each both synthesized __lambda_0: the second registration silently did
nothing, and the second unit's closure ran the first unit's body with the
first unit's env layout.
"""
from __future__ import annotations

from pathlib import Path

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.generics.active_generics import reset_active_generics
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
from sushi_lang.compiler.loader import load_unit_recursively
from sushi_lang.semantics.units import UnitManager

HELPER_SRC = (
    "public fn helper_apply() i32:\n"
    "    let i32 a = 100\n"
    "    let fn(i32) -> i32 f = |i32 x| x + a\n"
    "    return Result.Ok(f(1).realise(0))\n"
)

MAIN_SRC = (
    'use "helper"\n'
    "\n"
    "fn main() i32:\n"
    "    let i32 two = 2\n"
    "    let fn(i32) -> i32 g = |i32 x| x * two\n"
    "    let i32 a = helper_apply().realise(0)\n"
    "    let i32 b = g(3).realise(0)\n"
    "    println(\"{a} {b}\")\n"
    "    return Result.Ok(0)\n"
)


def _lifted_names(program) -> list[str]:
    return [fn.name for fn in program.functions if fn.name.startswith("__lambda_")]


def test_two_units_get_distinct_lifted_names(tmp_path: Path):
    (tmp_path / "helper.sushi").write_text(HELPER_SRC, encoding="utf-8")
    (tmp_path / "main.sushi").write_text(MAIN_SRC, encoding="utf-8")

    reporter = Reporter(source=MAIN_SRC, filename="main")
    reset_active_generics()
    get_stdlib_registry()

    unit_manager = UnitManager(root_path=tmp_path, reporter=reporter)
    main_program, _ = parse_to_ast(MAIN_SRC)
    unit = unit_manager.load_unit("main", main_program)
    assert unit is not None
    loaded = {"main"}
    for dep in unit.dependencies:
        assert load_unit_recursively(unit_manager, dep, loaded, reporter)
    unit_manager.build_global_symbol_table()
    order = unit_manager.get_compilation_order()
    assert order is not None and len(order) == 2

    analyzer = SemanticAnalyzer(reporter, filename="main", unit_manager=unit_manager)
    analyzer.check(main_program)
    assert not reporter.has_errors, [d.code for d in reporter.items]

    all_names: list[str] = []
    lambdas_per_unit = []
    for u in order:
        names = _lifted_names(u.ast)
        lambdas_per_unit.append((u.name, names))
        all_names.extend(names)

    assert len(all_names) == 2, (
        f"each unit must keep its own lifted function: {lambdas_per_unit}"
    )
    assert len(set(all_names)) == 2, (
        f"lifted names collide across units: {lambdas_per_unit}"
    )
