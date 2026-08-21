"""The ownership decision at a consuming use, and the provenance it is taken from."""

from __future__ import annotations
from dataclasses import replace
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Expr, Lambda, Let, Name, Spread, StringLit
from sushi_lang.semantics.ownership import Ownership, Provenance, classify
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, ReferenceType

from .diagnostics import emit_consume_of_borrow, emit_consume_of_read
from .reads import read_type, reads_through_owner, root_owner
from .state import BorrowState
from .writes import check_owner_not_borrowed

if TYPE_CHECKING:
    from . import BorrowChecker


def binds_a_bare_literal_string(declared_ty, init) -> bool:
    """Option B: is this binding a `string` whose value is a plain literal?"""
    return declared_ty == BuiltinType.STRING and isinstance(init, StringLit)


def reconcile_closure_bind(checker: 'BorrowChecker', stmt: Let) -> None:
    """Record whether a `fn(...)` binding owns a heap environment."""
    if not isinstance(stmt.ty, FunctionType):
        return
    dest = checker.borrow_state.get(stmt.name)
    if dest is None:
        return
    value = stmt.value
    if isinstance(value, Lambda):
        dest.var_type = replace(stmt.ty, captures=tuple(value.captures or ()))
    elif isinstance(value, Name):
        src = checker.borrow_state.get(value.id)
        if src is None:
            # Not a local: a reference to a top-level function, which captures
            # nothing. State the empty tuple -- leaving it None would read as
            # "unstated", and a plain fn reference would then move on every use.
            dest.var_type = replace(stmt.ty, captures=())
        elif isinstance(src.var_type, FunctionType):
            dest.var_type = src.var_type


def source_provenance(checker: 'BorrowChecker', expr: Expr) -> Provenance:
    """Where the value at a consuming use came from -- the half only semantics knows."""
    if isinstance(expr, Name):
        return name_provenance(checker, expr.id)

    if reads_through_owner(checker, expr):
        return Provenance.BORROWED

    return Provenance.FRESH


def name_provenance(checker: 'BorrowChecker', name: str) -> Provenance:
    """The `Provenance` of a source that is a bare name."""
    state = checker.borrow_state.get(name)
    if state is None:
        return Provenance.FRESH
    if state.owns_no_heap:
        # Nothing to borrow: this binding's value owns no heap, so every position may
        # have it. Only a `string` bound straight from a literal answers True (#338
        # removed the method-parameter exemption -- the view it let out dangled).
        #
        # OWNED and not BORROWED, and that is about the SEAM: the backend re-derives
        # the class from the TYPE alone, so it answers MOVE for any `string`, and
        # (BORROWED, MOVE) is REJECT -- a CE0129 for a sound shape.
        return Provenance.OWNED
    if (state.is_borrowed_binding
            or state.is_borrow_param
            or isinstance(state.var_type, ReferenceType)):
        return Provenance.BORROWED
    return Provenance.OWNED


def consume(checker: 'BorrowChecker', expr: Expr, use) -> None:
    """Classify a consuming use, stamp the decision, and act on it."""
    # A bloom `arr...` MOVES its source into the callee. CE0120 restricts the source
    # to a bare array variable, so unwrapping here makes a use-after-bloom a CE2405
    # instead of a use-after-free (#174).
    if isinstance(expr, Spread):
        expr = expr.value

    provenance = source_provenance(checker, expr)
    # Only PROVENANCE is stamped. The `use` is the backend's to name: semantics
    # cannot tell `S(x)` from `f(x)` -- both are a `Call` here.
    expr.ownership_provenance = provenance

    if isinstance(expr, Name):
        consume_named(checker, expr.id, provenance, expr.loc)
        return

    # A read through a live owner has no owner to mark moved, but it CAN be rejected
    # (#242): the owner keeps the value and still frees it. Leaving this cell copying
    # made the backend answer REJECT with no diagnostic ahead of it -- a CE0129.
    if provenance is not Provenance.BORROWED:
        return
    if classify(provenance,
                checker.types.type_class(read_type(checker, expr))) is Ownership.REJECT:
        emit_consume_of_read(checker, expr)


def consume_each(checker: 'BorrowChecker', args, use) -> None:
    """Consume every argument of an ownership sink at `use`."""
    for arg in args:
        consume(checker, arg, use)


def consume_named(checker: 'BorrowChecker', name: str, provenance: Provenance,
                  use_span: Optional[Span]) -> None:
    """Apply the ownership decision to a source that is a bare name."""
    state = checker.borrow_state.get(name)
    if state is None:
        return

    if state.is_argv_view:
        # Moving main's borrowed argv view would double-free process argv (N2). A more
        # specific diagnostic than CE2411, so it wins.
        checker.err.emit(er.ERR.CE2410, use_span, name=name)
        return

    decision = classify(provenance,
                        checker.types.type_class_of_source(state, state.var_type))
    if decision is Ownership.MOVE:
        # The same value cannot be borrowed and handed away in ONE statement (CE2401).
        # Here because the counters are live: the call arm registers every argument's
        # borrow before consuming any, so both orders of `both(peek s, s)` are one
        # rule.
        if state.is_borrowed:
            checker.err.emit_with(er.ERR.CE2401, use_span, name=name) \
                .note("borrowed here, in the same statement",
                      state.first_borrow_span) \
                .help(f"the new owner frees this value while the borrow still points "
                      f"at it; borrow it twice, or clone what the owning position "
                      f"needs: `{name}.clone()`") \
                .emit()
            return
        # Handing the owner away leaves every binding reading out of it pointing at
        # storage the new owner frees (#242).
        check_owner_not_borrowed(checker, name, use_span, "move")
        state.is_moved = True
        state.moved_at_span = state.moved_at_span or use_span
        # A move deeper than the owner's declaration cannot dominate the scope exit,
        # so the backend guards this owner's frees with a runtime drop flag (#414).
        if checker.branch_depth > state.declared_branch_depth:
            checker.conditional_moves.add(state.name)
    elif decision is Ownership.REJECT:
        emit_consume_of_borrow(checker, name, use_span, state)


def bind(checker: 'BorrowChecker', stmt: Let) -> None:
    """Give a `let` binding the provenance of its initializer (#242)."""
    expr = stmt.value
    provenance = source_provenance(checker, expr)
    expr.ownership_provenance = provenance

    dest = checker.borrow_state.get(stmt.name)
    if dest is None:
        return

    src_state = checker.borrow_state.get(expr.id) if isinstance(expr, Name) else None

    if src_state is not None and src_state.is_argv_view:
        # Binding main's argv view by value would make the new binding free argv
        # (N2). The same hard error as any other move of it, and more specific than
        # anything the table says.
        checker.err.emit(er.ERR.CE2410, expr.loc, name=expr.id)
        return

    # The SOURCE's recorded type where there is one: the DECLARED type has lost the
    # capture list, so it would move a plain fn reference and report a false CE2405.
    ty = src_state.var_type if src_state is not None else stmt.ty

    decision = classify(provenance, checker.types.type_class_of_source(src_state, ty))
    if decision is Ownership.MOVE:
        src_state.is_moved = True
        src_state.moved_at_span = src_state.moved_at_span or expr.loc
        # Same rule as consume(): a conditional move needs a runtime drop flag (#414).
        if checker.branch_depth > src_state.declared_branch_depth:
            checker.conditional_moves.add(src_state.name)
    elif decision is Ownership.REJECT:
        record_borrowed_binding(checker, stmt, dest)


def record_borrowed_binding(checker: 'BorrowChecker', stmt: Let,
                            dest: BorrowState) -> None:
    """Record that a `let` borrows storage its initializer's owner keeps (#242)."""
    dest.is_borrowed_binding = True
    dest.is_let_borrow = True
    dest.bound_at_span = stmt.loc

    owner = root_owner(stmt.value)
    if owner is None:
        return
    owner_state = checker.borrow_state.get(owner)
    if owner_state is None:
        return
    dest.borrows_from = owner
    owner_state.binding_borrows.append((stmt.name, stmt.loc))
    checker._scope_binding_borrows[-1].append((owner, stmt.name))
