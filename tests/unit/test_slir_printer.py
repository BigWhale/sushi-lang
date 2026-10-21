"""The SLIR printer: the text of `--dump-slir` (docs/design/ir.md 8.11, 11.3; PLAN F.3 L1-L17).

The golden `tests/ir/spine/greet/test_spine_greet.slir` is the contract of the text. These
tests build the four functions of that golden by hand and print them: the text must be the
golden, byte for byte. They also print each shape that no lowered body makes yet (a
`Downcast` place, a `DropIfSet`, a `SwitchInt` with no other value, an `Indirect` callee,
and the rest of ir.md 8.4 to 8.8), and hold the printer to one table and to a text that
reads no hash order. They call a Python function over data; they do not run the compiler.
"""
from __future__ import annotations

import ast
import inspect
import typing
from pathlib import Path

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import BuiltinMethod, Intrinsic
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import printer
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock, SlirFunction, SlirLocal, SlirStopped
from sushi_lang.semantics.slir.ids import BlockId, BorrowId, LocalId
from sushi_lang.semantics.typesys import (
    ArrayType, BorrowMode, BuiltinType, EnumType, EnumVariantInfo, StructType,
)

ROOT = Path(__file__).resolve().parents[2]
GREET_GOLDEN = ROOT / "tests" / "ir" / "spine" / "greet" / "test_spine_greet.slir"

SPAN = Span(1, 1, 1, 9)
OWNED, GUARANTEED = Convention.OWNED, Convention.GUARANTEED
I32, STRING, BOOL, BLANK = BuiltinType.I32, BuiltinType.STRING, BuiltinType.BOOL, BuiltinType.BLANK

STD_ERROR = EnumType(name="StdError", variants=(EnumVariantInfo(name="Error", associated_types=()),),
                     is_error=True)


def _result(ok: object) -> EnumType:
    return EnumType(name=f"Result<{ok}, StdError>",
                    variants=(EnumVariantInfo(name="Ok", associated_types=(ok,)),
                              EnumVariantInfo(name="Err", associated_types=(STD_ERROR,))),
                    generic_base="Result", generic_args=(ok, STD_ERROR))


RESULT_STRING = _result(STRING)
RESULT_BLANK = _result(BLANK)


def _l(index: int) -> LocalId:
    return LocalId(index)


def _b(index: int) -> BlockId:
    return BlockId(index)


def _local(index: int) -> p.Local:
    return p.Local(LocalId(index))


def _copy(index: int) -> p.Copy:
    return p.Copy(_local(index))


def _move(index: int) -> p.Move:
    return p.Move(_local(index))


def _const(value: k.Constant) -> p.Const:
    return p.Const(value)


def _locals(*rows: tuple[int, object, str | None]) -> tuple[SlirLocal, ...]:
    return tuple(SlirLocal(LocalId(index), ty, name, None if name is None else SPAN)
                 for index, ty, name in rows)


def _live(index: int) -> st.StorageLive:
    return st.StorageLive(_l(index), SPAN)


def _dead(index: int) -> st.StorageDead:
    return st.StorageDead(_l(index), SPAN)


def _println(dest: int, arg: p.Operand) -> st.Call:
    return st.Call(_local(dest), st.Builtin(Intrinsic("println"), (STRING,)),
                   ((arg, GUARANTEED),), SPAN)


def _ok(ty: EnumType, payload: p.Operand) -> r.Aggregate:
    return r.Aggregate(r.AggVariant(ty, "Ok"), (payload,))


def _decorate() -> SlirFunction:
    interp = st.Call(_local(2), st.Builtin(Intrinsic("interp"), (STRING, STRING)),
                     ((_copy(1), GUARANTEED), (_const(k.Str(b"!")), GUARANTEED)), SPAN)
    block = SlirBlock(_b(0), (
        _live(2), interp,
        st.Assign(_local(0), _ok(RESULT_STRING, _move(2)), SPAN),
        _dead(2),
        st.Drop(_local(1), SPAN),
    ), tm.Return(SPAN))
    return SlirFunction("test_spine_greet$decorate", "decorate", "test_spine_greet",
                        ((_l(1), OWNED),),
                        _locals((0, RESULT_STRING, None), (1, STRING, "name"), (2, STRING, None)),
                        (block,), _b(0))


def _shout() -> SlirFunction:
    block = SlirBlock(_b(0), (
        _live(2), _println(2, _copy(1)), _dead(2),
        st.Assign(_local(0), _ok(RESULT_BLANK, _const(k.Blank())), SPAN),
        st.Drop(_local(1), SPAN),
    ), tm.Return(SPAN))
    return SlirFunction("test_spine_greet$shout", "shout", "test_spine_greet",
                        ((_l(1), OWNED),),
                        _locals((0, RESULT_BLANK, None), (1, STRING, "text"), (2, BLANK, None)),
                        (block,), _b(0))


def _payload(variant: str) -> p.Move:
    return p.Move(p.Field(p.Downcast(_local(3), variant), 0))


def _greet() -> SlirFunction:
    decorate = st.Call(_local(3), st.Direct("test_spine_greet$decorate", "decorate"),
                       ((_move(1), OWNED),), SPAN)
    shout = st.Call(_local(7), st.Direct("test_spine_greet$shout", "shout"),
                    ((_move(5), OWNED),), SPAN)
    blocks = (
        SlirBlock(_b(0), (
            _live(3), decorate, st.Assign(_local(4), r.Discriminant(_local(3)), SPAN),
        ), tm.SwitchInt(_copy(4), ((0, _b(1)), (1, _b(2))), None, SPAN)),
        SlirBlock(_b(1), (
            _live(5), st.Assign(_local(5), r.Use(_payload("Ok")), SPAN), _dead(3),
            _live(6), st.Assign(_local(6), r.Use(_const(k.Bool(True))), SPAN),
        ), tm.SwitchInt(_copy(2), ((0, _b(4)),), _b(3), SPAN)),
        SlirBlock(_b(2), (
            st.Assign(_local(0), r.Aggregate(r.AggVariant(RESULT_BLANK, "Err"),
                                             (_payload("Err"),)), SPAN),
            _dead(3),
        ), tm.Return(SPAN)),
        SlirBlock(_b(3), (
            _live(7), shout, st.Assign(_local(6), r.Use(_const(k.Bool(False))), SPAN), _dead(7),
        ), tm.Goto(_b(4), SPAN)),
        SlirBlock(_b(4), (
            st.Assign(_local(0), _ok(RESULT_BLANK, _const(k.Blank())), SPAN),
            st.DropIfSet(_local(5), _l(6), SPAN), _dead(6), _dead(5),
        ), tm.Return(SPAN)),
    )
    return SlirFunction("test_spine_greet$greet", "greet", "test_spine_greet",
                        ((_l(1), OWNED), (_l(2), GUARANTEED)),
                        _locals((0, RESULT_BLANK, None), (1, STRING, "name"), (2, BOOL, "loud"),
                                (3, RESULT_STRING, None), (4, I32, None), (5, STRING, "msg"),
                                (6, BOOL, None), (7, RESULT_BLANK, None)),
                        blocks, _b(0))


def _main() -> SlirFunction:
    def greet(dest: int, text: bytes, loud: bool) -> st.Call:
        return st.Call(_local(dest), st.Direct("test_spine_greet$greet", "greet"),
                       ((_const(k.Str(text)), OWNED), (_const(k.Bool(loud)), GUARANTEED)), SPAN)

    block = SlirBlock(_b(0), (
        _live(1), greet(1, b"So long", False), _dead(1),
        _live(2), _println(2, _const(k.Str(b"quiet"))), _dead(2),
        _live(3), greet(3, b"Mostly Harmless", True), _dead(3),
        _live(4), _println(4, _const(k.Str(b"loud"))), _dead(4),
        st.Assign(_local(0), r.Use(_const(k.Int(0, I32))), SPAN),
    ), tm.Return(SPAN))
    return SlirFunction("user_main", "main", "test_spine_greet", (),
                        _locals((0, I32, None), (1, RESULT_BLANK, None), (2, BLANK, None),
                                (3, RESULT_BLANK, None), (4, BLANK, None)),
                        (block,), _b(0))


def test_the_greet_functions_print_as_the_golden() -> None:
    text = printer.print_units([("test_spine_greet", [_decorate(), _shout(), _greet(), _main()])])
    assert text == GREET_GOLDEN.read_text(encoding="utf-8")


def test_a_function_whose_lowering_stopped_prints_one_line() -> None:
    text = printer.print_units([("m", [_main(), SlirStopped("helper", "ExprStmt")])])
    assert text.endswith("    return\n}\n\nfn helper: stopped (slir, ExprStmt)\n")
    assert text.startswith("unit m\n\nfn main() -> i32 {\n")


def test_blocks_and_locals_print_in_id_order() -> None:
    blocks = (SlirBlock(_b(1), (), tm.Return(SPAN)), SlirBlock(_b(0), (), tm.Goto(_b(1), SPAN)))
    made = SlirFunction("f", "f", "m", (), _locals((1, BOOL, None), (0, I32, None)), blocks, _b(0))
    assert printer.print_function(made) == (
        "fn f() -> i32 {\n    let _0: i32\n    let _1: bool\n\n"
        "  bb0:\n    goto -> bb1\n\n  bb1:\n    return\n}\n")


def test_the_name_of_a_local_with_a_long_type_takes_two_spaces() -> None:
    long = _result(RESULT_STRING)
    made = SlirFunction("f", "f", "m", (), _locals((0, I32, None), (1, long, "r")),
                        (SlirBlock(_b(0), (), tm.Return(SPAN)),), _b(0))
    assert "    let _1: Result@(Result@(string, StdError), StdError)  // r\n" in (
        printer.print_function(made))


PAIR = StructType(name="Pair", fields=(("first", I32), ("second", I32)))
TRIPLE = ArrayType(base_type=I32, size=3)

SHAPES: list[tuple[object, str]] = [
    # places and operands (ir.md 8.4, L8)
    (p.Copy(p.Global("test$counter")), "copy @test$counter"),
    (p.Move(p.Field(_local(3), 1)), "move (_3.1)"),
    (p.Copy(p.Index(_local(3), _copy(4))), "copy (_3[copy _4])"),
    (p.Copy(p.Deref(_local(2))), "copy (*_2)"),
    (p.Copy(p.Downcast(_local(3), "Err")), "copy (_3 as Err)"),
    (_payload("Some"), "move ((_3 as Some).0)"),
    # constants (ir.md 8.5, L9)
    (_const(k.Int(-7, BuiltinType.I64)), "const -7_i64"),
    (_const(k.Float(1.5, BuiltinType.F64)), "const 1.5_f64"),
    (_const(k.Bool(False)), "const false"),
    (_const(k.Blank()), "const ~"),
    (_const(k.Str('Mostly "Harmless"\n'.encode())), 'const "Mostly \\"Harmless\\"\\n"'),
    (_const(k.Aggregate(TRIPLE, (k.Int(2, I32), k.Int(3, I32), k.Int(5, I32)))),
     "const i32[3](2_i32, 3_i32, 5_i32)"),
    (_const(k.FnRef("test$helper")), "const fn test$helper"),
    # rvalues (ir.md 8.7, L10)
    (r.Use(_copy(1)), "copy _1"),
    (r.Binary("+", _copy(1), _const(k.Int(1, I32))), "copy _1 + const 1_i32"),
    (r.Unary("neg", _copy(1)), "neg copy _1"),
    (r.Cast(_copy(1), BuiltinType.I64), "copy _1 as i64"),
    (r.Aggregate(r.AggVariant(STD_ERROR, "Error"), ()), "StdError::Error"),
    (r.Aggregate(r.AggVariant(RESULT_STRING, "Ok"), (_move(2),)), "Result::Ok(move _2)"),
    (r.Aggregate(r.AggStruct(PAIR), (_copy(1), _const(k.Int(2, I32)))),
     "Pair(copy _1, const 2_i32)"),
    (r.Aggregate(r.AggTuple(PAIR), (_copy(1), _copy(2))), "(copy _1, copy _2)"),
    (r.Aggregate(r.AggArray(TRIPLE), (_copy(1), _copy(2), _copy(3))),
     "[copy _1, copy _2, copy _3]"),
    (r.Aggregate(r.AggClosure(PAIR), (_const(k.FnRef("__lambda_0")), _move(3))),
     "closure(const fn __lambda_0, move _3)"),
    (r.Aggregate(r.AggIterator(PAIR), (_move(3),)), "iterator(move _3)"),
    (r.Discriminant(_local(3)), "discriminant(_3)"),
    (r.Clone(_copy(1)), "clone(copy _1)"),
    (r.Len(_local(1)), "len(_1)"),
    (r.Slice(_local(1), _const(k.Int(0, I32)), _copy(2)), "slice(_1, const 0_i32, copy _2)"),
    (r.Ref(_local(1), BorrowMode.PEEK), "ref peek _1"),
    # statements and callees (ir.md 8.6, L6, L7)
    (st.Assign(_local(1), r.Ref(_local(2), BorrowMode.POKE), SPAN), "_1 = ref poke _2"),
    (st.Drop(_local(5), SPAN), "Drop(_5)"),
    (st.DropIfSet(_local(5), _l(6), SPAN), "DropIfSet(_5, _6)"),
    (st.BeginBorrow(BorrowId(0), _local(3), BorrowMode.POKE, SPAN), "BeginBorrow(b0, _3, poke)"),
    (st.EndBorrow(BorrowId(0), SPAN), "EndBorrow(b0)"),
    (st.StorageLive(_l(3), SPAN), "StorageLive(_3)"),
    (st.StorageDead(_l(3), SPAN), "StorageDead(_3)"),
    (st.Call(_local(4), st.Indirect(_copy(3)), ((_const(k.Int(1, I32)), GUARANTEED),), SPAN),
     "_4 = call indirect(copy _3)(const 1_i32 @guaranteed)"),
    (st.Call(_local(4), st.Builtin(BuiltinMethod("string", "len"), ()),
             ((_copy(1), Convention.IN_GUARANTEED),), SPAN),
     "_4 = call builtin string.len(copy _1 @in_guaranteed)"),
    (st.Call(_local(2), st.Direct("test$bump", "bump"), ((_copy(1), Convention.INOUT),), SPAN),
     "_2 = call @bump(copy _1 @inout)"),
    (st.Call(_local(2), st.Direct("test$helper", "helper"), (), SPAN), "_2 = call @helper()"),
    # terminators (ir.md 8.8, L11)
    (tm.Goto(_b(4), SPAN), "goto -> bb4"),
    (tm.SwitchInt(_copy(4), ((0, _b(1)), (1, _b(2))), None, SPAN),
     "switchInt(copy _4) -> [0: bb1, 1: bb2, otherwise: unreachable]"),
    (tm.SwitchInt(_copy(2), (), _b(3), SPAN), "switchInt(copy _2) -> [otherwise: bb3]"),
    (tm.Return(SPAN), "return"),
    (tm.Trap("RE2020", (_copy(1), _copy(2)), SPAN), "trap RE2020(copy _1, copy _2)"),
    (tm.Trap("RE2026", (), SPAN), "trap RE2026"),
    (tm.Unreachable(SPAN), "unreachable"),
]


@pytest.mark.parametrize("value, text", SHAPES, ids=[text for _, text in SHAPES])
def test_each_shape_prints_its_text(value: object, text: str) -> None:
    assert printer.show(value) == text


_UNIONS = {"Place": p.Place, "Operand": p.Operand, "Constant": k.Constant, "Rvalue": r.Rvalue,
           "AggKind": r.AggKind, "SlirStatement": st.SlirStatement, "SlirCallee": st.SlirCallee,
           "BuiltinName": st.BuiltinName, "SlirTerminator": tm.SlirTerminator}


@pytest.mark.parametrize("union", list(_UNIONS.values()), ids=list(_UNIONS))
def test_each_member_of_a_union_has_a_row(union: object) -> None:
    missing = set(typing.get_args(union)) - set(printer.PRINTERS)
    assert not missing, f"the SLIR printer has no row for: {sorted(c.__name__ for c in missing)}"


def test_the_table_has_no_row_for_another_class() -> None:
    members = set().union(*(set(typing.get_args(union)) for union in _UNIONS.values()))
    extra = set(printer.PRINTERS) - members
    assert not extra, f"the SLIR printer has a row for: {sorted(c.__name__ for c in extra)}"


def test_each_shape_of_a_union_is_printed_by_a_case() -> None:
    printed = {type(value) for value, _ in SHAPES}
    printed |= {type(value.place) for value, _ in SHAPES if isinstance(value, (p.Copy, p.Move))}
    printed |= {type(value.constant) for value, _ in SHAPES if isinstance(value, p.Const)}
    printed |= {type(value.kind) for value, _ in SHAPES if isinstance(value, r.Aggregate)}
    printed |= {type(value.callee) for value, _ in SHAPES if isinstance(value, st.Call)}
    printed |= {type(value.callee.method) for value, _ in SHAPES
                if isinstance(value, st.Call) and isinstance(value.callee, st.Builtin)}
    printed |= {p.Local, Intrinsic, st.Direct}
    missing = set(printer.PRINTERS) - printed
    assert not missing, f"no case prints: {sorted(c.__name__ for c in missing)}"


def test_a_class_with_no_row_is_an_internal_error() -> None:
    class NotANode:
        pass

    with pytest.raises(InternalCompilerError):
        printer.show(NotANode())


_SOURCE = Path(inspect.getfile(printer)).read_text(encoding="utf-8")
_UNSTABLE_CALLS = {"id", "hash", "set", "frozenset", "vars"}


def test_the_printer_reads_no_address_and_no_hash_order() -> None:
    tree = ast.parse(_SOURCE)
    calls = sorted(f"{node.func.id}:{node.lineno}" for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in _UNSTABLE_CALLS)
    sets = sorted(f"set display:{node.lineno}" for node in ast.walk(tree)
                  if isinstance(node, (ast.Set, ast.SetComp)))
    assert not calls + sets, f"the printer output must not depend on these: {calls + sets}"
