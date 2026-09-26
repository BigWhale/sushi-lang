"""The Tier 4.1 layering invariant gets its gate (#276).

Two checks, because the invariant fails in two shapes:

1. An IMPORT of `sushi_lang.backend` anywhere under `sushi_lang/semantics/`.
   The documented grep, but done on the parsed AST, so every import form
   counts -- absolute, from-import, and a relative import that resolves into
   the backend package.

2. The shape the grep cannot see: a semantics pass that reads state only the
   backend populates (#239 -- the builtin-method registry was filled by
   `backend/types/primitives/*` as an import side effect, so the typecheck pass saw an
   empty registry for 19 days, with no backend import anywhere). The
   discriminator: a full semantic run in a process where the backend was
   NEVER imported must produce the same diagnostics as a run where it was.
   A dozen unit tests import the backend themselves, so this must run in a
   subprocess to control the import state.
"""
from __future__ import annotations

import ast
from pathlib import Path

SEMANTICS_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang" / "semantics"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolved_module(node: ast.AST, package_parts: list[str]) -> list[str]:
    """Return the module names one import statement binds, fully resolved.

    `package_parts` is the module's PACKAGE (`__package__`), so a relative
    import of level N resolves against `package_parts` minus N-1 tail parts.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level == 0:
            return [node.module or ""]
        base = package_parts[:len(package_parts) - (node.level - 1)]
        if node.module:
            base = base + [node.module]
        return [".".join(base)]
    return []


def _backend_imports_in(source: str, package_parts: list[str]) -> list[str]:
    """Every import in `source` that resolves into sushi_lang.backend."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        for mod in _resolved_module(node, package_parts):
            if mod == "sushi_lang.backend" or mod.startswith("sushi_lang.backend."):
                hits.append(mod)
    return hits


def _package_parts(py_file: Path) -> list[str]:
    """The module's package. For `__init__.py` and for a plain module this is
    the containing directory -- `__package__` is the same for both."""
    rel = py_file.relative_to(PROJECT_ROOT)
    return list(rel.parts[:-1])


def test_semantics_never_imports_backend():
    offenders = []
    for py_file in sorted(SEMANTICS_ROOT.rglob("*.py")):
        hits = _backend_imports_in(py_file.read_text(encoding="utf-8"),
                                   _package_parts(py_file))
        for mod in hits:
            offenders.append(f"{py_file.relative_to(PROJECT_ROOT)}: {mod}")
    assert not offenders, (
        "semantics must never import backend (Tier 4.1):\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_the_scanner_sees_every_import_form():
    """The gate above proves nothing if the scanner is blind. Feed it one
    violation per import form and require a hit for each."""
    # The package of sushi_lang/semantics/passes/types/method_registry.py.
    parts = ["sushi_lang", "semantics", "passes", "types"]
    forms = [
        "import sushi_lang.backend.types.primitives",
        "from sushi_lang.backend import ownership",
        "from sushi_lang.backend.types import primitives as p",
        "from ....backend import ownership",
        "from ....backend.types.primitives import ints",
    ]
    for form in forms:
        assert _backend_imports_in(form, parts), f"scanner is blind to: {form}"
    assert not _backend_imports_in("from sushi_lang.semantics import ast", parts)



# A primitive-method call whose RETURN TYPE the typecheck pass must infer from the
# builtin-method registry -- the exact state #239 left unpopulated.




