"""The backend AST gate: the backend reads SLIR, not the AST (docs/design/ir.md 6.2 and 6.4).

The scan reads the source of each module of `sushi_lang/backend/` and finds each import of
the AST (`ast_import_scan.py`): the node module, the AST walks and the AST builder. The
emitter of the bodies (`backend/slir/`) imports none, and no other module may.

`DECLARATION_ROUND` holds the modules that still import the AST, each with the names that
it imports and the reason. Each one reads the DECLARATIONS of a module, and nothing of a
body that SLIR has: ir.md 8.2 states that the declarations of a module come from the AST,
until the declaration table of ir.md 6.3 exists. The table may only get shorter: a module
that no longer imports a name of its row fails the gate, and so does a new row.
"""
from __future__ import annotations

from ast_import_scan import ROOT, scan

BACKEND = ROOT / "sushi_lang" / "backend"
_AST = "sushi_lang.semantics.ast"
_WALK = "sushi_lang.semantics.ast_walk"
_ROUND = ("Phase 7: the declaration round reads the declarations of the AST (ir.md 8.2); the "
          "declaration table of ir.md 6.3 does not exist")

#: module -> ({AST module: the names it imports}, reason).
DECLARATION_ROUND: dict[str, tuple[dict[str, frozenset[str]], str]] = {
    "sushi_lang/backend/codegen_llvm.py": (
        {_AST: frozenset({"ConstDef", "ExtendDef", "ExtendWithDef", "FuncDef", "VarDef"})},
        _ROUND),
    "sushi_lang/backend/driver.py": ({_AST: frozenset({"ExtendDef"})}, _ROUND),
    "sushi_lang/backend/functions/__init__.py": (
        {_AST: frozenset({"ExtendDef", "FuncDef"})}, _ROUND),
    "sushi_lang/backend/functions/declarations.py": (
        {_AST: frozenset({"ExtendDef", "FuncDef"})}, _ROUND),
    "sushi_lang/backend/functions/helpers.py": (
        {_AST: frozenset({"ExtendDef", "FuncDef", "Param"})}, _ROUND),
    "sushi_lang/backend/stdlib_linker.py": (
        {_AST: frozenset({"Program"})},
        "Phase 7: the stdlib modules that a module links are the `use` declarations of "
        "the AST"),
    "sushi_lang/backend/library_manifest.py": (
        {_AST: frozenset({"*", "ExtendDef", "Node", "Program", "VarDef"}),
         _WALK: frozenset({"bodied_with_owner", "is_written", "node_fields", "walk_nodes"})},
        "Phase 7: the manifest of a library records the declarations of the AST, and the "
        "names that the body of each exported template names (ir.md 6.1 question 4); it "
        "writes no LLVM, and it moves out of the backend"),
}

#: The rows at the day of the pin. A row may leave both; none may join.
PINNED = frozenset({
    "sushi_lang/backend/codegen_llvm.py", "sushi_lang/backend/driver.py",
    "sushi_lang/backend/functions/__init__.py", "sushi_lang/backend/functions/declarations.py",
    "sushi_lang/backend/functions/helpers.py", "sushi_lang/backend/stdlib_linker.py",
    "sushi_lang/backend/library_manifest.py",
})


def _found():
    return scan(sorted(BACKEND.rglob("*.py")))


def test_no_backend_module_imports_the_ast():
    found = _found()
    outside = sorted(path for path in found if path not in DECLARATION_ROUND)
    assert not outside, (
        "a backend module imports the AST; the backend reads SLIR:\n  "
        + "\n  ".join(f"{path}: {sorted((m, sorted(n)) for m, n in found[path].items())}"
                      for path in outside))


def test_the_slir_emitter_imports_no_ast():
    assert not [path for path in DECLARATION_ROUND if path.startswith("sushi_lang/backend/slir/")]


def test_each_row_names_what_its_module_imports():
    found = _found()
    wrong = {}
    for path, (allowed, _reason) in DECLARATION_ROUND.items():
        imported = {module: frozenset(names) for module, names in found.get(path, {}).items()}
        if imported != allowed:
            wrong[path] = (imported, allowed)
    assert not wrong, (
        "a backend module imports a name that its row does not name, or no longer imports "
        "a name of its row; delete the name from the row:\n  "
        + "\n  ".join(f"{path}: imports {sorted((m, sorted(n)) for m, n in got.items())}, "
                      f"row {sorted((m, sorted(n)) for m, n in row.items())}"
                      for path, (got, row) in sorted(wrong.items())))


def test_no_row_is_new():
    grown = sorted(set(DECLARATION_ROUND) - PINNED)
    assert not grown, f"a row may be deleted and never added: {grown}"
    stale = sorted(PINNED - set(DECLARATION_ROUND))
    assert not stale, f"delete the pin row together with the row: {stale}"
