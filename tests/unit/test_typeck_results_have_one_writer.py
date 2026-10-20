"""Gate: the `typecheck` pass is the one writer of `TypeckResults` (docs/design/ir.md 7.3, ruling S5).

`TypeckResults` is the table of one body: every answer that `typecheck` gives, keyed by a
node, a local or a position. Lowering lists each body with its table, the pass writes the
rows, and `lift`, `borrow`, SLIR lowering and the dump read them. A table with a second
writer is the stamp channel again (ir.md 1.2), so the scan below holds four rules over the
Python source of `sushi_lang/`:

- only `semantics/shir/lowering.py`, the owner of the bodies, makes a `TypeckResults`;
- only the modules of the typecheck pass (`semantics/passes/types/`) call the write API;
- only `semantics/shir/typeck.py` names the tables that a `TypeckResults` holds;
- a `TypeckResults` has a read API and a write API, and nothing else.

No module writes a row yet: the typecheck pass starts to write them in P2.4, which adds
the check that the pass calls the write API. The scan reads source; it does not start the
compiler.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

DATA_MODULE = "semantics/shir/typeck.py"
OWNER_MODULE = "semantics/shir/lowering.py"
WRITER_PACKAGE = "semantics/passes/types/"

READ_API = frozenset({
    "type_of", "local_type", "capture_type", "callee_of", "convention_of", "instance_of",
    "field_index_of", "loop_kind_of", "range_value_of", "conversion_of", "try_facts_of",
    "display_type_of", "match_facts_of", "element_type_of",
})
WRITE_API = frozenset({
    "record_type", "record_local", "record_capture", "record_callee", "record_convention",
    "record_instance", "record_field_index", "record_loop_kind", "record_range_value",
    "record_conversion", "record_try", "record_display", "record_match", "record_element",
})
TABLES = frozenset({
    "_node_types", "_local_types", "_capture_types", "_callees", "_arg_conventions",
    "_instance_types", "_field_indices", "_loop_kinds", "_range_values", "_conversions",
    "_try_facts", "_display_types", "_match_facts", "_element_types",
})


def _modules() -> dict[str, ast.AST]:
    return {path.relative_to(SOURCE_ROOT).as_posix(): ast.parse(path.read_text("utf-8"))
            for path in sorted(SOURCE_ROOT.rglob("*.py"))}


MODULES = _modules()


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _calls_of(names: frozenset[str]) -> dict[str, list[str]]:
    """For each module, the calls of a name in `names`, as `name:line`."""
    found: dict[str, list[str]] = {}
    for module, tree in MODULES.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) in names:
                found.setdefault(module, []).append(f"{_called_name(node)}:{node.lineno}")
    return found


def _mentions_of(names: frozenset[str], module: str) -> list[str]:
    """Every name or attribute in `names` that `module` holds, as `name:line`."""
    found = []
    for node in ast.walk(MODULES[module]):
        word = (node.id if isinstance(node, ast.Name)
                else node.attr if isinstance(node, ast.Attribute) else None)
        if word in names:
            found.append(f"{word}:{node.lineno}")
    return found


def test_the_owner_of_the_bodies_alone_makes_the_table() -> None:
    calls = _calls_of(frozenset({"TypeckResults"}))
    stray = {module: sites for module, sites in calls.items() if module != OWNER_MODULE}
    assert not stray, (
        f"a module outside {OWNER_MODULE} makes a TypeckResults: {stray}. Each body has ONE "
        "table, which lowering lists beside it; read it from the LoweredProgram.")
    assert OWNER_MODULE in calls, f"{OWNER_MODULE} makes no TypeckResults; the scan has gone stale"


def test_the_typecheck_pass_alone_calls_the_write_api() -> None:
    calls = _calls_of(WRITE_API)
    stray = {module: sites for module, sites in calls.items()
             if not module.startswith(WRITER_PACKAGE)}
    assert not stray, (
        f"a module outside the typecheck pass writes an answer of typecheck: {stray}. "
        "Ruling S5: every answer of typecheck is a row of TypeckResults, and the pass is "
        "its one writer.")


def test_the_data_module_alone_names_the_tables() -> None:
    stray = {module: _mentions_of(TABLES, module) for module in MODULES
             if module != DATA_MODULE and _mentions_of(TABLES, module)}
    assert not stray, (
        f"a module reads or writes the tables of a TypeckResults past its API: {stray}")
    assert _mentions_of(TABLES, DATA_MODULE), "the tables are gone; the scan has gone stale"


def test_the_table_has_a_read_api_and_a_write_api_and_nothing_else() -> None:
    from sushi_lang.semantics.shir.typeck import TypeckResults

    methods = {name for name, member in inspect.getmembers(TypeckResults, inspect.isfunction)
               if not name.startswith("__")}
    assert methods == READ_API | WRITE_API, (
        f"TypeckResults has the methods {sorted(methods)}; the gate knows "
        f"{sorted(READ_API | WRITE_API)}. A method that writes goes in WRITE_API, so that "
        "the scan holds it to the typecheck pass.")
