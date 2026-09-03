"""Per-variable borrow state.

Three borrow flags, separate because their ESCAPES differ and the escape is what the
diagnostic has to name: a `is_borrowed_binding` view is a private deep copy, so a write
is merely lost (CE2414, clone-mutate-store-back); an `is_let_borrow` shares the owner's
DATA, so a reallocating write double-frees it (CE2426, #344, write to the owner instead);
an `is_borrow_param` is a shallow copy aliasing the caller's heap (CE2421 / CE2422, #326,
redeclare `poke`). `is_method_receiver` narrows the last to `self` for the same reason.
`is_let_borrow` is NOT `borrows_from is not None` -- a binding out of a temporary records
no owner name and is still one.

Every span here is the SECOND location of a relational diagnostic; without it the tier-3
ladder renders a use-after-move with no move. `invalidated_at` is set rather than
reported, which is what makes CE2412 a non-lexical lifetime rather than a ban.

The two counters clear per statement. `binding_borrows` does not: it sits on the OWNER
and lives to the end of the lexical scope, so `check_block` releases it, not
`clear_borrows`.

`owns_no_heap` is option B, on the BINDING rather than the type because
`BuiltinType.STRING` is an enum member with nowhere to put a flag -- do not "fix" it with
a string subtype. Re-derived on every rebind, never inherited.
"""

from __future__ import annotations
from typing import Optional
from dataclasses import dataclass, field

from sushi_lang.semantics.typesys import BorrowMode, Type
from sushi_lang.internals.report import Span


def borrow_mode(marker: Optional[str]) -> BorrowMode:
    """The `BorrowMode` a `peek` / `poke` source marker names."""
    return BorrowMode.POKE if marker == "poke" else BorrowMode.PEEK


@dataclass
class BorrowState:
    """Tracks the borrow state of a single variable."""
    name: str
    var_type: Optional[Type] = None
    poke_borrow_count: int = 0          # at most 1
    peek_borrow_count: int = 0          # unlimited
    is_moved: bool = False
    is_destroyed: bool = False          # released by an explicit `.destroy()`
    declared_branch_depth: int = 0      # checker.branch_depth at declaration (#414)
    is_argv_view: bool = False          # main's `string[] args`; moving it frees argv
    is_unit_var: bool = False           # a `var`: borrowable, never moved out of (CE2436)
    is_borrowed_binding: bool = False
    is_let_borrow: bool = False
    is_borrow_param: bool = False
    is_method_receiver: bool = False
    owns_no_heap: bool = False
    bound_at_span: Optional[Span] = None      # where the binding was introduced
    declared_at_span: Optional[Span] = None   # where the variable was introduced
    moved_at_span: Optional[Span] = None      # where ownership was transferred away
    consumed_by_method: Optional[str] = None  # the `nom self` method that took it (R27)
    borrows_from: Optional[str] = None        # the root owner a `let`-borrow reads out of
    invalidated_at: Optional[Span] = None     # where that owner changed or was released
    invalidated_by: tuple = ()                # (owner name, what the change was)
    # A tuple per entry, not a set: `Span` is an unfrozen dataclass and so unhashable.
    binding_borrows: list = field(default_factory=list)
    first_borrow_span: Optional[Span] = None

    @property
    def is_borrowed(self) -> bool:
        """Returns True if variable has any active borrows."""
        return self.poke_borrow_count > 0 or self.peek_borrow_count > 0
