"""The body passes write no type onto a constant or a `var` declaration.

The declared type of a constant is a declaration fact. Its one home is the record of the
constant table (`ConstSig.const_type`), and every reader reads it there: the backend, the
library manifest and the constant evaluator. The `typecheck`, `lift` and `borrow` passes
write no `ty` of a `ConstDef` or a `VarDef` (docs/design/ir.md, the MOVE criterion).

A scan of the compiler's Python source. A name is a declaration when a parameter annotation
names `ConstDef` or `VarDef`, or when it is bound from a `.decl` field (the declaration
that an initializer or a `ConstSig` holds). A write of `ty` through such a name, or through
a `.decl` field, is refused.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang" / "semantics" / "passes"
SCANNED = sorted([*(ROOT / "types").rglob("*.py"), *(ROOT / "borrow").rglob("*.py"),
                  ROOT / "lift.py"])
DECLARATION_TYPES = ("ConstDef", "VarDef")


def _declaration_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = set()
    for arg in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]:
        if arg.annotation is not None and any(
                kind in ast.unparse(arg.annotation) for kind in DECLARATION_TYPES):
            names.add(arg.arg)
    for node in ast.walk(func):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute)
                and node.value.attr == "decl"):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _is_declaration(node: ast.expr, names: set[str]) -> bool:
    return ((isinstance(node, ast.Name) and node.id in names)
            or (isinstance(node, ast.Attribute) and node.attr == "decl"))


def _type_writes(source: str, label: str) -> list[str]:
    found = []
    for func in ast.walk(ast.parse(source)):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = _declaration_names(func)
        for node in ast.walk(func):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if (isinstance(target, ast.Attribute) and target.attr == "ty"
                        and _is_declaration(target.value, names)):
                    found.append(f"{label}:{node.lineno} {func.name}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "setattr" and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value == "ty"
                    and _is_declaration(node.args[0], names)):
                found.append(f"{label}:{node.lineno} {func.name}")
    return found


@pytest.mark.parametrize("path", SCANNED, ids=[str(p.relative_to(ROOT)) for p in SCANNED])
def test_no_body_pass_writes_the_type_of_a_constant(path):
    writes = _type_writes(path.read_text(encoding="utf-8"), path.name)
    assert not writes, (
        "a body pass writes the type of a constant or a `var` declaration: "
        f"{writes}. Write ConstSig.const_type, the one home of the declared type")


def test_the_scan_sees_a_write():
    """The scan finds a write of each form, so a green run is no blind one."""
    source = (
        "def f(const: ConstDef, facts):\n"
        "    const.ty = 1\n"
        "    facts.decl.ty = 2\n"
        "    d = facts.decl\n"
        "    setattr(d, 'ty', 3)\n"
        "def g(var: Optional[VarDef]):\n"
        "    var.ty += 1\n")
    assert _type_writes(source, "probe") == [
        "probe:2 f", "probe:3 f", "probe:5 f", "probe:7 g"]
