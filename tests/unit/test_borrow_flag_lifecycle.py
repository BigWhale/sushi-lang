"""What each control-flow event does to each `BorrowState` flag. One cell, one assertion.

The state of the borrow pass over SHIR is keyed by `LocalId` (docs/design/ir.md 7.9), and
the joins read the one SHIR path predicate (`semantics/shir/paths.py`).
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import BorrowState, FlowFacts
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.ids import IdAllocator, LocalId, ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock

A, B, C, D = (LocalId(index) for index in range(4))

_SPAN = Span(1, 1, 1, 2)
_IDS: IdAllocator[ShirId] = IdAllocator(ShirId)


# The join algebra, asserted directly. A field's join rule is the whole design.

def test_monotone_facts_join_by_union():
    """Moved / destroyed / invalidated on ANY path hold after the join (conservative)."""
    left = FlowFacts(moved=frozenset({A}), destroyed=frozenset({B}))
    right = FlowFacts(moved=frozenset({C}), destroyed=frozenset({D}))
    joined = left | right
    assert joined.moved == {A, C}
    assert joined.destroyed == {B, D}


def test_permission_facts_join_by_intersection():
    """`owns_no_heap` GRANTS permission, so it survives only if it held on EVERY path."""
    left = FlowFacts(owns_no_heap=frozenset({A, B}))
    right = FlowFacts(owns_no_heap=frozenset({B, C}))
    assert (left | right).owns_no_heap == {B}


def test_a_move_on_every_path_joins_by_intersection():
    """A local moved in one arm is moved on SOME path: it stays `moved`, and it leaves
    `moved_on_every_path`, so SLIR lowering gives it a drop flag (ir.md 8.10, rule 4)."""
    one_arm = FlowFacts(moved=frozenset({A}), moved_on_every_path=frozenset({A}))
    other_arm = FlowFacts()
    both_arms = FlowFacts(moved=frozenset({A}), moved_on_every_path=frozenset({A}))
    assert (one_arm | other_arm).moved == {A}
    assert (one_arm | other_arm).moved_on_every_path == frozenset()
    assert (one_arm | both_arms).moved_on_every_path == {A}


def test_join_of_no_surviving_paths_is_blank():
    """Every arm terminated, so the code after the branch is unreachable."""
    assert FlowFacts.join([]) == FlowFacts()


def test_join_of_one_path_is_that_path():
    """Folding into a blank identity would silently empty the intersection field."""
    only = FlowFacts(moved=frozenset({A}), owns_no_heap=frozenset({B}))
    assert FlowFacts.join([only]) == only


def test_invalidation_carries_its_span_through_a_join():
    """Restoring the flag without the span renders CE2412 with no location."""
    left = FlowFacts(invalidation=((A, _SPAN, (C, "assign")),))
    joined = left | FlowFacts()
    assert joined.invalidation == ((A, _SPAN, (C, "assign")),)


# Snapshot / restore must cover the SAME fields. A field in one and not the other
# is exactly how a fact leaks across arms.

def test_every_flow_fact_field_is_restored():
    """A fact that is snapshot but never restored leaks; the reverse silently drops it."""
    import ast
    import inspect
    import textwrap

    from sushi_lang.semantics.passes.borrow import flow

    fields = set(FlowFacts.__dataclass_fields__)

    def _mentioned(method) -> set[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        names = {node.arg for node in ast.walk(tree) if isinstance(node, ast.keyword)
                 and node.arg}
        names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        return names & fields

    snapshot = _mentioned(flow.snapshot_flow)
    restore = _mentioned(flow.restore_flow)
    assert snapshot == fields, f"snapshot_flow misses {sorted(fields - snapshot)}"
    assert restore == fields, f"restore_flow misses {sorted(fields - restore)}"


# REBIND. A rebind re-initializes: every fact about the OLD VALUE is stale, and every fact
# about WHERE THE STORAGE IS stands, because no rebind moves storage (#590).

_LIVE = {"is_moved": True, "is_moved_on_every_path": True, "moved_at_span": _SPAN,
         "is_destroyed": True, "invalidated_at": _SPAN,
         "invalidated_by": (C, "assign"),
         "is_borrowed_binding": True, "is_let_borrow": True,
         "borrows_from": (C,)}

_REBIND_CLEARS = [
    ("is_moved", False),
    ("is_moved_on_every_path", False),
    ("moved_at_span", None),
    ("is_destroyed", False),
    ("invalidated_at", None),
    ("invalidated_by", ()),
]

# Clearing these three let one rebind launder a binding into a writable local (#590).
_REBIND_KEEPS = ["is_borrowed_binding", "is_let_borrow", "borrows_from"]


def _reinitialized(flag: str) -> BorrowState:
    from sushi_lang.semantics.passes.borrow.flow import reinitialize

    state = BorrowState(name="x", local=A)
    setattr(state, flag, _LIVE[flag])
    reinitialize(state)
    return state


@pytest.mark.parametrize("flag,cleared_value", _REBIND_CLEARS)
def test_rebind_clears_the_value_facts(flag, cleared_value):
    """Each flag fell out of step one at a time; each was its own bug."""
    assert getattr(_reinitialized(flag), flag) == cleared_value


@pytest.mark.parametrize("flag", _REBIND_KEEPS)
def test_rebind_keeps_the_storage_facts(flag):
    """A rebind supplies a new value; it does not move the binding's storage."""
    assert getattr(_reinitialized(flag), flag) == _LIVE[flag]


# The path predicate of the joins is the ONE SHIR predicate (H7). A path that leaves the
# round of its loop does not reach the join after a branch.

def _block(*statements: object) -> ShirBlock:
    return ShirBlock(_IDS.allocate(), _SPAN, list(statements))


def _if(then: ShirBlock, otherwise: ShirBlock | None) -> s.If:
    return s.If(_IDS.allocate(), _SPAN, e.Lit(_IDS.allocate(), _SPAN, e.BoolConst(True)),
                then, otherwise)


def test_the_joins_read_the_one_shir_path_predicate():
    from sushi_lang.semantics.passes.borrow import flow
    from sushi_lang.semantics.shir import paths

    assert flow.terminates is paths.terminates


def test_break_does_not_terminate_a_path():
    """`break` leaves the round of its loop, not the function: it ends a path only for
    the join in a loop body, which asks with `leaves_round`."""
    from sushi_lang.semantics.passes.borrow.flow import terminates

    for jump in (s.Break(_IDS.allocate(), _SPAN), s.Continue(_IDS.allocate(), _SPAN)):
        assert terminates(jump) is False
        assert terminates(jump, leaves_round=True) is True


def test_an_if_without_an_else_never_terminates():
    """The fall-through path survives, so the branch cannot terminate every path."""
    from sushi_lang.semantics.passes.borrow.flow import terminates

    def returning() -> ShirBlock:
        return _block(s.Return(_IDS.allocate(), _SPAN,
                               e.Lit(_IDS.allocate(), _SPAN, e.BlankConst())))

    assert terminates(_if(returning(), returning())) is True
    assert terminates(_if(returning(), None)) is False
