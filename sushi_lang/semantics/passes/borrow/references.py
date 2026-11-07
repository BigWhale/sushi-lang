"""The reference bindings of the borrow pass over SHIR (docs/design/ir.md 7.9, 8.2).

A reference `let` (`let peek T x = <place>`, `let poke T x = <place>`, #409) and a `peek` or
`poke` binder of a pattern (#300, #788) point INTO a place. The state of each keeps its
reference type, so a write through a `peek` reference is CE2408 and a consuming use of a
reference is CE2411. Each freezes the root of its place, its owner, to its last use: a
change of the owner before a later use is CE2412, and the reference has a region row
(`regions.py`). One `poke` reference of an owner at a time (CE2403), and no `peek` reference
beside a `poke` one (CE2407); the binders of one pattern point into disjoint slots, so they
share. A `poke` reference into a place that a `peek` reference roots is CE2408.

A `poke` reference `let` writes its owner: it passes the write gate (`reject_readonly_write`)
and changes the owner for each `let`-borrow of it. A reference `let` of a constant: a `peek`
reads it, and a `poke` is CE2400 (`borrow_of_constant`). A reference binder of a match that
only borrows its scrutinee needs a scrutinee that is a place (CE2404): a match borrows a
scrutinee that it does not take and that reads storage a name keeps, a `??`, a get-out or
a read-through `or_err` of it included.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Iterable, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.places import Step, is_get_call, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.typesys import BorrowMode, ReferenceType

from . import arms
from .borrows import borrow_of_constant
from .diagnostics import spelled
from .pattern_takes import owns_scrutinee
from .reads import borrow_owner, view_receiver
from .regions import note_binder, note_part_view
from .state import BorrowState, record_owners
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId
    from sushi_lang.semantics.shir.nodes import ShirBlock

    from . import BorrowChecker
    from .state import Owner


def check_let_reference(checker: 'BorrowChecker', node: s.Assign, local: LocalId) -> None:
    """`let peek T x = <place>` / `let poke T x = <place>`: declare the reference, read its
    place, and freeze the owner of the place. A value that is no `Borrow` of a place has the
    error of `typecheck` (CE2404), and the reference then freezes nothing."""
    value = node.value
    ty = checker.local_type(local)
    mode = value.mode if isinstance(value, e.Borrow) else BorrowMode.PEEK
    state = BorrowState(name=checker.name_of(local), local=local,
                        var_type=None if ty is None else ReferenceType(ty, mode),
                        bound_at_span=node.loc, declared_at_span=node.loc)
    if isinstance(value, e.Borrow):
        arms.check(checker, value.place)
    checker.declare(state)
    checker.block_locals[-1].append(local)
    if not isinstance(value, e.Borrow) or borrow_of_constant(checker, value.place, mode):
        note_part_view(checker, state)
        return
    freeze_reference(checker, state, value.place, node.loc, writes=True)
    note_part_view(checker, state)


def freeze_reference(checker: 'BorrowChecker', state: BorrowState, place: e.ShirExpr,
            span: Optional[Span], *, writes: bool,
            siblings: frozenset[Owner] = frozenset(),
            frame: Optional[list[tuple[Owner, Owner]]] = None,
                     bound_at: Optional[Span] = None) -> None:
    """Freeze the owner of `place` for the reference `state`, after the gates of the second
    reference and, for a `poke`, of the read-only root. `writes`: a `poke` changes the owner
    for each `let`-borrow of it (a reference `let`). `bound_at` is where the owner records the
    binding, when that is not `span`."""
    owner = borrow_owner(checker, place)
    owner_state = checker.borrow_state.get(owner) if owner is not None else None
    if owner is None or owner_state is None:
        return
    is_poke = isinstance(state.var_type, ReferenceType) and state.var_type.is_poke()
    if is_poke:
        if pokes_through_a_peek(state, owner_state):
            reject_poke_through_peek(checker, owner_state, span)
            return
        if writes and reject_readonly_write(checker, owner, span, "take a `poke` binding",
                                            receiver=_owner_place(place)):
            return
    if reject_a_second_writer(checker, owner_state, is_poke, span, siblings):
        return
    if is_poke and writes:
        # A writer invalidates every value binding read out of the owner, as `f(poke x)`
        # does (#242).
        check_owner_not_borrowed(checker, owner, span, "take `poke`", place=place)
    record_owners(checker.borrow_state, state, (owner,), bound_at or span,
                  checker._scope_binding_borrows[-1] if frame is None else frame)


def _owner_place(place: e.ShirExpr) -> e.ShirExpr:
    """`o.get()` binds the payload of an `Own`: the place that the write reaches is `o`."""
    if is_get_call(place) and isinstance(place, e.Call) and isinstance(place.target, e.Dotted):
        return place.target.recv
    return place


def declare_reference_binders(checker: 'BorrowChecker', node: s.Match,
                              references: Iterable[tuple[LocalId, BorrowMode, Optional[Span]]],
                              roots: dict[LocalId, e.ShirExpr], body: ShirBlock,
                              frame: list[tuple[Owner, Owner]]) -> None:
    """Declare each `peek` or `poke` binder of an arm: a reference into its payload, which
    freezes the owner of its root (the scrutinee, or the part of an in-place tuple literal)
    to its last use in the arm `body`, whatever the type of the payload. A match that only
    borrows its root needs a place there (CE2404). A match that owns its root freezes a
    local that it takes, and nothing for a temporary."""
    declared: list[LocalId] = []
    for local, mode, written in references:
        shir_local = checker.locals.get(local)
        at = written or (shir_local.loc if shir_local is not None else None) or node.loc
        ty = checker.local_type(local)
        state = BorrowState(name=checker.name_of(local), local=local,
                            var_type=ReferenceType(ty, mode) if ty is not None else None,
                            is_borrow_param=True, declared_at_span=at, bound_at_span=at)
        checker.declare(state)
        root = roots.get(local, node.scrutinee)
        borrowed = not node.consumes and (not owns_scrutinee(checker, node, root)
                                          or _reads_a_name(checker, root))
        if borrowed and not _is_a_place(root):
            checker.err.emit(er.ERR.CE2404, at, expr=spelled(checker, root))
        elif borrowed or isinstance(root, e.LocalRef):
            freeze_reference(checker, state, root, at, writes=False, siblings=frozenset(declared),
                    frame=frame)
        declared.append(local)
        note_binder(checker, state, body)


def _reads_a_name(checker: 'BorrowChecker', expr: e.ShirExpr) -> bool:
    """Whether `expr` reads storage that a name keeps, through each `??`, each get-out and
    each `or_err` of a plain payload too: a copy of such a payload is no storage of the
    match."""
    def crosses(call: e.ShirExpr) -> bool:
        resolved = checker.answers().callee_of(call.shir_id)
        callee = resolved.callee if resolved is not None else None
        return view_receiver(checker, call) is not None or (
            isinstance(callee, Builtin) and callee.method.name == "or_err")

    walked = walk_place(expr, Step.MEMBER | Step.INDEX | Step.TRY | Step.CALL,
                        crosses_call=crosses)
    return isinstance(walked.node, (e.LocalRef, e.StorageRef, e.ConstRef))


def _is_a_place(expr: e.ShirExpr) -> bool:
    """A name, or a field or an element chain off one."""
    return isinstance(walk_place(expr, Step.MEMBER | Step.INDEX).node,
                      (e.LocalRef, e.StorageRef, e.ConstRef))


def reject_a_second_writer(checker: 'BorrowChecker', owner_state: BorrowState, is_poke: bool,
                           span: Optional[Span],
                           siblings: frozenset[Owner] = frozenset()) -> bool:
    """Report a reference beside a live one of the same owner that excludes it; True when
    reported. `poke` beside `poke` is CE2403, a mix is CE2407, and two `peek` references
    share. `siblings` are the binders of the same pattern, which point into disjoint
    payload slots."""
    for bound_local, bound_span in list(owner_state.binding_borrows):
        bound = checker.borrow_state.get(bound_local)
        if bound_local in siblings or bound is None \
                or not isinstance(bound.var_type, ReferenceType):
            continue
        if bound.var_type.is_poke():
            code = er.ERR.CE2403 if is_poke else er.ERR.CE2407
        elif is_poke:
            code = er.ERR.CE2407
        else:
            continue
        diag = checker.err.emit_with(code, span, name=owner_state.name)
        if bound_span is not None:
            diag.note_at(f"'{bound.name}' binds it here", bound_span)
        diag.emit()
        return True
    return False


def pokes_through_a_peek(state: BorrowState, owner_state: BorrowState) -> bool:
    """Would this `poke` reference write the value of the caller through a `peek`?"""
    return (isinstance(state.var_type, ReferenceType) and state.var_type.is_poke()
            and isinstance(owner_state.var_type, ReferenceType)
            and owner_state.var_type.is_peek())


def reject_poke_through_peek(checker: 'BorrowChecker', owner_state: BorrowState,
                             span: Optional[Span]) -> None:
    """CE2408 for a `poke` reference out of a read-only borrow."""
    diag = checker.err.emit_with(er.ERR.CE2408, span, name=owner_state.name)
    if owner_state.declared_at_span is not None:
        diag.note_at(f"'{owner_state.name}' is declared here as a read-only borrow",
                     owner_state.declared_at_span)
    diag.help("a `poke` element binding would write the caller's container through a "
              "read-only borrow; declare the parameter `poke` if the elements must be "
              "written, or drop the marker and bind the element by value")
    diag.emit()
