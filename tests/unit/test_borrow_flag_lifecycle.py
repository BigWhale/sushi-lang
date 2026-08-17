"""What each control-flow event does to each `BorrowState` flag. One cell, one assertion."""
from __future__ import annotations

import pytest

from sushi_lang.semantics.passes.borrow import BorrowState, FlowFacts


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


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

    from sushi_lang.semantics.passes.borrow import BorrowChecker

    fields = set(FlowFacts.__dataclass_fields__)

    def _mentioned(method) -> set[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        names = {node.arg for node in ast.walk(tree) if isinstance(node, ast.keyword)
                 and node.arg}
        names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        return names & fields

    snapshot = _mentioned(BorrowChecker._snapshot_flow)
    restore = _mentioned(BorrowChecker._restore_flow)
    assert snapshot == fields, f"_snapshot_flow misses {sorted(fields - snapshot)}"
    assert restore == fields, f"_restore_flow misses {sorted(fields - restore)}"


# REBIND. A rebind re-initializes: every fact about the OLD value is stale.

_REBIND_CLEARS = [
    ("is_moved", False),
    ("moved_at_span", None),
    ("is_destroyed", False),
    ("invalidated_at", None),
    ("invalidated_by", ()),
    ("is_borrowed_binding", False),
    ("is_let_borrow", False),
    ("borrows_from", None),
]


@pytest.mark.parametrize("flag,cleared_value", _REBIND_CLEARS)
def test_rebind_clears(flag, cleared_value):
    """Each flag fell out of step one at a time; each was its own bug."""
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.passes.borrow import BorrowChecker

    state = BorrowState(name="x")
    setattr(state, flag, {"is_moved": True, "moved_at_span": Span(1, 1, 1, 2),
                          "is_destroyed": True, "invalidated_at": Span(1, 1, 1, 2),
                          "invalidated_by": ("c", "assign"),
                          "is_borrowed_binding": True, "is_let_borrow": True,
                          "borrows_from": "c"}[flag])
    BorrowChecker._reinitialize(state)
    assert getattr(state, flag) == cleared_value


def test_rebind_after_destroy_is_not_a_use_after_destroy(analyze):
    """#294. The `.destroy()` released the OLD value; the rebind supplies a new one."""
    src = (
        "fn main() i32:\n"
        "    let Own@(i32) o = Own.alloc(5)\n"
        "    o.destroy()\n"
        "    o := Own.alloc(7)\n"
        "    println(o.get())\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2406" not in _codes(analyze(src))


def test_rebind_of_a_borrowed_binding_makes_it_an_owner(analyze):
    """The provenance triple is re-derived, so consuming the re-initialized value is legal."""
    src = (
        "fn eat(nom i32[] a) i32:\n"
        "    return Result.Ok(a.len())\n"
        "\n"
        "fn f(peek i32[] src) i32:\n"
        "    let i32[] b = src\n"
        "    b := from([1, 2, 3])\n"
        "    return Result.Ok(eat(nom b)??)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2411" not in _codes(analyze(src))


# BRANCH JOIN. Exclusive paths must not see each other's facts.

def test_a_move_in_one_if_arm_does_not_reach_its_sibling(analyze):
    """The original per-arm snapshot fix (Tier 2), pinned here as a matrix cell."""
    src = (
        "fn eat(nom i32[] a) i32:\n"
        "    return Result.Ok(a.len())\n"
        "\n"
        "fn f(bool flag) i32:\n"
        "    let i32[] xs = from([1, 2, 3])\n"
        "    if (flag):\n"
        "        return Result.Ok(eat(nom xs)??)\n"
        "    else:\n"
        "        return Result.Ok(eat(nom xs)??)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2405" not in _codes(analyze(src))


def test_a_move_in_one_match_arm_does_not_reach_its_sibling(analyze):
    """`match` arms are exclusive paths too; they used to share one mutable state."""
    src = (
        "enum Choice:\n"
        "    First\n"
        "    Second\n"
        "\n"
        "fn eat(nom string s) ~:\n"
        "    println(\"ate {s}\")\n"
        "    return Result.Ok(~)\n"
        "\n"
        "fn f(Choice c) i32:\n"
        "    let string val = \"value-{1}\"\n"
        "    match c:\n"
        "        Choice.First -> eat(nom val)\n"
        "        Choice.Second -> eat(nom val)\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2405" not in _codes(analyze(src))


def test_a_returning_arm_contributes_no_facts_after_the_branch(analyze):
    """#287. A `return` leaves the function, so its move cannot reach a sibling path."""
    src = (
        "fn pick(i32 which, string val) string:\n"
        "    if (which > 0):\n"
        "        return Result.Ok(val)\n"
        "    return Result.Ok(val)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2405" not in _codes(analyze(src))


def test_a_move_in_a_non_returning_arm_still_joins(analyze):
    """The conservative half of #287: a move that CAN reach the code after the branch does."""
    src = (
        "fn eat(nom i32[] a) i32:\n"
        "    return Result.Ok(a.len())\n"
        "\n"
        "fn f(bool flag) i32:\n"
        "    let i32[] xs = from([1, 2, 3])\n"
        "    if (flag):\n"
        "        println(eat(nom xs)??)\n"
        "    return Result.Ok(eat(nom xs)??)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2405" in _codes(analyze(src))


def test_a_conditional_literal_rebind_does_not_grant_permission(analyze):
    """`owns_no_heap` set in ONE arm must not hold after the join (intersection)."""
    src = (
        "fn eat(nom string s) ~:\n"
        "    println(\"ate {s}\")\n"
        "    return Result.Ok(~)\n"
        "\n"
        "fn f(bool flag) i32:\n"
        "    let string a = \"owned-{1}\"\n"
        "    if (flag):\n"
        "        a := \"hi\"\n"
        "    eat(nom a)\n"
        "    println(\"after: {a}\")\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2405" in _codes(analyze(src))


def test_an_unconditional_literal_rebind_does_grant_permission(analyze):
    """The other side: option B still applies when it holds on every path."""
    src = (
        "fn eat(nom string s) ~:\n"
        "    println(\"ate {s}\")\n"
        "    return Result.Ok(~)\n"
        "\n"
        "fn f() i32:\n"
        "    let string a = \"owned-{1}\"\n"
        "    a := \"hi\"\n"
        "    eat(nom a)\n"
        "    println(\"after: {a}\")\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2405" not in _codes(analyze(src))


def test_an_invalidation_in_one_arm_does_not_reach_its_sibling(analyze):
    """CE2412 is path-sensitive: the sibling arm never touched the owner."""
    src = (
        "fn f(bool flag) i32:\n"
        "    let List@(string) c = List.new()\n"
        "    c.push(\"one-{1}\")\n"
        "    let string v = c.get(0)??\n"
        "    if (flag):\n"
        "        c.clear()\n"
        "    else:\n"
        "        println(v)\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2412" not in _codes(analyze(src))


def test_an_invalidation_still_reaches_a_read_after_the_branch(analyze):
    """The conservative half: the union means a read AFTER the `if` is still rejected."""
    src = (
        "fn f(bool flag) i32:\n"
        "    let List@(string) c = List.new()\n"
        "    c.push(\"one-{1}\")\n"
        "    let string v = c.get(0)??\n"
        "    if (flag):\n"
        "        c.clear()\n"
        "    println(v)\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2412" in _codes(analyze(src))


# KNOWN-CONSERVATIVE cells. Decisions, not omissions.

def test_break_does_not_terminate_a_path():
    """`break` leaves the STATEMENT, not the function, and must not drop its facts."""
    from sushi_lang.semantics.ast import Break, Continue
    from sushi_lang.semantics.passes.borrow import BorrowChecker

    assert BorrowChecker._terminates(Break(loc=None)) is False
    assert BorrowChecker._terminates(Continue(loc=None)) is False


def test_an_if_without_an_else_never_terminates():
    """The fall-through path survives, so the branch cannot terminate every path."""
    from sushi_lang.semantics.ast import Block, If, Return
    from sushi_lang.semantics.passes.borrow import BorrowChecker

    returning = Block(statements=[Return(value=None, loc=None)], loc=None)
    with_else = If(arms=[(None, returning)], else_block=returning, loc=None)
    without_else = If(arms=[(None, returning)], else_block=None, loc=None)

    assert BorrowChecker._terminates(with_else) is True
    assert BorrowChecker._terminates(without_else) is False


def test_a_conditional_rebind_stays_conservative(analyze):
    """A rebind on ONE branch does not clear the other path's move at the join."""
    src = (
        "fn eat(nom i32[] a) i32:\n"
        "    return Result.Ok(a.len())\n"
        "\n"
        "fn f(bool flag) i32:\n"
        "    let i32[] xs = from([1, 2, 3])\n"
        "    if (flag):\n"
        "        println(eat(nom xs)??)\n"
        "    else:\n"
        "        xs := from([9])\n"
        "    return Result.Ok(eat(nom xs)??)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2405" in _codes(analyze(src))
