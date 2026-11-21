"""The link plan is the one writer of `-l` and `-L` (docs/adr/0003-one-executable-link-plan.md).

`backend/link_plan.py:link_command` makes the `cc` command of every link from one
`LinkPlan`. A second place that writes a library flag is a second link rule: the
hard-coded `-lm` of the driver was one. The scan reads every string constant under
`sushi_lang/`, the parts of an f-string included. The command line may name its own flag
`-L` (`compiler/cli.py`) and nothing more.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

SEAM = SOURCE_ROOT / "backend" / "link_plan.py"
CLI = SOURCE_ROOT / "compiler" / "cli.py"


def _flag_constants(tree: ast.AST) -> list[tuple[int, str]]:
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.startswith(("-l", "-L"))]


def test_only_the_link_plan_writes_a_library_flag():
    offenders = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path == SEAM:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, value in _flag_constants(tree):
            if path == CLI and value == "-L":
                continue
            offenders.append(f"{path.relative_to(SOURCE_ROOT.parent)}:{line}: {value!r}")
    assert not offenders, (
        "a library flag outside backend/link_plan.py; add the library to the link plan "
        "instead:\n" + "\n".join(offenders))


def test_the_seam_writes_both_flags():
    tree = ast.parse(SEAM.read_text(encoding="utf-8"), filename=str(SEAM))
    values = {value for _, value in _flag_constants(tree)}
    assert {"-l", "-L"} <= values
