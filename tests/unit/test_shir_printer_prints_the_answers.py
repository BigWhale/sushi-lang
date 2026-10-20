"""`--dump-shir` prints the answers of `typecheck` in place (docs/design/ir.md 7.3).

The answers that the dump shows: the resolved `Callee` in place of the written call
target, each argument of a call with its resolved convention, and the type of each local
from the table. Where the table has no row for a node, the dump prints the written form,
so a body that `typecheck` did not reach prints as before. These tests print the body
of `typeck_sample.py` with its hand-made table and compare the text. They call a Python
function over data; they do not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import callee as c
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.printer import ShirPrinter, print_body, print_units
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.typesys import BuiltinType

from typeck_sample import answers, sample, span

ANSWERED = """\
ShirBody sample
  params : [(_0 "name": string, Owned), (_1 "loud": bool, Guaranteed)]
  locals : [_2 "msg": string, _3 "a": i32, _4 "b": i32, _5 "i": i32]
  block  :
    Assign(LocalRef(_2), Try(Call(Free(@decorate), [(LocalRef(_0), Owned)])), is_init: true)
    If(LocalRef(_1), then: [ExprStmt(Call(Free(@shout), [(LocalRef(_2), Owned)]))])
    Destructure([_3, [_4, _]], TupleInit([Lit(1), TupleInit([Lit(2), Lit(3)])]))
    Loop(Items(Range(Lit(0), Lit(3), inclusive: false, reverse: false), _5, Bare), \
[ExprStmt(Call(Intrinsic("println"), [(Interp(["", LocalRef(_5), "!"]), Guaranteed)]))])
    Match(Cast(LocalRef(_3), i64), [Arm(Literal(0), []), Arm(Wildcard, [])], consumes: false)
    ExprStmt(Field(StructInit(@Pair, [LocalRef(_3), LocalRef(_4)], written_order: [0, 1]), "first"))
    Return(EnumInit(@Result, "Ok", [Lit(Blank)]))
"""

WRITTEN = """\
ShirBody sample
  params : [(_0 "name": string, Owned), (_1 "loud": bool, Guaranteed)]
  locals : [_2 "msg": string, _3 "a", _4 "b", _5 "i"]
  block  :
    Assign(LocalRef(_2), Try(Call(Named(GlobalRef(@decorate)), [(LocalRef(_0), Owned)])), is_init: true)
    If(LocalRef(_1), then: [ExprStmt(Call(Named(GlobalRef(@shout)), [(LocalRef(_2), Owned)]))])
    Destructure([_3, [_4, _]], TupleInit([Lit(1), TupleInit([Lit(2), Lit(3)])]))
    Loop(Items(Range(Lit(0), Lit(3), inclusive: false, reverse: false), _5, Bare), \
[ExprStmt(Call(BuiltinFn("println"), [Interp(["", LocalRef(_5), "!"])]))])
    Match(Cast(LocalRef(_3), i64), [Arm(Literal(0), []), Arm(Wildcard, [])], consumes: false)
    ExprStmt(Field(StructInit(@Pair, [LocalRef(_3), LocalRef(_4)], written_order: [0, 1]), "first"))
    Return(EnumInit(@Result, "Ok", [Lit(Blank)]))
"""


def test_a_body_prints_its_answers_in_place() -> None:
    made = sample()
    assert print_body(made.body, made.decls, answers(made)) == ANSWERED


def test_a_body_with_no_table_prints_the_written_form() -> None:
    made = sample()
    assert print_body(made.body, made.decls) == WRITTEN


def test_a_body_with_an_empty_table_prints_the_written_form() -> None:
    made = sample()
    assert print_body(made.body, made.decls, TypeckResults()) == WRITTEN


def test_a_node_with_no_answer_prints_its_written_form() -> None:
    made = sample()
    table = answers(made, skip="callee shout", skip_local=3)
    text = print_body(made.body, made.decls, table)
    assert "Call(Named(GlobalRef(@shout)), [(LocalRef(_2), Owned)])" in text
    assert '_3 "a", _4 "b": i32' in text
    assert "Call(Free(@decorate)" in text


def test_an_argument_with_no_convention_prints_as_written() -> None:
    made = sample()
    table = answers(made, skip="convention println 0")
    text = print_body(made.body, made.decls, table)
    assert 'Call(Intrinsic("println"), [Interp(["", LocalRef(_5), "!"])])' in text


def test_a_program_prints_each_body_with_its_table() -> None:
    made = sample()
    text = print_units([("main", [(made.body, answers(made)), (made.body, None)])], made.decls)
    assert text == f"unit main\n\n{ANSWERED}\n{WRITTEN}"


def test_a_bloom_keeps_its_dots_inside_the_pair() -> None:
    decls = DeclNames()
    call = e.Call(ShirId(1), span(1), e.Named(e.GlobalRef(ShirId(0), span(1), decls.refer("f"))),
                  [e.Arg(e.LocalRef(ShirId(2), span(1), LocalId(0)), None, bloom=True)])
    table = TypeckResults()
    table.record_convention(call.shir_id, 0, Convention.OWNED)
    assert (ShirPrinter(decls, table).show(call)
            == "Call(Named(GlobalRef(@f)), [(LocalRef(_0)..., Owned)])")


def _recv() -> e.LocalRef:
    return e.LocalRef(ShirId(0), span(1), LocalId(0))


@pytest.mark.parametrize("make, text", [
    (lambda d: c.Free(d.refer("f")), "Free(@f)"),
    (lambda d: c.Method(_recv(), d.refer("m")), "Method(LocalRef(_0), @m)"),
    (lambda d: c.Static(BuiltinType.I32, d.refer("at")), "Static(i32, @at)"),
    (lambda d: c.Builtin(_recv(), c.BuiltinMethod("string", "len")), 'Builtin(LocalRef(_0), "len")'),
    (lambda d: c.BuiltinStatic(BuiltinType.F64, c.BuiltinMethod("float_bits", "from_bits")),
     'BuiltinStatic(f64, "from_bits")'),
    (lambda d: c.Conversion(d.refer("to_app")), "Conversion(@to_app)"),
    (lambda d: c.Extern(d.refer("abs")), "Extern(@abs)"),
    (lambda d: c.Intrinsic("print"), 'Intrinsic("print")'),
    (lambda d: c.Indirect(_recv()), "Indirect(LocalRef(_0))"),
], ids=["Free", "Method", "Static", "Builtin", "BuiltinStatic", "Conversion", "Extern",
        "Intrinsic", "Indirect"])
def test_each_callee_prints_in_the_field_order_of_ir_md(make, text: str) -> None:
    decls = DeclNames()
    assert ShirPrinter(decls).show(make(decls)) == text
