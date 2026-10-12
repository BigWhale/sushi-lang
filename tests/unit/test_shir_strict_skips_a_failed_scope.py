"""Gate: strict mode never stops on an unresolved name, nor in a unit with a scope error.

Under `SUSHI_SHIR_STRICT=1` a hole stops the compile with the stop code CE0155. Two kinds
of hole are holes of a program that always has an error, and the error, not CE0155, must
be the diagnostic of the program (rulings of 2026-10-12):

- A name with no declaration (`Unresolved`) is a hole, and SHIR has no node for it. The
  program has an error: CE1001 or CE1002 from the scope pass, or CE0144 from the typecheck
  pass for a type pack name. So the hole of an unresolved name never stops the compile.
- A type name written as a value is a hole too. The scope pass reports CE2105 for it, so
  strict mode skips a unit whose scope pass reported an error.

The rule has five parts, and this gate holds each one:

1. `lower_unit` takes the result of the scope pass as the keyword `scope_failed`, with no
   default, so that no caller can forget it.
2. The one function that reads the variable is `stops_on_hole`, and it reads
   `scope_failed` too. `BodyLowerer.hole` raises CE0155 only behind `self.stops`.
3. The analyzer gives `scope_failed` from the reporter of the unit, which only the scope
   pass has used when lowering starts.
4. `BodyLowerer.error_hole` records a hole and never raises CE0155.
5. `_name` is the one lowering that calls `error_hole`, and it calls it for an
   `Unresolved` name. `hole` calls it too, after its stop check.

The scan reads Python source and signatures; it does not start the compiler. The proof
on a program is a compile by hand: `SUSHI_SHIR_STRICT=1 ./sushic` on a CE1001 fixture
gives CE1001, and on a fixture of `variadic/pack_name_is_not_a_value/` it gives CE0144,
not CE0155.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sushi_lang.semantics.shir import lowering

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
LOWER_NODES = ROOT / "semantics" / "shir" / "lower_nodes.py"
ANALYZER = ROOT / "semantics" / "semantic_analyzer.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _loads(node: ast.AST, name: str) -> bool:
    return any(isinstance(sub, ast.Name) and sub.id == name for sub in ast.walk(node))


def _functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]


def test_lower_unit_requires_the_scope_result() -> None:
    parameter = inspect.signature(lowering.lower_unit).parameters.get("scope_failed")
    assert parameter is not None, "lower_unit takes no 'scope_failed'"
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, "'scope_failed' is not a keyword"
    assert parameter.default is inspect.Parameter.empty, "'scope_failed' has a default"


def test_the_reader_of_the_variable_reads_the_scope_result() -> None:
    readers = [fn for fn in _functions(_tree(LOWER_NODES)) if _loads(fn, "STRICT_VARIABLE")]
    assert [fn.name for fn in readers] == ["stops_on_hole"], (
        f"the functions that read STRICT_VARIABLE: {[fn.name for fn in readers]}")
    reader = readers[0]
    assert [arg.arg for arg in reader.args.args] == ["scope_failed"], (
        "stops_on_hole must take 'scope_failed' alone")
    assert any(_loads(stmt, "scope_failed") for stmt in reader.body), (
        "stops_on_hole does not read 'scope_failed'")


def _guards_the_stop_code(node: ast.If) -> bool:
    test = node.test
    guarded = (isinstance(test, ast.Attribute) and test.attr == "stops"
               and isinstance(test.value, ast.Name) and test.value.id == "self")
    raises = any(isinstance(sub, ast.Constant) and sub.value == "CE0155"
                 for stmt in node.body for sub in ast.walk(stmt))
    return guarded and raises


def test_a_hole_stops_only_behind_the_flag() -> None:
    holes = [fn for fn in _functions(_tree(LOWER_NODES)) if fn.name == "hole"]
    assert len(holes) == 1, "BodyLowerer.hole is not found"
    stop_codes = [sub for sub in ast.walk(holes[0])
                  if isinstance(sub, ast.Constant) and sub.value == "CE0155"]
    guards = [sub for sub in ast.walk(holes[0])
              if isinstance(sub, ast.If) and _guards_the_stop_code(sub)]
    assert len(stop_codes) == 1 and len(guards) == 1, (
        "BodyLowerer.hole must raise CE0155 once, inside `if self.stops:`")


def test_the_analyzer_gives_the_result_of_the_scope_pass() -> None:
    calls = [node for node in ast.walk(_tree(ANALYZER))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "lower_unit"]
    assert len(calls) == 1, f"the analyzer calls lower_unit {len(calls)} times"
    given = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    value = given.get("scope_failed")
    assert isinstance(value, ast.Attribute) and value.attr == "has_offered_errors", (
        "the analyzer must give scope_failed=<the unit reporter>.has_offered_errors")


def test_an_error_hole_never_stops() -> None:
    holes = [fn for fn in _functions(_tree(LOWER_NODES)) if fn.name == "error_hole"]
    assert len(holes) == 1, "BodyLowerer.error_hole is not found"
    assert not any(isinstance(sub, ast.Constant) and sub.value == "CE0155"
                   for sub in ast.walk(holes[0])), "error_hole must not raise CE0155"


def _calls_error_hole(node: ast.AST) -> bool:
    return any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
               and sub.func.attr == "error_hole" for sub in ast.walk(node))


def test_an_unresolved_name_is_the_one_error_hole() -> None:
    shir = ROOT / "semantics" / "shir"
    callers = [f"{path.name}:{fn.name}" for path in sorted(shir.glob("*.py"))
               for fn in _functions(_tree(path)) if _calls_error_hole(fn)]
    assert callers == ["lower_nodes.py:_name", "lower_nodes.py:hole"], (
        f"the callers of error_hole: {callers}")
    name = next(fn for fn in _functions(_tree(LOWER_NODES)) if fn.name == "_name")
    branches = [sub for sub in ast.walk(name) if isinstance(sub, (ast.If, ast.match_case))
                and _calls_error_hole(sub)
                and any(isinstance(ref, ast.Attribute) and ref.attr == "Unresolved"
                        for ref in ast.walk(sub))]
    assert branches, "_name must call error_hole in the branch of an Unresolved name"
