"""The backend AST gate: the backend reads SLIR, not the AST (docs/design/ir.md 6.2 and 6.4).

The scan reads the source of each module of `sushi_lang/backend/` and finds each import of
the AST (`ast_import_scan.py`): the node module, the AST walks and the AST builder. The
emitter of the bodies (`backend/slir/`) imports none. The declaration round and the stdlib
link set read the declaration table of ir.md 6.3 (`semantics/decl_table.py`) and import
none. The manifest of a library writes no LLVM, and it is not a module of the backend
(`compiler/library_manifest.py`).
"""
from __future__ import annotations

from ast_import_scan import ROOT, scan

BACKEND = ROOT / "sushi_lang" / "backend"


def test_no_backend_module_imports_the_ast():
    found = scan(sorted(BACKEND.rglob("*.py")))
    assert not found, (
        "a backend module imports the AST; the backend reads SLIR:\n  "
        + "\n  ".join(f"{path}: {sorted((m, sorted(n)) for m, n in found[path].items())}"
                      for path in sorted(found)))
