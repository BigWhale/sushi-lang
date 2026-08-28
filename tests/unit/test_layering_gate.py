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
import json
import subprocess
import sys
import textwrap
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


_ISOLATION_SCRIPT = textwrap.dedent('''
    import json
    import sys

    mode = sys.argv[1]
    src_path = sys.argv[2]

    if mode == "warm":
        import sushi_lang.backend.codegen_llvm  # noqa: F401

    from pathlib import Path

    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
    from sushi_lang.semantics.units import UnitManager

    text = Path(src_path).read_text(encoding="utf-8")
    program, _tree = parse_to_ast(text)
    reporter = Reporter(source=text, filename="main")

    get_stdlib_registry()

    unit_manager = UnitManager(root_path=Path(src_path).parent, reporter=reporter)
    unit = unit_manager.load_unit("main", program)
    unit_manager.get_compilation_order()

    analyzer = SemanticAnalyzer(reporter, filename="main", unit_manager=unit_manager)
    analyzer.check(program)

    print(json.dumps({
        "codes": sorted(d.code for d in reporter.items),
        "backend_types_loaded": any(m.startswith("sushi_lang.backend.types")
                                    for m in sys.modules),
    }))
''')

# A primitive-method call whose RETURN TYPE the typecheck pass must infer from the
# builtin-method registry -- the exact state #239 left unpopulated.
_PROBE_PROGRAM = (
    "fn main() i32:\n"
    "    let string s = \"Mostly Harmless\"\n"
    "    let i32 n = s.len()\n"
    "    println(\"length {n}\")\n"
    "    return Result.Ok(0)\n"
)


def _run_isolated(tmp_path: Path, mode: str) -> dict:
    script = tmp_path / "probe.py"
    script.write_text(_ISOLATION_SCRIPT, encoding="utf-8")
    src = tmp_path / "main.sushi"
    src.write_text(_PROBE_PROGRAM, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), mode, str(src)],
        capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_semantics_diagnostics_do_not_depend_on_backend_import(tmp_path):
    cold = _run_isolated(tmp_path, "cold")
    warm = _run_isolated(tmp_path, "warm")

    # The cold run must stay cold where it matters: the builtin-method
    # registry is populated by `backend.types.primitives` imports (#239), so
    # if the semantic flow ever pulls THAT in, this test stops discriminating
    # and would pass on nothing. (`backend.platform_detect` does load, through
    # `sushi_stdlib` module discovery -- a different package from `semantics`,
    # outside the Tier 4.1 ruling, and it carries no registry state.)
    assert not cold["backend_types_loaded"], (
        "the cold run imported sushi_lang.backend.types -- the discriminator "
        "below no longer proves anything; find and break the import chain"
    )
    assert warm["backend_types_loaded"]
    assert cold["codes"] == warm["codes"], (
        "diagnostics differ with and without the backend imported -- a "
        "semantics pass reads state only the backend populates (the #239 "
        f"shape). cold={cold['codes']} warm={warm['codes']}"
    )
    assert cold["codes"] == [], f"the probe program must analyze clean: {cold['codes']}"
