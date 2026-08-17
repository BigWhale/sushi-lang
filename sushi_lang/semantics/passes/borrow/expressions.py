"""Expression walking. Every `Expr` node has an arm; the `else` is a hard CE0125."""

from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    ArrayLiteral,
    BinaryOp,
    BlankLit,
    BoolLit,
    Borrow,
    CastExpr,
    Call,
    DotCall,
    DynamicArrayFrom,
    DynamicArrayNew,
    EnumConstructor,
    Expr,
    FloatLit,
    IndexAccess,
    InterpolatedString,
    IntLit,
    Lambda,
    MemberAccess,
    MethodCall,
    Name,
    RangeExpr,
    Spread,
    StringLit,
    TryExpr,
    UnaryOp,
)
from sushi_lang.semantics.ownership import ConsumingUse

from .borrows import check_borrow
from .calls import (
    apply_destroy_effects,
    consume_call_args,
    consume_indirect_args,
    is_enum_constructor,
    maybe_mark_container_insert,
    maybe_mark_own_alloc_move,
    settle_method_args,
)
from .consume import consume, consume_each, consume_named, name_provenance
from .diagnostics import emit_use_after_move, emit_use_of_invalidated_borrow
from .writes import maybe_reject_mutation

if TYPE_CHECKING:
    from . import BorrowChecker


# Nodes that own nothing and name nothing. Listed EXPLICITLY so `check_expr`'s `case _`
# can be a hard error (CE0125) instead of a silent skip.
INERT_EXPRS = (IntLit, FloatLit, BoolLit, BlankLit, StringLit, DynamicArrayNew)


def check_expr(checker: 'BorrowChecker', expr: Expr) -> None:
    """Check borrow safety for an expression."""
    match expr:
        case Borrow():
            check_borrow(checker, expr)
        case Name():
            _check_name(checker, expr)
        case Call():
            _check_call(checker, expr)
        case MethodCall():
            _check_method_call(checker, expr)
        case DotCall():
            _check_dot_call(checker, expr)
        case BinaryOp():
            check_expr(checker, expr.left)
            check_expr(checker, expr.right)
        case UnaryOp() | CastExpr() | TryExpr():
            check_expr(checker, expr.expr)
        case IndexAccess():
            check_expr(checker, expr.array)
            check_expr(checker, expr.index)
        case MemberAccess():
            check_expr(checker, expr.receiver)
        case RangeExpr():
            check_expr(checker, expr.start)
            check_expr(checker, expr.end)
        case Spread():
            # Bloom: `arr...`. The source is USED here, so a moved source is reported;
            # the MOVE happens in `consume_call_args`, which unwraps the Spread.
            check_expr(checker, expr.value)
        case EnumConstructor():
            _check_sink_elements(checker, expr.args, ConsumingUse.ENUM_PAYLOAD)
        case DynamicArrayFrom():
            _check_sink_elements(checker, expr.elements.elements,
                                 ConsumingUse.ARRAY_ELEMENT)
        case ArrayLiteral():
            _check_sink_elements(checker, expr.elements, ConsumingUse.ARRAY_ELEMENT)
        case InterpolatedString():
            for part in expr.parts:
                if not isinstance(part, str):
                    check_expr(checker, part)
        case Lambda():
            _check_lambda(checker, expr)
        case _ if isinstance(expr, INERT_EXPRS):
            return
        case _:
            # NOT a silent fall-through: a node with no arm gets NO borrow checking, a
            # soundness hole rather than a crash (#174, #175, #176). The CI gate is
            # tests/unit/test_borrow_dispatch_is_total.py; this is the backstop.
            er.raise_internal_error("CE0125", node=type(expr).__name__)


def _check_name(checker: 'BorrowChecker', expr: Name) -> None:
    """A bare name: report a use after a move, after a destroy, or after an invalidation."""
    state = checker.borrow_state.get(expr.id)
    if state is None:
        return
    if state.is_moved:
        emit_use_after_move(checker, expr.id, expr.loc, state)
    elif state.is_destroyed:
        checker.err.emit(er.ERR.CE2406, expr.loc, name=expr.id)
    elif state.invalidated_at is not None:
        # A `let`-borrow binding read after its owner changed (#242).
        emit_use_of_invalidated_borrow(checker, expr.id, expr.loc, state)


def _check_call(checker: 'BorrowChecker', expr: Call) -> None:
    """A direct or indirect call: walk it, then apply the callee's declared modes."""
    check_expr(checker, expr.callee)
    for arg in expr.args:
        check_expr(checker, arg)
    # An argument is consumed if and only if the parameter it lands on DECLARES a
    # consume. The callee's kind decides where the declaration is read from
    # (docs/design/borrow-model.md S5); the mode decides what happens.
    consume_call_args(checker, expr)
    # A callee that destroys its `poke` parameter destroys the CALLER's value (#168).
    # CE2406 still fires from the Name arm above -- no new emit site.
    apply_destroy_effects(checker, expr)


def _check_method_call(checker: 'BorrowChecker', expr: MethodCall) -> None:
    """`x.m(args)`: gate the write, then apply the method's declared modes."""
    _check_receiver_and_args(checker, expr)
    maybe_reject_mutation(checker, expr)
    settle_method_args(checker, expr)
    maybe_mark_container_insert(checker, expr)
    maybe_mark_own_alloc_move(checker, expr)


def _check_dot_call(checker: 'BorrowChecker', expr: DotCall) -> None:
    """`X.Y(args)`: an enum constructor, an indirect call, an FFI call, or a method."""
    # An FFI string argument NEVER consumes (docs/design/borrow-model.md S5), and that
    # holds STRUCTURALLY: an FFI call arrives as a DotCall, and this function consumes
    # only for an enum constructor, an indirect call and a container insert. Do NOT add
    # a blanket `consume(arg, CALL_ARG)` loop -- it would make every `libc.*(s)` call
    # site a false CE2405. `tests/ffi/test_ffi_string_arg_not_consumed.sushi` is the gate.
    _check_receiver_and_args(checker, expr)
    maybe_reject_mutation(checker, expr)
    if is_enum_constructor(checker, expr):
        # `Box.Full(a)` arrives here as a DotCall, not an EnumConstructor.
        consume_each(checker, expr.args, ConsumingUse.ENUM_PAYLOAD)
    elif getattr(expr, "callee_fn_type", None) is not None:
        # Keyed on Pass 2's `callee_fn_type` stamp, so an FFI / extension / builtin
        # method keeps the rule above.
        consume_indirect_args(checker, expr)
    else:
        settle_method_args(checker, expr)
        maybe_mark_container_insert(checker, expr)
    maybe_mark_own_alloc_move(checker, expr)


def _check_lambda(checker: 'BorrowChecker', expr: Lambda) -> None:
    """Each capture is the CAPTURE consuming use -- the heap env outlives this scope."""
    # A capture holds no source `Expr`, so the provenance goes on the `Param` itself and
    # `emit_lambda` reads it there.
    for cap in (expr.captures or []):
        if not isinstance(cap.name, str):
            continue
        provenance = name_provenance(checker, cap.name)
        cap.ownership_provenance = provenance
        consume_named(checker, cap.name, provenance, expr.loc)


def _check_receiver_and_args(checker: 'BorrowChecker', expr) -> None:
    """Walk a method-shaped call's receiver and every argument."""
    check_expr(checker, expr.receiver)
    for arg in expr.args:
        check_expr(checker, arg)


def _check_sink_elements(checker: 'BorrowChecker', elements, use) -> None:
    """Walk each element of an ownership sink, then consume it (#134).

    A bare owning element variable MOVES into the constructed value; a MemberAccess
    element keeps its continuing-owner copy, because only bare Names are marked moved.
    """
    for elem in elements:
        check_expr(checker, elem)
        consume(checker, elem, use)
