"""Gate: the SHIR nodes are the nodes of `ir.md` 7.4 and 7.5, and each is in its union.

The lowering dispatch (P1.15) and every pass on SHIR read a node through its union:
`ShirExpr` for an expression, `ShirStmt` for a statement. A node class that is in no
union is a node that no dispatch can reach, and a dispatch that is total over the union
is then not total over the nodes. So this gate holds three rules:

1. The node classes are the classes that `ir.md` names, with the fields that it gives,
   in its order. The tables below are copied from 7.4 and 7.5; a change of the design
   changes them first.
2. Each node class is in the union of its level, and the unions hold nothing else.
3. Each AST body class has a row of `ir.md` 7.10, and each node of the row is in a
   union. So lowering has a node for every class that it must lower.

The model is `tests/unit/test_node_walk_is_total.py`, over the SHIR package only. These
tests read Python objects; they do not run the compiler.
"""
from __future__ import annotations

import dataclasses
import inspect
import re
import types
import typing

import pytest

from sushi_lang.semantics import ast as a
from sushi_lang.semantics.shir import exprs
from sushi_lang.semantics.shir.nodes import ShirNode

#: The fields that every node has (ir.md 7.3), and the recorded value of an expression
#: that the compiler can read (ruling E3, ir.md 7.4).
_BASE_FIELDS = ("shir_id", "loc")
_EXPR_BASE_FIELDS = _BASE_FIELDS + ("readable",)

#: ir.md 7.4, the expression nodes and their fields in the written order. `Lambda` adds
#: `ret` and `err`, the written return type and error type, and `StructInit` adds
#: `written_order` (ruling R1).
EXPRESSION_FIELDS: dict[str, tuple[str, ...]] = {
    "Lit": ("value",),
    "LocalRef": ("local",),
    "ConstRef": ("const_id",),
    "StorageRef": ("decl",),
    "GlobalRef": ("decl",),
    "Field": ("base", "name"),
    "Index": ("base", "index", "unchecked"),
    "Slice": ("base", "start", "end", "inclusive"),
    "Call": ("target", "args"),
    "Unary": ("op", "operand"),
    "Binary": ("op", "lhs", "rhs"),
    "Cast": ("operand", "target"),
    "Borrow": ("place", "mode"),
    "Take": ("place",),
    "Try": ("inner",),
    "Lambda": ("params", "ret", "err", "body"),
    "StructInit": ("decl", "fields", "written_order"),
    "EnumInit": ("decl", "variant", "payload"),
    "TupleInit": ("parts",),
    "ArrayInit": ("kind", "runs"),
    "Range": ("start", "end", "inclusive", "reverse"),
    "Interp": ("parts",),
}

#: The parts of an expression that are not nodes (ir.md 7.4): a call target, an
#: argument, an array kind and an array run, a lambda parameter, and a literal value.
#: `RangeRun` is the `Span(Range)` run of ir.md; the name `Span` is the source span.
EXPRESSION_PARTS: dict[str, tuple[str, ...]] = {
    "Named": ("ref",),
    "Dotted": ("recv", "name"),
    "Static": ("ty", "name"),
    "Arg": ("value", "marker", "bloom"),
    "Fixed": (),
    "Dynamic": (),
    "Single": ("value",),
    "Repeat": ("value", "count"),
    "RangeRun": ("range",),
    "LambdaParam": ("local", "convention"),
    "IntConst": ("value", "radix", "byte_spelling"),
    "FloatConst": ("value",),
    "BoolConst": ("value",),
    "BlankConst": (),
    "StrConst": ("value",),
}

#: ir.md 7.10, one row per AST expression class: the SHIR nodes that lowering can make
#: of it. The rows add what 7.10 leaves out: a unit variant (`Sign.Plus`) and a
#: function behind an alias are a `MemberAccess`, and a `DotCall` is also a variant or
#: a struct behind an alias. A bloom `arr...` has no node; it is a flag of its `Arg`.
AST_EXPRESSION_ROWS: dict[str, tuple[str, ...]] = {
    "Name": ("LocalRef", "ConstRef", "StorageRef", "GlobalRef"),
    "IntLit": ("Lit",),
    "FloatLit": ("Lit",),
    "BoolLit": ("Lit",),
    "BlankLit": ("Lit",),
    "StringLit": ("Lit",),
    "InterpolatedString": ("Interp",),
    "ArrayLiteral": ("ArrayInit",),
    "DynamicArrayNew": ("ArrayInit",),
    "DynamicArrayFrom": ("ArrayInit",),
    "IndexAccess": ("Index", "Slice"),
    "UnaryOp": ("Unary",),
    "BinaryOp": ("Binary",),
    "Call": ("Call", "StructInit"),
    "MethodCall": ("Call",),
    "DotCall": ("Call", "StructInit", "EnumInit"),
    "MemberAccess": ("Field", "StorageRef", "ConstRef", "GlobalRef", "EnumInit"),
    "EnumConstructor": ("EnumInit",),
    "CastExpr": ("Cast",),
    "Borrow": ("Borrow",),
    "TryExpr": ("Try",),
    "RangeExpr": ("Range",),
    "Spread": (),
    "Lambda": ("Lambda",),
    "TupleLiteral": ("TupleInit",),
}

_NO_NODE_OF_ITS_OWN = {"Spread"}


def _classes_of(module: types.ModuleType) -> dict[str, type]:
    return {name: obj for name, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__}


def _union_members(alias: object) -> set[str]:
    return {member.__name__ for member in typing.get_args(alias)}


def _field_names(cls: type) -> tuple[str, ...]:
    return tuple(item.name for item in dataclasses.fields(cls))  # type: ignore[arg-type]


# --- 1. the classes are the classes of ir.md ----------------------------------------


def test_every_expression_node_of_ir_md_exists() -> None:
    missing = set(EXPRESSION_FIELDS) - set(_classes_of(exprs))
    assert not missing, f"ir.md 7.4 names these expression nodes; none exists: {missing}"


def test_every_expression_part_of_ir_md_exists() -> None:
    missing = set(EXPRESSION_PARTS) - set(_classes_of(exprs))
    assert not missing, f"ir.md 7.4 names these parts of an expression: {missing}"


@pytest.mark.parametrize("name", sorted(EXPRESSION_FIELDS))
def test_an_expression_node_has_the_fields_of_ir_md(name: str) -> None:
    cls = getattr(exprs, name)
    assert issubclass(cls, exprs.ExprNode)
    assert _field_names(cls) == _EXPR_BASE_FIELDS + EXPRESSION_FIELDS[name]


@pytest.mark.parametrize("name", sorted(EXPRESSION_PARTS))
def test_an_expression_part_has_the_fields_of_ir_md(name: str) -> None:
    cls = getattr(exprs, name)
    assert not issubclass(cls, ShirNode), f"{name} is a part of a node, not a node"
    assert _field_names(cls) == EXPRESSION_PARTS[name]


def test_the_recorded_value_is_on_every_expression_and_takes_no_position() -> None:
    """Ruling E3: lowering records a readable value; a node is built with no value."""
    readable = {item.name: item for item in dataclasses.fields(exprs.ExprNode)}["readable"]
    assert readable.kw_only and readable.default is None


# --- 2. each node is in its union, and the union holds nothing else -----------------


def test_the_expression_union_is_the_expression_nodes() -> None:
    assert _union_members(exprs.ShirExpr) == set(EXPRESSION_FIELDS)


def test_every_expression_class_is_in_the_union() -> None:
    nodes = {name for name, cls in _classes_of(exprs).items()
             if issubclass(cls, exprs.ExprNode) and cls is not exprs.ExprNode}
    assert nodes == _union_members(exprs.ShirExpr), (
        "an expression node that is not in ShirExpr is a node no dispatch reaches")


# --- 3. lowering has a node for every AST body class --------------------------------


def test_every_ast_expression_class_has_a_row() -> None:
    assert set(AST_EXPRESSION_ROWS) == {cls.__name__ for cls in typing.get_args(a.Expr)}


@pytest.mark.parametrize("ast_name", sorted(AST_EXPRESSION_ROWS))
def test_the_nodes_of_a_row_are_expression_nodes(ast_name: str) -> None:
    row = AST_EXPRESSION_ROWS[ast_name]
    assert (not row) == (ast_name in _NO_NODE_OF_ITS_OWN)
    assert set(row) <= _union_members(exprs.ShirExpr)


# --- a later walk reaches every node that a field holds -----------------------------

#: The containers that a field may hold a node in. A walk descends a list and a tuple
#: and the parts of this package; `Optional` and a union only spell alternatives.
_WRAPPERS_THAT_HOLD_A_NODE = {"Optional", "Union", "list", "tuple"}


def test_no_field_wraps_a_node_in_a_container_that_a_walk_skips() -> None:
    held = set(EXPRESSION_FIELDS) | {"ShirExpr", "ShirNode", "ShirBlock", "ExprNode"}
    offenders = []
    for name, cls in sorted(_classes_of(exprs).items()):
        if not dataclasses.is_dataclass(cls):
            continue
        for item in dataclasses.fields(cls):
            annotation = str(item.type)
            if not set(re.findall(r"\w+", annotation)) & held:
                continue
            unknown = set(re.findall(r"(\w+)\s*\[", annotation)) - _WRAPPERS_THAT_HOLD_A_NODE
            if unknown:
                offenders.append(f"{name}.{item.name}: {annotation} ({unknown})")
    assert not offenders, f"these fields hold a node where a walk does not look: {offenders}"
