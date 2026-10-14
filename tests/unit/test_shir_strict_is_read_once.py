"""Gate: one module of the compiler reads `SUSHI_SHIR_STRICT` (docs/compiler-reference.md).

Under `SUSHI_SHIR_STRICT=1`, a construct that lowering cannot handle yet stops the compile
with the stop code CE0155, and does not become a hole. When two modules read the variable,
one of them can stop the compile while the other records a hole. So the scan below holds
two rules over the Python source of `sushi_lang/`:

- exactly one string constant is the name of the variable, so one place reads it (a text
  that names the variable among other words, such as the doc of CE0155, reads nothing);
- that constant is in the module of the hole, `semantics/shir/lower_nodes.py`.

The scan reads source; it does not start the compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
VARIABLE = "SUSHI_SHIR_STRICT"
READER = "semantics/shir/lower_nodes.py"


def _names_of_the_variable() -> list[str]:
    found = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found.extend(f"{path.relative_to(SOURCE_ROOT).as_posix()}:{node.lineno}"
                     for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and node.value == VARIABLE)
    return found


def test_one_place_reads_the_variable() -> None:
    found = _names_of_the_variable()
    assert len(found) == 1 and found[0].startswith(READER + ":"), (
        f"{VARIABLE} must be read in one place, in {READER}; found: {found}")
