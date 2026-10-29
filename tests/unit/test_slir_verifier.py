"""The SLIR verifier over hand-built functions (docs/design/ir.md 8.13).

A correct lowering never makes a function that breaks a rule, so no fixture can show a rule
refuse. These tests give the per-function check of the verifier a function that breaks one
rule each, and the functions of the worked example of ir.md 8.11, which keep every rule.
They call a Python function over data; they do not run the compiler.
"""
from __future__ import annotations

from typing import Mapping, Optional, get_args

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.errors.registry import REGISTRY, Category, ErrorMessage, Severity
from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.passes.collect.functions import FuncSig, FunctionTable, Param
from sushi_lang.semantics.shir.callee import BuiltinMethod, Intrinsic
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock, SlirFunction, SlirLocal
from sushi_lang.semantics.slir.ids import BlockId, BorrowId, LocalId
from sushi_lang.semantics.slir.verify import DeclaredParams, declared_params, verify_function
from sushi_lang.semantics.typesys import (
    BorrowMode, BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo, StructType, Type,
    UnknownType,
)
from sushi_lang.semantics.unit_symbols import emitted_symbol

from tests.unit.test_slir_printer import (
    RESULT_STRING, _decorate, _greet, _main, _shout,
)

SPAN = Span(1, 1, 1, 9)
BAD = Span(7, 5, 7, 20)
OWNED, GUARANTEED = Convention.OWNED, Convention.GUARANTEED
I32, STRING, BOOL, BLANK = BuiltinType.I32, BuiltinType.STRING, BuiltinType.BOOL, BuiltinType.BLANK
PAIR = StructType(name="Pair", fields=(("first", I32), ("second", I32)))

GREET_DECLARED = {
    "test_spine_greet$decorate": DeclaredParams((OWNED,)),
    "test_spine_greet$shout": DeclaredParams((OWNED,)),
    "test_spine_greet$greet": DeclaredParams((OWNED, GUARANTEED)),
}


def _local(index: int) -> p.Local:
    return p.Local(LocalId(index))


def _copy(index: int) -> p.Copy:
    return p.Copy(_local(index))


def _move(index: int) -> p.Move:
    return p.Move(_local(index))


def _set(index: int, value: r.Rvalue, span: Span = SPAN) -> st.Assign:
    return st.Assign(_local(index), value, span)


def _text(index: int, span: Span = SPAN) -> st.Call:
    """`_index = interp("x")`: a new owning string."""
    return st.Call(_local(index), st.Builtin(Intrinsic("interp"), (STRING,)),
                   ((p.Const(k.Str(b"x")), GUARANTEED),), span)


def _flag(index: int, value: bool, span: Span = SPAN) -> st.Assign:
    return _set(index, r.Use(p.Const(k.Bool(value))), span)


def _live(index: int) -> st.StorageLive:
    return st.StorageLive(LocalId(index), SPAN)


def _dead(index: int, span: Span = SPAN) -> st.StorageDead:
    return st.StorageDead(LocalId(index), span)


def _drop(index: int, span: Span = SPAN) -> st.Drop:
    return st.Drop(_local(index), span)


def _block(index: int, terminator: object, *statements: object) -> SlirBlock:
    return SlirBlock(BlockId(index), tuple(statements), terminator)  # type: ignore[arg-type]


def _ret(span: Span = SPAN) -> tm.Return:
    return tm.Return(span)


def _goto(index: int, span: Span = SPAN) -> tm.Goto:
    return tm.Goto(BlockId(index), span)


def _switch(local: int, zero: int, other: int) -> tm.SwitchInt:
    return tm.SwitchInt(_copy(local), ((0, BlockId(zero)),), BlockId(other), SPAN)


def _function(types: list[Optional[Type]], *blocks: SlirBlock,
              params: tuple[tuple[int, Convention], ...] = (),
              entry: int = 0) -> SlirFunction:
    locals_ = tuple(SlirLocal(LocalId(index), ty, None, None)  # type: ignore[arg-type]
                    for index, ty in enumerate(types))
    return SlirFunction("unit$probe", "probe", "unit",
                        tuple((LocalId(index), convention) for index, convention in params),
                        locals_, blocks, BlockId(entry))


def _passes(function: SlirFunction,
            declared: Optional[Mapping[str, DeclaredParams]] = None) -> None:
    table = declared or {}
    verify_function(function, drops=frozenset(),
                    declared=lambda callee: table.get(callee.symbol))


def _refused(function: SlirFunction, rule: str, span: Optional[Span] = BAD,
             declared: Optional[Mapping[str, DeclaredParams]] = None) -> InternalCompilerError:
    with pytest.raises(InternalCompilerError) as caught:
        _passes(function, declared)
    error = caught.value
    assert error.code == "CE0158"
    assert error.params["rule"] == rule
    assert error.params["function"] == function.name
    assert error.span == span
    return error


# -- functions that keep every rule ----------------------------------------------------


def test_the_functions_of_the_worked_example_pass():
    for function in (_decorate(), _shout(), _greet(), _main()):
        _passes(function, GREET_DECLARED)


def test_a_loop_whose_body_declares_and_drops_an_owning_local_passes():
    _passes(_function(
        [I32, BOOL, STRING],
        _block(0, _goto(1), _set(1, r.Use(p.Const(k.Bool(True))))),
        _block(1, _switch(1, 3, 2)),
        _block(2, _goto(1), _live(2), _text(2), _drop(2), _dead(2)),
        _block(3, _ret(), _set(0, r.Use(p.Const(k.Int(0, I32))))),
    ))


def test_a_flagged_local_drops_with_drop_where_its_state_is_exact_and_with_its_flag_after_a_join():
    _passes(_function(
        [BLANK, BOOL, STRING, BOOL, STRING],
        _block(0, _switch(1, 2, 1), _live(2), _text(2), _live(3), _flag(3, True)),
        _block(1, _goto(2), _set(4, r.Use(_move(2))), _flag(3, False), _drop(4)),
        _block(2, _goto(3), st.DropIfSet(_local(2), LocalId(3), SPAN), _text(2), _flag(3, True)),
        _block(3, _ret(), _set(0, r.Use(p.Const(k.Blank()))), _drop(2), _dead(3), _dead(2)),
        params=((1, GUARANTEED),),
    ))


def test_a_var_arg_callee_checks_its_declared_prefix_alone():
    call = st.Call(_local(1), st.Direct("printf", "printf"),
                   ((p.Const(k.Int(1, I32)), GUARANTEED), (p.Const(k.Int(2, I32)), OWNED)), SPAN)
    _passes(_function([BLANK, I32], _block(0, _ret(), call, _set(0, r.Use(p.Const(k.Blank()))))),
            {"printf": DeclaredParams((GUARANTEED,), var_arg=True)})


# -- one function for each rule --------------------------------------------------------


def test_a_local_with_no_type_is_refused():
    error = _refused(_function([BLANK, None], _block(0, _ret())), "typed locals", None)
    assert "_1" in error.params["detail"]


def test_a_local_with_an_unresolved_type_is_refused():
    _refused(_function([BLANK, UnknownType("Foo")], _block(0, _ret())), "typed locals", None)


def test_a_field_past_the_fields_of_a_struct_is_refused():
    read = _set(1, r.Use(p.Copy(p.Field(_local(2), 2))), BAD)
    _refused(_function([BLANK, I32, PAIR], _block(0, _ret(), read)), "typed places")


def test_a_field_of_a_value_that_has_no_fields_is_refused():
    read = _set(1, r.Use(p.Copy(p.Field(_local(1), 0))), BAD)
    _refused(_function([BLANK, I32], _block(0, _ret(), read)), "typed places")


def test_a_downcast_to_a_variant_that_the_enum_does_not_have_is_refused():
    read = _set(1, r.Use(p.Copy(p.Field(p.Downcast(_local(2), "Gone"), 0))), BAD)
    _refused(_function([BLANK, STRING, RESULT_STRING], _block(0, _ret(), read)), "typed places")


def test_a_place_that_names_no_local_of_the_function_is_refused():
    _refused(_function([BLANK], _block(0, _ret(), _set(9, r.Use(p.Const(k.Blank())), BAD))),
             "typed places")


def test_a_use_after_a_move_is_refused():
    _refused(_function(
        [BLANK, STRING, STRING, I32],
        _block(0, _ret(), _set(2, r.Use(_move(1))), _set(3, r.Len(_local(1)), BAD), _drop(2)),
        params=((1, OWNED),),
    ), "no use after a move")


def test_a_use_after_a_move_on_one_path_is_refused():
    _refused(_function(
        [BLANK, BOOL, STRING, STRING, I32],
        _block(0, _switch(1, 2, 1), _live(2), _text(2)),
        _block(1, _goto(2), _set(3, r.Use(_move(2)))),
        _block(2, _ret(), _set(4, r.Len(_local(2)), BAD)),
        params=((1, GUARANTEED),),
    ), "no use after a move")


def test_a_move_of_a_payload_moves_its_whole_local():
    payload = p.Move(p.Field(p.Downcast(_local(1), "Ok"), 0))
    _refused(_function(
        [BLANK, RESULT_STRING, STRING, I32],
        _block(0, _ret(), _set(2, r.Use(payload)), _set(3, r.Discriminant(_local(1)), BAD),
               _drop(2)),
        params=((1, OWNED),),
    ), "no use after a move")


def test_a_move_out_of_a_global_is_refused():
    _refused(_function([BLANK, STRING],
                       _block(0, _ret(), _set(1, r.Use(p.Move(p.Global("unit$log"))), BAD),
                              _drop(1))),
             "no move out of a global")


def test_a_block_with_no_terminator_is_refused():
    _refused(_function([BLANK], _block(0, None)), "one terminator per block", None)


def test_a_terminator_among_the_statements_is_refused():
    _refused(_function([BLANK], _block(0, _ret(), tm.Return(BAD))), "one terminator per block")


def test_a_jump_to_a_block_that_the_function_does_not_have_is_refused():
    _refused(_function([BLANK], _block(0, _goto(4, BAD))), "known blocks")


def test_an_entry_that_the_function_does_not_have_is_refused():
    _refused(_function([BLANK], _block(0, _ret()), entry=2), "known blocks", None)


def test_a_drop_of_a_plain_value_is_refused():
    _refused(_function([BLANK, I32], _block(0, _ret(), _set(1, r.Use(p.Const(k.Int(1, I32)))),
                                            _drop(1, BAD))),
             "drop of an owning type")


def test_a_drop_flag_that_is_no_bool_is_refused():
    _refused(_function(
        [BLANK, STRING, I32],
        _block(0, _ret(), _text(1), st.DropIfSet(_local(1), LocalId(2), BAD)),
    ), "bool drop flag")


def test_an_owning_local_that_the_function_returns_with_is_refused():
    _refused(_function([BLANK, STRING], _block(0, _ret(BAD), _live(1), _text(1))),
             "one drop or move on every path")


def test_an_owning_local_dropped_two_times_is_refused():
    _refused(_function([BLANK, STRING],
                       _block(0, _ret(), _live(1), _text(1), _drop(1), _drop(1, BAD))),
             "one drop or move on every path")


def test_an_owning_local_dropped_on_one_path_alone_is_refused():
    _refused(_function(
        [BLANK, BOOL, STRING],
        _block(0, _switch(1, 2, 1), _live(2), _text(2)),
        _block(1, _goto(2), _drop(2)),
        _block(2, _ret(), _dead(2, BAD)),
        params=((1, GUARANTEED),),
    ), "one drop or move on every path")


def test_a_drop_if_set_whose_flag_is_false_on_a_live_path_is_refused():
    _refused(_function(
        [BLANK, STRING, BOOL],
        _block(0, _ret(), _live(1), _text(1), _flag(2, False),
               st.DropIfSet(_local(1), LocalId(2), BAD)),
    ), "one drop or move on every path")


def test_a_drop_if_set_whose_flag_is_true_after_a_move_is_refused():
    _refused(_function(
        [BLANK, STRING, BOOL, STRING],
        _block(0, _ret(), _live(1), _text(1), _flag(2, True), _set(3, r.Use(_move(1))),
               st.DropIfSet(_local(1), LocalId(2), BAD), _drop(3)),
    ), "one drop or move on every path")


def test_a_drop_of_a_borrowed_parameter_is_refused():
    _refused(_function([BLANK, STRING], _block(0, _ret(), _drop(1, BAD)),
                       params=((1, GUARANTEED),)),
             "one drop or move on every path")


def test_an_owned_parameter_that_the_function_never_drops_is_refused():
    _refused(_function([BLANK, STRING], _block(0, _ret(BAD)), params=((1, OWNED),)),
             "one drop or move on every path")


def test_an_inout_parameter_that_holds_no_value_at_the_return_is_refused():
    _refused(_function([BLANK, STRING], _block(0, _ret(BAD), _drop(1)),
                       params=((1, Convention.INOUT),)),
             "one drop or move on every path")


def test_an_overwrite_of_a_live_owning_local_is_refused():
    _refused(_function([BLANK, STRING],
                       _block(0, _ret(), _live(1), _text(1), _text(1, BAD), _drop(1))),
             "one drop or move on every path")


def test_the_end_of_the_storage_of_a_live_owning_local_is_refused():
    _refused(_function([BLANK, STRING], _block(0, _ret(), _live(1), _text(1), _dead(1, BAD))),
             "one drop or move on every path")


def test_the_start_of_the_storage_of_a_live_owning_local_is_refused():
    error = _refused(_function([BLANK, STRING], _block(0, _ret(), _live(1), _text(1),
                                                       st.StorageLive(LocalId(1), BAD))),
                     "one drop or move on every path")
    assert "starts the storage of _1" in error.params["detail"]


def test_the_end_of_the_storage_names_the_end():
    error = _refused(_function([BLANK, STRING], _block(0, _ret(), _live(1), _text(1),
                                                       _dead(1, BAD))),
                     "one drop or move on every path")
    assert "ends the storage of _1" in error.params["detail"]


def test_a_read_of_a_local_that_holds_no_value_is_refused():
    _refused(_function([BLANK, I32, I32],
                       _block(0, _ret(), _live(1), _set(2, r.Use(_copy(1)), BAD),
                              _set(0, r.Use(p.Const(k.Blank()))))),
             "no use of a local outside its storage")


def test_a_read_of_a_local_after_the_end_of_its_storage_is_refused():
    _refused(_function([BLANK, I32, I32],
                       _block(0, _ret(), _live(1), _set(1, r.Use(p.Const(k.Int(1, I32)))),
                              _dead(1), _set(2, r.Use(_copy(1)), BAD),
                              _set(0, r.Use(p.Const(k.Blank()))))),
             "no use of a local outside its storage")


def test_a_move_of_a_local_that_holds_no_value_is_refused():
    _refused(_function([BLANK, STRING, STRING],
                       _block(0, _ret(), _set(2, r.Use(_move(1)), BAD), _drop(2),
                              _set(0, r.Use(p.Const(k.Blank()))))),
             "no use of a local outside its storage")


def test_a_return_with_no_value_in_the_return_slot_is_refused():
    _refused(_function([I32], _block(0, _ret(BAD))), "a written return slot")


def test_a_return_with_no_value_in_the_return_slot_on_one_path_is_refused():
    _refused(_function(
        [I32, BOOL],
        _block(0, _switch(1, 2, 1)),
        _block(1, _goto(2), _set(0, r.Use(p.Const(k.Int(1, I32))))),
        _block(2, _ret(BAD)),
        params=((1, GUARANTEED),),
    ), "a written return slot")


def _call(*args: tuple[p.Operand, Convention]) -> st.Call:
    return st.Call(_local(1), st.Direct("unit$take", "take"), args, BAD)


def test_an_argument_whose_convention_is_not_the_declared_one_is_refused():
    call = _call((p.Const(k.Int(1, I32)), OWNED))
    _refused(_function([BLANK, BLANK], _block(0, _ret(), call)), "declared conventions",
             declared={"unit$take": DeclaredParams((GUARANTEED,))})


def test_a_call_with_more_arguments_than_the_declared_parameters_is_refused():
    one = (p.Const(k.Int(1, I32)), GUARANTEED)
    _refused(_function([BLANK, BLANK], _block(0, _ret(), _call(one, one))),
             "declared conventions", declared={"unit$take": DeclaredParams((GUARANTEED,))})


def test_a_direct_callee_with_no_declaration_is_refused():
    _refused(_function([BLANK, BLANK], _block(0, _ret(), _call())), "declared conventions")


def test_a_var_arg_callee_checks_the_convention_of_its_declared_prefix():
    call = _call((p.Const(k.Int(1, I32)), OWNED), (p.Const(k.Int(2, I32)), GUARANTEED))
    _refused(_function([BLANK, BLANK], _block(0, _ret(), call)), "declared conventions",
             declared={"unit$take": DeclaredParams((GUARANTEED,), var_arg=True)})


def _method(*args: tuple[p.Operand, Convention]) -> st.Call:
    return st.Call(_local(1), st.Builtin(BuiltinMethod("string", "len", nom_self=False), ()), args, BAD)


def test_a_built_in_method_call_with_no_receiver_operand_is_refused():
    _refused(_function([BLANK, I32], _block(0, _ret(), _method(), _set(0, r.Use(p.Const(k.Blank()))))),
             "a receiver operand")


def test_a_built_in_method_call_with_its_receiver_first_passes():
    text = _set(2, r.Use(p.Const(k.Str(b"Mostly Harmless"))))
    _passes(_function(
        [BLANK, I32, STRING],
        _block(0, _ret(), _live(2), text, _method((_copy(2), GUARANTEED)),
               _set(0, r.Use(p.Const(k.Blank()))), _drop(2), _dead(2)),
    ))


TAKE = {"unit$take": DeclaredParams((OWNED,))}


def test_a_copy_of_an_owning_value_into_an_owned_argument_is_refused():
    call = _call((_copy(2), OWNED))
    _refused(_function(
        [BLANK, BLANK, DynamicArrayType(I32)],
        _block(0, _ret(), call, _drop(2), _set(0, r.Use(p.Const(k.Blank())))),
        params=((2, OWNED),),
    ), "no copy of an owning value into an owned argument", declared=TAKE)


def test_a_copy_of_a_string_into_an_owned_argument_passes():
    """A string bound from a literal owns no heap, so `borrow` adopts it: two calls take a
    copy of it, and the local drops at its exit through its `owned` byte (ir.md 7.9)."""
    text = _set(1, r.Use(p.Const(k.Str(b"Mostly Harmless"))))
    first = st.Call(_local(2), st.Direct("unit$take", "take"), ((_copy(1), OWNED),), SPAN)
    second = st.Call(_local(3), st.Direct("unit$take", "take"), ((_copy(1), OWNED),), SPAN)
    _passes(_function(
        [BLANK, STRING, BLANK, BLANK],
        _block(0, _ret(), _live(1), text, first, second, _set(0, r.Use(p.Const(k.Blank()))),
               _drop(1), _dead(1)),
    ), TAKE)


@pytest.mark.parametrize("op", ["and", "or"])
def test_a_binary_that_carries_a_short_circuit_is_refused(op: str):
    value = r.Binary(op, p.Const(k.Bool(True)), p.Const(k.Bool(False)))  # type: ignore[arg-type]
    _refused(_function([BLANK, BOOL], _block(0, _ret(), _set(1, value, BAD))),
             "no short circuit in a binary")


@pytest.mark.parametrize("code, count", [
    ("RE9999", 0),      # no registered code
    ("CE0155", 0),      # a registered code that is no runtime code
    ("RE2020", 1),      # its text prints two values
    ("RE2021", 1),      # its text prints none
])
def test_a_trap_that_names_no_runtime_code_with_its_operand_count_is_refused(code: str, count: int):
    trap = tm.Trap(code, tuple(p.Const(k.Int(index, I32)) for index in range(count)), BAD)
    _refused(_function([BLANK], _block(0, trap)), "registered trap")


def test_a_trap_with_the_operands_of_its_text_passes():
    trap = tm.Trap("RE2020", (p.Const(k.Int(5, I32)), p.Const(k.Int(3, I32))), SPAN)
    _passes(_function([BLANK], _block(0, trap)))


def test_a_trap_that_reads_an_i32_local_for_a_d_conversion_passes():
    trap = tm.Trap("RE2020", (_copy(1), p.Const(k.Int(3, I32))), SPAN)
    _passes(_function([BLANK, I32], _block(0, trap, _live(1),
                                           _set(1, r.Use(p.Const(k.Int(5, I32)))))))


@pytest.mark.parametrize("position, message", [
    ("probe.sushi:7:5", ""),                 # a bare assert: no `: `, an empty message
    ("probe.sushi:7:5: ", "the count is 3"),  # an assert with a message
])
def test_an_assert_trap_with_the_position_and_a_constant_message_passes(position: str,
                                                                        message: str):
    trap = tm.Trap("RE2026", (p.Const(k.text(position)), p.Const(k.text(message))), SPAN)
    _passes(_function([BLANK], _block(0, trap)))


def test_an_assert_trap_that_reads_its_message_from_a_live_local_passes():
    """The trap reads the message and drops nothing: the program ends there."""
    trap = tm.Trap("RE2026", (p.Const(k.text("probe.sushi:7:5: ")), _copy(1)), SPAN)
    _passes(_function([BLANK, STRING], _block(0, trap, _live(1), _text(1))))


def test_a_trap_that_moves_an_operand_is_refused():
    """A trap reads each operand as a `Guaranteed` argument (ir.md 8.8): a `Move` is refused."""
    trap = tm.Trap("RE2026", (p.Const(k.text("probe.sushi:7:5: ")), _move(1)), BAD)
    _refused(_function([BLANK, STRING], _block(0, trap, _live(1), _text(1))), "registered trap")


@pytest.mark.parametrize("code, args", [
    ("RE2020", (p.Const(k.text("5")), p.Const(k.Int(3, I32)))),   # %d takes an i32
    ("RE2020", (p.Const(k.Int(5, BuiltinType.I64)), p.Const(k.Int(3, I32)))),  # not an i64
    ("RE2026", (p.Const(k.text("probe.sushi:7:5")), p.Const(k.Int(0, I32)))),  # %.*s, a string
    ("RE2026", (p.Const(k.Bool(True)), p.Const(k.text("")))),     # the position is a string
    ("RE2026", (p.Const(k.text("probe.sushi:7:5")),)),             # its text prints two values
    ("RE2026", ()),                                                 # a bare assert has two
    ("RE2020", (p.Copy(p.Global("unit$g")), p.Const(k.Int(3, I32)))),  # no type to read
])
def test_a_trap_operand_of_the_wrong_type_for_its_conversion_is_refused(
        code: str, args: tuple[p.Operand, ...]):
    _refused(_function([BLANK], _block(0, tm.Trap(code, args, BAD))), "registered trap")


def test_a_trap_whose_text_holds_a_conversion_that_no_operand_fills_is_refused(monkeypatch):
    monkeypatch.setitem(REGISTRY, "RE9998", ErrorMessage("RE9998", Severity.ERROR,
                                                         "a ratio of %f", Category.RUNTIME))
    error = _refused(_function([BLANK], _block(0, tm.Trap("RE9998", (p.Const(k.Int(1, I32)),),
                                                          BAD))), "registered trap")
    assert "%f" in error.params["detail"]


def test_a_statement_with_no_span_is_refused():
    error = _refused(_function([BLANK], _block(0, _ret(), _set(0, r.Use(p.Const(k.Blank())),
                                                               None))),  # type: ignore[arg-type]
                     "spans", None)
    assert "bb0" in error.params["detail"]


def test_a_terminator_with_no_span_is_refused():
    _refused(_function([BLANK], _block(0, tm.Return(None))), "spans", None)  # type: ignore[arg-type]


# -- shapes that no lowering makes yet, which the verifier does not check ----------------


def _not_checked(function: SlirFunction) -> None:
    with pytest.raises(InternalCompilerError) as caught:
        _passes(function)
    assert caught.value.code == "CE0000"
    assert "not checked yet" in caught.value.params["detail"]
    assert caught.value.span == BAD


def test_a_begin_borrow_is_not_checked_yet():
    begin = st.BeginBorrow(BorrowId(0), _local(1), BorrowMode.PEEK, BAD)
    _not_checked(_function([BLANK, I32], _block(0, _ret(), _set(1, r.Use(p.Const(k.Int(1, I32)))),
                                                begin)))


def test_an_end_borrow_is_not_checked_yet():
    _not_checked(_function([BLANK], _block(0, _ret(), st.EndBorrow(BorrowId(0), BAD))))


@pytest.mark.parametrize("projection", [
    p.Field(p.Global("unit$pair"), 0),
    p.Index(p.Global("unit$table"), p.Const(k.Int(0, I32))),
    p.Downcast(p.Global("unit$cache"), "Some"),
    p.Deref(p.Global("unit$cell")),
], ids=["field", "index", "downcast", "deref"])
def test_a_projection_of_a_global_is_not_checked_yet(projection: p.Place):
    _not_checked(_function([BLANK, I32], _block(0, _ret(), _set(1, r.Use(p.Copy(projection)), BAD))))


@pytest.mark.parametrize("target", [p.Field(_local(1), 0), p.Global("unit$log")],
                         ids=["projection", "global"])
def test_a_drop_of_a_place_that_is_no_local_is_not_checked_yet(target: p.Place):
    _not_checked(_function([BLANK, PAIR], _block(0, _ret(), st.Drop(target, BAD))))


def test_a_drop_if_set_of_a_projection_is_not_checked_yet():
    drop = st.DropIfSet(p.Field(_local(1), 0), LocalId(2), BAD)
    _not_checked(_function([BLANK, PAIR, BOOL], _block(0, _ret(), _flag(2, True), drop)))


# -- the readers that lowering and the verifier share ------------------------------------


def test_every_rvalue_class_has_its_operands():
    place, operand = _local(1), _copy(2)
    values = {
        r.Use: r.Use(operand), r.Unary: r.Unary("-", operand),  # type: ignore[arg-type]
        r.Cast: r.Cast(operand, I32), r.Clone: r.Clone(operand),
        r.Binary: r.Binary("+", operand, operand),  # type: ignore[arg-type]
        r.Aggregate: r.Aggregate(r.AggStruct(PAIR), (operand, operand)),
        r.Discriminant: r.Discriminant(place), r.Len: r.Len(place),
        r.Ref: r.Ref(place, BorrowMode.PEEK), r.Slice: r.Slice(place, operand, operand),
    }
    assert set(values) == set(get_args(r.Rvalue))
    assert r.operands_of(values[r.Slice]) == (place, operand, operand)
    assert r.operands_of(values[r.Aggregate]) == (operand, operand)


def test_the_operands_of_an_unknown_rvalue_class_are_an_internal_error():
    with pytest.raises(InternalCompilerError) as caught:
        r.operands_of(object())  # type: ignore[arg-type]
    assert caught.value.code == "CE0000"


def test_the_declared_parameters_of_a_callee_are_read_from_the_function_table_at_each_call():
    funcs = FunctionTable()
    callee = st.Direct(emitted_symbol("late", "unit"), "late")
    assert declared_params(funcs, callee) is None
    funcs.declare("late", FuncSig("late", params=[Param("x", STRING, None, None, 0, is_nom=True)],
                                  unit_name="unit"))
    assert declared_params(funcs, callee) == DeclaredParams((OWNED,))
    assert declared_params(funcs, st.Direct(emitted_symbol("late", "other"), "late")) is None


# -- the rule of a cast (ir.md 8.7) ---------------------------------------------------------


I64, U8, F64 = BuiltinType.I64, BuiltinType.U8, BuiltinType.F64


def _cast_of(operand: p.Operand, source: Type, target: Type, span: Span = SPAN) -> SlirFunction:
    """`_2 = operand as target`, where `_1` is a parameter of type `source`."""
    done = _set(0, r.Use(p.Const(k.Blank())))
    return _function([BLANK, source, target],
                     _block(0, _ret(), _live(2), _set(2, r.Cast(operand, target), span), done,
                            _dead(2)),
                     params=((1, GUARANTEED),))


@pytest.mark.parametrize("source, target", [
    (I32, I64), (I64, I32), (I32, U8), (I32, F64), (F64, I32), (BuiltinType.F32, F64),
    (BOOL, BOOL),
], ids=str)
def test_a_cast_between_numeric_types_or_of_one_type_passes(source: Type, target: Type):
    _passes(_cast_of(_copy(1), source, target))


def test_a_cast_of_a_numeric_constant_passes():
    _passes(_cast_of(p.Const(k.Int(255, U8)), I32, I32))


@pytest.mark.parametrize("value, ty", [
    (255, U8), (0, U8), (-128, BuiltinType.I8), (127, BuiltinType.I8),
    (65535, BuiltinType.U16), (-32768, BuiltinType.I16), (4294967295, BuiltinType.U32),
    (-(1 << 63), I64), ((1 << 64) - 1, BuiltinType.U64),
], ids=str)
def test_an_integer_constant_in_the_range_of_its_type_passes(value: int, ty: Type):
    _passes(_cast_of(p.Const(k.Int(value, ty)), ty, ty))


@pytest.mark.parametrize("value, ty", [
    (300, U8), (-1, U8), (128, BuiltinType.I8), (65536, BuiltinType.U16),
    (-32769, BuiltinType.I16), (4294967296, BuiltinType.U32), (1 << 63, I64),
], ids=str)
def test_an_integer_constant_out_of_the_range_of_its_type_is_refused(value: int, ty: Type):
    _refused(_cast_of(p.Const(k.Int(value, ty)), ty, ty, BAD), "a constant in range")


@pytest.mark.parametrize("source, target", [
    (BOOL, I32), (I32, BOOL), (STRING, I32), (I32, STRING), (PAIR, I32),
], ids=str)
def test_a_cast_that_is_not_numeric_is_refused(source: Type, target: Type):
    _refused(_cast_of(_copy(1), source, target, BAD), "a numeric cast")


def test_a_cast_of_a_boolean_constant_is_refused():
    _refused(_cast_of(p.Const(k.Bool(True)), I32, I32, BAD), "a numeric cast")


# -- a switch on an integer ------------------------------------------------------------


def _switch_on(ty: Type, cases: tuple[int, ...], span: Span = SPAN) -> SlirFunction:
    """`switchInt(copy _1) -> [cases..., otherwise: bb1]` over the parameter `_1: ty`."""
    switch = tm.SwitchInt(_copy(1), tuple((case, BlockId(1)) for case in cases), BlockId(1),
                          span)
    return _function([BLANK, ty], _block(0, switch),
                     _block(1, _ret(), _set(0, r.Use(p.Const(k.Blank())))),
                     params=((1, GUARANTEED),))


@pytest.mark.parametrize("ty, cases", [
    (BuiltinType.I8, (-1, 0, 127, -128)),     # a match on an i8: `0xff` is the case -1
    (U8, (0, 47, 255)),
    (BuiltinType.U64, ((1 << 64) - 1,)),
    (BOOL, (0,)),                              # an `if`
    (I32, (0, 1)),                             # the tag of a `Result`
], ids=str)
def test_a_switch_with_distinct_cases_in_the_range_of_its_operand_passes(
        ty: Type, cases: tuple[int, ...]):
    _passes(_switch_on(ty, cases))


def test_a_switch_on_a_boolean_constant_passes():
    switch = tm.SwitchInt(p.Const(k.Bool(False)), ((0, BlockId(1)),), BlockId(1), SPAN)
    _passes(_function([BLANK], _block(0, switch),
                      _block(1, _ret(), _set(0, r.Use(p.Const(k.Blank()))))))


@pytest.mark.parametrize("ty, cases", [
    (BuiltinType.I8, (255,)),      # a bit pattern that is not read at the type
    (U8, (-1,)),
    (U8, (256,)),
    (BOOL, (2,)),
    (I32, (3, 1, 3)),              # one value two times
    (STRING, (0,)),                # no integer to switch on
    (PAIR, (0,)),
], ids=str)
def test_a_switch_with_a_case_that_its_operand_cannot_hold_or_holds_twice_is_refused(
        ty: Type, cases: tuple[int, ...]):
    _refused(_switch_on(ty, cases, BAD), "an integer switch")


def test_the_trap_after_the_last_arm_of_a_match_names_that_arm_and_passes():
    trap = tm.Trap("RE2023", (p.Const(k.text("0x80..=0xff")),), SPAN)
    _passes(_function([BLANK], _block(0, trap)))


# -- a switch on a tag and an enum value ------------------------------------------------

SHAPE = EnumType(name="Shape", variants=(
    EnumVariantInfo(name="Circle", associated_types=(I32,)),
    EnumVariantInfo(name="Rect", associated_types=(I32, I32)),
    EnumVariantInfo(name="Dot", associated_types=())))


def _tag_switch(cases: tuple[int, ...], span: Span = SPAN) -> SlirFunction:
    """`_2 = discriminant(_1)`, then a switch on `_2` over the parameter `_1: Shape`."""
    switch = tm.SwitchInt(_copy(2), tuple((case, BlockId(1)) for case in cases), None, span)
    return _function([BLANK, SHAPE, I32], _block(0, switch, _set(2, r.Discriminant(_local(1)))),
                     _block(1, _ret(), _set(0, r.Use(p.Const(k.Blank())))),
                     params=((1, GUARANTEED),))


def test_a_switch_on_the_tags_of_an_enum_passes():
    _passes(_tag_switch((0, 1, 2)))


@pytest.mark.parametrize("cases", [(3,), (-1,), (0, 7)], ids=str)
def test_a_switch_on_a_tag_with_a_case_that_names_no_variant_is_refused(cases: tuple[int, ...]):
    _refused(_tag_switch(cases, BAD), "a switch on a tag")


def test_a_switch_on_a_local_that_another_block_writes_is_an_integer_switch():
    """The tag rule reads the `Discriminant` of the block of the switch alone."""
    switch = tm.SwitchInt(_copy(2), ((7, BlockId(2)),), BlockId(2), SPAN)
    _passes(_function([BLANK, SHAPE, I32],
                      _block(0, _goto(1), _set(2, r.Discriminant(_local(1)))),
                      _block(1, switch),
                      _block(2, _ret(), _set(0, r.Use(p.Const(k.Blank())))),
                      params=((1, GUARANTEED),)))


def test_a_discriminant_of_a_value_that_is_no_enum_is_refused():
    _refused(_function([BLANK, I32, I32], _block(0, _ret(), _set(2, r.Discriminant(_local(1)), BAD),
                                                 _set(0, r.Use(p.Const(k.Blank())))),
                       params=((1, GUARANTEED),)), "an enum value")


def _variant(parts: tuple[p.Operand, ...], variant: str = "Rect", ty: Type = SHAPE,
             span: Span = SPAN) -> SlirFunction:
    value = r.Aggregate(r.AggVariant(ty, variant), parts)
    return _function([BLANK, ty], _block(0, _ret(), _live(1), _set(1, value, span), _dead(1),
                                         _set(0, r.Use(p.Const(k.Blank())))))


def test_a_variant_with_one_part_for_each_value_of_its_payload_passes():
    _passes(_variant((p.Const(k.Int(3, I32)), p.Const(k.Int(4, I32)))))
    _passes(_variant((), "Dot"))


@pytest.mark.parametrize("parts, variant, ty", [
    ((p.Const(k.Int(3, I32)),), "Rect", SHAPE),        # one part of two
    ((p.Const(k.Int(3, I32)),), "Dot", SHAPE),          # a part of no payload
    ((), "Gone", SHAPE),                                # no such variant
    ((), "Rect", PAIR),                                 # no enum
], ids=["count", "no payload", "variant", "no enum"])
def test_a_variant_that_its_enum_does_not_declare_is_refused(
        parts: tuple[p.Operand, ...], variant: str, ty: Type):
    _refused(_variant(parts, variant, ty, BAD), "an enum value")


def _constant(value: k.Constant, span: Span = SPAN) -> SlirFunction:
    return _function([BLANK, SHAPE], _block(0, _ret(), _live(1),
                                            _set(1, r.Use(p.Const(value)), span), _dead(1),
                                            _set(0, r.Use(p.Const(k.Blank())))))


def test_a_constant_variant_with_its_payload_passes():
    _passes(_constant(k.Aggregate(SHAPE, (k.Int(5, I32),), "Circle")))
    _passes(_constant(k.Aggregate(SHAPE, (), "Dot")))


@pytest.mark.parametrize("value", [
    k.Aggregate(SHAPE, (), "Circle"),
    k.Aggregate(SHAPE, (k.Int(5, I32),), "Gone"),
    k.Aggregate(PAIR, (), "Circle"),
], ids=["count", "variant", "no enum"])
def test_a_constant_variant_that_its_enum_does_not_declare_is_refused(value: k.Constant):
    _refused(_constant(value, BAD), "an enum value")


def test_an_expect_trap_with_the_position_and_the_message_passes():
    trap = tm.Trap("RE2029", (p.Const(k.text("f.sushi:3:5")), p.Const(k.text("no value"))),
                   SPAN)
    _passes(_function([BLANK], _block(0, trap)))
