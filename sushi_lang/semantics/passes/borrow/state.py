"""Per-variable borrow state for the borrow checker."""

from __future__ import annotations
from typing import Optional
from dataclasses import dataclass, field

from sushi_lang.semantics.typesys import Type
from sushi_lang.internals.report import Span


@dataclass
class BorrowState:
    """Tracks the borrow state of a single variable."""
    name: str
    var_type: Optional[Type] = None
    poke_borrow_count: int = 0  # Number of active poke borrows (max 1)
    peek_borrow_count: int = 0  # Number of active peek borrows (unlimited)
    is_moved: bool = False
    is_destroyed: bool = False  # Variable has been explicitly destroyed (via .destroy())
    is_argv_view: bool = False  # main's `string[] args`: a borrowed view of process argv;
                                # moving it by value would free argv, so it is a hard error
    is_borrowed_binding: bool = False  # A `match` payload binding, a `foreach` item, or a
                                # `let` bound from a read through a live owner: a
                                # read-only borrow (ownership-conventions.md S8).
    is_let_borrow: bool = False  # ...and this one is the `let` spelling specifically
                                # (#242). Narrower than is_borrowed_binding for the
                                # DIAGNOSTIC: a match binding is a private deep copy so a
                                # write is only lost (CE2414), while a `let`-borrow shares
                                # the owner's data so a reallocating write double-frees
                                # (CE2426, #344).
                                #
                                # NOT `borrows_from is not None`: a binding out of a
                                # temporary records no owner name and is still a
                                # `let`-borrow.
    is_borrow_param: bool = False  # A parameter whose declared MODE is a borrow -- i.e.
                                # anything but `nom`, in any callable, `self` included:
                                # a write cannot reach the caller (CE2421 / CE2422) and
                                # consuming an owning one gives it a second owner (CE2411).
                                # See docs/design/borrow-model.md S1.
                                #
                                # Its own kind, not a flavour of is_borrowed_binding: a
                                # parameter is a SHALLOW copy aliasing the caller's heap,
                                # so the same write was a double free (#326).
    is_method_receiver: bool = False  # ...and this one is `self` specifically. The narrower
                                # flag is for the DIAGNOSTIC, not the rule: the two
                                # escapes differ (`poke self` vs `poke T`), so the two
                                # carry different codes and different help.
    owns_no_heap: bool = False  # Option B: this binding's CURRENT value owns no
                                # heap, so consuming it transfers nothing. Only a `string`
                                # bound from a literal sets it.
                                #
                                # On the BINDING and not the type on purpose:
                                # `BuiltinType.STRING` is an enum member with nowhere to put
                                # a flag. Do not "fix" it by inventing a string subtype.
                                #
                                # RE-DERIVED on every rebind, never inherited. Default False
                                # means "assume it owns heap".
    bound_at_span: Optional[Span] = None  # Where the binding was introduced. CE2411 is a
                                # RELATIONAL error -- the use is only wrong BECAUSE of what
                                # the binding borrows from -- so it renders both.
    declared_at_span: Optional[Span] = None  # Where this variable was introduced. CE2411
                                # for a read THROUGH an owner points here as its second
                                # location: the error exists only because this owner keeps
                                # the value, so the ladder's tier 3 needs it.
    borrows_from: Optional[str] = None  # The root owner this binding reads out of, for a
                                # `let`-borrow binding (#242). `let x = c.get(0)??` names
                                # `c`. None for a `match` / `foreach` binding, whose owner
                                # is the scrutinee expression rather than a named local.
    invalidated_at: Optional[Span] = None  # On a `let`-borrow BINDING: where its owner was
                                # changed or released. Set rather than reported, so CE2412
                                # fires only on a read AFTER it (non-lexical lifetimes).
    invalidated_by: tuple = ()  # (owner name, what the change was), for that diagnostic.
    binding_borrows: list = field(default_factory=list)  # On the OWNER: every live
                                # `let`-borrow binding reading out of it. Mutating the
                                # owner while this is non-empty is CE2412.
                                #
                                # NOT one of the counters above: those clear per statement,
                                # while a binding borrow lives to the end of its lexical
                                # scope, so `_check_block` releases it.
    first_borrow_span: Optional[Span] = None
    moved_at_span: Optional[Span] = None  # Where ownership was transferred away.
                                          # Use-after-move is a RELATIONAL error: the
                                          # use is only wrong BECAUSE of the move, so
                                          # CE2405 points at both.

    @property
    def is_borrowed(self) -> bool:
        """Returns True if variable has any active borrows."""
        return self.poke_borrow_count > 0 or self.peek_borrow_count > 0
