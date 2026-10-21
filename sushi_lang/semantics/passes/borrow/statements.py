"""The statement arms of the borrow pass over SHIR (docs/design/ir.md 7.5, 7.9).

`check_block` checks the statements of one block in the written order, each through the
table of the pass (`arms.ARMS`), and releases the `let`-borrows that the block opened. The
borrows of a statement end with the statement (`clear_borrows`).

An `Assign` of a local is a `let` (`is_init`) or a rebind. A `let` BINDS: it declares the
local, then takes its value by the decision of a consuming use (`bind`). A rebind
RE-INITIALIZES the local: it is refused through a view of another value's storage
(`reject_readonly_write`), it takes its value as a consuming use, and every fact about the
old value goes. A `return` hands its value to the caller, a consuming use. An expression
statement reads its value. The `Assign` of any other place is checked by the field and
element groups, and the `return` of a `peek` return by its own group: each stops the
walk.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals.diagnostics import StageStop

from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.paths import terminates
from sushi_lang.semantics.typesys import ReferenceType

from . import arms
from .bindings import release_binding_borrow
from .borrows import clear_borrows
from .consume import bind, binds_a_bare_literal_string, consume
from .expressions import reject_a_use_after_the_change
from .flow import reinitialize
from .state import BorrowState
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker


def check_block(checker: 'BorrowChecker', block: ShirBlock) -> None:
    """Check a block, releasing the `let`-borrows it opened on the way out.

    A statement of an unrolled `expand` copy that ends every path, with more statements
    after it, leaves the rest of the block on no path (ir.md 7.2): the group of the
    unrolled copies checks such a block, and the walk of the body stops there.
    """
    copies = checker.body.copies if checker.body is not None else None
    checker._scope_binding_borrows.append([])
    try:
        for index, statement in enumerate(block.statements):
            arms.check(checker, statement)
            if (copies is not None and copies.holding(block.shir_id, index)
                    and index + 1 < len(block.statements)
                    and terminates(statement, leaves_round=True)):
                raise StageStop(arms.STAGE, "Expand", statement.loc)
    finally:
        for owner, binding in checker._scope_binding_borrows.pop():
            release_binding_borrow(checker.borrow_state.get(owner), binding)


def check_assign(checker: 'BorrowChecker', node: s.Assign) -> None:
    if not isinstance(node.target, e.LocalRef):
        arms.stop_at(checker, node)
    if node.is_init:
        _check_let(checker, node, node.target.local)
    else:
        _check_rebind(checker, node, node.target.local)
    clear_borrows(checker)


def _check_let(checker: 'BorrowChecker', node: s.Assign, local: LocalId) -> None:
    """`let T x = v`: declare the local, then give it the provenance of its value."""
    if isinstance(node.value, e.Borrow):
        # `let poke T x = <place>` (#409): a borrow binding, not a value.
        arms.stop_at(checker, node)
    ty = checker.local_type(local)
    state = BorrowState(name=checker.name_of(local), local=local, var_type=ty,
                        declared_at_span=node.loc,
                        # Option B: a string bound straight from a literal owns no heap,
                        # so consuming it transfers nothing and CE2405 must not fire on it.
                        owns_no_heap=binds_a_bare_literal_string(ty, node.value))
    checker.declare(state)
    arms.check(checker, node.value)
    bind(checker, node, state)


def _check_rebind(checker: 'BorrowChecker', node: s.Assign, local: LocalId) -> None:
    """`x := v`: the local takes ownership of a new value."""
    state = checker.borrow_state.get(local)
    # No "rebind while borrowed" check here, deliberately: this runs BEFORE the value
    # walk, and moving it after would reject `x := f(peek x)`. CE2401 lives at the
    # consuming use instead.
    if state is not None:
        # THE gate, in the rebind position: a name that is a view of another value's
        # storage cannot be rebound, a name with storage of its own can. A `poke`
        # reference is the middle case and stays legal -- the store goes through the
        # pointer, which is what the mode is for.
        refused = reject_readonly_write(checker, local, node.loc, "rebind the name",
                                        rebind=True)
        # The store goes through the pointer, so it is a USE of the reference: after a
        # change of its owner it writes into storage the owner no longer holds.
        if not refused and isinstance(state.var_type, ReferenceType):
            reject_a_use_after_the_change(checker, local, node.target.loc)
        # Option B: RE-DERIVE, never inherit. A rebind can only CLEAR this flag, never
        # set it on a value that owns heap.
        state.owns_no_heap = binds_a_bare_literal_string(state.var_type, node.value)
    arms.check(checker, node.value)
    # Replacing what the owner holds invalidates every binding reading out of it (#242).
    check_owner_not_borrowed(checker, local, node.loc, "assign", place=node.target)
    consume(checker, node.value)
    # A rebind RE-INITIALIZES, so a previous move no longer holds. The value was checked
    # above, so `s := "{s}-x"` still reports the moved `s`.
    if state is not None:
        reinitialize(state)


def check_return(checker: 'BorrowChecker', node: s.Return) -> None:
    """A `return` hands its value to the caller: a consuming use."""
    if checker.borrowed_return:
        # A `peek` body hands a VIEW to the caller (#1229): its group checks it.
        arms.stop_at(checker, node)
    arms.check(checker, node.value)
    consume(checker, node.value)
    clear_borrows(checker)


def check_expr_stmt(checker: 'BorrowChecker', node: s.ExprStmt) -> None:
    """A value that the statement reads and drops."""
    arms.check(checker, node.value)
    clear_borrows(checker)
