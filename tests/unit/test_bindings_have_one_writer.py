"""Gate: the `scope` pass is the one writer of `Bindings` (docs/design/ir.md 7.1, ruling S3).

`Bindings` is the table of one body: what each name occurrence denotes, the `LocalId` of
each binder, and the captures of each lambda. Lowering reads it and `lift` reads it. A
table with a second writer is the stamp channel again (ir.md 1.2), so the scan below
holds four rules over the Python source of `sushi_lang/`:

- only the writer module of the scope pass makes a `Bindings`;
- only that module calls the write API of a `Bindings`;
- only the scope pass names the writer;
- only `shir/bindings.py` names the tables that a `Bindings` holds.

A `Bindings` has a read API and a write API, and nothing else, so a new method that
writes cannot pass in silence. The scan reads source; it does not start the compiler.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

DATA_MODULE = "semantics/shir/bindings.py"
WRITER_MODULE = "semantics/passes/scope_bindings.py"
SCOPE_PASS = frozenset({"semantics/passes/scope.py", WRITER_MODULE})

READ_API = frozenset({"resolution_of", "local_of", "captures_of"})
WRITE_API = frozenset({"record_resolution", "record_binder", "record_captures"})
TABLES = frozenset({"_resolutions", "_binder_locals", "_lambda_captures"})


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


def _mentions_of(names: frozenset[str]) -> dict[str, list[str]]:
    """For each module, every name or attribute in `names` that it holds, as `name:line`."""
    found: dict[str, list[str]] = {}
    for module, tree in MODULES.items():
        for node in ast.walk(tree):
            word = (node.id if isinstance(node, ast.Name)
                    else node.attr if isinstance(node, ast.Attribute) else None)
            if word in names:
                found.setdefault(module, []).append(f"{word}:{node.lineno}")
    return found


def test_the_writer_module_alone_makes_a_bindings():
    made = _calls_of(frozenset({"Bindings"}))
    stray = {module: sites for module, sites in made.items() if module != WRITER_MODULE}
    assert not stray, (
        f"a module outside the scope pass makes a Bindings: {stray}. The scope pass is "
        "the one writer of the table (ir.md 7.1, ruling S3); read the table it made."
    )
    assert WRITER_MODULE in made, f"{WRITER_MODULE} makes no Bindings; the scan has gone stale"


def test_the_writer_module_alone_calls_the_write_api():
    calls = _calls_of(WRITE_API)
    stray = {module: sites for module, sites in calls.items() if module != WRITER_MODULE}
    assert not stray, (
        f"a module outside the scope pass writes into a Bindings: {stray}. "
        "The scope pass is the one writer (ruling S3)."
    )
    written = {site.split(":")[0] for site in calls.get(WRITER_MODULE, [])}
    assert written == WRITE_API, (
        f"{WRITER_MODULE} calls {sorted(written)} of the write API {sorted(WRITE_API)}; "
        "the scan has gone stale"
    )


def test_the_scope_pass_alone_names_the_writer():
    named = _mentions_of(frozenset({"BindingsWriter"}))
    stray = sorted(set(named) - SCOPE_PASS)
    assert not stray, (
        f"a module outside the scope pass names BindingsWriter: {stray}. The writer is "
        "the scope pass's own; another pass reads the Bindings that it made."
    )
    assert "semantics/passes/scope.py" in named, "the scope pass does not use the writer"


def test_the_data_module_alone_names_the_tables():
    named = _mentions_of(TABLES)
    stray = {module: sites for module, sites in named.items() if module != DATA_MODULE}
    assert not stray, (
        f"a module reads or writes the tables of a Bindings past its API: {stray}. "
        "Use the read API; the scope pass writes through the write API."
    )


def test_a_bindings_has_a_read_api_and_a_write_api_and_nothing_else():
    from sushi_lang.semantics.shir.bindings import Bindings

    methods = {name for name, member in inspect.getmembers(Bindings, inspect.isfunction)
               if not name.startswith("__")}
    assert methods == READ_API | WRITE_API, (
        f"Bindings has the methods {sorted(methods)}; the gate knows "
        f"{sorted(READ_API | WRITE_API)}. A method that writes goes in WRITE_API, so that "
        "the scan holds it to the scope pass."
    )
