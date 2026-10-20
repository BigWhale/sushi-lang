"""Gate: an error hole carries the codes that it stands for (docs/design/ir.md 7.4).

An error hole is the hole of a program that always has an error: it never stops the
compile under `SUSHI_SHIR_STRICT=1`. When lowering and the typecheck pass disagree, an
error hole could stand in a correct program and hide a fault (ruling of 2026-10-20 on
note 139). So each error hole names the codes of the error that it stands for, and at the
end of the analysis an error hole whose codes no diagnostic offered is the stop code
CE0155. Three rules:

1. Each call of `error_hole` in `semantics/shir/` names one code or more, each a string
   literal that the error registry holds.
2. `unreported_error_holes` answers each error hole whose codes no error offered, and no
   hole with no codes.
3. The analyzer calls `check_error_holes` at the end of the analysis. From the switch to
   SHIR it runs on every compile, with no strict variable: it answers the stop of the
   first error hole whose codes no error offered, and the pipeline reports it when no
   error and no earlier stop came (ruling of 2026-10-20, R-F).

The scan reads source, and rule 2 calls a function over data; nothing starts the compiler.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from sushi_lang.internals.errors import REGISTRY
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.lower_nodes import STRICT_VARIABLE
from sushi_lang.semantics.shir.lowering import check_error_holes, unreported_error_holes
from sushi_lang.semantics.shir.nodes import Hole

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SHIR = ROOT / "semantics" / "shir"
ANALYZER = ROOT / "semantics" / "semantic_analyzer.py"


def _error_hole_calls() -> list[tuple[str, ast.Call]]:
    found = []
    for path in sorted(SHIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found.extend((f"{path.name}:{node.lineno}", node) for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "error_hole")
    return found


def test_every_error_hole_names_registered_codes() -> None:
    calls = _error_hole_calls()
    assert calls, "no call of error_hole is found"
    bad = []
    for site, call in calls:
        codes = call.args[1:]
        if not codes or not all(isinstance(code, ast.Constant) and code.value in REGISTRY
                                for code in codes):
            bad.append(site)
    assert not bad, f"an error hole that names no registered code as a literal: {bad}"


def _hole(*codes: str) -> Hole:
    return Hole(ShirId(0), Span(1, 1, 1, 1), "IndexAccess", codes)


def test_an_error_hole_with_an_offered_code_is_reported() -> None:
    assert unreported_error_holes([_hole("CE2122")], Counter({"CE2122": 1})) == []
    assert unreported_error_holes([_hole("CE1001", "CE1002")], Counter({"CE1002": 2})) == []


def test_an_error_hole_with_no_offered_code_is_not_reported() -> None:
    hole = _hole("CE2122")
    assert unreported_error_holes([hole], Counter({"CE2045": 1})) == [hole]
    assert unreported_error_holes([hole], Counter()) == [hole]


def test_a_hole_with_no_codes_is_no_error_hole() -> None:
    assert unreported_error_holes([_hole()], Counter()) == []


def test_the_analyzer_checks_the_error_holes() -> None:
    tree = ast.parse(ANALYZER.read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "check_error_holes" for node in ast.walk(tree)), (
        "the analyzer does not call check_error_holes")


def test_the_check_runs_on_every_compile(monkeypatch) -> None:
    monkeypatch.delenv(STRICT_VARIABLE, raising=False)
    first, second = _hole("CE2122"), _hole("CE2045")
    stop = check_error_holes([first, second], Counter({"CE1001": 1}))
    assert stop is not None and stop.code == "CE0155"
    assert stop.params == {"stage": "lowering, error hole",
                           "construct": "IndexAccess with no CE2122"}
    assert stop.span == first.loc
    assert check_error_holes([first], Counter({"CE2122": 1})) is None
