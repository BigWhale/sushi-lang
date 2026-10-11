"""Gate: lowering reads no value through a node whose value the compiler cannot read.

Ruling E3 (docs/design/ir.md 7.4): lowering records the value of an operand that the
typecheck pass reads, and it calls the AST constant evaluator for it only when the subtree
holds no OPAQUE node. The evaluator writes on the AST when it reaches a call or a member
access (it stamps `namespace_ref`, and it folds an alias out of the receiver), so such a
node in the subtree must stop the read. A node whose value is never a number that the
typecheck pass reads (a struct, a variant, a tuple, a dynamic array) is opaque too.
`BodyLowerer.operand` sees an opaque node through the count that `made_opaque` keeps. So
this gate holds two rules:

1. Each node of `OPAQUE` that a lowering module makes takes its id from `made_opaque`, as
   the argument itself or through a name that the same function binds to it.
2. A node of `OPAQUE_KIND` is opaque only with one array kind: an `ArrayInit` of the kind
   `Dynamic` takes its id from `made_opaque`. A `Fixed` literal is not opaque, because the
   evaluator reads a fixed literal of constants with no write on the AST.

The golden fixtures under `tests/ir/shir/calls/` and `tests/ir/shir/fields/` show the
effect: a call, a field read and a constant behind an alias as a divisor have no value
after them. This gate reads source text; it does not run the compiler.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sushi_lang.semantics.shir import lower_aggregates, lower_nodes, lower_patterns

#: The modules of body lowering that make a node.
_MODULES = (lower_nodes, lower_patterns, lower_aggregates)

#: The SHIR nodes whose value the compiler cannot read: a local, a unit `var`, a
#: function, a call, a field read, a struct, a variant, a tuple and a hole.
OPAQUE: frozenset[str] = frozenset({
    "LocalRef", "StorageRef", "GlobalRef", "Call", "Field", "StructInit", "EnumInit",
    "TupleInit", "Hole",
})

#: The SHIR nodes that are opaque with one kind argument only, and that kind.
OPAQUE_KIND: dict[str, str] = {"ArrayInit": "Dynamic"}


def _is_made_opaque(node: ast.expr) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "made_opaque")


def _opaque_names(function: ast.AST) -> set[str]:
    """The names that `function` binds to an id from `made_opaque`."""
    return {target.id for stmt in ast.walk(function) if isinstance(stmt, ast.Assign)
            and _is_made_opaque(stmt.value)
            for target in stmt.targets if isinstance(target, ast.Name)}


def _called_name(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def _constructed(node: ast.Call) -> str | None:
    name = _called_name(node)
    if name in OPAQUE:
        return name
    kind = OPAQUE_KIND.get(name or "")
    if kind is not None and any(_called_name(arg) == kind for arg in node.args):
        return name
    return None


def _constructions() -> list[tuple[str, str, int, ast.Call, set[str]]]:
    found = []
    for module in _MODULES:
        found.extend(_constructions_of(module))
    return found


def _constructions_of(module: object) -> list[tuple[str, str, int, ast.Call, set[str]]]:
    path = Path(inspect.getfile(module))  # type: ignore[arg-type]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        names = _opaque_names(function)
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and (kind := _constructed(node)) is not None:
                found.append((kind, path.name, node.lineno, node, names))
    return found


def test_every_kind_of_opaque_node_is_made() -> None:
    made = {kind for kind, *_ in _constructions()}
    expected = OPAQUE | set(OPAQUE_KIND)
    assert made == expected, f"lowering makes no node of these kinds: {sorted(expected - made)}"


def test_each_opaque_node_takes_its_id_from_made_opaque() -> None:
    bad = []
    for kind, file, line, node, names in _constructions():
        first = node.args[0] if node.args else None
        if first is None or not (_is_made_opaque(first)
                                 or (isinstance(first, ast.Name) and first.id in names)):
            bad.append(f"{file}:{line} {kind}")
    assert not bad, f"these opaque nodes do not stop the read of ruling E3: {bad}"
