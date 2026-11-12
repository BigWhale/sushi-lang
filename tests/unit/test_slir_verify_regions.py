"""The region rules and the view-local rules of the SLIR verifier over hand-built functions
(docs/design/ir.md 8.6, 8.13).

A correct lowering never makes a function that breaks a rule, so no fixture can show a rule
refuse. Each test gives the verifier a function that keeps the rules, or that breaks one
rule. They call a Python function over data; they do not run the compiler.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock, SlirFunction, SlirLocal
from sushi_lang.semantics.slir.ids import BlockId, BorrowId, LocalId
from sushi_lang.semantics.slir.verify import DeclaredParams
from sushi_lang.semantics.typesys import BorrowMode, Type

from tests.unit.test_slir_verifier import (
    BAD, BLANK, BOOL, GUARANTEED, I32, OWNED, SPAN, STRING, _block, _copy, _dead, _drop,
    _goto, _live, _local, _move, _passes, _refused, _ret, _set, _switch, _text,
)

REGION, OWNER, VIEW = "a borrow region", "no change of an owner in its region", "a view local"
POKE = {"unit$poke": DeclaredParams((Convention.INOUT,)),
        "unit$eat": DeclaredParams((OWNED,))}


def _function(types: list[Type], views: set[int], *blocks: SlirBlock,
              params: tuple[tuple[int, Convention], ...] = ()) -> SlirFunction:
    locals_ = tuple(SlirLocal(LocalId(index), ty, None, None, view=index in views)
                    for index, ty in enumerate(types))
    return SlirFunction("unit$probe", "probe", "unit",
                        tuple((LocalId(index), convention) for index, convention in params),
                        locals_, blocks, BlockId(0))


def _begin(region: int, owner: int, view: Optional[int] = None,
           span=SPAN) -> st.BeginBorrow:
    return st.BeginBorrow(BorrowId(region), _local(owner), BorrowMode.PEEK,
                          None if view is None else LocalId(view), span)


def _end(region: int, span=SPAN) -> st.EndBorrow:
    return st.EndBorrow(BorrowId(region), span)


def _blank() -> st.Assign:
    return _set(0, r.Use(p.Const(k.Blank())))


def _call(name: str, arg: p.Operand, convention: Convention, span=SPAN) -> st.Call:
    return st.Call(_local(3), st.Direct(f"unit${name}", name), ((arg, convention),), span)


def _viewed(*middle: object, after: tuple[object, ...] = (),
            types: Optional[list[Type]] = None) -> SlirFunction:
    """`_1` owns a string, `_2` is a view of it in the region `b0`, `_3` takes a call: the
    statements `middle` stand in the region, and `after` after its end."""
    return _function(
        types or [BLANK, STRING, STRING, BLANK], {2},
        _block(0, _ret(), _live(1), _text(1), _live(2), _begin(0, 1, 2),
               _set(2, r.Use(_copy(1))), *middle, _end(0), _dead(2), *after, _drop(1),
               _dead(1), _blank()))


# -- functions that keep every rule ----------------------------------------------------


def test_a_view_in_its_region_passes():
    _passes(_viewed(_call("read", _copy(2), GUARANTEED)),
            {"unit$read": DeclaredParams((GUARANTEED,))})


def test_a_change_of_the_owner_after_the_end_of_its_region_passes():
    _passes(_viewed(after=(_call("poke", _copy(1), Convention.INOUT),)), POKE)


def test_a_region_that_ends_on_each_arm_of_a_switch_passes():
    _passes(_function(
        [BLANK, BOOL, STRING, STRING], {3},
        _block(0, _switch(1, 2, 1), _live(2), _text(2), _live(3), _begin(0, 2, 3),
               _set(3, r.Use(_copy(2)))),
        _block(1, _goto(3), _end(0)),
        _block(2, _goto(3), _end(0)),
        _block(3, _ret(), _dead(3), _drop(2), _dead(2), _blank()),
        params=((1, GUARANTEED),)))


def test_a_region_over_two_owners_passes():
    _passes(_function(
        [BLANK, STRING, STRING, STRING], {3},
        _block(0, _ret(), _live(1), _text(1), _live(2), _text(2), _live(3), _begin(0, 1, 3),
               _begin(1, 2, 3), _set(3, r.Use(_copy(1))), _end(0), _end(1), _dead(3),
               _drop(2), _dead(2), _drop(1), _dead(1), _blank())))


# -- the pairing of `BeginBorrow` and `EndBorrow` ----------------------------------------


def test_an_end_with_no_begin_is_refused():
    _refused(_function([BLANK], set(), _block(0, _ret(), _end(0, BAD), _blank())), REGION)


def test_a_region_open_at_the_return_is_refused():
    _refused(_function([BLANK, STRING], set(),
                       _block(0, _ret(BAD), _live(1), _text(1), _begin(0, 1), _blank())),
             REGION)


def test_a_region_that_ends_on_one_path_alone_is_refused():
    _refused(_function(
        [BLANK, BOOL, STRING, STRING], {3},
        _block(0, _switch(1, 2, 1), _live(2), _text(2), _live(3), _begin(0, 2, 3),
               _set(3, r.Use(_copy(2)))),
        _block(1, _goto(3), _end(0)),
        _block(2, _goto(3, BAD)),
        _block(3, _ret(), _dead(3), _drop(2), _dead(2), _blank()),
        params=((1, GUARANTEED),)), REGION)


def test_a_region_that_begins_two_times_on_one_path_is_refused():
    _refused(_viewed(_begin(0, 1, 2, BAD)), REGION)


# -- no change of an owner in its region -------------------------------------------------


def test_a_write_of_the_owner_in_its_region_is_refused():
    _refused(_viewed(_set(1, r.Use(_copy(2)), BAD)), OWNER)


def test_a_move_of_the_owner_in_its_region_is_refused():
    _refused(_viewed(_call("eat", _move(1), OWNED, BAD)), OWNER, declared=POKE)


def test_a_drop_of_the_owner_in_its_region_is_refused():
    _refused(_viewed(_drop(1, BAD), _live(1), _text(1)), OWNER)


def test_an_inout_argument_of_the_owner_in_its_region_is_refused():
    _refused(_viewed(_call("poke", _copy(1), Convention.INOUT, BAD)), OWNER, declared=POKE)


# -- a view local ----------------------------------------------------------------------


def test_a_drop_of_a_view_local_is_refused():
    _refused(_viewed(_drop(2, BAD)), VIEW)


def test_a_move_of_a_view_local_is_refused():
    _refused(_viewed(_set(3, r.Use(_move(2)), BAD), types=[BLANK, STRING, STRING, STRING]),
             VIEW)


def test_a_view_local_as_an_owned_argument_is_refused():
    _refused(_viewed(_call("eat", _copy(2), OWNED, BAD)), VIEW, declared=POKE)


def test_a_view_local_that_no_copy_writes_is_refused():
    _refused(_function([BLANK, I32], {1},
                       _block(0, _ret(), _live(1), _set(1, r.Use(p.Const(k.Int(1, I32))), BAD),
                              _dead(1), _blank())),
             VIEW)


def test_a_view_local_that_two_statements_write_is_refused():
    _refused(_viewed(_set(2, r.Use(_copy(1)), BAD)), VIEW)


# -- the read of a view local in its region (ir.md 8.13) -------------------------------

READ = {"unit$read": DeclaredParams((GUARANTEED,))}


def _read_view(view: int = 2, span=SPAN) -> st.Call:
    """`_4 = read(copy _view)`: a read of the view local."""
    return st.Call(_local(4), st.Direct("unit$read", "read"),
                   ((_copy(view), GUARANTEED),), span)


def test_a_read_of_a_view_local_in_a_later_block_of_its_region_passes():
    _passes(_function(
        [BLANK, STRING, STRING, BLANK, BLANK], {2},
        _block(0, _goto(1), _live(1), _text(1), _live(2), _begin(0, 1, 2),
               _set(2, r.Use(_copy(1)))),
        _block(1, _ret(), _read_view(), _end(0), _dead(2), _drop(1), _dead(1), _blank())),
        READ)


def test_a_read_of_a_view_local_with_no_region_passes():
    """A view of a value that no local owns (a constant) has no region."""
    _passes(_function(
        [BLANK, STRING, STRING, BLANK, BLANK], {2},
        _block(0, _ret(), _live(1), _text(1), _live(2), _set(2, r.Use(_copy(1))),
               _read_view(), _dead(2), _drop(1), _dead(1), _blank())),
        READ)


def test_a_read_of_a_view_local_after_the_end_of_its_region_is_refused():
    _refused(_function(
        [BLANK, STRING, STRING, BLANK, BLANK], {2},
        _block(0, _ret(), _live(1), _text(1), _live(2), _begin(0, 1, 2),
               _set(2, r.Use(_copy(1))), _read_view(), _end(0), _read_view(span=BAD),
               _dead(2), _drop(1), _dead(1), _blank())),
        VIEW, declared=READ)


def test_a_read_of_a_view_local_after_the_end_of_one_of_its_regions_is_refused():
    _refused(_function(
        [BLANK, STRING, STRING, STRING, BLANK], {3},
        _block(0, _ret(), _live(1), _text(1), _live(2), _text(2), _live(3), _begin(0, 1, 3),
               _begin(1, 2, 3), _set(3, r.Use(_copy(1))), _end(1), _read_view(3, BAD),
               _end(0), _dead(3), _drop(2), _dead(2), _drop(1), _dead(1), _blank())),
        VIEW, declared=READ)


# -- a region names its view (ir.md 8.6, 8.13) -------------------------------------------


def _apart(*after: object) -> SlirFunction:
    """`b0` over `_1` names the view `_2`, and the `StorageLive` of `_2` stands between the
    `BeginBorrow` and the write of `_2`: the statements `after` follow the write."""
    return _function(
        [BLANK, STRING, STRING, BLANK, BLANK], {2},
        _block(0, _ret(), _live(1), _text(1), _begin(0, 1, 2), _live(2),
               _set(2, r.Use(_copy(1))), *after, _dead(2), _drop(1), _dead(1), _blank()))


def test_a_read_of_a_view_local_in_a_region_that_names_it_passes():
    _passes(_apart(_read_view(), _end(0)), READ)


def test_a_read_of_a_view_local_after_the_end_of_a_region_that_names_it_is_refused():
    """The region is the one that names the view, not the one right before its write."""
    _refused(_apart(_read_view(), _end(0), _read_view(span=BAD)), VIEW, declared=READ)


def test_a_region_that_names_no_view_local_is_refused():
    _refused(_function(
        [BLANK, STRING, STRING, BLANK, BLANK], {2},
        _block(0, _ret(), _live(1), _text(1), _live(2), _begin(0, 1, 3, BAD),
               _set(2, r.Use(_copy(1))), _end(0), _dead(2), _drop(1), _dead(1), _blank())),
        VIEW)


def test_a_region_that_names_two_views_is_refused():
    _refused(_function(
        [BLANK, STRING, STRING, STRING, BLANK], {2, 3},
        _block(0, _ret(), _live(1), _text(1), _live(2), _begin(0, 1, 2),
               _set(2, r.Use(_copy(1))), _end(0), _live(3), _begin(0, 1, 3, BAD),
               _set(3, r.Use(_copy(1))), _end(0), _dead(3), _dead(2), _drop(1), _dead(1),
               _blank())),
        VIEW)


# -- the uses of an address local (ir.md 8.2, 8.13) -------------------------------------

ADDRESS = "an address local"


def _ref(local: int, owner: int, span=SPAN) -> st.Assign:
    return _set(local, r.Ref(_local(owner), BorrowMode.PEEK), span)


def _read(local: int, span=SPAN) -> st.Assign:
    """`_4 = copy _local`: a use of the local."""
    return _set(4, r.Use(_copy(local)), span)


def _referenced(*blocks: SlirBlock) -> SlirFunction:
    """`_1` holds an `i32`, `_2` is a bool, `_3` is an address local of `_1`, `_4` reads."""
    return _function([BLANK, I32, BOOL, I32, I32], set(), *blocks)


def _one_of(value: int) -> st.Assign:
    return _set(1, r.Use(p.Const(k.Int(value, I32))))


def test_an_address_local_used_in_its_region_on_each_path_passes():
    _passes(_referenced(
        _block(0, _switch(2, 2, 1), _live(1), _one_of(1), _live(2),
               _set(2, r.Use(p.Const(k.Bool(True)))), _live(3), _live(4), _begin(0, 1),
               _ref(3, 1)),
        _block(1, _goto(3), _read(3)),
        _block(2, _goto(3), _read(3)),
        _block(3, _ret(), _end(0), _dead(4), _dead(3), _dead(2), _dead(1), _blank())))


def test_a_use_of_an_address_local_before_its_ref_on_one_path_is_refused():
    """The `Ref` stands on one arm of the switch, and the join reads the local."""
    _refused(_referenced(
        _block(0, _switch(2, 2, 1), _live(1), _one_of(1), _live(2),
               _set(2, r.Use(p.Const(k.Bool(True)))), _live(3), _live(4), _begin(0, 1)),
        _block(1, _goto(3), _ref(3, 1)),
        _block(2, _goto(3)),
        _block(3, _ret(), _read(3, BAD), _end(0), _dead(4), _dead(3), _dead(2), _dead(1),
               _blank())), ADDRESS)


def test_a_use_of_an_address_local_after_the_end_of_its_region_is_refused():
    _refused(_referenced(
        _block(0, _ret(), _live(1), _one_of(1), _live(3), _live(4), _begin(0, 1), _ref(3, 1),
               _read(3), _end(0), _read(3, BAD), _dead(4), _dead(3), _dead(1), _blank())),
        ADDRESS)


def test_a_use_of_an_address_local_in_a_region_over_another_owner_is_refused():
    _refused(_referenced(
        _block(0, _ret(), _live(1), _one_of(1), _live(2),
               _set(2, r.Use(p.Const(k.Bool(True)))), _live(3), _live(4), _begin(0, 2),
               _ref(3, 1), _read(3, BAD), _end(0), _dead(4), _dead(3), _dead(2), _dead(1),
               _blank())),
        ADDRESS)



# -- the writes of a view local and of an address local on each path (ir.md 8.9, 8.13) ---


def _alternatives(left: tuple[object, ...], right: tuple[object, ...],
                  body: tuple[object, ...] = (_read_view(3), _end(0))) -> SlirFunction:
    """The two paths of an arm with alternatives: `_1` is the bool of the switch, `_2` owns a
    string, `_3` is a view of it. `left` and `right` are the blocks of the two paths, and
    they meet at the body `body`."""
    return _function(
        [BLANK, BOOL, STRING, STRING, BLANK], {3},
        _block(0, _switch(1, 2, 1), _live(2), _text(2), _live(3)),
        _block(1, _goto(3), *left),
        _block(2, _goto(3), *right),
        _block(3, _ret(), *body, _dead(3), _drop(2), _dead(2), _blank()),
        params=((1, GUARANTEED),))


def test_two_paths_that_each_write_a_view_local_one_time_pass():
    write = (_begin(0, 2, 3), _set(3, r.Use(_copy(2))))
    _passes(_alternatives(write, write), READ)


def test_one_path_that_writes_a_view_local_two_times_is_refused():
    _refused(_alternatives(
        (_begin(0, 2, 3), _set(3, r.Use(_copy(2))), _set(3, r.Use(_copy(2)), BAD)),
        (_begin(0, 2, 3), _set(3, r.Use(_copy(2))))), VIEW, declared=READ)


def test_a_view_local_that_one_statement_writes_in_each_round_of_a_loop_passes():
    """The answer of the step of a walk: its storage starts before the loop, and the header
    writes it in each round."""
    _passes(_function(
        [BLANK, BOOL, STRING, STRING, BLANK], {3},
        _block(0, _goto(1), _live(2), _text(2), _live(3)),
        _block(1, _switch(1, 3, 2), _set(3, r.Use(_copy(2)))),
        _block(2, _goto(1), _read_view(3)),
        _block(3, _ret(), _dead(3), _drop(2), _dead(2), _blank()),
        params=((1, GUARANTEED),)), READ)


def test_a_view_local_that_a_later_round_writes_with_another_statement_is_refused():
    _refused(_function(
        [BLANK, BOOL, STRING, STRING, BLANK], {3},
        _block(0, _goto(1), _live(2), _text(2), _live(3), _set(3, r.Use(_copy(2)))),
        _block(1, _switch(1, 3, 2), _read_view(3)),
        _block(2, _goto(1), _set(3, r.Use(_copy(2)), BAD)),
        _block(3, _ret(), _dead(3), _drop(2), _dead(2), _blank()),
        params=((1, GUARANTEED),)), VIEW, declared=READ)


def test_two_paths_that_meet_with_two_region_ids_are_refused():
    """Each path begins a region over the owner, but with another id."""
    function = _alternatives((_begin(0, 2),), (_begin(1, 2),), body=(_end(0),))
    blocks = tuple(replace(block, terminator=tm.Goto(BlockId(3), BAD))
                   if block.id == BlockId(2) else block for block in function.blocks)
    _refused(replace(function, blocks=blocks), REGION)


def _poke_alternatives(left: tuple[object, ...], right: tuple[object, ...],
                       span=SPAN) -> SlirFunction:
    """The two paths of an arm with `poke` alternatives: `_3` is an address local of `_1`,
    and `_5` holds another `i32`, as the two parts of a tuple literal. The body reads `_3`
    at `span`."""
    return _function(
        [BLANK, I32, BOOL, I32, I32, I32], set(),
        _block(0, _switch(2, 2, 1), _live(1), _one_of(1), _live(5),
               _set(5, r.Use(p.Const(k.Int(2, I32)))), _live(2),
               _set(2, r.Use(p.Const(k.Bool(True)))), _live(3), _live(4)),
        _block(1, _goto(3), *left),
        _block(2, _goto(3), *right),
        _block(3, _ret(), _read(3, span), _end(0), _dead(4), _dead(3), _dead(2), _dead(5),
               _dead(1), _blank()))


def test_two_paths_that_each_write_an_address_local_with_one_ref_pass():
    write = (_begin(0, 1), _ref(3, 1))
    _passes(_poke_alternatives(write, write))


def test_one_path_that_writes_an_address_local_with_two_refs_is_refused():
    _refused(_poke_alternatives((_begin(0, 1), _ref(3, 1), _ref(3, 1, BAD)),
                                (_begin(0, 1), _ref(3, 1))), ADDRESS)


def test_two_paths_that_write_an_address_local_into_two_owners_in_one_region_pass():
    """Each path points into its own part of a tuple literal, in the region `b0` over that
    part: at the body, `b0` is open over both owners."""
    _passes(_poke_alternatives((_begin(0, 1), _ref(3, 1)), (_begin(0, 5), _ref(3, 5))))


def test_a_use_of_an_address_local_with_a_root_that_no_open_region_holds_is_refused():
    """The second path points into `_5`, but its region is over `_1`."""
    _refused(_poke_alternatives((_begin(0, 1), _ref(3, 1)), (_begin(0, 1), _ref(3, 5)),
                                span=BAD), ADDRESS)
