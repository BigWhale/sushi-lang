"""`walk_nodes` must reach every node an AST field can hold. No silent skips.

Seven hand-rolled `dataclasses.fields` recursions walked the AST before this, and they
disagreed. The lambda lifter's own two walks were the clearest case: `_walk` descended a
tuple, `_rewrite_captures` skipped a tuple-typed field on a node, so one new node kind
with a tuple field would have been seen by the lambda search and missed by the capture
rewrite (#688). `If.arms` is the only tuple in the AST today and it sits inside a list,
which is why nobody had noticed.

Proved by BEHAVIOUR, not by reading the walk: a marker node is hidden one level down
behind every field kind, and the walk has to find it. The corpus test then proves the
other half -- that a LEAF really holds no node -- by comparing the walk against an
exhaustive search that descends every dataclass, list, tuple, dict and set there is.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from sushi_lang.semantics import ast as a
from sushi_lang.semantics.ast import Node
from sushi_lang.semantics.ast_walk import (
    DESCENDED_FIELD_KINDS, FIELD_KINDS, children, field_kind, node_fields, walk_nodes)
from sushi_lang.internals.parser import parse_to_ast

TESTS_ROOT = Path(__file__).resolve().parents[1]

# A node the walk cannot invent, so finding it proves the walk arrived.
MARKER = "__node_walk_marker__"


def _marker() -> a.Name:
    return a.Name(None, MARKER)


def _found(root: object) -> list[str]:
    """Every marker `walk_nodes` reaches under `root`."""
    seen: list[str] = []

    def visit(node: Node) -> bool:
        if isinstance(node, a.Name) and node.id == MARKER:
            seen.append(node.id)
        return True

    walk_nodes(root, visit)
    return seen


def _all_nodes(root: object) -> set[int]:
    """Every node an EXHAUSTIVE search finds, by identity.

    Descends everything a Python object graph can hold, so it is an upper bound on what
    any walk could reach. `walk_nodes` descending three field kinds has to match it.
    """
    seen: set[int] = set()
    found: set[int] = set()

    def search(value: object) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, Node):
            found.add(id(value))
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                search(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                search(key)
                search(item)
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            for f in dataclasses.fields(value):
                search(getattr(value, f.name, None))

    search(root)
    return found


# --- the kinds themselves ---------------------------------------------------------


def test_field_kinds_are_named_and_descended_is_a_subset():
    assert DESCENDED_FIELD_KINDS < FIELD_KINDS
    assert FIELD_KINDS - DESCENDED_FIELD_KINDS == {"leaf"}


@pytest.mark.parametrize("value,kind", [
    (_marker(), "node"),
    ([_marker()], "list"),
    ((_marker(),), "tuple"),
    (None, "leaf"),
    ("text", "leaf"),
    (7, "leaf"),
    (frozenset({"x"}), "leaf"),
    ({"k": 1}, "leaf"),
    (a.Param(name="p", ty=None), "leaf"),
])
def test_field_kind_is_total(value, kind):
    """Every value answers a kind, and the kind is one of the named ones."""
    assert field_kind(value) == kind
    assert field_kind(value) in FIELD_KINDS


# --- the walk finds a node behind every descended kind ----------------------------


def test_the_walk_finds_a_node_behind_a_plain_field():
    assert _found(a.ExprStmt(None, _marker())) == [MARKER]


def test_the_walk_finds_a_node_behind_a_list_field():
    assert _found(a.Block(None, [a.ExprStmt(None, _marker())])) == [MARKER]


def test_the_walk_finds_a_node_behind_a_tuple_inside_a_list():
    """`If.arms` is the shape that exists today: a list of (cond, Block) tuples."""
    arms = [(_marker(), a.Block(None, []))]
    assert _found(a.If(None, arms, None)) == [MARKER]


def test_the_walk_finds_a_node_behind_a_bare_tuple_field():
    """The disagreement this walk ends: `_rewrite_captures` skipped a tuple field.

    No node declares one today, so this is the rule stated rather than a fixture --
    a tuple-typed field added tomorrow is walked, not silently skipped.
    """
    stmt = a.ExprStmt(None, a.IntLit(None, 0))
    stmt.expr = (_marker(),)  # type: ignore[assignment]
    assert _found(stmt) == [MARKER]


def test_the_walk_finds_a_node_behind_a_mixed_list():
    """`InterpolatedString.parts` holds plain strings beside expressions."""
    assert _found(a.InterpolatedString(None, ["n=", _marker()])) == [MARKER]


def test_the_walk_takes_a_list_or_a_tuple_as_its_root():
    assert _found([a.ExprStmt(None, _marker())]) == [MARKER]
    assert _found((a.ExprStmt(None, _marker()),)) == [MARKER]


def test_the_walk_takes_a_blank_slot():
    """A caller passes a body slot straight in; None needs no guard at the call site."""
    assert _found(None) == []
    assert _found("not a node") == []


# --- the contracts the callers depend on ------------------------------------------


def test_children_answers_in_field_declaration_order():
    """The order names `__lambda_<n>`; a rearrangement renames every lifted function."""
    first, second = a.IntLit(None, 1), a.IntLit(None, 2)
    node = a.BinaryOp(None, "+", first, second)
    declared = [name for name, _ in node_fields(node)]
    assert declared.index("left") < declared.index("right")
    assert [id(c) for c in children(node)] == [id(first), id(second)]


def test_a_false_visit_leaves_the_subtree_alone():
    """The lifter answers False at a Lambda: a nested one is lifted later, not now."""
    inner = a.ExprStmt(None, _marker())
    outer = a.Block(None, [a.Block(None, [inner])])
    seen: list[str] = []

    def visit(node: Node) -> bool:
        if isinstance(node, a.Name):
            seen.append(node.id)
        return not isinstance(node, a.Block) or node is outer

    walk_nodes(outer, visit)
    assert seen == []


def test_node_fields_reads_every_declared_field():
    node = a.Name(None, "x")
    assert ({name for name, _ in node_fields(node)}
            == {f.name for f in dataclasses.fields(node)})


# --- no leaf holds a node ----------------------------------------------------------


def _node_subclasses() -> set[type]:
    out = {Node}
    pending = [Node]
    while pending:
        for sub in pending.pop().__subclasses__():
            if sub not in out:
                out.add(sub)
                pending.append(sub)
    return out


# A container an annotation may wrap a node in. `walk_nodes` descends a list and a
# tuple; `Optional` and `Union` only spell alternatives and hold nothing themselves.
# A field that wraps a node in anything else -- a dict, a set, a mapping of its own --
# would be a node the walk cannot reach, so it is refused HERE, at the declaration,
# rather than in whichever pass first misses it.
WRAPPERS_THAT_HOLD_A_NODE = {"Optional", "Union", "List", "Tuple"}


def test_no_declared_field_wraps_a_node_in_a_container_the_walk_skips():
    node_names = {cls.__name__ for cls in _node_subclasses()} | {"Expr", "Stmt"}
    offenders = []
    for cls in sorted(_node_subclasses(), key=lambda c: c.__name__):
        for f in dataclasses.fields(cls):
            annotation = str(f.type).replace("'", "").replace('"', "")
            named = set(re.findall(r"\w+", annotation))
            if not (named & node_names):
                continue
            wrappers = set(re.findall(r"(\w+)\s*\[", annotation))
            unknown = wrappers - WRAPPERS_THAT_HOLD_A_NODE
            if unknown:
                offenders.append(f"{cls.__name__}.{f.name}: {annotation} ({unknown})")
    assert not offenders, (
        "these fields hold a node inside a container walk_nodes does not descend: "
        f"{offenders}. Teach ast_walk.field_kind the container, or do not use it."
    )


def _corpus() -> list[Path]:
    """Real sources, chosen for the field kinds they put a node behind.

    Named rather than swept: a sweep that silently shrinks to one file still passes,
    and `test_the_corpus_holds_every_shape_it_claims_to` refuses that here.
    """
    picks = [
        "control_flow/test_if_statements.sushi",       # If.arms: a tuple in a list
        "strings/test_concat_only.sushi",              # InterpolatedString.parts
        "closures/test_closure_list_capture.sushi",    # Lambda, captures
        "enums/test_enum_deep_nested_match.sushi",     # Match arms and patterns
    ]
    return [TESTS_ROOT / p for p in picks]


# The node kinds the corpus exists to put in front of the walk. A pick that is renamed
# or rewritten until it no longer holds one of these makes the gate measure less than
# it says, so the shapes are asserted and not assumed.
REQUIRED_SHAPES = (a.If, a.InterpolatedString, a.Lambda, a.Match)


def test_the_corpus_holds_every_shape_it_claims_to():
    kinds: set[type] = set()
    for path in _corpus():
        assert path.exists(), f"corpus file is gone: {path}"
        program, _tree = parse_to_ast(path.read_text())

        def visit(node: Node) -> bool:
            kinds.add(type(node))
            return True

        walk_nodes(program, visit)
    missing = [cls.__name__ for cls in REQUIRED_SHAPES if cls not in kinds]
    assert not missing, f"no corpus file holds: {missing}"


@pytest.mark.parametrize("path", _corpus(), ids=lambda p: p.name)
def test_the_walk_reaches_every_node_an_exhaustive_search_finds(path):
    """The other half: a LEAF really holds no node.

    If a typesys `Type`, a `Param`, a `DocBlock` or any other leaf ever holds a node,
    the exhaustive search finds it and `walk_nodes` does not.
    """
    program, _tree = parse_to_ast(path.read_text())

    reached: set[int] = set()

    def visit(node: Node) -> bool:
        reached.add(id(node))
        return True

    walk_nodes(program, visit)
    missed = _all_nodes(program) - reached
    assert not missed, (
        f"{len(missed)} node(s) in {path.name} sit behind a field kind the walk does "
        "not descend"
    )
