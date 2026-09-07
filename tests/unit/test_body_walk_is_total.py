"""`body_types` must reach every statement and every expression. No silent skips.

The walk that fed the `ptr` quarantine (CE5009) read `block.stmts`. The field is
`Block.statements`, the name `stmts` is nowhere in the repository, and the `getattr`
default turned that into an empty tuple: the whole body half of the gate enforced
nothing, and `let ptr p = 0` in a unit with no danger zone compiled (#596). An `if`
arm was a second hole in the same walk, because its blocks sit inside tuples.

Proved by BEHAVIOUR, not by reading `case` arms: every node kind is built with a
`ForeignPtrType` hidden one level down, and the walk has to find it.
"""
from __future__ import annotations

import typing

import pytest

from sushi_lang.semantics import ast as a
from sushi_lang.semantics.ast_walk import TERMINAL_NODES, _expr_types, _stmt_types, body_types
from sushi_lang.semantics.typesys import ForeignPtrType


def _marker() -> ForeignPtrType:
    """A type the walk cannot invent, so finding it proves the walk arrived."""
    return ForeignPtrType()


def _carrier() -> a.CastExpr:
    """The smallest expression that names the marker, for a sub-expression slot."""
    return a.CastExpr(None, a.IntLit(None, 0), _marker())


def _every_statement() -> dict[str, a.Stmt]:
    """One value per statement kind, each hiding the marker exactly one level down."""
    block = a.Block(None, [a.ExprStmt(None, _carrier())])
    return {
        "Let": a.Let(None, "p", _marker(), a.IntLit(None, 0)),
        "Rebind": a.Rebind(None, a.Name(None, "p"), _carrier()),
        "ExprStmt": a.ExprStmt(None, _carrier()),
        "Return": a.Return(None, _carrier()),
        "Print": a.Print(None, _carrier()),
        "PrintLn": a.PrintLn(None, _carrier()),
        "If": a.If(None, [(a.BoolLit(None, True), block)], None),
        "While": a.While(None, a.BoolLit(None, True), block),
        "Foreach": a.Foreach(None, "p", _marker(), a.Name(None, "xs"), a.Block(None, [])),
        "Expand": a.Expand(None, "p", _carrier(), a.Block(None, [])),
        "Match": a.Match(None, _carrier(), []),
    }


def _every_expression() -> dict[str, object]:
    """One value per expression kind, each hiding the marker exactly one level down."""
    return {
        "InterpolatedString": a.InterpolatedString(None, ["n=", _carrier()]),
        "ArrayLiteral": a.ArrayLiteral(None, [a.ArrayElement(None, _carrier())]),
        "DynamicArrayFrom": a.DynamicArrayFrom(
            None, a.ArrayLiteral(None, [a.ArrayElement(None, _carrier())])),
        "IndexAccess": a.IndexAccess(None, _carrier(), a.IntLit(None, 0)),
        "UnaryOp": a.UnaryOp(None, "neg", _carrier()),
        "BinaryOp": a.BinaryOp(None, "+", _carrier(), a.IntLit(None, 0)),
        "RangeExpr": a.RangeExpr(None, _carrier(), a.IntLit(None, 1), False),
        "Call": a.Call(None, a.Name(None, "f"), [_carrier()]),
        "DotCall": a.DotCall(None, a.Name(None, "m"), "f", [_carrier()]),
        "MethodCall": a.MethodCall(None, _carrier(), "f", []),
        "MemberAccess": a.MemberAccess(None, _carrier(), "x"),
        "EnumConstructor": a.EnumConstructor(None, "E", "V", [_carrier()]),
        "CastExpr": _carrier(),
        "Borrow": a.Borrow(None, _carrier(), "peek"),
        "TryExpr": a.TryExpr(None, _carrier()),
        "Spread": a.Spread(None, _carrier()),
        "Lambda": a.Lambda(None, [a.Param("p", _marker())], a.IntLit(None, 0)),
    }


def _found(pairs) -> bool:
    return any(isinstance(ty, ForeignPtrType) for ty, _span in pairs)


@pytest.mark.parametrize("kind", sorted(_every_statement()))
def test_every_statement_kind_is_walked(kind):
    assert _found(_stmt_types(_every_statement()[kind])), (
        f"body_types does not walk {kind}. A statement with no arm hides every type "
        "it names from the `ptr` quarantine -- silently."
    )


@pytest.mark.parametrize("kind", sorted(_every_expression()))
def test_every_expression_kind_is_walked(kind):
    assert _found(_expr_types(_every_expression()[kind])), (
        f"body_types does not walk {kind}. An expression with no arm hides every type "
        "it names from the `ptr` quarantine -- silently."
    )


def test_every_statement_kind_has_a_fixture_or_is_terminal():
    declared = {cls.__name__ for cls in a.Stmt.__subclasses__()}
    covered = set(_every_statement()) | TERMINAL_NODES
    assert not declared - covered, (
        f"statement kinds with no fixture and not in TERMINAL_NODES: "
        f"{sorted(declared - covered)}"
    )


def test_every_expression_kind_has_a_fixture_or_is_terminal():
    declared = {t.__name__ for t in typing.get_args(a.Expr)}
    covered = set(_every_expression()) | TERMINAL_NODES
    assert not declared - covered, (
        f"expression kinds with no fixture and not in TERMINAL_NODES: "
        f"{sorted(declared - covered)}"
    )


def test_a_terminal_names_nothing():
    """A kind listed as terminal must really hold no type and no sub-expression."""
    for node in (a.Break(None), a.Continue(None), a.Name(None, "x"),
                 a.IntLit(None, 0), a.FloatLit(None, 0.0), a.BoolLit(None, True),
                 a.BlankLit(None), a.StringLit(None, "s"), a.DynamicArrayNew(None)):
        walk = _stmt_types if isinstance(node, a.Stmt) else _expr_types
        assert list(walk(node)) == []


def test_a_call_site_type_argument_is_a_naming_position():
    """`identity@(ptr)(x)` spells `ptr` in a slot no sub-expression carries."""
    call = a.Call(None, a.Name(None, "identity"), [], type_args=[_marker()])
    assert _found(_expr_types(call))


def test_a_lambda_return_and_error_arm_are_naming_positions():
    lam = a.Lambda(None, [], a.IntLit(None, 0), ret=_marker())
    assert _found(_expr_types(lam))
    lam = a.Lambda(None, [], a.IntLit(None, 0), err_type=_marker())
    assert _found(_expr_types(lam))


def test_body_types_reaches_a_function_body():
    """The whole-program entry, not just the dispatch under it."""
    func = a.FuncDef(None, "main", [], None,
                     a.Block(None, [a.Let(None, "p", _marker(), a.IntLit(None, 0))]))
    program = a.Program(None, uses=[], constants=[], structs=[], enums=[], perks=[],
                        functions=[func], extensions=[], generic_extensions=[],
                        perk_impls=[])
    assert _found(body_types(program))
