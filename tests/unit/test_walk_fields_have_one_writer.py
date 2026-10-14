"""The walk facts of a loop have ONE writer: the `borrow` pass (ruling S6, docs/design/ir.md).

`Foreach.walks_in_place` and the `iterator_state` of a call (CURSOR, LENT, OWNED) are
questions about ownership. The `borrow` pass sees every input that the `typecheck` pass
sees, so the `borrow` pass writes both, and no other module writes either. The `typecheck`
pass does not name either field at all: a stamp table that copies a field is a writer too.

The scan reads the compiler's Python source. It refuses an assignment to the attribute and
a `setattr` that names it. The AST dataclass declares each field with its default, which is
not an attribute assignment, so the scan does not see it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import sushi_lang

FIELDS = frozenset({"walks_in_place", "iterator_state"})
PACKAGE = Path(sushi_lang.__file__).parent
BORROW_PASS = PACKAGE / "semantics" / "passes" / "borrow"
TYPECHECK_PASS = PACKAGE / "semantics" / "passes" / "types"


def _modules(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _relative(path: Path) -> str:
    return str(path.relative_to(PACKAGE.parent))


def _assigned_attributes(target: ast.AST) -> list[ast.Attribute]:
    if isinstance(target, ast.Attribute):
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [a for element in target.elts for a in _assigned_attributes(element)]
    return []


def _writes(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            for attribute in _assigned_attributes(target):
                if attribute.attr in FIELDS:
                    found.append((attribute.lineno, attribute.attr))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in FIELDS):
            found.append((node.lineno, node.args[1].value))
    return found


def _writers() -> dict[str, list[tuple[int, str]]]:
    writers = {}
    for path in _modules(PACKAGE):
        found = _writes(ast.parse(path.read_text(), filename=str(path)))
        if found:
            writers[_relative(path)] = found
    return writers


def test_only_the_borrow_pass_writes_the_walk_fields():
    borrow = _relative(BORROW_PASS)
    outside = {module: lines for module, lines in _writers().items()
               if not module.startswith(borrow + "/")}
    assert not outside, (
        "the walk facts have one writer, the borrow pass (ruling S6). These modules "
        f"write one of {sorted(FIELDS)}: {outside}")


def test_the_typecheck_pass_does_not_name_the_walk_fields():
    named = {}
    for path in _modules(TYPECHECK_PASS):
        tree = ast.parse(path.read_text(), filename=str(path))
        lines = sorted({node.lineno for node in ast.walk(tree)
                        if (isinstance(node, ast.Attribute) and node.attr in FIELDS)
                        or (isinstance(node, ast.Constant) and node.value in FIELDS)})
        if lines:
            named[_relative(path)] = lines
    assert not named, (
        "the typecheck pass reads and writes no walk fact (ruling S6). These lines name "
        f"one of {sorted(FIELDS)}: {named}")


def test_the_scan_sees_the_borrow_pass_writers():
    """The control: the scan finds both fields written in the borrow pass."""
    borrow = _relative(BORROW_PASS)
    written = {attr for module, lines in _writers().items()
               if module.startswith(borrow + "/") for _, attr in lines}
    assert written == FIELDS
