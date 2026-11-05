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
from sushi_lang.semantics.ast import Lambda, Let, Name
from sushi_lang.semantics.ownership import Ownership, Provenance, TypeClass, classify
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, ReferenceType

from .borrowed_returns import borrowed_call, call_owners, reject_view_of_temporary
from .diagnostics import (
    CopyUse,
    emit_consume_of_borrow,
    emit_consume_of_read,
    no_clone_reason,
    refuses_clone,
)
from .reads import borrow_owner, read_through_call, read_type, unknown, view_receiver
from .state import BorrowState, record_owners
from .takes import field_take, take
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

    The constant evaluator makes each value of an initializer body, so that value is FRESH,
    and a constant that the initializer names is read, not moved. In any other body, a local
    has the provenance of its state. A constant and a `var` are storage that the program
    keeps (#726): neither is FRESH, so a take of an owning one is refused. A field, an
    element and a slice are storage that the owner of their base keeps, so each is a read
    through that owner (BORROWED): a plain one is a copy, and a take of an owning one is
    refused. The answer of a container get-out (`answers_view`) is a view of what its
    receiver holds, a read through the receiver too (BORROWED), and so is the answer of a
    call with a `peek` return, a `??` over one included (`borrowed_returns.py`). `r??` yields what `r` holds,
    and the payload has the provenance of `r` (#548): a borrowed `r` keeps its owner, so
    the payload is a read through it; an owned `r` was SPENT by the `??` itself
    (`unwrap_place`), so nothing owns the payload now and the position adopts it. A `??`
    over a `??` that reads through, and over a read-through `or_err`, reads through too
    (`reads_through`). A marked field take is the one read through an owner that is not
    a borrow: the owner is a local this function holds, and the take spends it; any other
    `Take` is its place, and the marker is inert (`takes.field_take`). Anything else makes
    a new value.
    """
    if isinstance(checker.body.origin, Initializer):
        return Provenance.FRESH
    if isinstance(node, e.Take):
        if field_take(checker, node.place) is not None:
            return Provenance.OWNED
        return source_provenance(checker, node.place)
    if isinstance(node, e.LocalRef):
        return name_provenance(checker, node.local)
    if isinstance(node, (e.ConstRef, e.StorageRef)):
        return Provenance.OWNED
    if borrowed_call(checker, node) is not None:
        return Provenance.BORROWED
    if isinstance(node, (e.Field, e.Index, e.Slice)) or view_receiver(checker, node) is not None:
        return Provenance.BORROWED
    if isinstance(node, e.Try) and reads_through(checker, node.inner):
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
           type_class: TypeClass, write: bool = True) -> Ownership:
    """The `Ownership` of the consuming use `node`, from `classify()`, written ONE time as a
    row of the `OwnershipResults` of the body. The one writer of the row.

    A discovery round of a loop decides and writes nothing: the round after it decides
    from the facts of the fixed point, and so does a body with an error of typecheck. An
    unknown value is adopted: it moves nothing and is no error (docs/design/ir.md 7.9).
    With no `write`, the decision is no row: the root that a field take spends.
    """
    if unknown(checker, node):
        return Ownership.ADOPT
    decision = classify(provenance, type_class)
    if write and not checker.discovery_rounds and not checker.typecheck_failed:
        checker.table().record_ownership(node.shir_id, decision)
    return decision


def read_through_receiver(checker: 'BorrowChecker', node: e.ShirExpr) -> Optional[e.ShirExpr]:
    """The receiver of a call that reads a borrowed receiver through, or None.

    `m.or_err(nom e)` takes `nom self`, but a BORROWED `m` whose payload owns a resource
    is read through, as `??` reads through a borrowed wrapper
    (docs/design/error-conversion.md section 4, docs/design/ir.md 8.3). A receiver that
    owns nothing is copied, and an owned one is taken, so neither is read through.
    """
    receiver = read_through_call(checker, node)
    if receiver is None or source_provenance(checker, receiver) is not Provenance.BORROWED:
        return None
    return receiver


def reads_through(checker: 'BorrowChecker', operand: e.ShirExpr) -> bool:
    """Does a `??` over `operand` read a borrowed wrapper through? The operand is a local
    that holds a borrow, a `??` that reads through (a nested `??`), the answer of a call
    with a `peek` return, or a read-through `or_err` (`read_through_receiver`)."""
    root = walk_place(operand, Step.TRY).node
    if isinstance(root, e.LocalRef):
        return name_provenance(checker, root.local) is Provenance.BORROWED
    return (borrowed_call(checker, root) is not None
            or read_through_receiver(checker, root) is not None)


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
    if isinstance(node, e.Take):
        return _consume_take(checker, node, use_of_copy)
    provenance = source_provenance(checker, node)
    if isinstance(node, e.LocalRef):
        return consume_named(checker, node, provenance, node.loc, use_of_copy)
    decision = decide(checker, node, provenance, source_class(checker, node))
    if isinstance(node, (e.ConstRef, e.StorageRef)):
        reject_move_of_storage(checker, node, decision)
    elif decision is Ownership.REJECT:
        # A read through a live owner has no owner to mark moved, but it CAN be rejected
        # (#242): the owner keeps the value and still frees it.
        emit_consume_of_read(checker, node, use_of_copy)
    return decision


def consume_marked(checker: 'BorrowChecker', node: e.ShirExpr) -> Ownership:
    """A consuming use that carries a written `nom` marker: a field take
    (`takes.field_take`), or else an ordinary consuming use."""
    root = field_take(checker, node)
    if root is not None:
        return take(checker, node, root, node)
    return consume(checker, node)


def _consume_take(checker: 'BorrowChecker', node: e.Take,
                  use_of_copy: Optional[CopyUse]) -> Ownership:
    """A `Take` at a consuming use: a field take, or its place with the same decision
    (the marker is inert)."""
    root = field_take(checker, node.place)
    if root is not None:
        return take(checker, node, root, node.place)
    decide(checker, node, source_provenance(checker, node), source_class(checker, node))
    return consume(checker, node.place, use_of_copy)


def reject_move_of_storage(checker: 'BorrowChecker', node: e.ConstRef | e.StorageRef,
                           decision: Ownership) -> bool:
    """CE2436 for a take of a constant or a `var` that would be a move. True when it was
    refused.

    The unit-storage rule (#726): a constant and a `var` are each one object the program
    keeps for its whole run, and neither has an owner that can hand it away. A plain value
    still copies out. A name behind an alias is the same declaration (#1016).
    """
    if decision is not Ownership.MOVE:
        return False
    if isinstance(node, e.ConstRef):
        name, kind = checker.decl_name(node.const_id), "a constant"
    else:
        name, kind = checker.decl_name(node.decl), "a unit variable"
    checker.err.emit(er.ERR.CE2436, node.loc, name=name, kind=kind)
    return True


def consume_named(checker: 'BorrowChecker', node: e.LocalRef, provenance: Provenance,
                  use_span: Optional[Span], use_of_copy: Optional[CopyUse] = None,
                  write: bool = True) -> Ownership:
    """Decide a consuming use of a local, and apply the decision to the local."""
    state = checker.borrow_state.get(node.local)
    ty = state.var_type if state is not None else checker.local_type(node.local)
    decision = decide(checker, node, provenance,
                      checker.types.type_class_of_source(state, ty), write)
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
    the owner, so the owner is frozen while the binding lives. A marked field take hands
    the field to the binding and spends its receiver; any other `Take` binds its place,
    with the same decision on the `Take`.
    """
    value = node.value
    if isinstance(value, e.Take):
        root = field_take(checker, value.place)
        if root is not None:
            take(checker, value, root, value.place)
            return
        decide(checker, value, source_provenance(checker, value), source_class(checker, value))
        value = value.place
    if reject_slice_of_temporary(checker, value) or reject_view_of_temporary(checker, value):
        return
    provenance = source_provenance(checker, value)
    src_state = (checker.borrow_state.get(value.local)
                 if isinstance(value, e.LocalRef) else None)
    if src_state is not None and src_state.is_argv_view:
        # Binding main's argv view by value would make the new binding free argv (N2).
        decide(checker, value, provenance, source_class(checker, value))
        checker.err.emit(er.ERR.CE2410, value.loc, name=src_state.name)
        return
    decision = decide(checker, value, provenance, source_class(checker, value))
    if isinstance(value, (e.ConstRef, e.StorageRef)):
        reject_move_of_storage(checker, value, decision)
    elif decision is Ownership.MOVE and src_state is not None:
        _move_out(checker, src_state, value.loc)
    elif decision is Ownership.REJECT:
        record_borrowed_binding(checker, node, dest)
    elif borrowed_call(checker, value) is not None:
        # A plain answer is a copy, and it still freezes the owners of its sources: the
        # source set of a template is the same at every type argument (#1229).
        record_owners(checker.borrow_state, dest, value_owners(checker, value), node.loc,
                      checker._scope_binding_borrows[-1])


def record_borrowed_binding(checker: 'BorrowChecker', node: s.Assign,
                            dest: BorrowState) -> None:
    """Record that a `let` borrows storage its value's owner keeps (#242)."""
    dest.is_borrowed_binding = True
    dest.is_let_borrow = True
    dest.bound_at_span = node.loc
    record_owners(checker.borrow_state, dest, value_owners(checker, node.value), node.loc,
                  checker._scope_binding_borrows[-1])


def value_owners(checker: 'BorrowChecker', value: e.ShirExpr) -> tuple:
    """The owners that a view of `value` freezes: the owners of the sources of a borrowed
    call (`borrowed_returns.call_owners`), else the owner of the place and its owners."""
    call = borrowed_call(checker, value)
    if call is not None:
        return call_owners(checker, call)[0]
    owner = borrow_owner(checker, value)
    return () if owner is None else view_owners(checker, owner)


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


def reject_slice_of_temporary(checker: 'BorrowChecker', value: e.ShirExpr) -> bool:
    """CE2437 for a `let` of a slice of a temporary array (S8). True when refused.

    The temporary is freed at the end of the statement, and the binding lives on. Inside
    one statement a slice of a temporary is legal, so only the `let` asks. Storage that
    a local, a constant or a `var` keeps, at the end of field and element steps, is no
    temporary.
    """
    if not isinstance(value, e.Slice):
        return False
    if isinstance(walk_place(value, Step.MEMBER | Step.INDEX).node,
                  (e.LocalRef, e.ConstRef, e.StorageRef)):
        return False
    checker.err.emit_with(er.ERR.CE2437, value.loc) \
        .help("bind the array first, or copy the part with `.ss(start, count)`") \
        .emit()
    return True
