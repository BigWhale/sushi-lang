"""Gate: lowering is the one writer of the copy boundaries of an unrolled `expand` (docs/design/ir.md 7.2).

The monomorphize pass puts one copy of the body of an `expand` in the enclosing block for
each element of the pack. SHIR keeps the statement range of each copy in a side table of
the body, keyed by the block (ruling of 2026-10-14 on note 123), and no node holds it.
`ExpandCopies` is that table. A table with a second writer is the stamp channel again
(ir.md 1.2), so the scan below holds four rules over the Python source of `sushi_lang/`:

- only body lowering (`semantics/shir/lower_nodes.py`) names `ExpandCopies`, to make one;
- only body lowering calls the write API;
- only `semantics/shir/body.py` names the table that an `ExpandCopies` holds;
- an `ExpandCopies` has a read API and a write API, and nothing else.

The scan reads source; it does not start the compiler.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

DATA_MODULE = "semantics/shir/body.py"
WRITER_MODULE = "semantics/shir/lower_nodes.py"

READ_API = frozenset({"blocks", "ranges_of", "holding"})
WRITE_API = frozenset({"record_copies"})
TABLES = frozenset({"_copy_ranges"})


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


def test_lowering_alone_makes_the_table() -> None:
    named = {module: _mentions_of(frozenset({"ExpandCopies"}), module) for module in MODULES}
    stray = {module: sites for module, sites in named.items()
             if sites and module not in (DATA_MODULE, WRITER_MODULE)}
    assert not stray, (
        f"a module outside body lowering names ExpandCopies: {stray}. Lowering is the one "
        "writer of the copy boundaries (ruling of 2026-10-14); read the table of the body.")
    assert named[WRITER_MODULE], f"{WRITER_MODULE} makes no ExpandCopies; the scan has gone stale"


def test_lowering_alone_calls_the_write_api() -> None:
    calls = _calls_of(WRITE_API)
    stray = {module: sites for module, sites in calls.items() if module != WRITER_MODULE}
    assert not stray, f"a module outside body lowering writes the copy boundaries: {stray}"
    assert WRITER_MODULE in calls, f"{WRITER_MODULE} writes no copy boundary; the scan has gone stale"


def test_the_data_module_alone_names_the_table() -> None:
    stray = {module: _mentions_of(TABLES, module) for module in MODULES
             if module != DATA_MODULE and _mentions_of(TABLES, module)}
    assert not stray, (
        f"a module reads or writes the table of an ExpandCopies past its API: {stray}")


def test_the_table_has_a_read_api_and_a_write_api_and_nothing_else() -> None:
    from sushi_lang.semantics.shir.body import ExpandCopies

    methods = {name for name, member in inspect.getmembers(ExpandCopies, inspect.isfunction)
               if not name.startswith("__")}
    assert methods == READ_API | WRITE_API, (
        f"ExpandCopies has the methods {sorted(methods)}; the gate knows "
        f"{sorted(READ_API | WRITE_API)}. A method that writes goes in WRITE_API, so that "
        "the scan holds it to lowering.")
