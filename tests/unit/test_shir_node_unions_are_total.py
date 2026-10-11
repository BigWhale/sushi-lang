"""Gate: the SHIR nodes are the nodes of `ir.md` 7.4, 7.5 and 7.8, and each is in its union.

The lowering dispatch (P1.15) and every pass on SHIR read a node through its union:
`ShirExpr` for an expression, `ShirStmt` for a statement, `ShirPattern` for a pattern.
A node class that is in no
union is a node that no dispatch can reach, and a dispatch that is total over the union
is then not total over the nodes. So this gate holds three rules:

1. The node classes are the classes that `ir.md` names, with the fields that it gives,
   in its order. The tables below are copied from 7.4, 7.5 and 7.8; a change of the design
   changes them first.
2. Each node class is in the union of its level, and the unions hold nothing else. The
   one exception is the `Hole` of `nodes.py`: a construct that lowering does not handle
   yet. It is in the expression union and in the statement union, and in no table of
   ir.md.
3. Each AST body class has a row of `ir.md` 7.10, and each node of the row is in a
   union. So lowering has a node for every class that it must lower.

The model is `tests/unit/test_node_walk_is_total.py`, over the SHIR package only. These
tests read Python objects; they do not run the compiler.
"""
from __future__ import annotations

import dataclasses
import inspect
import importlib
import pkgutil
import re
import types
import typing
from typing import Iterator

import pytest

import sushi_lang.semantics.shir as shir_pkg
from sushi_lang.semantics import ast as a
from sushi_lang.semantics.shir import exprs, patterns, stmts
from sushi_lang.semantics.shir.nodes import ShirBlock, ShirNode

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

#: ir.md 7.5, the statement nodes and their fields in the written order.
STATEMENT_FIELDS: dict[str, tuple[str, ...]] = {
    "Assign": ("target", "value", "is_init"),
    "Destructure": ("targets", "value"),
    "ExprStmt": ("value",),
    "Return": ("value",),
    "If": ("cond", "then", "otherwise"),
    "Loop": ("source", "body"),
    "Match": ("scrutinee", "arms", "consumes"),
    "Assert": ("cond", "message", "label"),
    "Break": (),
    "Continue": (),
    "Expand": ("binder", "pack", "body"),
}

#: The parts of a statement that are not nodes (ir.md 7.5): a loop source and the mode
#: of its binder, and a target of a destructure. `Nested` is a nested destructure,
#: `let ((a, b), c) = v`, which the list of 7.5 leaves out.
STATEMENT_PARTS: dict[str, tuple[str, ...]] = {
    "Cond": ("cond",),
    "Items": ("iterable", "binder", "by"),
    "Bare": (),
    "Ref": ("mode",),
    "Discard": (),
    "Nested": ("targets",),
}

#: ir.md 7.10, one row per AST statement class. 7.10 leaves out the classes that keep
#: their name. A `print` is an `ExprStmt` that holds a `Call`.
AST_STATEMENT_ROWS: dict[str, tuple[str, ...]] = {
    "Let": ("Assign", "Destructure"),
    "Rebind": ("Assign",),
    "ExprStmt": ("ExprStmt",),
    "Return": ("Return",),
    "Print": ("ExprStmt", "Call"),
    "PrintLn": ("ExprStmt", "Call"),
    "Assert": ("Assert",),
    "If": ("If",),
    "While": ("Loop",),
    "Foreach": ("Loop",),
    "Expand": ("Expand",),
    "Match": ("Match",),
    "Break": ("Break",),
    "Continue": ("Continue",),
}

#: ir.md 7.8, the pattern nodes and their fields in the written order. Two names change,
#: because the expression level has them: the pattern `Range` is `IntRange`, and the
#: binding mode `Take` is `Nom`. `IntRange` keeps the written bounds and `inclusive`:
#: the values that the bounds give depend on the type at the position, and lowering has
#: no types. An alternative of `Or` can be a binding, as in the AST (`Maybe.Some(x | y)`).
PATTERN_FIELDS: dict[str, tuple[str, ...]] = {
    "Wildcard": (),
    "Literal": ("value",),
    "IntRange": ("low", "high", "inclusive"),
    "Variant": ("decl", "variant", "items"),
    "Tuple": ("elements",),
    "Own": ("inner",),
    "Or": ("alternatives",),
}

#: The parts of a pattern that are not nodes (ir.md 7.8): a binding, the `nom` mode of a
#: binding, and an arm. A binding has the modes `Bare` and `Ref` of a loop binder too.
PATTERN_PARTS: dict[str, tuple[str, ...]] = {
    "PatBinding": ("local", "by"),
    "Nom": (),
    "Arm": ("pattern", "body"),
}

#: ir.md 7.10 gives the 9 AST pattern classes and `MatchArm` one row; this table gives
#: each class its own row. A bare name in a pattern is a `str` in the AST: it gives a
#: `PatBinding` with the mode `Bare`, and `_` gives `Wildcard`.
AST_PATTERN_ROWS: dict[str, tuple[str, ...]] = {
    "Pattern": ("Variant",),
    "WildcardPattern": ("Wildcard",),
    "LiteralPattern": ("Literal",),
    "RangePattern": ("IntRange",),
    "TuplePattern": ("Tuple",),
    "OwnPattern": ("Own",),
    "OrPattern": ("Or",),
    "RefBinding": ("PatBinding",),
    "NomBinding": ("PatBinding",),
    "MatchArm": ("Arm",),
}

_NO_NODE_OF_ITS_OWN = {"Spread"}

#: The node classes that are in no union: the bases, and the block, which a node holds.
_OUTSIDE_EVERY_UNION = {"ShirNode", "ExprNode", "ShirBlock"}

#: The node that stands for a construct that lowering does not handle yet. It is in the
#: expression union and in the statement union (`nodes.py`).
_HOLE = {"Hole"}

#: level -> (module, union, node fields, part fields, base fields)
_LEVELS: dict[str, tuple[types.ModuleType, object, dict[str, tuple[str, ...]],
                         dict[str, tuple[str, ...]], tuple[str, ...]]] = {
    "expression": (exprs, exprs.ShirExpr, EXPRESSION_FIELDS, EXPRESSION_PARTS,
                   _EXPR_BASE_FIELDS),
    "statement": (stmts, stmts.ShirStmt, STATEMENT_FIELDS, STATEMENT_PARTS, _BASE_FIELDS),
    "pattern": (patterns, patterns.ShirPattern, PATTERN_FIELDS, PATTERN_PARTS, _BASE_FIELDS),
}

_NODES = [(level, name) for level, row in _LEVELS.items() for name in sorted(row[2])]
_PARTS = [(level, name) for level, row in _LEVELS.items() for name in sorted(row[3])]


def _classes_of(module: types.ModuleType) -> dict[str, type]:
    return {name: obj for name, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__}


def _package_classes() -> Iterator[type]:
    for info in pkgutil.walk_packages(shir_pkg.__path__, shir_pkg.__name__ + "."):
        yield from _classes_of(importlib.import_module(info.name)).values()


def _union_members(alias: object) -> set[str]:
    return {member.__name__ for member in typing.get_args(alias)}


def _all_nodes() -> set[str]:
    return set().union(*(_union_members(row[1]) for row in _LEVELS.values()))


def _field_names(cls: type) -> tuple[str, ...]:
    return tuple(item.name for item in dataclasses.fields(cls))  # type: ignore[arg-type]


def _ast_subclasses(root: type) -> set[str]:
    found, pending = set(), [root]
    while pending:
        for sub in pending.pop().__subclasses__():
            if sub.__name__ not in found:
                found.add(sub.__name__)
                pending.append(sub)
    return found


def _ast_pattern_classes() -> set[str]:
    """The AST classes that a pattern position holds, and the arm."""
    arm_pattern = typing.get_type_hints(a.MatchArm)["pattern"]
    members = set(typing.get_args(a.PatternItem)) | set(typing.get_args(arm_pattern))
    return {cls.__name__ for cls in members if cls is not str} | {"MatchArm"}


def _ids(pair: tuple[str, str]) -> str:
    return f"{pair[0]}-{pair[1]}"


# --- 1. the classes are the classes of ir.md ----------------------------------------


@pytest.mark.parametrize("level", sorted(_LEVELS))
def test_every_node_and_part_of_ir_md_exists(level: str) -> None:
    module, _union, fields, parts, _base = _LEVELS[level]
    missing = (set(fields) | set(parts)) - set(_classes_of(module))
    assert not missing, f"ir.md names these {level} classes; none exists: {missing}"


@pytest.mark.parametrize("pair", _NODES, ids=_ids)
def test_a_node_has_the_fields_of_ir_md(pair: tuple[str, str]) -> None:
    level, name = pair
    module, _union, fields, _parts, base = _LEVELS[level]
    cls = getattr(module, name)
    assert issubclass(cls, ShirNode)
    assert _field_names(cls) == base + fields[name]


@pytest.mark.parametrize("pair", _PARTS, ids=_ids)
def test_a_part_has_the_fields_of_ir_md(pair: tuple[str, str]) -> None:
    level, name = pair
    module, _union, _fields, parts, _base = _LEVELS[level]
    cls = getattr(module, name)
    assert not issubclass(cls, ShirNode), f"{name} is a part of a node, not a node"
    assert _field_names(cls) == parts[name]


def test_the_recorded_value_is_on_every_expression_and_takes_no_position() -> None:
    """Ruling E3: lowering records a readable value; a node is built with no value."""
    readable = {item.name: item for item in dataclasses.fields(exprs.ExprNode)}["readable"]
    assert readable.kw_only and readable.default is None


def test_a_block_holds_statements() -> None:
    statements = {item.name: item for item in dataclasses.fields(ShirBlock)}["statements"]
    assert statements.type == "list[ShirStmt]"


def test_a_match_holds_arms() -> None:
    arms = {item.name: item for item in dataclasses.fields(stmts.Match)}["arms"]
    assert arms.type == "list[Arm]"


# --- 2. each node is in its union, and the union holds nothing else -----------------


@pytest.mark.parametrize("level", sorted(_LEVELS))
def test_the_union_is_the_nodes_of_its_level(level: str) -> None:
    module, union, fields, _parts, _base = _LEVELS[level]
    nodes = {name for name, cls in _classes_of(module).items()
             if issubclass(cls, ShirNode) and name not in _OUTSIDE_EVERY_UNION}
    assert _union_members(union) - _HOLE == set(fields) == nodes


@pytest.mark.parametrize("union", [exprs.ShirExpr, stmts.ShirStmt], ids=["ShirExpr", "ShirStmt"])
def test_the_hole_is_in_the_expression_and_the_statement_union(union: object) -> None:
    assert _HOLE <= _union_members(union)
    assert not _HOLE & _union_members(patterns.ShirPattern)


def test_every_node_of_the_package_is_in_a_union() -> None:
    outside = {cls.__name__ for cls in _package_classes() if issubclass(cls, ShirNode)}
    outside -= _all_nodes() | _OUTSIDE_EVERY_UNION
    assert not outside, f"no dispatch reaches these nodes, because no union holds them: {outside}"


def test_no_node_is_in_two_unions() -> None:
    unions = sorted(_LEVELS)
    for index, first in enumerate(unions):
        for second in unions[index + 1:]:
            shared = (_union_members(_LEVELS[first][1]) & _union_members(_LEVELS[second][1])
                      - _HOLE)
            assert not shared, f"{first} and {second} share these nodes: {shared}"


# --- 3. lowering has a node for every AST body class --------------------------------


def test_every_ast_body_class_has_a_row() -> None:
    assert set(AST_EXPRESSION_ROWS) == {cls.__name__ for cls in typing.get_args(a.Expr)}
    assert set(AST_STATEMENT_ROWS) == _ast_subclasses(a.Stmt)
    assert set(AST_PATTERN_ROWS) == _ast_pattern_classes()


@pytest.mark.parametrize("ast_name", sorted(AST_EXPRESSION_ROWS))
def test_an_ast_expression_lowers_to_expression_nodes(ast_name: str) -> None:
    row = AST_EXPRESSION_ROWS[ast_name]
    assert (not row) == (ast_name in _NO_NODE_OF_ITS_OWN)
    assert set(row) <= _union_members(exprs.ShirExpr)


@pytest.mark.parametrize("ast_name", sorted(AST_STATEMENT_ROWS))
def test_an_ast_statement_lowers_to_nodes(ast_name: str) -> None:
    row = AST_STATEMENT_ROWS[ast_name]
    assert row and set(row) <= _all_nodes()
    assert set(row) & _union_members(stmts.ShirStmt), "a statement lowers to a statement"


@pytest.mark.parametrize("ast_name", sorted(AST_PATTERN_ROWS))
def test_an_ast_pattern_lowers_to_pattern_nodes_or_parts(ast_name: str) -> None:
    row = AST_PATTERN_ROWS[ast_name]
    assert row and set(row) <= _union_members(patterns.ShirPattern) | set(PATTERN_PARTS)


# --- a later walk reaches every node that a field holds -----------------------------

#: The containers that a field may hold a node in. A walk descends a list and a tuple
#: and the parts of this package; `Optional` and a union only spell alternatives.
_WRAPPERS_THAT_HOLD_A_NODE = {"Optional", "Union", "list", "tuple"}


def test_no_field_wraps_a_node_in_a_container_that_a_walk_skips() -> None:
    held = _all_nodes() | {"ShirExpr", "ShirStmt", "ShirPattern"} | _OUTSIDE_EVERY_UNION
    offenders = []
    for cls in sorted(_package_classes(), key=lambda c: f"{c.__module__}.{c.__name__}"):
        if not dataclasses.is_dataclass(cls):
            continue
        for item in dataclasses.fields(cls):
            annotation = str(item.type)
            if not set(re.findall(r"\w+", annotation)) & held:
                continue
            unknown = set(re.findall(r"(\w+)\s*\[", annotation)) - _WRAPPERS_THAT_HOLD_A_NODE
            if unknown:
                offenders.append(f"{cls.__name__}.{item.name}: {annotation} ({unknown})")
    assert not offenders, f"these fields hold a node where a walk does not look: {offenders}"
