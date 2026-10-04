"""A conversion is found through ONE reader, `find_conversion` (docs/design/error-conversion.md 8.2).

`SymbolTables.conversions` holds every `extend <Source> as <Target>:`. The `??` check and
the cast check ask `semantics/conversions.py:find_conversion`, and nothing else reads the
table: a second reader would be a second rule for which conversion a pair names (a chain,
a guess from one side). A writer calls `ConversionTable.file`, and that is the one public
method of the table. The read is the private `_lookup`, which only `find_conversion` calls,
and no module outside the seam names `_lookup` or the private store `_by_pair`.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM = ROOT / "semantics" / "conversions.py"
_PRIVATE = {"_lookup", "_by_pair"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _attribute_lines(tree: ast.AST, names: set[str]) -> list[int]:
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in names]


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"semantics/conversions.py has no function '{name}'")


def test_no_module_outside_the_seam_reaches_the_private_table():
    hits = [f"{path.relative_to(ROOT)}:{line}"
            for path in sorted(ROOT.rglob("*.py")) if path != SEAM
            for line in _attribute_lines(_tree(path), _PRIVATE)]
    assert hits == [], (
        "a module reads the conversion table around `find_conversion`:\n  "
        + "\n  ".join(hits))


def test_only_find_conversion_calls_the_lookup():
    tree = _tree(SEAM)
    reader = _function(tree, "find_conversion")
    inside = set(_attribute_lines(reader, {"_lookup"}))
    everywhere = set(_attribute_lines(tree, {"_lookup"}))
    assert inside, "find_conversion no longer calls the table's `_lookup`"
    assert everywhere == inside


def test_the_table_has_one_public_method_and_it_writes():
    tree = _tree(SEAM)
    table = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "ConversionTable")
    public = sorted(node.name for node in table.body
                    if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"))
    assert public == ["file"]


def test_the_control_finds_the_callers_in_the_typecheck_pass():
    """The `??` check and the cast check, both in one module of the typecheck pass."""
    callers = {path.relative_to(ROOT).as_posix()
               for path in sorted(ROOT.rglob("*.py")) if path != SEAM
               for node in ast.walk(_tree(path))
               if isinstance(node, ast.Call)
               and getattr(node.func, "id", None) == "find_conversion"}
    assert callers == {"semantics/passes/types/expressions.py"}
