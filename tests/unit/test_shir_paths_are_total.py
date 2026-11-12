"""Gate: one path predicate over SHIR, with a rule for each statement class (docs/design/ir.md 7.5).

`semantics/shir/paths.py` answers where the path through a SHIR statement goes. The
typecheck pass reads it for the missing `return` (CE0107) and for a dead statement
(CE0140), and the borrow pass reads it at a join. So this gate holds three rules:

1. `RULES` has a row for each member of `ShirStmt`, and no row for another class.
2. Each row gives the answer of ir.md 7.5 over a block that the test builds.
3. A `match` ends the path when it has an arm and each arm ends it. A match whose arms
   miss a value (the caller names it in `uncovered`) does not end it.

These tests read Python objects and call the predicate over nodes that they build. They
do not run the compiler.
"""
from __future__ import annotations

import typing

from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import paths
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import CopyRange, ExpandCopies, Instance, ShirBody
from sushi_lang.semantics.shir.ids import DeclId, IdAllocator, ShirId
from sushi_lang.semantics.shir.nodes import Hole, ShirBlock

_SPAN = Span(1, 1, 1, 2)
_IDS: IdAllocator[ShirId] = IdAllocator(ShirId)


def _names(classes: set[type]) -> list[str]:
    return sorted(cls.__name__ for cls in classes)


def _block(*statements: object) -> ShirBlock:
    return ShirBlock(_IDS.allocate(), _SPAN, list(statements))


def _value() -> e.Lit:
    return e.Lit(_IDS.allocate(), _SPAN, e.BoolConst(True))


def _return() -> s.Return:
    return s.Return(_IDS.allocate(), _SPAN, _value())


def _say() -> s.ExprStmt:
    return s.ExprStmt(_IDS.allocate(), _SPAN, _value())


def _if(then: ShirBlock, otherwise: ShirBlock | None) -> s.If:
    return s.If(_IDS.allocate(), _SPAN, _value(), then, otherwise)


def _break() -> s.Break:
    return s.Break(_IDS.allocate(), _SPAN)


def test_each_statement_class_has_a_rule() -> None:
    missing = set(typing.get_args(s.ShirStmt)) - set(paths.RULES)
    assert not missing, f"the path predicate has no rule for: {_names(missing)}"


def test_the_rules_name_no_other_class() -> None:
    extra = set(paths.RULES) - set(typing.get_args(s.ShirStmt))
    assert not extra, f"the path predicate names a class of no statement union: {_names(extra)}"


def test_a_return_leaves_and_an_ordinary_statement_goes_on() -> None:
    assert paths.terminates(_return())
    assert not paths.terminates(_say())
    assert not paths.terminates(Hole(_IDS.allocate(), _SPAN, "Stmt"))
    assert paths.terminates(_block(_say(), _return()))
    assert not paths.terminates(_block(_say()))


def test_an_if_ends_the_path_only_with_an_else_whose_blocks_all_end_it() -> None:
    assert paths.terminates(_if(_block(_return()), _block(_return())))
    assert not paths.terminates(_if(_block(_return()), None))
    assert not paths.terminates(_if(_block(_return()), _block(_say())))
    elif_ = _if(_block(_return()), _block(_if(_block(_return()), _block(_return()))))
    assert paths.terminates(elif_)


def test_a_break_and_a_continue_leave_the_round_only() -> None:
    for jump in (_break(), s.Continue(_IDS.allocate(), _SPAN)):
        assert not paths.terminates(jump)
        assert paths.terminates(jump, leaves_round=True)
        assert paths.ends_the_path(jump)
    assert paths.terminates(_if(_block(_break()), _block(_return())), leaves_round=True)
    assert not paths.ends_the_path(_if(_block(_break()), _block(_return())))


def test_a_loop_never_ends_the_path_and_its_body_is_not_read() -> None:
    loop = s.Loop(_IDS.allocate(), _SPAN, s.Cond(_value()), _block(_return()))
    assert not paths.terminates(loop)
    loop_with_break = s.Loop(_IDS.allocate(), _SPAN, s.Cond(_value()), _block(_break()))
    assert not paths.terminates(loop_with_break, leaves_round=True)


def _match(*bodies: ShirBlock) -> s.Match:
    arms = [p.Arm(p.Wildcard(_IDS.allocate(), _SPAN), body) for body in bodies]
    return s.Match(_IDS.allocate(), _SPAN, _value(), arms, False)


def test_a_match_ends_the_path_only_when_each_arm_ends_it() -> None:
    assert paths.terminates(_match(_block(_return()), _block(_say(), _return())))
    assert not paths.terminates(_match(_block(_return()), _block(_say())))
    assert not paths.terminates(_match())
    assert paths.terminates(_block(_say(), _match(_block(_return()), _block(_return()))))
    assert paths.ends_the_path(_match(_block(_return()), _block(_return())))


def test_a_break_in_an_arm_leaves_the_round_only() -> None:
    match = _match(_block(_break()), _block(_return()))
    assert not paths.terminates(match)
    assert paths.terminates(match, leaves_round=True)


def test_a_match_whose_arms_miss_a_value_does_not_end_the_path() -> None:
    match = _match(_block(_return()), _block(_return()))
    uncovered = {match.shir_id}
    assert not paths.terminates(match, uncovered=uncovered)
    assert not paths.ends_the_path(match, uncovered=uncovered)
    nested = _if(_block(match), _block(_return()))
    assert not paths.terminates(nested, uncovered=uncovered)
    assert paths.terminates(nested)
    block = _block(match, _say())
    assert paths.first_dead_statement(block, _body(block, ExpandCopies()),
                                      uncovered=uncovered) is None
    dead = block.statements[1]
    assert paths.first_dead_statement(block, _body(block, ExpandCopies())) == (match, dead)


def _body(block: ShirBlock, copies: ExpandCopies) -> ShirBody:
    return ShirBody(DeclId(0), Instance(), [], [], block, copies)


def test_the_first_dead_statement_comes_after_the_first_ender() -> None:
    ender, dead, later = _return(), _say(), _say()
    block = _block(_say(), ender, dead, later)
    assert paths.first_dead_statement(block, _body(block, ExpandCopies())) == (ender, dead)
    live = _block(_say(), _return())
    assert paths.first_dead_statement(live, _body(live, ExpandCopies())) is None


def test_two_copies_of_an_expand_are_not_one_written_sequence() -> None:
    ender, after = _return(), _say()
    block = _block(ender, after)
    copies = ExpandCopies()
    copies.record_copies(block.shir_id, (CopyRange(0, 1), CopyRange(1, 2)))
    assert paths.first_dead_statement(block, _body(block, copies)) is None
