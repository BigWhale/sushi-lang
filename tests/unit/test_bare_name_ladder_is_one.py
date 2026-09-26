"""Section 8's bare-name ladder has ONE implementation, and every rung is reachable.

The ladder had two implementations -- the scope pass's and the typecheck pass's -- and
they disagreed at the type rung. An enum name written where a value belongs was "not a
variable, so not my fault" to both, escaped semantics whole and died in the emitter as
CE0055, which blames the compiler for the user's typo (#600).

Two gates here. The first is on the ladder itself: every rung of `RUNGS` is asked, in
order, and a higher rung wins over a lower one -- so a rung cannot be silently dropped
or reordered. The second is on the two consumers: each answers every rung, and a bare
type name in every value position reads CE2105 and never CE0055, CE1001 or CE2400.
"""
from __future__ import annotations

import pytest
import typing

from sushi_lang.semantics.name_ladder import RUNGS, BareName, Rungs, classify




class _Claims:
    """A `Rungs` that claims every name at exactly the rungs it is given."""

    def __init__(self, *claimed: BareName) -> None:
        self.claimed = frozenset(claimed)
        self.asked: list[BareName] = []

    def _ask(self, rung: BareName) -> bool:
        self.asked.append(rung)
        return rung in self.claimed

    def is_local(self, name: str) -> bool:
        return self._ask(BareName.LOCAL)

    def is_constant(self, name: str) -> bool:
        return self._ask(BareName.CONSTANT)

    def is_stdlib_constant(self, name: str) -> bool:
        return self._ask(BareName.STDLIB_CONSTANT)

    def is_function(self, name: str) -> bool:
        return self._ask(BareName.FUNCTION)

    def is_namespace(self, name: str) -> bool:
        return self._ask(BareName.NAMESPACE)

    def is_type(self, name: str) -> bool:
        return self._ask(BareName.TYPE)


def test_every_rung_is_a_bare_name_and_nothing_is_not_one():
    """`RUNGS` covers `BareName` exactly, less the one answer that is not a rung."""
    assert set(RUNGS) | {BareName.NOTHING} == set(BareName)
    assert BareName.NOTHING not in RUNGS
    assert len(RUNGS) == len(set(RUNGS))


@pytest.mark.parametrize("rung", RUNGS, ids=lambda r: r.name)
def test_each_rung_is_reachable(rung):
    """A rung that claims the name alone is the answer. No rung is dead code."""
    assert classify("whatever", _Claims(rung)) is rung


def test_no_rung_claiming_it_is_nothing():
    assert classify("whatever", _Claims()) is BareName.NOTHING


@pytest.mark.parametrize("index", range(len(RUNGS) - 1))
def test_a_higher_rung_wins_over_the_one_below(index):
    """The ORDER is the ladder. Row 1 winning is #296's rule; the rest follow it."""
    higher, lower = RUNGS[index], RUNGS[index + 1]
    assert classify("whatever", _Claims(higher, lower)) is higher


def test_the_ladder_stops_asking_once_a_rung_claims():
    """A lower rung is never consulted, so a lookup below the answer costs nothing."""
    claims = _Claims(BareName.CONSTANT)
    classify("whatever", claims)
    assert claims.asked == [BareName.LOCAL, BareName.CONSTANT]


@pytest.mark.parametrize("consumer", ["scope", "typecheck"])
def test_both_consumers_answer_every_rung(consumer):
    """The gate that keeps the two implementations one: a missing rung is a hole."""
    from sushi_lang.semantics.passes.scope import ScopeAnalyzer
    from sushi_lang.semantics.passes.types.visitor import _InferenceRungs

    answerer = ScopeAnalyzer if consumer == "scope" else _InferenceRungs
    asked = typing.get_protocol_members(Rungs)
    assert len(asked) == len(RUNGS), "a rung with no question, or a question with no rung"
    for method in sorted(asked):
        assert callable(getattr(answerer, method, None)), \
            f"{answerer.__name__} does not answer {method}"


# Every value position a bare name can stand in. Each used to answer something that was
# not about the position: an enum name reached the emitter (CE0055), a struct name read
# "undeclared identifier" (CE1001), and the borrow position read CE2400 for one and
# CE1001 for the other.

# The four kinds of type name the ladder's TYPE rung knows. A primitive is absent: a
# bare `i32` in an expression is a parse error (CE6001), so it never reaches the ladder.









