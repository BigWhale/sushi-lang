"""`RecursiveVisitor` must have an arm for every node the walk reaches. No silent skips.

`visitors.NodeVisitor.visit` builds the method name from the class name and falls back to
`generic_visit`. `RecursiveVisitor.generic_visit` was a bare `pass`, so a node kind that
`ast.py` added was skipped in silence by every consumer -- `StatementValidator`,
`ExpressionValidator` and `TypeInferenceVisitor` -- and a class RENAME read the same way
(#639). `Expand` and `Lambda` were both missing. The hazard class is the one CE0125 closed
in the borrow checker and CE0130 in the scope checker.

The walk hands `visit()` a statement, an expression or a block, and nothing else. A node
kind a parent's arm reads INSIDE itself is named in `WALKED_IN_PARENT`, so the gate can
tell a deliberate leaf from a forgotten arm.
"""
from __future__ import annotations

import inspect
import typing

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics import ast as a
from sushi_lang.semantics.passes.types.visitor import TypeInferenceVisitor
from sushi_lang.semantics.visitors import WALKED_IN_PARENT, RecursiveVisitor


def _stmt_kinds() -> set[str]:
    """Every statement node type: the direct subclasses of `Stmt` (semantics/ast.py)."""
    return {cls.__name__ for cls in a.Stmt.__subclasses__()}


def _expr_kinds() -> set[str]:
    """Every node type in the `Expr` union (semantics/ast.py)."""
    return {t.__name__ for t in typing.get_args(a.Expr)}


def _reachable_kinds() -> set[str]:
    """Every kind the walk hands to `visit()`. Proved below, not assumed."""
    return _stmt_kinds() | _expr_kinds() | {"Block"}


def _node_subclass_names() -> set[str]:
    """Every `Node` subclass name, at any depth."""
    names: set[str] = set()

    def walk(cls: type) -> None:
        for sub in cls.__subclasses__():
            names.add(sub.__name__)
            walk(sub)

    walk(a.Node)
    return names


def _block() -> a.Block:
    return a.Block(None, [a.ExprStmt(None, a.IntLit(None, 0))])


def _every_statement() -> dict[str, a.Stmt]:
    """One value per statement kind, each with its real children filled in."""
    return {
        "Let": a.Let(None, "x", None, a.IntLit(None, 0)),
        "Rebind": a.Rebind(None, a.Name(None, "x"), a.IntLit(None, 1)),
        "ExprStmt": a.ExprStmt(None, a.Name(None, "x")),
        "Return": a.Return(None, a.IntLit(None, 0)),
        "Print": a.Print(None, a.StringLit(None, "s")),
        "PrintLn": a.PrintLn(None, a.StringLit(None, "s")),
        "If": a.If(None, [(a.BoolLit(None, True), _block())], _block()),
        "While": a.While(None, a.BoolLit(None, True), _block()),
        "Foreach": a.Foreach(None, "i", None, a.Name(None, "xs"), _block()),
        "Expand": a.Expand(None, "p", a.Name(None, "args"), _block()),
        "Match": a.Match(None, a.Name(None, "r"), [
            a.MatchArm(None, a.WildcardPattern(None), _block()),
            a.MatchArm(None, a.WildcardPattern(None), a.IntLit(None, 0)),
        ]),
        "Break": a.Break(None),
        "Continue": a.Continue(None),
    }


def _every_expression() -> dict[str, object]:
    """One value per expression kind, each with its real children filled in."""
    literal = a.ArrayLiteral(None, [a.ArrayElement(None, a.IntLit(None, 1),
                                                   a.IntLit(None, 3))])
    return {
        "Name": a.Name(None, "x"),
        "IntLit": a.IntLit(None, 0),
        "FloatLit": a.FloatLit(None, 0.0),
        "BoolLit": a.BoolLit(None, True),
        "BlankLit": a.BlankLit(None),
        "StringLit": a.StringLit(None, "s"),
        "InterpolatedString": a.InterpolatedString(None, ["n=", a.Name(None, "n")]),
        "ArrayLiteral": literal,
        "IndexAccess": a.IndexAccess(None, a.Name(None, "xs"), a.IntLit(None, 0)),
        "UnaryOp": a.UnaryOp(None, "neg", a.IntLit(None, 1)),
        "BinaryOp": a.BinaryOp(None, "+", a.IntLit(None, 1), a.IntLit(None, 2)),
        "Call": a.Call(None, a.Name(None, "f"), [a.IntLit(None, 1)]),
        "MethodCall": a.MethodCall(None, a.Name(None, "v"), "len", []),
        "DotCall": a.DotCall(None, a.Name(None, "m"), "f", [a.IntLit(None, 1)]),
        "MemberAccess": a.MemberAccess(None, a.Name(None, "p"), "x"),
        "EnumConstructor": a.EnumConstructor(None, "E", "V", [a.IntLit(None, 1)]),
        "DynamicArrayNew": a.DynamicArrayNew(None),
        "DynamicArrayFrom": a.DynamicArrayFrom(None, literal),
        "CastExpr": a.CastExpr(None, a.IntLit(None, 0), None),
        "Borrow": a.Borrow(None, a.Name(None, "x"), "peek"),
        "TryExpr": a.TryExpr(None, a.Name(None, "r")),
        "RangeExpr": a.RangeExpr(None, a.IntLit(None, 0), a.IntLit(None, 9), False),
        "Spread": a.Spread(None, a.Name(None, "args")),
        "Lambda": a.Lambda(None, [a.Param("p", None)], _block(), is_block_body=True),
    }


class _Recorder(RecursiveVisitor):
    """Records every kind `visit()` is handed. A backstop hit raises, it is not recorded."""

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def visit(self, node: a.Node) -> None:
        self.seen.add(type(node).__name__)
        return super().visit(node)


def _walk_every_fixture() -> _Recorder:
    recorder = _Recorder()
    for node in (*_every_statement().values(), *_every_expression().values()):
        recorder.visit(node)
    recorder.visit(_block())
    return recorder


def test_every_statement_kind_has_a_visit_method():
    missing = sorted(
        name for name in _stmt_kinds()
        if not hasattr(RecursiveVisitor, f"visit_{name.lower()}")
    )
    assert not missing, (
        f"RecursiveVisitor has no visit_* arm for statement(s): {missing}.\n"
        "A statement with no arm is skipped in SILENCE by every consumer. Add the arm, "
        "with the recursion its children need."
    )


def test_every_expression_kind_has_a_visit_method():
    missing = sorted(
        name for name in _expr_kinds()
        if not hasattr(RecursiveVisitor, f"visit_{name.lower()}")
    )
    assert not missing, (
        f"RecursiveVisitor has no visit_* arm for expression(s): {missing}.\n"
        "An expression with no arm is skipped in SILENCE by every consumer. Add the arm, "
        "with the recursion its children need."
    )


def test_a_block_has_a_visit_method():
    assert hasattr(RecursiveVisitor, "visit_block"), (
        "visit_block is the walk's own recursion into a body. With no arm the whole "
        "body of every if, while, foreach and match goes unvisited."
    )


def test_no_visit_method_names_a_kind_the_walk_cannot_reach():
    """The mirror: an arm for a node the walk never hands over is dead code or a typo."""
    arms = {
        name[len("visit_"):] for name in vars(RecursiveVisitor)
        if name.startswith("visit_")
    }
    reachable = {name.lower() for name in _reachable_kinds()}
    stray = sorted(arms - reachable)
    assert not stray, f"RecursiveVisitor has an arm for unreachable node(s): {stray}"


def test_the_exemption_set_names_real_nodes():
    unknown = sorted(WALKED_IN_PARENT - _node_subclass_names())
    assert not unknown, (
        f"WALKED_IN_PARENT names something that is not an ast.Node subclass: {unknown}. "
        "A typo here exempts nothing and hides a real hole."
    )


def test_the_exemption_set_hides_no_dispatched_kind():
    """An exemption may not cover a kind the walk hands to `visit()`."""
    overlap = sorted(WALKED_IN_PARENT & _reachable_kinds())
    assert not overlap, (
        f"WALKED_IN_PARENT names dispatched kind(s): {overlap}. A statement, an "
        "expression and a block all reach `visit()` and each needs a real arm."
    )


def test_the_exemption_set_holds_no_stale_entry():
    stale = sorted(
        name for name in WALKED_IN_PARENT
        if hasattr(RecursiveVisitor, f"visit_{name.lower()}")
    )
    assert not stale, (
        f"WALKED_IN_PARENT names kind(s) that now have an arm: {stale}. "
        "The set records what a PARENT arm reads; drop the entry."
    )


def test_every_reachable_kind_has_a_fixture():
    """The behaviour tests below prove nothing unless every kind is exercised."""
    covered = set(_every_statement()) | set(_every_expression()) | {"Block"}
    assert not _reachable_kinds() - covered, (
        f"kinds with no fixture: {sorted(_reachable_kinds() - covered)}"
    )


def test_no_legal_node_reaches_the_backstop():
    """Behaviour, not source reading: the whole fixture corpus walks without an ICE."""
    _walk_every_fixture()


def test_the_walk_hands_visit_nothing_but_a_reachable_kind():
    """What makes the reachable set the right set: `visit()` sees nothing else."""
    stray = sorted(_walk_every_fixture().seen - _reachable_kinds())
    assert not stray, (
        f"`visit()` was handed {stray}, which is outside the statement/expression/block "
        "set the gate checks. Widen the reachable set and give each kind an arm."
    )


def test_the_backstop_raises_instead_of_passing_silently():
    """An `ArrayElement` is read inside `visit_arrayliteral` and never handed over."""
    element = a.ArrayElement(Span(4, 7, 4, 9), a.IntLit(None, 0))
    with pytest.raises(InternalCompilerError) as caught:
        RecursiveVisitor().visit(element)
    assert caught.value.code == "CE0136"
    assert caught.value.params.get("node") == "ArrayElement"


def test_the_backstop_is_located():
    """A diagnostic with no span cannot point at the source that provoked it."""
    span = Span(11, 2, 11, 8)
    with pytest.raises(InternalCompilerError) as caught:
        RecursiveVisitor().visit(a.ArrayElement(span, a.IntLit(None, 0)))
    assert caught.value.span == span


def test_the_backstop_is_not_a_bare_pass():
    src = inspect.getsource(RecursiveVisitor.generic_visit)
    assert "raise_internal_error" in src and "CE0136" in src, (
        "RecursiveVisitor.generic_visit must raise CE0136. A bare `pass` skips an "
        "unknown node with no diagnostic at all (#639)."
    )


def test_type_inference_covers_every_expression_kind():
    """The third consumer extends NodeVisitor, so it inherits no arm at all."""
    missing = sorted(
        name for name in _expr_kinds()
        if not hasattr(TypeInferenceVisitor, f"visit_{name.lower()}")
    )
    assert not missing, (
        f"TypeInferenceVisitor has no visit_* arm for: {missing}. Its generic_visit "
        "answers None, so the expression gets NO type and the fault surfaces far away."
    )
