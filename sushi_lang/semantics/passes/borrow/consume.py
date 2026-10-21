"""The ownership decision at a consuming use, and the provenance it is taken from.

A consuming use is a value in a position that takes it over (`ConsumingUse`,
`semantics/ownership.py`; the positions of SHIR are `consuming_uses` in
`semantics/shir/ownership_check.py`). The pass decides its `Ownership` ONE time, with
`classify()`, and writes the answer as a row of the `OwnershipResults` of the body
(`decide`, docs/design/ir.md 7.9). A `let` BINDS: it takes the decision too, and a borrow
that it reads is a `let`-borrow of the owner (`bind`).
"""

from __future__ import annotations
from dataclasses import replace
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Expr, IndexAccess, Lambda, Let, Name
from sushi_lang.semantics.ownership import Ownership, Provenance, TypeClass, classify
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, ReferenceType

from .diagnostics import (
    CopyUse,
    emit_consume_of_borrow,
    emit_consume_of_read,
    no_clone_reason,
    refuses_clone,
)
from .reads import borrow_owner, names_kept_storage, read_type
from .state import BorrowState, record_owners
from .writes import check_owner_not_borrowed

if TYPE_CHECKING:
    from . import BorrowChecker


def binds_a_bare_literal_string(declared_ty, init) -> bool:
    """Option B: is this binding a `string` whose value is a plain text literal?"""
    return (declared_ty == BuiltinType.STRING and isinstance(init, e.Lit)
            and isinstance(init.value, e.StrConst))


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


def source_provenance(checker: 'BorrowChecker', node: e.ShirExpr) -> Provenance:
    """Where the value at a consuming use came from -- the half only semantics knows.

    A local has the provenance of its state. A constant is storage that the program keeps
    (#726): it is not FRESH, so a take of an owning one is refused. `r??` yields what `r`
    holds, and the payload has the provenance of `r` (#548): a borrowed `r` keeps its
    owner, so the payload is a read through it; an owned `r` was SPENT by the `??` itself
    (`unwrap_place`), so nothing owns the payload now and the position adopts it.
    Anything else makes a new value.
    """
    if isinstance(node, e.LocalRef):
        return name_provenance(checker, node.local)
    if isinstance(node, e.ConstRef):
        return Provenance.OWNED
    if isinstance(node, e.Try) and isinstance(node.inner, e.LocalRef):
        if name_provenance(checker, node.inner.local) is Provenance.BORROWED:
            return Provenance.BORROWED
    return Provenance.FRESH


def source_class(checker: 'BorrowChecker', node: e.ShirExpr) -> TypeClass:
    """What the value at a consuming use owns. A local applies option B (`owns_no_heap`)."""
    if isinstance(node, e.LocalRef):
        state = checker.borrow_state.get(node.local)
        ty = state.var_type if state is not None else checker.local_type(node.local)
        return checker.types.type_class_of_source(state, ty)
    return checker.types.type_class(read_type(checker, node))


def decide(checker: 'BorrowChecker', node: e.ShirExpr, provenance: Provenance,
           type_class: TypeClass) -> Ownership:
    """The `Ownership` of the consuming use `node`, from `classify()`, written ONE time as a
    row of the `OwnershipResults` of the body. The one writer of the row.

    A discovery round of a loop decides and writes nothing: the round after it decides
    from the facts of the fixed point.
    """
    decision = classify(provenance, type_class)
    if not checker.discovery_rounds:
        checker.table().record_ownership(node.shir_id, decision)
    return decision


def read_through_receiver(checker: 'BorrowChecker', expr: Expr) -> Optional[Expr]:
    """The receiver of a built-in call that reads a borrowed receiver through, or None.

    `m.or_err(nom e)` takes `nom self`, but a BORROWED `m` whose payload owns a resource
    is read through, as `??` reads through a borrowed wrapper
    (docs/design/error-conversion.md section 4). The answer then carries the borrow. A
    receiver that owns nothing is copied, so it is not read through. Under a `??` the call
    is transparent (`places.try_operand`), so this asks about the call itself.
    """
    from sushi_lang.semantics.generics.builtin_signatures import read_through_receiver_of
    receiver = read_through_receiver_of(expr)
    if receiver is None:
        return None
    if checker.types.type_class(getattr(expr, "resolved_enum_type", None)) \
            is not TypeClass.MOVE:
        return None
    return receiver


def unwrap_place(checker: 'BorrowChecker', node: e.Try) -> None:
    """`r??` spends an owned `r`, and refuses a borrowed `r` whose error owns (#548, #1171).

    The unwrap moves the payload out of the wrapper, so a wrapper the writer OWNS has
    nothing left to free: the `??` is a consuming position of its own, and `r` after it
    is CE2405 exactly as after `f(nom r)`. The class is the WRAPPER's and not the
    payload's, because the Err arm travels too. A wrapper that owns nothing is copied out
    of and stays usable.

    A BORROWED wrapper keeps its owner. Its Ok payload is a read through it, and
    `source_provenance` says so at the position that takes it. Its error leaves the
    function in the returned Err, so an error type that owns is the consuming use of a
    borrow (CE2411, docs/design/borrow-model.md S10d); the help names `r.clone()??`.
    Any other operand makes a new value, which the `??` adopts.
    """
    inner = node.inner
    provenance = source_provenance(checker, inner)
    if isinstance(inner, e.LocalRef) and provenance is Provenance.OWNED:
        consume_named(checker, inner, provenance, node.loc)
        return
    if propagates_a_borrowed_error(checker, node):
        consume(checker, inner, PROPAGATE_THE_COPY)
        return
    decide(checker, inner, provenance, source_class(checker, inner))


# What the CE2411 escape does with the copy of a borrowed wrapper under `??`.
PROPAGATE_THE_COPY = CopyUse("propagate the copy", "??")


def propagates_a_borrowed_error(checker: 'BorrowChecker', node: e.Try) -> bool:
    """Does `r??` put an owning error that another owner keeps in the returned Err?

    The error type is a fact of the `??` that the typecheck pass answers (`TryFacts`).
    """
    if source_provenance(checker, node.inner) is not Provenance.BORROWED:
        return False
    facts = checker.answers().try_facts_of(node.shir_id)
    return facts is not None and checker.types.type_class(facts.err) is TypeClass.MOVE


def name_provenance(checker: 'BorrowChecker', local) -> Provenance:
    """The `Provenance` of a source that is a local."""
    state = checker.borrow_state.get(local)
    if state is None:
        return Provenance.FRESH
    if state.owns_no_heap:
        # Nothing to borrow: this binding's value owns no heap, so every position may
        # have it. Only a `string` bound straight from a literal answers True (#338
        # removed the method-parameter exemption -- the view it let out dangled).
        return Provenance.OWNED
    if (state.is_borrowed_binding
            or state.is_borrow_param
            or isinstance(state.var_type, ReferenceType)):
        return Provenance.BORROWED
    return Provenance.OWNED


def consume(checker: 'BorrowChecker', node: e.ShirExpr,
            use_of_copy: Optional[CopyUse] = None) -> Ownership:
    """Decide a consuming use, write the decision, and act on it.

    `use_of_copy` is what the position does with the copy that the CE2411 escape takes:
    `x.clone() as T`, `r.clone().map_err(f)`.
    """
    provenance = source_provenance(checker, node)
    if isinstance(node, e.LocalRef):
        return consume_named(checker, node, provenance, node.loc, use_of_copy)
    decision = decide(checker, node, provenance, source_class(checker, node))
    if isinstance(node, e.ConstRef):
        reject_move_of_constant(checker, node, decision)
    elif decision is Ownership.REJECT:
        # A read through a live owner has no owner to mark moved, but it CAN be rejected
        # (#242): the owner keeps the value and still frees it.
        emit_consume_of_read(checker, node, use_of_copy)
    return decision


def consume_each(checker: 'BorrowChecker', args) -> None:
    """Consume every argument of an ownership sink."""
    for arg in args:
        consume(checker, arg)


def reject_move_of_constant(checker: 'BorrowChecker', node: e.ConstRef,
                            decision: Ownership) -> bool:
    """CE2436 for a take of a CONSTANT that would be a move. True when it was refused.

    The `const` half of the unit-storage rule (#726): a constant is one object the program
    keeps for its whole run, and it has no owner that can hand it away. A plain value
    still copies out. A constant behind an alias is the same declaration (#1016).
    """
    if decision is not Ownership.MOVE:
        return False
    checker.err.emit(er.ERR.CE2436, node.loc, name=checker.decl_name(node.const_id),
                     kind="a constant")
    return True


def consume_named(checker: 'BorrowChecker', node: e.LocalRef, provenance: Provenance,
                  use_span: Optional[Span],
                  use_of_copy: Optional[CopyUse] = None) -> Ownership:
    """Decide a consuming use of a local, and apply the decision to the local."""
    state = checker.borrow_state.get(node.local)
    ty = state.var_type if state is not None else checker.local_type(node.local)
    decision = decide(checker, node, provenance,
                      checker.types.type_class_of_source(state, ty))
    if state is None:
        return decision

    if state.is_argv_view:
        # Moving main's borrowed argv view would double-free process argv (N2). A more
        # specific diagnostic than CE2411, so it wins.
        checker.err.emit(er.ERR.CE2410, use_span, name=state.name)
        return decision

    if decision is Ownership.MOVE:
        # The same value cannot be borrowed and handed away in ONE statement (CE2401).
        # Here because the counters are live: the call arm registers every argument's
        # borrow before consuming any, so both orders of `both(peek s, s)` are one
        # rule.
        if state.is_borrowed:
            name = state.name
            diag = checker.err.emit_with(er.ERR.CE2401, use_span, name=name)
            if state.first_borrow_span is not None:
                diag.note_at("borrowed here, in the same statement",
                             state.first_borrow_span)
            if refuses_clone(checker, state.var_type):
                diag.help(f"the new owner frees this value while the borrow still "
                          f"points at it; borrow it twice: "
                          f"{no_clone_reason(checker, name, state.var_type)}")
            else:
                diag.help(f"the new owner frees this value while the borrow still "
                          f"points at it; borrow it twice, or clone what the owning "
                          f"position needs: `{name}.clone()`")
            diag.emit()
            return decision
        _move_out(checker, state, use_span)
    elif decision is Ownership.REJECT:
        emit_consume_of_borrow(checker, state.name, use_span, state, use_of_copy)
    return decision


def _move_out(checker: 'BorrowChecker', state: BorrowState,
              use_span: Optional[Span]) -> None:
    """Hand a local away: a `nom` argument, a `??`, and a `let` initializer alike."""
    # Handing the owner away leaves every binding reading out of it pointing at
    # storage the new owner frees (#242).
    check_owner_not_borrowed(checker, state.local, use_span, "move")
    state.is_moved = True
    state.is_moved_on_every_path = True
    state.moved_at_span = state.moved_at_span or use_span
    state.move_reported_by = None
    # A local moved here and not moved on every path at an exit of its scope is
    # conditionally moved (`flow.record_exit`, #414).
    if state.local is not None:
        checker.moved_locals.add(state.local)


def bind(checker: 'BorrowChecker', node: s.Assign, dest: BorrowState) -> None:
    """Give a `let` the provenance of its value (#242).

    A `let` takes the decision of a consuming use: an owned local moves into the binding,
    a new value is adopted, and a read of a borrow makes the binding a `let`-borrow of
    the owner, so the owner is frozen while the binding lives.
    """
    value = node.value
    provenance = source_provenance(checker, value)
    src_state = (checker.borrow_state.get(value.local)
                 if isinstance(value, e.LocalRef) else None)
    if src_state is not None and src_state.is_argv_view:
        # Binding main's argv view by value would make the new binding free argv (N2).
        decide(checker, value, provenance, source_class(checker, value))
        checker.err.emit(er.ERR.CE2410, value.loc, name=src_state.name)
        return
    decision = decide(checker, value, provenance, source_class(checker, value))
    if isinstance(value, e.ConstRef):
        reject_move_of_constant(checker, value, decision)
    elif decision is Ownership.MOVE and src_state is not None:
        _move_out(checker, src_state, value.loc)
    elif decision is Ownership.REJECT:
        record_borrowed_binding(checker, node, dest)


def record_borrowed_binding(checker: 'BorrowChecker', node: s.Assign,
                            dest: BorrowState) -> None:
    """Record that a `let` borrows storage its value's owner keeps (#242)."""
    dest.is_borrowed_binding = True
    dest.is_let_borrow = True
    dest.bound_at_span = node.loc
    owner = borrow_owner(node.value)
    if owner is None:
        return
    record_owners(checker.borrow_state, dest, view_owners(checker, owner), node.loc,
                  checker._scope_binding_borrows[-1])


def is_slice(expr: Optional[Expr]) -> bool:
    """Is `expr` a slice, `data[a..b]` (docs/design/slices.md)?"""
    return isinstance(expr, IndexAccess) and expr.is_slice


def view_owners(checker: 'BorrowChecker', owner) -> tuple:
    """The owners a binding that reads out of `owner` freezes: `owner`, and its owners.

    A `let` of a let-borrow (a read, a field, a slice) views the storage of the FIRST
    owner, so a change of that owner must reach the second binding too. Each binding
    holds the whole set, so a chain of any length reaches the first owner.
    """
    state = checker.borrow_state.get(owner)
    if state is not None and state.is_let_borrow and not state.is_lent_iterator:
        return (owner, *state.borrows_from)
    return (owner,)


def reject_slice_of_temporary(checker: 'BorrowChecker', expr: Expr) -> bool:
    """CE2437 for a `let` of a slice of a temporary array (S8). True when refused.

    The temporary is freed at the end of the statement, and the binding lives on. Inside
    one statement a slice of a temporary is legal, so only the `let` asks.
    """
    if not is_slice(expr) or names_kept_storage(checker, expr):
        return False
    checker.err.emit_with(er.ERR.CE2437, expr.loc) \
        .help("bind the array first, or copy the part with `.ss(start, count)`") \
        .emit()
    return True
