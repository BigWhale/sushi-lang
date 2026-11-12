"""The SLIR verifier over hand-built functions (docs/design/ir.md 8.13).

A correct lowering never makes a function that breaks a rule, so no fixture can show a rule
refuse. These tests give the per-function check of the verifier a function that breaks one
rule each, and the functions of the worked example of ir.md 8.11, which keep every rule.
They call a Python function over data; they do not run the compiler.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Optional, get_args

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.errors.registry import REGISTRY, Category, ErrorMessage, Severity
from sushi_lang.internals.report import Span
from sushi_lang.semantics import ast
from sushi_lang.semantics.conversions import conversion_symbol
from sushi_lang.semantics.param_modes import Convention, ParamMode
from sushi_lang.semantics.generics.name_mangling import method_symbol
from sushi_lang.semantics.passes.collect.externals import ExternalSig, ExternalTable
from sushi_lang.semantics.passes.collect.functions import (
    ExtensionMethod, ExtensionTable, FuncSig, FunctionTable, Param,
)
from sushi_lang.semantics.passes.collect.perks import PerkImplementationTable
from sushi_lang.semantics.shir.callee import BuiltinMethod, Intrinsic
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock, SlirFunction, SlirLocal
from sushi_lang.semantics.slir.ids import BlockId, BorrowId, LocalId
from sushi_lang.semantics.slir.verify import (
    CONVENTIONS, DeclaredParams, declared_params, verify_function,
)
from sushi_lang.semantics.slir.verify_quarantine import is_public_function
from sushi_lang.semantics.slir.verify_rules import PTR_QUARANTINE, STACK_ITERATOR
from sushi_lang.semantics.typesys import (
    ArrayType, BorrowMode, BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo,
    ForeignPtrType, FunctionType, IteratorType, PointerType, ReferenceType, StructType, Type, UnknownType,
)
from sushi_lang.semantics.unit_symbols import emitted_symbol

from tests.unit.test_slir_printer import (
    RESULT_STRING, _decorate, _greet, _main, _result, _shout,
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
                       _block(0, _ret(), _set(1, r.Use(p.Move(p.Global("unit$log", STRING))),
                                                  BAD),
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


def _indirect(*args: tuple[p.Operand, Convention]) -> st.Call:
    return st.Call(_local(1), st.Indirect(p.Copy(_local(2))), args, BAD)


_BY_NOM = FunctionType((I32,), I32, None, captures=(), param_modes=(ParamMode.NOM,))


def _closure(local: int, ty: Type) -> st.Assign:
    return _set(local, r.Aggregate(r.AggClosure(ty), (p.Const(k.FnRef("unit$f")),)))


def test_an_indirect_argument_with_the_convention_of_its_function_type_passes():
    call = _indirect((p.Const(k.Int(1, I32)), OWNED))
    blank = _set(0, r.Use(p.Const(k.Blank())))
    _passes(_function([BLANK, I32, _BY_NOM], _block(0, _ret(), _closure(2, _BY_NOM), call, blank)))


def test_an_indirect_argument_with_another_convention_is_refused():
    call = _indirect((p.Const(k.Int(1, I32)), GUARANTEED))
    _refused(_function([BLANK, I32, _BY_NOM], _block(0, _ret(), _closure(2, _BY_NOM), call)),
             CONVENTIONS)


def test_an_indirect_call_with_a_wrong_count_is_refused():
    _refused(_function([BLANK, I32, _BY_NOM], _block(0, _ret(), _closure(2, _BY_NOM), _indirect())),
             CONVENTIONS)


def test_an_indirect_call_of_a_value_that_is_no_function_is_refused():
    call = _indirect((p.Const(k.Int(1, I32)), OWNED))
    _refused(_function([BLANK, I32, I32], _block(0, _ret(), _set(2, r.Use(p.Const(k.Int(1, I32)))), call)),
             CONVENTIONS)


def test_an_owned_copy_of_a_function_value_passes():
    """A named function owns no heap, and `borrow` adopts it (docs/design/ir.md 7.9)."""
    owning = replace(_BY_NOM, captures=None)
    takes = FunctionType((owning,), I32, None, captures=(), param_modes=(ParamMode.NOM,))
    call = st.Call(_local(1), st.Indirect(p.Copy(_local(3))), ((p.Copy(_local(2)), OWNED),), BAD)
    _passes(_function([BLANK, I32, owning, takes],
                      _block(0, _ret(), _closure(2, owning), _closure(3, takes), call,
                             st.Drop(_local(2), SPAN), _set(0, r.Use(p.Const(k.Blank()))))))


_ITERATOR = IteratorType(I32)
_ELEMENTS = DynamicArrayType(I32)


def _iterator_value(local: int, *, heap: bool) -> st.Assign:
    """An iterator over the array that the parameter `_1` holds."""
    source = r.IterElements(_ELEMENTS, True)
    return _set(local, r.Aggregate(r.AggIterator(_ITERATOR, source, heap), (_copy(1),)))


def _returns_iterator(*, heap: bool) -> SlirFunction:
    return _function([_ITERATOR, _ELEMENTS, _ITERATOR],
                     _block(0, _ret(), _live(2), _iterator_value(2, heap=heap),
                            st.Assign(_local(0), r.Use(_move(2)), BAD), _dead(2)),
                     params=((1, GUARANTEED),))


def test_a_heap_state_iterator_reaches_the_return_slot():
    _passes(_returns_iterator(heap=True))


def test_a_stack_state_iterator_in_the_return_slot_is_refused():
    _refused(_returns_iterator(heap=False), STACK_ITERATOR)


def test_a_stack_state_iterator_after_a_move_into_a_local_is_refused():
    function = _function([_ITERATOR, _ELEMENTS, _ITERATOR, _ITERATOR],
                         _block(0, _ret(), _live(2), _iterator_value(2, heap=False), _live(3),
                                _set(3, r.Use(_move(2))),
                                st.Assign(_local(0), r.Use(_move(3)), BAD), _dead(3), _dead(2)),
                         params=((1, GUARANTEED),))
    _refused(function, STACK_ITERATOR)


def test_a_stack_state_iterator_written_into_the_return_slot_is_refused():
    function = _function([_ITERATOR, _ELEMENTS],
                         _block(0, _ret(), st.Assign(_local(0), r.Aggregate(
                             r.AggIterator(_ITERATOR, r.IterElements(_ELEMENTS, True), False),
                             (_copy(1),)), BAD)),
                         params=((1, GUARANTEED),))
    _refused(function, STACK_ITERATOR)


_HELD = EnumType(name="Maybe<Iterator<i32>>", variants=[
    EnumVariantInfo(name="Some", associated_types=(_ITERATOR,)),
    EnumVariantInfo(name="None", associated_types=())])


def test_a_stack_state_iterator_in_a_variant_in_the_return_slot_is_refused():
    function = _function([_HELD, _ELEMENTS, _ITERATOR],
                         _block(0, _ret(), _live(2), _iterator_value(2, heap=False),
                                st.Assign(_local(0), r.Aggregate(r.AggVariant(_HELD, "Some"),
                                                                 (_move(2),)), BAD),
                                _dead(2)),
                         params=((1, GUARANTEED),))
    _refused(function, STACK_ITERATOR)


def test_a_stack_state_iterator_as_an_owned_argument_is_refused():
    takes = FunctionType((_ITERATOR,), BLANK, None, captures=(), param_modes=(ParamMode.NOM,))
    call = st.Call(_local(0), st.Indirect(p.Copy(_local(3))), ((_move(2), OWNED),), BAD)
    function = _function([BLANK, _ELEMENTS, _ITERATOR, takes],
                         _block(0, _ret(), _live(2), _iterator_value(2, heap=False),
                                _closure(3, takes), call, _dead(2)),
                         params=((1, GUARANTEED),))
    _refused(function, STACK_ITERATOR)


def _consumer(family: str) -> SlirFunction:
    method = BuiltinMethod(family, "collect", nom_self=True)
    call = st.Call(_local(3), st.Builtin(method, ()), ((_move(2), OWNED),), BAD)
    return _function([BLANK, _ELEMENTS, _ITERATOR, I32],
                     _block(0, _ret(), _live(2), _iterator_value(2, heap=False), _live(3), call,
                            _dead(3), _dead(2), _set(0, r.Use(p.Const(k.Blank())))),
                     params=((1, GUARANTEED),))


def test_a_stack_state_iterator_given_to_a_consumer_stays_in_the_function():
    _passes(_consumer("iterator"))


def test_a_stack_state_iterator_given_to_another_owned_builtin_is_refused():
    _refused(_consumer("maybe"), STACK_ITERATOR)


def test_the_step_of_an_iterator_takes_no_argument():
    call = st.Call(_local(0), st.Indirect(p.Copy(_local(2))), ((p.Const(k.Int(1, I32)), GUARANTEED),),
                   BAD)
    function = _function([BLANK, _ELEMENTS, _ITERATOR],
                         _block(0, _ret(), _live(2), _iterator_value(2, heap=True), call,
                                _drop(2), _dead(2)),
                         params=((1, GUARANTEED),))
    _refused(function, CONVENTIONS)


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


LIST_I32 = StructType(name="List<i32>", fields=(("len", I32), ("capacity", I32),
                                                 ("data", I32)))
INOUT, IN_GUARANTEED = Convention.INOUT, Convention.IN_GUARANTEED


def _list_of(element: Type) -> StructType:
    return StructType(name=f"List<{element}>", fields=LIST_I32.fields, generic_base="List",
                      generic_args=(element,))


def _read_element(list_type: StructType, place: p.Place, span: Span = SPAN) -> SlirFunction:
    """`_1 = copy <place>` over the List `_2`, a parameter that the function does not own."""
    return _function([BLANK, I32, list_type],
                     _block(0, _ret(), _set(1, r.Use(p.Copy(place)), span),
                            _set(0, r.Use(p.Const(k.Blank())))),
                     params=((2, GUARANTEED),))


def test_a_field_of_an_element_of_a_list_is_typed_from_its_type_argument():
    _passes(_read_element(_list_of(PAIR), p.Field(p.Index(_local(2), FOUR), 1)))


def test_a_field_of_an_element_of_a_list_of_i32_is_refused():
    _refused(_read_element(_list_of(I32), p.Field(p.Index(_local(2), FOUR), 0), BAD),
             "typed places")


def test_an_index_of_a_list_with_no_type_argument_is_refused():
    bare = StructType(name="List<?>", fields=LIST_I32.fields, generic_base="List")
    _refused(_read_element(bare, p.Index(_local(2), FOUR), BAD), "typed places")


def _list_method(name: str, *args: tuple[p.Operand, Convention]) -> SlirFunction:
    """`_1 = list.<name>(args)` over the List `_2`, which the function does not own."""
    call = st.Call(_local(1), st.Builtin(BuiltinMethod("list", name, nom_self=False), ()),
                   args, BAD)
    return _function([BLANK, BLANK, LIST_I32],
                     _block(0, _ret(), call, _set(0, r.Use(p.Const(k.Blank())))),
                     params=((2, INOUT),))


FOUR = p.Const(k.Int(4, I32))


@pytest.mark.parametrize("name, args", [
    ("push", ((_copy(2), GUARANTEED), (FOUR, OWNED))),
    ("push", ((_copy(2), INOUT), (FOUR, GUARANTEED))),
    ("insert", ((_copy(2), INOUT), (FOUR, OWNED), (FOUR, OWNED))),
    ("contains", ((_copy(2), GUARANTEED), (FOUR, OWNED))),
    ("len", ((_copy(2), INOUT),)),
])
def test_a_list_method_with_another_convention_is_refused(name: str, args):
    """The verifier reads the convention of the receiver of a List method from
    `receiver_convention` and of each argument from `argument_conventions`, as typecheck
    writes them (docs/design/ir.md 8.3, 8.13)."""
    _refused(_list_method(name, *args), "declared conventions")


@pytest.mark.parametrize("name, args", [
    ("push", ((_copy(2), INOUT), (FOUR, OWNED))),
    ("insert", ((_copy(2), INOUT), (FOUR, GUARANTEED), (FOUR, OWNED))),
    ("contains", ((_copy(2), GUARANTEED), (FOUR, GUARANTEED))),
    ("len", ((_copy(2), GUARANTEED),)),
])
def test_a_list_method_with_the_conventions_of_its_rows_passes(name: str, args):
    _passes(_list_method(name, *args))


MAP_I32 = StructType(name="HashMap<i32, i32>", fields=(("buckets", I32), ("size", I32),
                                                       ("capacity", I32), ("tombstones", I32)))


def _map_method(name: str, *args: tuple[p.Operand, Convention]) -> SlirFunction:
    """`_1 = hashmap.<name>(args)` over the map `_2`, which the function does not own."""
    call = st.Call(_local(1), st.Builtin(BuiltinMethod("hashmap", name, nom_self=False), ()),
                   args, BAD)
    return _function([BLANK, BLANK, MAP_I32],
                     _block(0, _ret(), call, _set(0, r.Use(p.Const(k.Blank())))),
                     params=((2, INOUT),))


@pytest.mark.parametrize("name, args", [
    ("insert", ((_copy(2), GUARANTEED), (FOUR, OWNED), (FOUR, OWNED))),
    ("insert", ((_copy(2), INOUT), (FOUR, GUARANTEED), (FOUR, OWNED))),
    ("insert", ((_copy(2), INOUT), (FOUR, OWNED), (FOUR, GUARANTEED))),
    ("get", ((_copy(2), GUARANTEED), (FOUR, OWNED))),
    ("remove", ((_copy(2), GUARANTEED), (FOUR, GUARANTEED))),
    ("free", ((_copy(2), GUARANTEED),)),
    ("len", ((_copy(2), INOUT),)),
    ("cursor_next.keys", ((_copy(2), GUARANTEED), (FOUR, INOUT))),
    ("cursor_next.values", ((_copy(2), IN_GUARANTEED), (FOUR, GUARANTEED))),
])
def test_a_map_method_with_another_convention_is_refused(name: str, args):
    """The rule of a List method holds for a HashMap method: the receiver from
    `receiver_convention`, each argument from `argument_conventions` (ir.md 8.3, 8.13)."""
    _refused(_map_method(name, *args), "declared conventions")


@pytest.mark.parametrize("name, args", [
    ("insert", ((_copy(2), INOUT), (FOUR, OWNED), (FOUR, OWNED))),
    ("get", ((_copy(2), GUARANTEED), (FOUR, GUARANTEED))),
    ("remove", ((_copy(2), INOUT), (FOUR, GUARANTEED))),
    ("contains_key", ((_copy(2), GUARANTEED), (FOUR, GUARANTEED))),
    ("free", ((_copy(2), INOUT),)),
    ("len", ((_copy(2), GUARANTEED),)),
    ("cursor_next.values", ((_copy(2), IN_GUARANTEED), (_copy(2), INOUT))),
])
def test_a_map_method_with_the_conventions_of_its_rows_passes(name: str, args):
    _passes(_map_method(name, *args))


def test_a_cursor_step_with_another_count_of_operands_is_refused():
    """A `cursor_next` row takes the map and the cursor (ir.md 8.6, 8.13)."""
    _refused(_map_method("cursor_next.keys", (_copy(2), IN_GUARANTEED)), "declared conventions")


OWN_STRING = StructType(name="Own<string>", fields=(("value", PointerType(STRING)),),
                        generic_base="Own")


def _own_method(name: str, *args: tuple[p.Operand, Convention]) -> SlirFunction:
    """`_1 = own.<name>(args)` over the `Own@(string)` `_2`, which the function does not
    own."""
    answer = STRING if name == "get" else BLANK
    call = st.Call(_local(1), st.Builtin(BuiltinMethod("own", name, nom_self=False), ()),
                   args, BAD)
    return _function([BLANK, answer, OWN_STRING],
                     _block(0, _ret(), call, _set(0, r.Use(p.Const(k.Blank())))),
                     params=((2, INOUT),))


@pytest.mark.parametrize("name, args", [
    ("get", ((_copy(2), INOUT),)),
    ("destroy", ((_copy(2), GUARANTEED),)),
    ("alloc", ((_copy(2), GUARANTEED), (p.Const(k.Str(b"x")), GUARANTEED))),
])
def test_an_own_method_with_another_convention_is_refused(name: str, args):
    """The rule of a List method holds for an `Own@(T)` method: `destroy` writes its
    receiver, `get` reads it, and the value of `alloc` is `Owned` (ir.md 8.3, 8.13)."""
    _refused(_own_method(name, *args), "declared conventions")


@pytest.mark.parametrize("name, args", [
    ("destroy", ((_copy(2), INOUT),)),
    ("alloc", ((_copy(2), GUARANTEED), (p.Const(k.Str(b"x")), OWNED))),
])
def test_an_own_method_with_the_conventions_of_its_rows_passes(name: str, args):
    _passes(_own_method(name, *args))


def _wrapper_method(family: str, name: str,
                    *args: tuple[p.Operand, Convention]) -> SlirFunction:
    """`_1 = <family>.<name>(args)` over the plain `Result@(i32, StdError)` `_2`: a
    `nom self` method takes it, and any other method reads it."""
    call = st.Call(_local(1), st.Builtin(BuiltinMethod(family, name,
                                                       nom_self=name in ("map_err", "or_err")),
                                         ()), args, BAD)
    return _function([BLANK, _result(I32), _result(I32)],
                     _block(0, _ret(), call, _set(0, r.Use(p.Const(k.Blank())))),
                     params=((2, OWNED),))


@pytest.mark.parametrize("family, name, args", [
    ("result", "map_err", ((_copy(2), GUARANTEED), (p.Const(k.Blank()), GUARANTEED))),
    ("maybe", "or_err", ((p.Move(_local(2)), OWNED), (p.Const(k.Blank()), GUARANTEED))),
    ("result", "is_ok", ((p.Move(_local(2)), OWNED),)),
])
def test_a_wrapper_method_with_another_convention_is_refused(family: str, name: str, args):
    """The rule of a List method holds for a method of `Result` and `Maybe`: `map_err` and
    `or_err` take their receiver `@owned`, `is_ok` reads it, and the error value of
    `or_err` is `@owned` (ir.md 8.3, 8.13)."""
    _refused(_wrapper_method(family, name, *args), "declared conventions")


@pytest.mark.parametrize("family, name, args", [
    ("result", "map_err", ((p.Move(_local(2)), OWNED), (p.Const(k.Blank()), GUARANTEED))),
    ("maybe", "or_err", ((p.Move(_local(2)), OWNED), (p.Const(k.Blank()), OWNED))),
    ("result", "is_ok", ((_copy(2), GUARANTEED),)),
])
def test_a_wrapper_method_with_the_conventions_of_its_rows_passes(family: str, name: str,
                                                                  args):
    _passes(_wrapper_method(family, name, *args))


def _static(name: str, *args: tuple[p.Operand, Convention]) -> st.Call:
    """`_1 = <name>(args)`: a static of a built-in container, an intrinsic of its name."""
    return st.Call(_local(1), st.Builtin(Intrinsic(name), tuple(I32 for _ in args)), args, BAD)


def _static_function(call: st.Call) -> SlirFunction:
    return _function([BLANK, BLANK], _block(0, _ret(), call, _set(0, r.Use(p.Const(k.Blank())))))


@pytest.mark.parametrize("name, convention", [
    ("Own.alloc", GUARANTEED), ("List.with_capacity", OWNED)])
def test_a_static_of_a_container_with_another_convention_is_refused(name: str,
                                                                     convention: Convention):
    """The verifier reads the convention of each argument of a container static from
    `argument_conventions`, as typecheck writes it (docs/design/ir.md 8.3, 8.13)."""
    call = _static(name, (p.Const(k.Int(4, I32)), convention))
    _refused(_static_function(call), "declared conventions")


@pytest.mark.parametrize("name, convention", [
    ("Own.alloc", OWNED), ("List.with_capacity", GUARANTEED)])
def test_a_static_of_a_container_with_the_convention_of_its_row_passes(name: str,
                                                                       convention: Convention):
    _passes(_static_function(_static(name, (p.Const(k.Int(4, I32)), convention))))


def test_a_static_of_a_primitive_type_name_has_no_container_rule():
    _passes(_static_function(_static("string.from_bytes", (p.Const(k.Int(4, I32)), OWNED))))


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
    ("CE0156", 0),      # a registered code that is no runtime code
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
    ("RE2020", (p.Copy(p.Global("unit$g", STRING)), p.Const(k.Int(3, I32)))),  # a string
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


# -- the projections of a global -------------------------------------------------------


@pytest.mark.parametrize("projection", [
    p.Field(p.Global("unit$pair", PAIR), 1),
    p.Index(p.Global("unit$table", ArrayType(base_type=I32, size=3)), p.Const(k.Int(0, I32))),
], ids=["field", "index"])
def test_a_projection_of_a_global_is_typed_from_the_global(projection: p.Place):
    _passes(_function([BLANK, I32], _block(0, _ret(), _set(1, r.Use(p.Copy(projection))),
                                           _set(0, r.Use(p.Const(k.Blank()))))))


@pytest.mark.parametrize("projection", [
    p.Field(p.Global("unit$count", I32), 0),
    p.Index(p.Global("unit$pair", PAIR), p.Const(k.Int(0, I32))),
    p.Field(p.Global("unit$pair", PAIR), 2),
], ids=["field of an i32", "index of a struct", "no such field"])
def test_a_projection_that_does_not_fit_the_type_of_the_global_is_refused(projection: p.Place):
    _refused(_function([BLANK, I32], _block(0, _ret(), _set(1, r.Use(p.Copy(projection)), BAD))),
             "typed places")




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
    funcs, extensions = FunctionTable(), ExtensionTable()
    callee = st.Direct(emitted_symbol("late", "unit"), "late")
    assert declared_params(funcs, extensions, callee) is None
    funcs.declare("late", FuncSig("late", params=[Param("x", STRING, None, None, 0, is_nom=True)],
                                  unit_name="unit"))
    assert declared_params(funcs, extensions, callee) == DeclaredParams((OWNED,))
    assert declared_params(funcs, extensions,
                           st.Direct(emitted_symbol("late", "other"), "late")) is None


def test_the_declared_parameters_of_a_perk_method_are_read_from_its_implementation():
    """A perk method is the method of the implementation whose perk symbol is the symbol of
    the callee, with its receiver first. Another symbol has no row."""
    funcs, extensions = FunctionTable(), ExtensionTable()
    impls = PerkImplementationTable()
    method = ast.FuncDef(None, "bump", [ast.Param("by", I32)], BuiltinType.BLANK,
                         ast.Block(None, []), self_mode="poke")
    impls.register(ast.ExtendWithDef(None, PAIR, "Bumps", [method]), str(PAIR))
    assert declared_params(funcs, extensions, st.Direct(method_symbol(PAIR, "bump", None),
                                                        "Pair.bump"), perk_impls=impls) \
        == DeclaredParams((Convention.INOUT, GUARANTEED))
    assert declared_params(funcs, extensions, st.Direct(method_symbol(PAIR, "other", None),
                                                        "Pair.other"), perk_impls=impls) is None


def test_the_declared_parameters_of_a_method_are_its_receiver_then_its_parameters():
    """A method or a static is the extension of the written name whose symbol is the symbol
    of the callee: each of two units' private methods of one name is its own row (C5)."""
    funcs, extensions = FunctionTable(), ExtensionTable()
    by = [Param("by", I32, None, None, 0)]
    extensions.add_method(ExtensionMethod(PAIR, "bump", params=by, self_mode="poke",
                                          unit_name="one"))
    extensions.add_method(ExtensionMethod(PAIR, "bump", params=[], unit_name="two"))
    extensions.add_method(ExtensionMethod(PAIR, "at", params=by, unit_name="one",
                                          is_static=True))
    assert declared_params(funcs, extensions, st.Direct(method_symbol(PAIR, "bump", "one"),
                                                        "Pair.bump")) \
        == DeclaredParams((Convention.INOUT, GUARANTEED))
    assert declared_params(funcs, extensions, st.Direct(method_symbol(PAIR, "bump", "two"),
                                                        "Pair.bump")) \
        == DeclaredParams((GUARANTEED,))
    assert declared_params(funcs, extensions, st.Direct(method_symbol(PAIR, "at", "one"),
                                                        "Pair.at")) == DeclaredParams((GUARANTEED,))
    assert declared_params(funcs, extensions, st.Direct(method_symbol(PAIR, "bump", "three"),
                                                        "Pair.bump")) is None


def test_the_declared_parameters_of_a_stdlib_function_are_the_row_of_its_module():
    """A function of a registry stdlib module borrows each argument. The count is the arity
    of its row, and of its family for `min`; a name that the module has no row for is no
    row, and so is a user function of the same name."""
    funcs, extensions = FunctionTable(), ExtensionTable()
    assert declared_params(funcs, extensions, st.Direct("sushi_sqrt", "sqrt", "math")) \
        == DeclaredParams((GUARANTEED,))
    assert declared_params(funcs, extensions, st.Direct("sushi_min_u8", "min", "math")) \
        == DeclaredParams((GUARANTEED, GUARANTEED))
    assert declared_params(funcs, extensions, st.Direct("sushi_now", "now", "time")) \
        == DeclaredParams(())
    assert declared_params(funcs, extensions, st.Direct("sushi_now", "now", "math")) is None
    assert declared_params(funcs, extensions, st.Direct("sushi_sqrt", "sqrt")) is None


def test_the_declared_parameters_of_a_conversion_are_one_owned_parameter():
    """A conversion `<source>.as` takes `nom self`. Its symbol is the symbol of `as` on its
    source with its target after it; a symbol of another source has no row."""
    funcs, extensions = FunctionTable(), ExtensionTable()
    low = EnumType(name="LowError", variants=(EnumVariantInfo("Bad", ()),), is_error=True)
    app = EnumType(name="AppError", variants=(EnumVariantInfo("Low", (low,)),), is_error=True)
    symbol = conversion_symbol(low, app)
    assert declared_params(funcs, extensions, st.Direct(symbol, "LowError.as")) \
        == DeclaredParams((OWNED,))
    assert declared_params(funcs, extensions, st.Direct(symbol, "AppError.as")) is None
    assert declared_params(funcs, extensions,
                           st.Direct(method_symbol(low, "as", None), "LowError.as")) is None


def test_a_conversion_call_with_a_borrowed_operand_is_refused():
    funcs, extensions = FunctionTable(), ExtensionTable()
    low = EnumType(name="LowError", variants=(EnumVariantInfo("Bad", ()),), is_error=True)
    app = EnumType(name="AppError", variants=(EnumVariantInfo("Low", (low,)),), is_error=True)
    callee = st.Direct(conversion_symbol(low, app), "LowError.as")
    call = st.Call(_local(1), callee, ((_copy(2), GUARANTEED),), BAD)
    function = _function([BLANK, app, low], _block(0, _ret(), call), params=((2, GUARANTEED),))
    with pytest.raises(InternalCompilerError) as caught:
        verify_function(function, drops=frozenset(),
                        declared=lambda each: declared_params(funcs, extensions, each))
    assert caught.value.params["rule"] == CONVENTIONS
    assert "its parameter is @owned" in caught.value.params["detail"]


def test_a_stdlib_call_with_an_owned_argument_is_refused():
    """The rule of the conventions holds for a stdlib callee: its row borrows."""
    callee = st.Direct("sushi_sqrt", "sqrt", "math")
    call = st.Call(_local(1), callee, ((p.Const(k.Float(2.0, F64)), OWNED),), BAD)
    function = _function([BLANK, F64], _block(0, _ret(), _live(1), call, _dead(1),
                                              _set(0, r.Use(p.Const(k.Blank())))))
    with pytest.raises(InternalCompilerError) as caught:
        verify_function(function, drops=frozenset(), declared=lambda c: declared_params(
            FunctionTable(), ExtensionTable(), c))
    assert caught.value.code == "CE0158"
    assert caught.value.params["rule"] == CONVENTIONS
    assert caught.value.span == BAD


def _snprintf() -> ExternalTable:
    """`libc.snprintf(poke u8[] buf, i64 n, string fmt, ...) i32 = "snprintf"`."""
    externals = ExternalTable()
    externals.add(ExternalSig(
        "snprintf", "snprintf", (ReferenceType(DynamicArrayType(U8), BorrowMode.POKE), I64,
                                 STRING), I32, "libc", is_variadic=True))
    return externals


def test_the_declared_parameters_of_a_c_function_are_its_modes_with_var_arg():
    """A `Direct` callee with no module is the C function of the external table whose
    link name is its symbol and whose name is its name (ir.md 8.13)."""
    funcs, extensions, externals = FunctionTable(), ExtensionTable(), _snprintf()
    assert declared_params(funcs, extensions, st.Direct("snprintf", "snprintf"),
                           externals=externals) \
        == DeclaredParams((Convention.INOUT, GUARANTEED, GUARANTEED), var_arg=True)
    assert declared_params(funcs, extensions, st.Direct("snprintf", "printf"),
                           externals=externals) is None
    assert declared_params(funcs, extensions, st.Direct("sprintf", "snprintf"),
                           externals=externals) is None
    assert declared_params(funcs, extensions, st.Direct("snprintf", "snprintf")) is None


def test_a_c_function_call_with_a_wrong_convention_in_its_declared_prefix_is_refused():
    """The tail of a `var_arg` extern declares no parameter, but its declared prefix
    keeps the rule: a `poke u8[]` buffer given `@guaranteed` is refused."""
    buffer = DynamicArrayType(U8)
    call = st.Call(_local(2), st.Direct("snprintf", "snprintf"),
                   ((_copy(1), GUARANTEED), (p.Const(k.Int(8, I64)), GUARANTEED),
                    (p.Const(k.Str(b"%d")), GUARANTEED), (p.Const(k.Int(5, I32)), GUARANTEED)),
                   BAD)
    function = _function([BLANK, buffer, I32],
                         _block(0, _ret(), _live(2), call, _dead(2),
                                _set(0, r.Use(p.Const(k.Blank())))),
                         params=((1, Convention.INOUT),))
    with pytest.raises(InternalCompilerError) as caught:
        verify_function(function, drops=frozenset(), declared=lambda c: declared_params(
            FunctionTable(), ExtensionTable(), c, externals=_snprintf()))
    assert caught.value.code == "CE0158"
    assert caught.value.params["rule"] == CONVENTIONS
    assert caught.value.span == BAD


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


@pytest.mark.parametrize("parts", [
    (p.Const(k.Bool(True)), p.Const(k.Int(4, I32))),      # a bool for an i32
    (p.Const(k.Str(b"x")),),                              # a string for an i32
], ids=["bool", "string"])
def test_a_variant_with_a_part_of_another_type_than_its_payload_is_refused(
        parts: tuple[p.Operand, ...]):
    variant = "Rect" if len(parts) == 2 else "Circle"
    _refused(_variant(parts, variant, SHAPE, BAD), "an enum value")


def test_a_constant_variant_with_a_part_of_another_type_than_its_payload_is_refused():
    _refused(_constant(k.Aggregate(SHAPE, (k.Bool(True),), "Circle"), BAD), "an enum value")


# -- a struct value and a field place ---------------------------------------------------

TUPLE = StructType(name="$Tuple<i32, bool>", fields=(("0", I32), ("1", BOOL)))
SEG = StructType(name="Seg", fields=(("a", PAIR), ("n", I32)))
NAMED = StructType(name="Named", fields=(("name", STRING), ("n", I32)))


def _struct(value: r.Rvalue, ty: Type, span: Span = SPAN) -> SlirFunction:
    """`_1 = value` over the parameter `_2: i32`, with `_1` of the type `ty`."""
    return _function([BLANK, ty, I32], _block(0, _ret(), _live(1), _set(1, value, span),
                                              _dead(1), _set(0, r.Use(p.Const(k.Blank())))),
                     params=((2, GUARANTEED),))


def test_a_struct_and_a_tuple_with_one_part_of_the_type_of_each_field_pass():
    _passes(_struct(r.Aggregate(r.AggStruct(PAIR), (_copy(2), p.Const(k.Int(4, I32)))), PAIR))
    _passes(_struct(r.Aggregate(r.AggTuple(TUPLE), (_copy(2), p.Const(k.Bool(True)))), TUPLE))
    _passes(_struct(r.Use(p.Const(k.Aggregate(SEG, (k.Aggregate(PAIR, (k.Int(1, I32),
                                                                       k.Int(2, I32))),
                                                    k.Int(3, I32))))), SEG))


@pytest.mark.parametrize("value", [
    r.Aggregate(r.AggStruct(PAIR), (_copy(2),)),                             # one part of two
    r.Aggregate(r.AggStruct(PAIR), (_copy(2), p.Const(k.Bool(True)))),       # a bool for an i32
    r.Aggregate(r.AggTuple(TUPLE), (p.Const(k.Bool(True)), _copy(2))),       # parts swapped
    r.Aggregate(r.AggStruct(I32), ()),                                       # no struct
    r.Use(p.Const(k.Aggregate(PAIR, (k.Int(1, I32),)))),                     # constant count
    r.Use(p.Const(k.Aggregate(PAIR, (k.Int(1, I32), k.Str(b"x"))))),         # constant type
    r.Use(p.Const(k.Aggregate(SEG, (k.Aggregate(PAIR, (k.Int(1, I32), k.Bool(False))),
                                    k.Int(3, I32))))),                       # a field of a field
    r.Use(p.Const(k.Aggregate(I32, ()))),                                    # constant no struct
], ids=["count", "type", "tuple", "no struct", "constant count", "constant type",
        "nested constant", "constant no struct"])
def test_a_struct_value_that_its_type_does_not_declare_is_refused(value: r.Rvalue):
    _refused(_struct(value, PAIR, BAD), "a struct value")


def test_a_write_and_a_read_of_the_fields_of_a_struct_local_pass():
    _passes(_function([BLANK, PAIR, I32], _block(
        0, _ret(), _live(1), _set(1, r.Aggregate(r.AggStruct(PAIR), (p.Const(k.Int(1, I32)),
                                                                     p.Const(k.Int(2, I32))))),
        st.Assign(p.Field(_local(1), 0), r.Use(p.Const(k.Int(5, I32))), SPAN), _live(2),
        _set(2, r.Use(p.Copy(p.Field(_local(1), 1)))), _dead(2), _dead(1),
        _set(0, r.Use(p.Const(k.Blank()))))))


def test_a_write_of_a_field_of_a_local_that_holds_no_value_is_refused():
    write = st.Assign(p.Field(_local(1), 0), r.Use(p.Const(k.Int(5, I32))), BAD)
    _refused(_function([BLANK, PAIR], _block(0, _ret(), _live(1), write, _dead(1),
                                             _set(0, r.Use(p.Const(k.Blank()))))),
             "no use of a local outside its storage")


def test_a_move_of_a_field_moves_its_whole_local():
    """No local is moved in part: a read of a sibling field after the move is refused."""
    _refused(_function(
        [BLANK, NAMED, STRING, I32],
        _block(0, _ret(), _set(2, r.Use(p.Move(p.Field(_local(1), 0)))),
               _set(3, r.Use(p.Copy(p.Field(_local(1), 1))), BAD), _drop(2)),
        params=((1, OWNED),),
    ), "no use after a move")


def test_a_drop_of_a_local_after_a_move_of_its_field_is_refused():
    _refused(_function(
        [BLANK, NAMED, STRING],
        _block(0, _ret(), _set(2, r.Use(p.Move(p.Field(_local(1), 0)))), _drop(2),
               _drop(1, BAD), _set(0, r.Use(p.Const(k.Blank())))),
        params=((1, OWNED),),
    ), "no use after a move")


# -- the element of an owning array (ir.md 8.10 rule 6, 8.13) ---------------------------

STRINGS = DynamicArrayType(STRING)
ZERO = p.Const(k.Int(0, I32))


def _strings(index: int) -> st.Assign:
    """`_index = []`: an empty `string[]`, which owns its buffer."""
    return _set(index, r.Aggregate(r.AggArray(STRINGS, ()), ()))


def test_the_write_of_an_owning_element_drops_the_old_element_and_passes():
    _passes(_function([BLANK, STRINGS, STRING], _block(
        0, _ret(), _live(1), _strings(1), _live(2), _text(2),
        st.Drop(p.Index(_local(1), ZERO), SPAN),
        st.Assign(p.Index(_local(1), ZERO), r.Use(_move(2)), SPAN), _dead(2),
        _drop(1), _dead(1), _set(0, r.Use(p.Const(k.Blank()))))))


def test_a_drop_of_a_plain_element_is_refused():
    plain = DynamicArrayType(I32)
    _refused(_function([BLANK, plain], _block(
        0, _ret(), _live(1), _set(1, r.Aggregate(r.AggArray(plain, ()), ())),
        st.Drop(p.Index(_local(1), ZERO), BAD))), "drop of an owning type")


def test_a_drop_of_an_element_after_a_move_of_its_array_is_refused():
    _refused(_function([BLANK, STRINGS, STRINGS], _block(
        0, _ret(), _live(1), _strings(1), _live(2), _set(2, r.Use(_move(1))),
        st.Drop(p.Index(_local(1), ZERO), BAD))), "no use after a move")


# -- the field of an owning struct (ir.md 8.10 rule 6, 8.13) -----------------------------

def _named(index: int) -> list[st.SlirStatement]:
    """`_index = Named(<new string>, 1)`, over the temporary `_index + 1`."""
    return [_live(index + 1), _text(index + 1), _live(index),
            _set(index, r.Aggregate(r.AggStruct(NAMED),
                                    (_move(index + 1), p.Const(k.Int(1, I32))))),
            _dead(index + 1)]


def test_the_write_of_an_owning_field_drops_the_old_field_and_passes():
    _passes(_function([BLANK, NAMED, STRING, STRING], _block(
        0, _ret(), *_named(1), _live(3), _text(3),
        st.Drop(p.Field(_local(1), 0), SPAN),
        st.Assign(p.Field(_local(1), 0), r.Use(_move(3)), SPAN), _dead(3),
        _drop(1), _dead(1), _set(0, r.Use(p.Const(k.Blank()))))))


def test_a_drop_of_a_plain_field_is_refused():
    _refused(_function([BLANK, NAMED, STRING], _block(
        0, _ret(), *_named(1), st.Drop(p.Field(_local(1), 1), BAD))), "drop of an owning type")


def test_a_drop_of_a_field_after_a_move_of_its_struct_is_refused():
    _refused(_function([BLANK, NAMED, STRING, NAMED], _block(
        0, _ret(), *_named(1), _live(3), _set(3, r.Use(_move(1))),
        st.Drop(p.Field(_local(1), 0), BAD))), "no use after a move")


def test_a_drop_of_a_field_of_a_view_is_refused():
    function = _function([BLANK, NAMED, STRING, NAMED], _block(
        0, _ret(), *_named(1), _live(3), _set(3, r.Use(_copy(1))),
        st.Drop(p.Field(_local(3), 0), BAD)))
    locals_ = tuple(replace(local, view=local.id == LocalId(3)) for local in function.locals)
    _refused(replace(function, locals=locals_), "a view local")


# -- the drop of a payload, a drop through a reference, a flag of a part (ir.md 8.13) -----

TEXT = EnumType(name="Text", variants=(
    EnumVariantInfo(name="Some", associated_types=(STRING,)),
    EnumVariantInfo(name="Count", associated_types=(I32,))))


def _texts(index: int) -> list[st.SlirStatement]:
    """`_index = Text.Some(<new string>)`, over the temporary `_index + 1`."""
    return [_live(index + 1), _text(index + 1), _live(index),
            _set(index, r.Aggregate(r.AggVariant(TEXT, "Some"), (_move(index + 1),))),
            _dead(index + 1)]


def test_a_drop_of_an_owning_payload_passes():
    _passes(_function([BLANK, TEXT, STRING], _block(
        0, _ret(), *_texts(1), st.Drop(p.Downcast(_local(1), "Some"), SPAN),
        _drop(1), _dead(1), _set(0, r.Use(p.Const(k.Blank()))))))


def test_a_drop_of_a_payload_that_owns_nothing_is_refused():
    _refused(_function([BLANK, TEXT, STRING], _block(
        0, _ret(), *_texts(1), st.Drop(p.Downcast(_local(1), "Count"), BAD))),
        "drop of an owning type")


@pytest.mark.parametrize("place", [
    p.Deref(_local(1)),
    p.Field(p.Deref(_local(1)), 0),
], ids=["the referenced value", "a field of it"])
def test_a_drop_through_a_reference_is_refused(place: p.Place):
    _refused(_function([BLANK, ReferenceType(NAMED, BorrowMode.POKE)], _block(
        0, _ret(), st.Drop(place, BAD)), params=((1, GUARANTEED),)),
        "drop of an owning type")


def test_a_drop_if_set_of_a_field_is_refused():
    drop = st.DropIfSet(p.Field(_local(1), 0), LocalId(3), BAD)
    _refused(_function([BLANK, NAMED, STRING, BOOL], _block(
        0, _ret(), *_named(1), _live(3), _flag(3, True), drop)), "bool drop flag")


# -- the address local (ir.md 8.2, 8.13) ------------------------------------------------

def _poke_writes_field(*extra: st.SlirStatement) -> SlirFunction:
    """`_3 = ref poke _1` over the owned `Named` `_1`, in the region `b0` over `_1`, then a
    write of the owning field of `_3` through the address (rule 6), then `extra`, then the
    end of the region and the drop of `_1`."""
    return _function([BLANK, NAMED, STRING, NAMED, STRING], _block(
        0, _ret(), *_named(1), _live(3),
        st.BeginBorrow(BorrowId(0), _local(1), BorrowMode.POKE, None, SPAN),
        _set(3, r.Ref(_local(1), BorrowMode.POKE)),
        _live(4), _text(4), st.Drop(p.Field(_local(3), 0), SPAN),
        st.Assign(p.Field(_local(3), 0), r.Use(_move(4)), SPAN), _dead(4), *extra,
        st.EndBorrow(BorrowId(0), SPAN), _dead(3), _drop(1), _dead(1),
        _set(0, r.Use(p.Const(k.Blank())))))


def test_an_address_local_that_writes_an_owning_part_through_its_address_passes():
    """An address local owns nothing: no drop at the end of its storage."""
    _passes(_poke_writes_field())


def test_an_address_local_written_by_two_refs_is_refused():
    _refused(_poke_writes_field(_set(3, r.Ref(_local(1), BorrowMode.POKE), BAD)),
             "an address local")


def test_a_ref_into_a_part_of_a_local_is_refused():
    _refused(_poke_writes_field(st.Assign(p.Field(_local(3), 0),
                                          r.Ref(_local(1), BorrowMode.PEEK), BAD)),
             "an address local")


def test_a_drop_of_an_address_local_is_refused():
    _refused(_poke_writes_field(st.Drop(_local(3), BAD)), "an address local")


def test_a_move_of_an_address_local_is_refused():
    _refused(_function([BLANK, NAMED, STRING, NAMED, NAMED], _block(
        0, _ret(), *_named(1), _live(3), _set(3, r.Ref(_local(1), BorrowMode.POKE)),
        _live(4), _set(4, r.Use(_move(3)), BAD), _drop(4), _dead(4), _dead(3),
        _drop(1), _dead(1), _set(0, r.Use(p.Const(k.Blank()))))), "an address local")


def _poke_rebinds(*middle: st.SlirStatement) -> SlirFunction:
    """`_3 = ref poke (_1.0)` over the owning field of the owned `Named` `_1`, in the region
    `b0` over `_1`, then a new string in `_4`, then `middle`, then the end of the region and
    the drop of `_1`."""
    return _function([BLANK, NAMED, STRING, STRING, STRING], _block(
        0, _ret(), *_named(1), _live(3),
        st.BeginBorrow(BorrowId(0), _local(1), BorrowMode.POKE, None, SPAN),
        _set(3, r.Ref(p.Field(_local(1), 0), BorrowMode.POKE)),
        _live(4), _text(4), *middle, _dead(4),
        st.EndBorrow(BorrowId(0), SPAN), _dead(3), _drop(1), _dead(1),
        _set(0, r.Use(p.Const(k.Blank())))))


def test_a_rebind_through_an_address_local_drops_the_old_value_and_passes():
    """Rule 6 through the address: the new value first, then the `Drop` of the local, then
    the store into it (ir.md 8.10, rule 8)."""
    _passes(_poke_rebinds(_drop(3), _set(3, r.Use(_move(4)))))


def test_a_drop_of_an_address_local_that_no_write_of_it_follows_is_refused():
    _refused(_poke_rebinds(_drop(3, BAD), _live(2), _text(2), _set(3, r.Use(_move(4))),
                           _drop(2), _dead(2)), "an address local")


# -- a Guaranteed parameter that owns (ir.md 8.3, 8.10 rule 5) -----------------------------

def test_a_rebind_of_a_borrowed_parameter_drops_nothing_and_its_new_value_drops_at_the_return():
    _passes(_function([BLANK, STRING, STRING], _block(
        0, _ret(), _live(2), _text(2), _set(1, r.Use(_move(2))), _dead(2), _drop(1),
        _set(0, r.Use(p.Const(k.Blank())))), params=((1, GUARANTEED),)))


def test_a_borrowed_parameter_rebound_on_one_path_drops_with_its_flag_at_the_join():
    _passes(_function(
        [BLANK, STRING, BOOL, BOOL, STRING],
        _block(0, _switch(2, 2, 1), _live(3), _flag(3, False)),
        _block(1, _goto(2), _live(4), _text(4), _set(1, r.Use(_move(4))), _dead(4),
               _flag(3, True)),
        _block(2, _ret(), _set(0, r.Use(p.Const(k.Blank()))),
               st.DropIfSet(_local(1), LocalId(3), SPAN), _dead(3)),
        params=((1, GUARANTEED), (2, GUARANTEED)),
    ))


def test_a_rebound_borrowed_parameter_that_the_return_keeps_is_refused():
    _refused(_function([BLANK, STRING, STRING], _block(
        0, _ret(BAD), _live(2), _text(2), _set(1, r.Use(_move(2))), _dead(2),
        _set(0, r.Use(p.Const(k.Blank())))), params=((1, GUARANTEED),)),
        "one drop or move on every path")


def test_a_second_rebind_of_a_borrowed_parameter_that_drops_nothing_is_refused():
    _refused(_function([BLANK, STRING, STRING], _block(
        0, _ret(), _live(2), _text(2), _set(1, r.Use(_move(2))), _dead(2), _live(2),
        _text(2), _set(1, r.Use(_move(2)), BAD), _dead(2), _drop(1),
        _set(0, r.Use(p.Const(k.Blank())))), params=((1, GUARANTEED),)),
        "one drop or move on every path")


# -- the concat peephole (ir.md 8.3, 8.9, 8.13) ----------------------------------------

def _concat_in_place(receiver: Convention) -> SlirFunction:
    """`_2 = string.concat_in_place(copy _1 @receiver, const "!" @guaranteed)` over the
    owned string `_1`."""
    method = BuiltinMethod("string", "concat_in_place", nom_self=False)
    call = st.Call(_local(2), st.Builtin(method, ()),
                   ((_copy(1), receiver), (p.Const(k.Str(b"!")), GUARANTEED)), BAD)
    return _function([BLANK, STRING, BLANK], _block(
        0, _ret(), _live(1), _text(1), _live(2), call, _dead(2), _drop(1), _dead(1),
        _set(0, r.Use(p.Const(k.Blank())))))


def test_the_concat_peephole_with_an_inout_receiver_passes():
    _passes(_concat_in_place(Convention.INOUT))


def test_the_concat_peephole_with_a_guaranteed_receiver_is_refused():
    _refused(_concat_in_place(GUARANTEED), CONVENTIONS)


# -- a peek return (ir.md 8.2, 8.13) ---------------------------------------------------

def _view_return(function: SlirFunction) -> SlirFunction:
    """`function` with `_0` a view local: the declaration returns `peek`."""
    locals_ = tuple(replace(local, view=local.id == LocalId(0)) for local in function.locals)
    return replace(function, locals=locals_)


def _returns_param(*extra: st.SlirStatement, span: Span = SPAN) -> SlirFunction:
    """`_0 = copy _1` of the borrowed `string[]` parameter `_1`, then `extra`."""
    return _function([STRINGS, STRINGS], _block(0, _ret(), _set(0, r.Use(_copy(1)), span),
                                                *extra), params=((1, GUARANTEED),))


def test_a_copy_of_an_owning_parameter_into_a_view_return_slot_passes():
    _passes(_view_return(_returns_param()))


def test_a_copy_of_an_owning_parameter_into_an_owning_return_slot_is_refused():
    _refused(_returns_param(span=BAD), "a peek return")


def test_a_copy_of_an_owning_payload_into_an_owning_return_slot_is_refused():
    result = EnumType(name="Result<string[], i32>", variants=(
        EnumVariantInfo(name="Ok", associated_types=(STRINGS,)),
        EnumVariantInfo(name="Err", associated_types=(I32,))))
    _refused(_function([result, STRINGS], _block(0, _ret(), _set(
        0, r.Aggregate(r.AggVariant(result, "Ok"), (_copy(1),)), BAD)),
        params=((1, GUARANTEED),)), "a peek return")


def test_a_copy_of_a_string_into_an_owning_return_slot_passes():
    _passes(_function([STRING, STRING], _block(0, _ret(), _set(0, r.Use(_copy(1)))),
                      params=((1, GUARANTEED),)))


def test_a_view_return_slot_written_on_each_of_two_paths_passes():
    _passes(_view_return(_function(
        [STRINGS, STRINGS, STRINGS, BOOL],
        _block(0, _switch(3, 1, 2), _live(3), _flag(3, True)),
        _block(1, _ret(), _set(0, r.Use(_copy(1)))),
        _block(2, _ret(), _set(0, r.Use(_copy(2)))),
        params=((1, GUARANTEED), (2, GUARANTEED)))))


def test_a_drop_of_a_view_return_slot_is_refused():
    _refused(_view_return(_returns_param(_drop(0, BAD))), "a view local")


def test_a_move_into_a_view_return_slot_is_refused():
    _refused(_view_return(_function([STRING, STRING], _block(
        0, _ret(), _live(1), _text(1), _set(0, r.Use(_move(1)), BAD), _dead(1)))),
        "a view local")


# -- one take of two or more parts of one root (ir.md 8.13) -------------------------------

TWO = StructType(name="$Tuple<string, string>", fields=(("0", STRING), ("1", STRING)))


def _two(index: int) -> list[st.SlirStatement]:
    """`_index = (<new string>, <new string>)`, over the temporaries `_index + 1` and
    `_index + 2`."""
    return [_live(index + 1), _text(index + 1), _live(index + 2), _text(index + 2),
            _live(index), _set(index, r.Aggregate(r.AggTuple(TWO),
                                                   (_move(index + 1), _move(index + 2)))),
            _dead(index + 2), _dead(index + 1)]


def _part(root: int, index: int) -> p.Move:
    return p.Move(p.Field(_local(root), index))


def _take_both(*moves: st.SlirStatement, after: tuple[st.SlirStatement, ...] = ()
               ) -> SlirFunction:
    """`_1` is a pair of new strings; `_4` and `_5` take its parts."""
    return _function([BLANK, TWO, STRING, STRING, STRING, STRING], _block(
        0, _ret(), *_two(1), _live(4), _live(5), *moves, *after,
        _drop(5), _dead(5), _drop(4), _dead(4), _dead(1),
        _set(0, r.Use(p.Const(k.Blank())))))


def test_two_moves_of_the_disjoint_parts_of_one_root_in_one_block_pass():
    _passes(_take_both(_set(4, r.Use(_part(1, 0))), _set(5, r.Use(_part(1, 1)))))


def test_a_second_move_of_the_same_part_is_refused():
    _refused(_take_both(_set(4, r.Use(_part(1, 0))), _set(5, r.Use(_part(1, 0)), BAD)),
             "no use after a move")


def test_a_move_of_a_part_of_a_moved_part_is_refused():
    nested = StructType(name="$Tuple<$Tuple<string, string>, string>",
                        fields=(("0", TWO), ("1", STRING)))
    _refused(_function([BLANK, nested, TWO, STRING, STRING, STRING, TWO, STRING], _block(
        0, _ret(), *_two(2), _live(5), _text(5), _live(1),
        _set(1, r.Aggregate(r.AggTuple(nested), (_move(2), _move(5)))), _dead(5), _dead(2),
        _live(6), _set(6, r.Use(_part(1, 0))),
        _live(7), _set(7, r.Use(p.Move(p.Field(p.Field(_local(1), 0), 1))), BAD))),
        "no use after a move")


def test_a_read_of_the_root_between_two_part_moves_is_refused():
    function = _function([BLANK, TWO, STRING, STRING, STRING, STRING, STRING], _block(
        0, _ret(), *_two(1), _live(4), _live(5), _set(4, r.Use(_part(1, 0))),
        _live(6), _set(6, r.Use(p.Copy(p.Field(_local(1), 1))), BAD),
        _set(5, r.Use(_part(1, 1)))))
    _refused(function, "no use after a move")


def test_a_part_move_in_a_later_block_is_refused():
    function = _function([BLANK, TWO, STRING, STRING, STRING, STRING],
                         _block(0, _goto(1), *_two(1), _live(4), _live(5),
                                _set(4, r.Use(_part(1, 0)))),
                         _block(1, _ret(), _set(5, r.Use(_part(1, 1)), BAD),
                                _drop(5), _dead(5), _drop(4), _dead(4), _dead(1),
                                _set(0, r.Use(p.Const(k.Blank())))))
    _refused(function, "no use after a move")


def _take_one(*drops: st.SlirStatement) -> SlirFunction:
    """`_1` is a pair of new strings; `_4` takes its first part, after `drops`."""
    return _function([BLANK, TWO, STRING, STRING, STRING], _block(
        0, _ret(), *_two(1), _live(4), *drops, _set(4, r.Use(_part(1, 0)), BAD),
        _drop(4), _dead(4), _dead(1), _set(0, r.Use(p.Const(k.Blank())))))


def test_a_take_of_one_part_that_drops_its_owning_sibling_passes():
    _passes(_take_one(st.Drop(p.Field(_local(1), 1), SPAN)))


def test_a_take_of_one_part_with_no_drop_of_its_owning_sibling_is_refused():
    """A take spends the whole root, so each other owning part drops at it (ir.md 8.13)."""
    _refused(_take_one(), "one drop or move on every path")


_NESTED = StructType(name="$Tuple<$Tuple<string, string>, string>",
                     fields=(("0", TWO), ("1", STRING)))


def _take_deep(*drops: st.SlirStatement) -> SlirFunction:
    """`_1` is `((<new>, <new>), <new>)`; `_6` takes `_1.0.0`, after `drops`."""
    return _function([BLANK, _NESTED, TWO, STRING, STRING, STRING, STRING], _block(
        0, _ret(), *_two(2), _live(5), _text(5), _live(1),
        _set(1, r.Aggregate(r.AggTuple(_NESTED), (_move(2), _move(5)))), _dead(5), _dead(2),
        _live(6), *drops, _set(6, r.Use(p.Move(p.Field(p.Field(_local(1), 0), 0))), BAD),
        _drop(6), _dead(6), _dead(1), _set(0, r.Use(p.Const(k.Blank())))))


def test_a_deep_take_that_drops_each_owning_sibling_on_its_path_passes():
    _passes(_take_deep(st.Drop(p.Field(p.Field(_local(1), 0), 1), SPAN),
                       st.Drop(p.Field(_local(1), 1), SPAN)))


def test_a_deep_take_with_no_drop_of_a_sibling_of_its_root_is_refused():
    _refused(_take_deep(st.Drop(p.Field(p.Field(_local(1), 0), 1), SPAN)),
             "one drop or move on every path")


_TWO_ITEM = EnumType(name="Maybe<$Tuple<string, string>>", variants=(
    EnumVariantInfo("Some", (TWO,)), EnumVariantInfo("None", ())))


def _step_take(peeks: tuple[bool, ...]) -> SlirFunction:
    """`_2` is the answer of a step of the iterator `_1`; `_3` takes the first element of its
    item, and nothing drops the second."""
    item = p.Field(p.Downcast(_local(2), "Some"), 0)
    step = st.Call(_local(2), st.Indirect(p.Copy(_local(1))), (), SPAN)
    return _function([BLANK, IteratorType(TWO, peeks=peeks), _TWO_ITEM, STRING], _block(
        0, _ret(), _live(2), step, _live(3), _set(3, r.Use(p.Move(p.Field(item, 0))), BAD),
        _drop(3), _dead(3), _dead(2), _drop(1), _set(0, r.Use(p.Const(k.Blank())))),
        params=((1, OWNED),))


def test_a_take_of_an_item_whose_other_element_is_lent_passes():
    _passes(_step_take((False, True)))


def test_a_take_of_an_item_whose_other_element_is_owned_is_refused():
    _refused(_step_take(()), "one drop or move on every path")


def test_a_repeat_run_that_moves_its_value_is_refused():
    repeat = r.Aggregate(r.AggArray(STRINGS, (r.RunRepeat(),)),
                         (_move(2), p.Const(k.Int(2, I32))))
    _refused(_function([BLANK, STRINGS, STRING], _block(
        0, _ret(), _live(2), _text(2), _live(1), _set(1, repeat, BAD))), "an array value")


_ENV = StructType(name="__closure_env<string, i32>", fields=(("_0", STRING), ("_1", I32)))
_CAPTURED_TEXT = p.Capture(_local(1), 0)


def _lifted(*statements: st.SlirStatement) -> SlirFunction:
    function = _function([BLANK, _ENV, STRING, I32], _block(0, _ret(), *statements),
                         params=((1, Convention.IN_GUARANTEED),))
    return replace(function, environment=LocalId(1))


def test_a_copy_and_an_inout_argument_of_a_capture_pass():
    grown = st.Call(_local(0), st.Builtin(Intrinsic("grow"), ()),
                    ((p.Copy(_CAPTURED_TEXT), Convention.INOUT),), SPAN)
    _passes(_lifted(_set(3, r.Use(p.Copy(p.Capture(_local(1), 1)))), grown))


def test_a_move_out_of_a_capture_place_is_refused():
    moved = st.Assign(_local(2), r.Use(p.Move(_CAPTURED_TEXT)), BAD)
    _refused(_lifted(st.StorageLive(LocalId(2), SPAN), moved), "capture places")


def test_a_write_of_the_environment_is_refused():
    written = st.Assign(_local(1), r.Use(_copy(1)), BAD)
    _refused(_lifted(written), "capture places")


# -- the `ptr` quarantine (S25) ---------------------------------------------------------

MAYBE_PTR = EnumType(name="Maybe<ptr>", variants=(
    EnumVariantInfo(name="Some", associated_types=(ForeignPtrType(),)),
    EnumVariantInfo(name="None", associated_types=())))


def _with_parameter(param: Type, ret: Type = BLANK) -> SlirFunction:
    return _function([ret, param], _block(0, _ret(), _set(0, r.Use(p.Const(k.Blank())))),
                     params=((1, GUARANTEED),))


def _quarantined(function: SlirFunction) -> InternalCompilerError:
    with pytest.raises(InternalCompilerError) as caught:
        verify_function(function, drops=frozenset(), declared=lambda _callee: None,
                        public=True)
    assert caught.value.code == "CE0158"
    assert caught.value.params["rule"] == PTR_QUARANTINE
    return caught.value


def test_a_public_function_with_a_ptr_parameter_is_refused():
    assert "_1" in _quarantined(_with_parameter(ForeignPtrType())).params["detail"]


def test_a_public_function_that_answers_a_maybe_ptr_is_refused():
    assert "return" in _quarantined(_with_parameter(I32, MAYBE_PTR)).params["detail"]


def test_a_function_that_is_not_public_may_take_a_ptr():
    _passes(_with_parameter(ForeignPtrType()))


def test_the_public_marker_is_read_from_the_declaration_of_the_symbol():
    funcs, extensions = FunctionTable(), ExtensionTable()
    function = _with_parameter(ForeignPtrType())
    assert not is_public_function(funcs, extensions, function)
    funcs.declare("probe", FuncSig("probe", unit_name="unit", is_public=True))
    assert is_public_function(funcs, extensions, function)

