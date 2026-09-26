"""What each control-flow event does to each `BorrowState` flag. One cell, one assertion."""
from __future__ import annotations

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import BorrowState, FlowFacts




# The join algebra, asserted directly. A field's join rule is the whole design.

def test_monotone_facts_join_by_union():
    """Moved / destroyed / invalidated on ANY path hold after the join (conservative)."""
    left = FlowFacts(moved=frozenset({"a"}), destroyed=frozenset({"b"}))
    right = FlowFacts(moved=frozenset({"c"}), destroyed=frozenset({"d"}))
    joined = left | right
    assert joined.moved == {"a", "c"}
    assert joined.destroyed == {"b", "d"}


def test_permission_facts_join_by_intersection():
    """`owns_no_heap` GRANTS permission, so it survives only if it held on EVERY path."""
    left = FlowFacts(owns_no_heap=frozenset({"a", "b"}))
    right = FlowFacts(owns_no_heap=frozenset({"b", "c"}))
    assert (left | right).owns_no_heap == {"b"}


def test_join_of_no_surviving_paths_is_blank():
    """Every arm terminated, so the code after the branch is unreachable."""
    assert FlowFacts.join([]) == FlowFacts()


def test_join_of_one_path_is_that_path():
    """Folding into a blank identity would silently empty the intersection field."""
    only = FlowFacts(moved=frozenset({"a"}), owns_no_heap=frozenset({"b"}))
    assert FlowFacts.join([only]) == only


def test_invalidation_carries_its_span_through_a_join():
    """Restoring the flag without the span renders CE2412 with no location."""
    left = FlowFacts(invalidation=(("v", "SPAN", ("c", "assign")),))
    joined = left | FlowFacts()
    assert joined.invalidation == (("v", "SPAN", ("c", "assign")),)


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

_LIVE = {"is_moved": True, "moved_at_span": Span(1, 1, 1, 2),
         "is_destroyed": True, "invalidated_at": Span(1, 1, 1, 2),
         "invalidated_by": ("c", "assign"),
         "is_borrowed_binding": True, "is_let_borrow": True,
         "borrows_from": "c"}

_REBIND_CLEARS = [
    ("is_moved", False),
    ("moved_at_span", None),
    ("is_destroyed", False),
    ("invalidated_at", None),
    ("invalidated_by", ()),
]

# Clearing these three let one rebind launder a binding into a writable local (#590).
_REBIND_KEEPS = ["is_borrowed_binding", "is_let_borrow", "borrows_from"]


def _reinitialized(flag: str) -> BorrowState:
    from sushi_lang.semantics.passes.borrow.flow import reinitialize

    state = BorrowState(name="x")
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




# BRANCH JOIN. Exclusive paths must not see each other's facts.

















# KNOWN-CONSERVATIVE cells. Decisions, not omissions.

def test_break_does_not_terminate_a_path():
    """`break` leaves the STATEMENT, not the function, and must not drop its facts."""
    from sushi_lang.semantics.ast import Break, Continue
    from sushi_lang.semantics.passes.borrow.flow import terminates

    assert terminates(Break(loc=None)) is False
    assert terminates(Continue(loc=None)) is False


def test_an_if_without_an_else_never_terminates():
    """The fall-through path survives, so the branch cannot terminate every path."""
    from sushi_lang.semantics.ast import Block, If, Return
    from sushi_lang.semantics.passes.borrow.flow import terminates

    returning = Block(statements=[Return(value=None, loc=None)], loc=None)
    with_else = If(arms=[(None, returning)], else_block=returning, loc=None)
    without_else = If(arms=[(None, returning)], else_block=None, loc=None)

    assert terminates(with_else) is True
    assert terminates(without_else) is False


