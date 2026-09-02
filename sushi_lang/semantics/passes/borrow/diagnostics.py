"""Rendering for the relational borrow diagnostics -- each points at its second location."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

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


@dataclass(frozen=True)
class BorrowKind:
    """One reason a value may not be consumed, as DATA."""
    matches: Callable[[BorrowState], bool]
    note_span: Callable[[BorrowState], Optional[Span]]
    note: str


# Which kind of borrow is this? The same question `writes.READONLY_RECEIVERS` answers for
# a WRITE, answered here for a CONSUME. Most specific first, and unlike that table the
# order IS precedence: a row whose span is missing falls through to the next one, which
# is how a reference parameter with no span still gets the `is_borrow_param` sentence.
BORROW_KINDS: tuple[BorrowKind, ...] = (
    BorrowKind(
        matches=lambda state: state.bound_at_span is not None,
        note_span=lambda state: state.bound_at_span,
        note="'{name}' borrows here, and the owner keeps the value",
    ),
    BorrowKind(
        matches=lambda state: (isinstance(state.var_type, ReferenceType)
                               and state.declared_at_span is not None),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is declared here as a `&{mode}` borrow of the caller's value",
    ),
    BorrowKind(
        # No span requirement, on purpose: this sentence still says something without a
        # location, so a receiver whose type span is missing degrades to a note rather
        # than to nothing. The row below needs its location to read at all.
        matches=lambda state: state.is_method_receiver,
        note_span=lambda state: state.declared_at_span,
        note="'self' is the receiver of a method on this type, which borrows the "
             "caller's value",
    ),
    BorrowKind(
        matches=lambda state: (state.is_borrow_param
                               and state.declared_at_span is not None),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is declared here, and a method parameter borrows the caller's "
             "value",
    ),
)


def emit_use_after_move(checker: 'BorrowChecker', name: str, use_span: Optional[Span],
                        state: BorrowState) -> None:
    """Report a use after the value left, pointing at where it left as well as the use.

    Two codes, and the method name is what tells them apart (ruling R27). A `nom`
    ARGUMENT is a real move with a visible marker, so it keeps CE2405. A consuming
    RECEIVER has no marker anywhere on the page -- a receiver's mode is
    declaration-only -- so CE2435 has to carry what the syntax cannot, and it names the
    method.
    """
    method = state.consumed_by_method
    if method is not None:
        diag = checker.err.emit_with(er.ERR.CE2435, use_span,
                                     name=name, method=method)
        if state.moved_at_span is not None:
            diag.note(f"'{name}' was consumed by '{method}' here", state.moved_at_span)
        diag.emit()
        return
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
    # Report once per binding. A suppressed (loop-discovery) pass reported nothing, so it
    # must consume nothing -- clearing there erases the real pass's invalidation.
    if not checker.err.suppressed:
        state.invalidated_at = None


def escape_help(checker: 'BorrowChecker', text: str, ty) -> str:
    """What CE2411 offers as the way out, which depends on WHAT is being consumed.

    `.clone()` for an ordinary owning value. A resource type has no clone (CE2431), so
    offering one there would be a rejection with no escape -- the exact hole the clone
    totality gate exists to prevent (HANDLES.md ruling R3). For a resource the second
    owner is `.share()`, and the message says why a descriptor cannot be deep-copied.
    """
    from sushi_lang.semantics.typesys import holds_declared_resource
    drops = checker.types.drops
    if drops and holds_declared_resource(ty, drops, resolve=checker.types.resolve_named):
        return (f"a descriptor cannot be deep-copied, so there is no `{text}.clone()`; "
                f"take a second owner with `{text}.share()`, or restructure so only one "
                f"owner is needed")
    return f"clone it to take an independent value: `{text}.clone()`"


def emit_consume_of_read(checker: 'BorrowChecker', expr: Expr) -> None:
    """Report CE2411 for a read through a live owner (`h.inner`, `c.get(0)??`)."""
    text = expr_to_string(expr)
    diag = checker.err.emit_with(er.ERR.CE2411, expr.loc, name=text)
    owner = root_owner(expr)
    state = checker.borrow_state.get(owner) if owner is not None else None
    if state is not None and state.declared_at_span is not None:
        diag.note(f"'{owner}' owns this value and still frees it",
                  state.declared_at_span)
    # The OWNER's type answers the escape question: a read through it can only be
    # refused when what is read owns something, and it is the owner that holds it.
    owner_type = state.var_type if state is not None else None
    # ONE branch, on purpose: a get-out `.clone()` still hits CE0019, and that is a real
    # defect rather than a reason to word around it. The three RED `test_own_get_*` files
    # hold the branch honest until it is fixed.
    diag.help(escape_help(checker, text, owner_type))
    diag.emit()


def emit_consume_of_borrow(checker: 'BorrowChecker', name: str,
                           use_span: Optional[Span], state: BorrowState) -> None:
    """Report CE2411, pointing at the binding or declaration as well as the use."""
    diag = checker.err.emit_with(er.ERR.CE2411, use_span, name=name)
    for kind in BORROW_KINDS:
        if kind.matches(state):
            mode = getattr(state.var_type, "mutability", "")
            diag.note(kind.note.format(name=name, mode=mode), kind.note_span(state))
            break
    diag.help(escape_help(checker, name, state.var_type))
    diag.emit()


def expr_to_string(expr: Expr) -> str:
    """Render an expression back to source text, for a message the user can paste."""
    match expr:
        case Name():
            return expr.id
        case IntLit():
            return str(expr.value)
        case BinaryOp():
            return (f"({expr_to_string(expr.left)} {expr.op} "
                    f"{expr_to_string(expr.right)})")
        case MethodCall() | DotCall():
            # Both spellings reach here, arguments included, so the text matches what the
            # user wrote.
            args = ", ".join(expr_to_string(a) for a in (expr.args or []))
            return f"{expr_to_string(expr.receiver)}.{expr.method}({args})"
        case MemberAccess():
            return f"{expr_to_string(expr.receiver)}.{expr.member}"
        case IndexAccess():
            return f"{expr_to_string(expr.array)}[{expr_to_string(expr.index)}]"
        case TryExpr():
            return f"{expr_to_string(expr.expr)}??"
        case _:
            return "<expression>"
