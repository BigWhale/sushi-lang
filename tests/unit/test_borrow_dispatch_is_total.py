"""Gate: the borrow pass has one arm for each SHIR statement and expression class.

The borrow pass walks a SHIR body through ONE dispatch table, `ARMS` in
`sushi_lang/semantics/passes/borrow/arms.py`, keyed on the SHIR class
(docs/design/ir.md 7.9, 10.3). The typecheck pass has the same dispatch shape, and both
passes read it from one module, `semantics/shir/dispatch.py`. So this gate holds five
rules:

1. The table has a row for each member of `ShirStmt` and `ShirExpr`.
2. The table has no row for another class.
3. A class with no row is an internal error at the span of the node: the backstop is
   located, and a node is never skipped in silence.
4. The arm of a construct that the pass does not check is an internal error (CE0000) that
   names the stage and the class, at the span of the node. In a body that has an error, it
   ends the walk of the body instead (`dispatch.AfterAnError`), and nothing reports it.
5. The two passes read one dispatch: neither looks a class up in its table by itself.

These tests read Python objects and call the dispatch over a node that the test builds.
They do not run the compiler.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import types
import typing

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import arms, calls
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import dispatch, exprs, stmts
from sushi_lang.semantics.shir.callee import Conversion
from sushi_lang.semantics.shir.typeck import ResolvedCall

_UNIONS = {"ShirStmt": stmts.ShirStmt, "ShirExpr": exprs.ShirExpr}


def _classes() -> set[type]:
    return set().union(*(typing.get_args(union) for union in _UNIONS.values()))


def _names(classes: set[type]) -> list[str]:
    return sorted(f"{cls.__module__}.{cls.__name__}" for cls in classes)


def _checker(*, failed: bool) -> types.SimpleNamespace:
    return types.SimpleNamespace(has_an_error=lambda: failed)


def _node(cls: type, span: Span) -> object:
    """A node of `cls` whose fields hold nothing but its span: no arm reads more here."""
    fields = {each.name: None for each in dataclasses.fields(cls)}
    fields["loc"] = span
    return cls(**fields)


@pytest.mark.parametrize("union", list(_UNIONS), ids=list(_UNIONS))
def test_each_member_of_a_node_union_has_a_row(union: str) -> None:
    missing = set(typing.get_args(_UNIONS[union])) - set(arms.ARMS)
    assert not missing, f"the borrow pass has no arm for: {_names(missing)}"


def test_the_table_has_no_row_for_another_class() -> None:
    extra = set(arms.ARMS) - _classes()
    assert not extra, f"the borrow pass has an arm for a class of no union: {_names(extra)}"


def test_every_row_is_callable() -> None:
    bad = [cls.__name__ for cls, arm in arms.ARMS.items() if not callable(arm)]
    assert not bad, f"these rows are no arm: {bad}"


def test_a_class_with_no_row_is_an_internal_error_at_its_span() -> None:
    @dataclasses.dataclass
    class NotANode:
        loc: Span

    span = Span(4, 2, 4, 7)
    with pytest.raises(InternalCompilerError) as caught:
        arms.check(object(), NotANode(span))
    assert caught.value.code == "CE0000"
    assert "NotANode" in str(caught.value.params.get("detail"))
    assert caught.value.span == span


@pytest.mark.parametrize("cls", sorted(_classes(), key=lambda cls: cls.__name__),
                         ids=lambda cls: cls.__name__)
def test_the_unhandled_arm_names_the_stage_the_class_and_the_span(cls: type) -> None:
    span = Span(3, 5, 3, 9)
    with pytest.raises(InternalCompilerError) as caught:
        arms.unhandled(_checker(failed=False), _node(cls, span))
    fault = caught.value
    assert fault.code == "CE0000"
    assert fault.params == {"detail": f"the borrow stage does not handle the construct "
                                      f"'{cls.__name__}'"}
    assert fault.span == span


def test_the_unhandled_arm_after_an_error_ends_the_walk() -> None:
    with pytest.raises(dispatch.AfterAnError):
        arms.unhandled(_checker(failed=True), _node(stmts.Assign, Span(3, 5, 3, 9)))


def test_a_callee_that_never_reaches_the_borrow_pass_is_an_internal_error_that_says_so() -> None:
    """The typecheck pass records a `Free`, a `Stdlib`, an `Intrinsic`, a `Builtin`, a
    `BuiltinStatic`, a `Method` and a `Static` callee, and every other call is an internal
    error there.
    A call with another callee row in the borrow pass is a fault of the compiler, and its
    internal error names the callee."""
    call = _node(exprs.Call, Span(6, 4, 6, 12))
    row = ResolvedCall(Conversion(None))  # type: ignore[arg-type]
    checker = types.SimpleNamespace(answers=lambda: types.SimpleNamespace(
        callee_of=lambda _id: row))
    with pytest.raises(InternalCompilerError) as caught:
        calls.check_call(checker, call)  # type: ignore[arg-type]
    assert caught.value.code == "CE0000"
    assert "Conversion" in str(caught.value.params.get("detail"))


@pytest.mark.parametrize("module", [arms, visit], ids=["borrow", "typecheck"])
def test_each_pass_reads_the_one_dispatch(module) -> None:
    """The lookup of an arm and the internal error of a construct that an arm does not
    handle come from `semantics/shir/dispatch.py`: a pass that
    looks its class up itself has a second dispatch with its own backstop."""
    tree = ast.parse(inspect.getsource(module))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "dispatch"}
    assert {"arm_of", "unhandled"} <= called, (
        f"{module.__name__} does not read the one dispatch of semantics/shir/dispatch.py")
    lookups = [node.lineno for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "get" and node.args
               and isinstance(node.args[0], ast.Call)
               and getattr(node.args[0].func, "id", None) == "type"]
    assert not lookups, (
        f"{module.__name__} looks a class up in its table by itself (lines {lookups})")


def test_the_one_dispatch_answers_the_row_of_a_class() -> None:
    def arm() -> None:
        return None

    assert dispatch.arm_of({int: arm}, 5, "probe") is arm
