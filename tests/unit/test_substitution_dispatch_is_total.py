"""The monomorphizer's substitution walk must have an arm for every node. No silent copies."""
from __future__ import annotations

import inspect
import typing

from expr_dispatch import dispatched_names, expr_union_names, expression_fields
from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.generics.monomorphize.transformer import INERT_EXPRS, TypeSubstitutor


def _statement_classes() -> set[str]:
    """Every `Stmt` subclass. The walk must reach each one."""
    return {t.__name__ for t in sushi_ast.Stmt.__subclasses__()}


def _dispatched_names(func) -> set[str]:
    return dispatched_names(func, inert=INERT_EXPRS)


def test_every_expression_node_has_an_arm():
    missing = sorted(expr_union_names() - _dispatched_names(TypeSubstitutor.substitute_expr))
    assert not missing, (
        f"TypeSubstitutor.substitute_expr has no arm for: {missing}.\n"
        "A node with no arm is COPIED, so it keeps the type parameter and the internal "
        "name (T, U) reaches the user (#602). Add a real arm, or add it to INERT_EXPRS if "
        "it genuinely holds no sub-expression and no type the source writes."
    )


def test_every_statement_node_has_an_arm():
    missing = sorted(_statement_classes() - _dispatched_names(TypeSubstitutor.substitute_statement))
    assert not missing, (
        f"TypeSubstitutor.substitute_statement has no arm for: {missing}.\n"
        "A statement with no arm is not substituted at all: every annotation it holds "
        "keeps its type parameter."
    )


def test_no_arm_names_a_node_outside_the_expr_union():
    """The mirror: an arm for a node that is not an Expr is dead code or a typo."""
    known = expr_union_names() | {
        # Non-Expr types legitimately tested inside substitute_expr's arms.
        "str",
    }
    stray = sorted(_dispatched_names(TypeSubstitutor.substitute_expr) - known)
    assert not stray, f"substitute_expr dispatches on non-Expr node(s): {stray}"


def test_inert_exprs_hold_neither_an_expression_nor_a_type():
    """An 'inert' node must be a leaf AND carry no type -- else substitution skips one."""
    source_written = {"target_type", "type_args", "ret", "err_type", "item_type", "ty"}
    for node_type in INERT_EXPRS:
        not_leaf = expression_fields(node_type)
        assert not not_leaf, (
            f"{node_type.__name__} is in INERT_EXPRS but its field(s) {not_leaf} hold "
            "an expression. It is not a leaf -- give it a real arm.")
        fields = typing.get_type_hints(node_type, globalns=vars(sushi_ast))
        written = sorted(set(fields) & source_written)
        assert not written, (
            f"{node_type.__name__} is in INERT_EXPRS but its field(s) {written} are a "
            "type the SOURCE writes, so they can name a type parameter. Give it an arm."
        )


def test_the_backstop_is_a_raise_and_not_a_copy():
    """#602: the fall-through used to be `copy.deepcopy`, which hid the miss."""
    for func in (TypeSubstitutor.substitute_expr, TypeSubstitutor.substitute_statement):
        src = inspect.getsource(func)
        assert "CE0135" in src, (
            f"{func.__name__} must end in the CE0135 backstop. A copy fall-through leaves "
            "the type parameter in place and reports nothing."
        )


def test_the_backstop_fires():
    """The registry text renders, so the backstop is a diagnostic and not a crash."""
    from sushi_lang.internals.errors import InternalCompilerError

    class NotAnExpr:
        pass

    substitutor = TypeSubstitutor(monomorphizer=None)
    for walk in (substitutor.substitute_expr, substitutor.substitute_statement):
        try:
            walk(NotAnExpr(), {})
        except InternalCompilerError as exc:
            assert exc.code == "CE0135"
        else:
            raise AssertionError(f"{walk.__name__} copied an unknown node instead of raising")

