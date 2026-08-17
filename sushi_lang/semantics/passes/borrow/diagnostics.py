"""Rendering for the relational borrow diagnostics -- each points at its second location."""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import (
    BinaryOp,
    DotCall,
    Expr,
    IndexAccess,
    IntLit,
    MemberAccess,
    MethodCall,
    Name,
    TryExpr,
)
from sushi_lang.semantics.typesys import ReferenceType

from .reads import root_owner
from .state import BorrowState

if TYPE_CHECKING:
    from . import BorrowChecker


def emit_use_after_move(checker: 'BorrowChecker', name: str, use_span: Optional[Span],
                        state: BorrowState) -> None:
    """Report a use-after-move, pointing at the MOVE as well as the use."""
    diag = checker.err.emit_with(er.ERR.CE2405, use_span, name=name)
    if state.moved_at_span is not None:
        diag.note(f"'{name}' was moved here", state.moved_at_span)
    diag.emit()


def emit_use_of_invalidated_borrow(checker: 'BorrowChecker', name: str,
                                   use_span: Optional[Span],
                                   state: BorrowState) -> None:
    """Report CE2412 at the change, and name the later use that makes it wrong."""
    owner, what = state.invalidated_by
    diag = checker.err.emit_with(er.ERR.CE2412, state.invalidated_at,
                                 owner=owner, name=name)
    if state.bound_at_span is not None:
        diag.note(f"'{name}' borrows from '{owner}' here", state.bound_at_span)
    diag.note(f"'{name}' is used here, after the change", use_span)
    diag.help(f"{what} after the last use of '{name}', "
              f"or bind an independent value with `.clone()`")
    diag.emit()
    # Report once per binding. A suppressed (loop-discovery) pass reported nothing, so
    # it must consume nothing -- clearing there erases the real pass's invalidation.
    if not checker.err.suppressed:
        state.invalidated_at = None


def emit_consume_of_read(checker: 'BorrowChecker', expr: Expr) -> None:
    """Report CE2411 for a read through a live owner (`h.inner`, `c.get(0)??`)."""
    text = expr_to_string(expr)
    diag = checker.err.emit_with(er.ERR.CE2411, expr.loc, name=text)
    owner = root_owner(expr)
    state = checker.borrow_state.get(owner) if owner is not None else None
    if state is not None and state.declared_at_span is not None:
        diag.note(f"'{owner}' owns this value and still frees it",
                  state.declared_at_span)
    # ONE branch, on purpose: a get-out `.clone()` still hits CE0019, and that is a
    # real defect rather than a reason to word around it. The three RED
    # `test_own_get_*` files hold the branch honest until it is fixed.
    diag.help(f"clone it to take an independent value: `{text}.clone()`")
    diag.emit()


def emit_consume_of_borrow(checker: 'BorrowChecker', name: str,
                           use_span: Optional[Span], state: BorrowState) -> None:
    """Report CE2411, pointing at the binding as well as the use."""
    diag = checker.err.emit_with(er.ERR.CE2411, use_span, name=name)
    if state.bound_at_span is not None:
        diag.note(f"'{name}' borrows here, and the owner keeps the value",
                  state.bound_at_span)
    elif isinstance(state.var_type, ReferenceType) \
            and state.declared_at_span is not None:
        diag.note(f"'{name}' is declared here as a `&{state.var_type.mutability}` "
                  f"borrow of the caller's value",
                  state.declared_at_span)
    elif state.is_method_receiver:
        # Unguarded on purpose: this sentence still says something without a location,
        # so a receiver whose type span is missing degrades to a note rather than to
        # nothing. The parameter arm below needs its location to read at all.
        diag.note("'self' is the receiver of a method on this type, which borrows "
                  "the caller's value", state.declared_at_span)
    elif state.is_borrow_param and state.declared_at_span is not None:
        diag.note(f"'{name}' is declared here, and a method parameter borrows the "
                  f"caller's value", state.declared_at_span)
    diag.help(f"clone it to take an independent value: `{name}.clone()`")
    diag.emit()


def expr_to_string(expr: Expr) -> str:
    """Convert an expression to a string for error messages."""
    if isinstance(expr, Name):
        return expr.id
    elif isinstance(expr, IntLit):
        return str(expr.value)
    elif isinstance(expr, BinaryOp):
        return f"({expr_to_string(expr.left)} {expr.op} {expr_to_string(expr.right)})"
    elif isinstance(expr, (MethodCall, DotCall)):
        # Both spellings reach here, arguments included, so the text matches what the
        # user wrote and the `help` is something they can paste.
        args = ", ".join(expr_to_string(a) for a in (getattr(expr, "args", None) or []))
        return f"{expr_to_string(expr.receiver)}.{expr.method}({args})"
    elif isinstance(expr, MemberAccess):
        return f"{expr_to_string(expr.receiver)}.{expr.member}"
    elif isinstance(expr, IndexAccess):
        return f"{expr_to_string(expr.array)}[{expr_to_string(expr.index)}]"
    elif isinstance(expr, TryExpr):
        return f"{expr_to_string(expr.expr)}??"
    else:
        return "<expression>"
