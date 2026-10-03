"""The copy of an array-template perk implementation is cut in ONE place (#699).

`extend T[] with P` covers every dynamic array, and an array type has no instantiation
set, so the copy for one array type is cut on a miss of the perk-implementation table:
`ArrayPerkCopies` (`semantics/generics/array_perk_copies.py`) is the hook, and the
analyzer is the one place that installs it. A second caller of `monomorphize_perk_impl`
could register a copy whose body nothing places and checks, and a second installer could
leave the hook armed in the backend, where a miss must stay a miss.

`generics/extensions.py` keeps its own call: it defines the copy and cuts the copies of
a GENERIC-target template, one per instantiation. An import counts as well as a call.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

CUTTERS = frozenset({
    SOURCE_ROOT / "semantics" / "generics" / "extensions.py",
    SOURCE_ROOT / "semantics" / "generics" / "array_perk_copies.py",
})
INSTALLERS = frozenset({
    SOURCE_ROOT / "semantics" / "semantic_analyzer.py",
})

CUT = "monomorphize_perk_impl"
HOOK = "on_array_miss"


def _cuts(tree: ast.AST) -> list[int]:
    """The line of every call or import that names the copy function."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == CUT for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == CUT:
                lines.append(node.lineno)
    return lines


def _installs(tree: ast.AST) -> list[int]:
    """The line of every assignment to the hook attribute."""
    lines: list[int] = []
    for node in ast.walk(tree):
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign))
                   else [])
        if any(isinstance(t, ast.Attribute) and t.attr == HOOK for t in targets):
            lines.append(node.lineno)
    return lines


def _scan(reader) -> dict[Path, list[int]]:
    found: dict[Path, list[int]] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = reader(tree)
        if lines:
            found[path] = lines
    return found


def test_the_scans_see_a_cut_and_an_install():
    """Always-fires control: each scan finds what it looks for."""
    source = (
        "from sushi_lang.semantics.generics.extensions import monomorphize_perk_impl\n"
        "def f(t, ty, s):\n"
        "    return extensions.monomorphize_perk_impl(t, ty, (), substitutor=s)\n"
        "def g(tables, hook):\n"
        "    tables.perk_impls.on_array_miss = hook\n"
    )
    tree = ast.parse(source)
    assert _cuts(tree) == [1, 3]
    assert _installs(tree) == [5]


def test_only_the_seam_cuts_a_perk_copy():
    outside = {path: lines for path, lines in _scan(_cuts).items() if path not in CUTTERS}
    assert not outside, (
        "a perk-implementation copy is cut outside the seam: "
        + ", ".join(f"{p.relative_to(SOURCE_ROOT)}:{lines}" for p, lines in outside.items()))


def test_only_the_analyzer_installs_the_hook():
    outside = {path: lines for path, lines in _scan(_installs).items()
               if path not in INSTALLERS}
    assert not outside, (
        "the array-copy hook is installed outside the analyzer: "
        + ", ".join(f"{p.relative_to(SOURCE_ROOT)}:{lines}" for p, lines in outside.items()))
