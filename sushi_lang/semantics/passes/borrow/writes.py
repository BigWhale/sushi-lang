"""The gates a write must pass: can it reach the value it appears to write?"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors.registry import ErrorMessage
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Expr
from sushi_lang.semantics.typesys import ReferenceType

from .reads import chain_call_boundary, root_owner
from .state import BorrowState

if TYPE_CHECKING:
    from . import BorrowChecker


# Methods that change or release what a container holds, so each is CE2412 for a live
# `let`-borrow out of the receiver. ONE set: a name missing from a copy would be a silent
# dangling borrow, not a wrong diagnostic.
MUTATING_METHODS = frozenset({
    "push", "pop", "insert", "remove", "clear", "reserve", "shrink_to_fit",
    "rehash", "destroy", "free", "fill", "reverse",
})


@dataclass(frozen=True)
class ReadOnlyReceiver:
    """One kind of receiver a write cannot reach through, as DATA."""
    code: ErrorMessage                                 # the registry entry (er.ERR.CExxxx)
    matches: Callable[[BorrowState], bool]             # is the state this kind?
    note_span: Callable[[BorrowState], Optional[Span]]  # where the kind was introduced
    note: str
    help: str


# The five kinds, most specific first. They are disjoint by construction -- the receiver is
# never a reference parameter, neither is ever a binding, and the two binding rows split on
# `is_let_borrow` -- so the order is documentation rather than precedence.
READONLY_RECEIVERS: tuple[ReadOnlyReceiver, ...] = (
    ReadOnlyReceiver(
        # `and not ReferenceType`: a `poke self` receiver (#327) is WRITABLE and must
        # fall through this row; a `peek self` receiver falls to the CE2408 row below,
        # which names its actual mode.
        code=er.ERR.CE2421,
        matches=lambda state: (state.is_method_receiver
                               and not isinstance(state.var_type, ReferenceType)),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is the receiver of a method on this type, a read-only borrow",
        help="the write ({what}) would land on the method's private copy of the "
             "receiver; declare the receiver mutable -- `(poke self, ...)` -- and "
             "the write reaches the caller (#327), or return the new value and let "
             "the caller store it",
    ),
    ReadOnlyReceiver(
        # A reference parameter carries its full `ReferenceType` as `var_type`, so the
        # question is answerable here and nowhere else: the typecheck pass unwraps a reference at
        # every mention, so no inferred type downstream can tell a borrow from a value.
        code=er.ERR.CE2408,
        matches=lambda state: (isinstance(state.var_type, ReferenceType)
                               and state.var_type.is_peek()),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is declared here as a read-only borrow",
        help="the write ({what}) would change the caller's value through a read-only "
             "borrow; declare the parameter `poke` if the callee must write, or take "
             "an independent value with `{name}.clone()`",
    ),
    ReadOnlyReceiver(
        # AFTER the `peek` row and excluding every reference parameter, so `peek` keeps
        # its own code and `poke` stays writable -- the escape this code names (#298).
        code=er.ERR.CE2422,
        matches=lambda state: (state.is_borrow_param
                               and not state.is_method_receiver
                               and not isinstance(state.var_type, ReferenceType)),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is declared here, as a by-value parameter of a method",
        help="the write ({what}) would land on the method's private copy of the "
             "argument; declare the parameter `poke` if the method must write through "
             "it, or take an independent value with `{name}.clone()`",
    ),
    ReadOnlyReceiver(
        # `and not is_let_borrow`: a match binding is a private deep copy, so the write
        # is only lost. A `let`-borrow shares the owner's data and gets CE2426 below.
        code=er.ERR.CE2414,
        matches=lambda state: (state.is_borrowed_binding
                               and not state.is_let_borrow),
        note_span=lambda state: state.bound_at_span,
        note="'{name}' is bound here, as a read-only view",
        help="the write ({what}) would land on a private copy and be lost; take an "
             "independent value with `{name}.clone()`, mutate it, and store it back "
             "into the owner",
    ),
    ReadOnlyReceiver(
        # The fifth kind (#344). CE2412 asks "may I mutate the OWNER while the binding
        # lives?"; this asks "may I write THROUGH the binding?" -- complementary, not
        # alternatives. The write reaches storage the owner keeps, so a reallocating
        # `.push()` frees the owner's buffer twice.
        #
        # Keyed on `is_let_borrow`, NOT `borrows_from is not None`: a binding out of a
        # temporary records no owner name.
        code=er.ERR.CE2426,
        matches=lambda state: state.is_let_borrow,
        note_span=lambda state: state.bound_at_span,
        note="'{name}' is bound here, borrowing storage its owner keeps",
        help="the write ({what}) reaches storage another value owns and still frees, so "
             "it is lost from the owner's view and a reallocating write frees the "
             "owner's buffer; write to the owner directly, or take an independent value "
             "with `{name}.clone()`, mutate it, and store it back",
    ),
)


def maybe_reject_mutation(checker: 'BorrowChecker', expr: Expr) -> None:
    """Reject `c.push(x)` while a `let`-borrow binding reads out of `c` (#242)."""
    # A call to a `poke self` method (#327) IS a write to the receiver root --
    # the typecheck pass stamps the resolution on the node, so this pass never re-resolves.
    is_poke_self_call = getattr(expr, "callee_self_mode", None) == "poke"
    if getattr(expr, "method", None) not in MUTATING_METHODS and not is_poke_self_call:
        return
    receiver = getattr(expr, "receiver", None)
    root = root_owner(receiver)
    what = f"call `.{expr.method}()`"
    if reject_readonly_write(checker, root, expr.loc, what, receiver=receiver):
        return
    check_owner_not_borrowed(checker, root, expr.loc, what)


def reject_readonly_write(checker: 'BorrowChecker', name: Optional[str],
                          span: Optional[Span], what: str,
                          receiver: Optional[Expr] = None) -> bool:
    """THE gate: a write that cannot reach the value it appears to write is rejected."""
    # The sixth kind keys on SHAPE, not on the state of a name (#352, #407): past a call
    # boundary the receiver is a temporary copy, whatever the root's mode is -- so this
    # answers BEFORE the state table, and the boundary is the diagnostic's second location.
    boundary = chain_call_boundary(receiver) if receiver is not None else None
    if boundary is not None:
        diag = checker.err.emit_with(er.ERR.CE2429, span)
        diag.note("the value past this expression is a temporary copy, not the "
                  "owner's storage", boundary)
        diag.help(f"the write ({what}) would land on the copy and be lost; bind a "
                  "clone, mutate it, and rebuild the owner -- or mutate in place "
                  "through a nested `Own(poke ...)` reference binding where the "
                  "`Own` sits in an enum payload")
        diag.emit()
        return True
    if name is None:
        return False
    state = checker.borrow_state.get(name)
    if state is None:
        return False
    for kind in READONLY_RECEIVERS:
        if not kind.matches(state):
            continue
        diag = checker.err.emit_with(kind.code, span, name=name)
        note_span = kind.note_span(state)
        if note_span is not None:
            diag.note(kind.note.format(name=name), note_span)
        diag.help(kind.help.format(name=name, what=what))
        diag.emit()
        return True
    return False


def check_owner_not_borrowed(checker: 'BorrowChecker', owner: Optional[str],
                             span: Optional[Span], what: str) -> None:
    """Reject a change to `owner` while a `let`-borrow binding reads out of it (#242)."""
    if owner is None:
        return
    state = checker.borrow_state.get(owner)
    if state is None or not state.binding_borrows:
        return
    # INVALIDATE, do not report: the change is an error only on a read AFTER it.
    # Reporting here would reject `let g = fns.get(0)??; g(10); fns.free()`.
    for name, bound_at in state.binding_borrows:
        binding = checker.borrow_state.get(name)
        if binding is not None and binding.invalidated_at is None:
            binding.invalidated_at = span
            binding.invalidated_by = (owner, what)
            binding.bound_at_span = binding.bound_at_span or bound_at
    # Invalidate ONCE, but NOT during a suppressed (loop-discovery) round: clearing in
    # round 1 leaves round 2 with no live borrows to invalidate.
    if not checker.err.suppressed:
        state.binding_borrows = []
