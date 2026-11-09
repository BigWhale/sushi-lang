"""`--dump-shir` prints the answers of `borrow` and `lift` in place (docs/design/ir.md 7.9).

The answers that the dump shows, where the table has a row:

- the `Ownership` of a consuming use, as a word before the node (`move LocalRef(_0)`,
  `adopt Lit(2)`, `reject ...`), the name of the answer in lower case;
- the conditionally moved locals, on a `cond_moved` line after the `locals` line;
- LENT or OWNED for an iterator call (`iterator: lent`) and its heap decision
  (`heap: false`), after the arguments of the `Call`;
- the walk in place of a loop (`in_place: true`), after the body of the `Loop`;
- the symbol and the environment of a `Lambda` (`symbol: "..."`, `env: [(adopt _4)]`),
  after its fields, each capture with its ownership and its mode.

With no table, or with an empty table, the dump is the text of today, so every golden stays
as it is. These tests print the body of `ownership_sample.py`. They call a Python function
over data; they do not run the compiler.
"""
from __future__ import annotations

from sushi_lang.semantics.shir.lift import LiftResults
from sushi_lang.semantics.shir.ownership import OwnershipResults
from sushi_lang.semantics.shir.printer import print_body, print_units

from ownership_sample import lift, ownership, sample

ANSWERED = """\
ShirBody sample
  params : [(_0 "s": string, Owned), (_1 "b": Box, Guaranteed), (_2 "xs": i32[], Guaranteed), \
(_3 "it": Lines, Inout), (_4 "k": i32, Guaranteed)]
  locals : [_5 "t", _6 "u", _7 "ys", _8 "e", _9 "x", _10 "y", _11 "line", _12 "g", _13 "v"]
  cond_moved : [_5]
  block  :
    Assign(LocalRef(_5), move LocalRef(_0), is_init: true)
    Assign(LocalRef(_5), adopt Call(Named(GlobalRef(@keep)), [(adopt LocalRef(_5), Owned), \
(Lit(1), Guaranteed), (adopt LocalRef(_2)..., Owned)]), is_init: false)
    Destructure([_6, _], adopt TupleInit([adopt LocalRef(_5), adopt Lit(2)]))
    Assign(LocalRef(_7), adopt ArrayInit(Fixed, [Repeat(Lit(7), Lit(3)), Single(adopt Lit(8))]), \
is_init: true)
    ExprStmt(StructInit(@Box, [adopt EnumInit(@Maybe, "Some", [adopt Lit(9)])], written_order: [0]))
    Match(adopt LocalRef(_1), [Arm(Wildcard, [])], consumes: true)
    Assign(LocalRef(_8), adopt Cast(adopt Call(Free(@fail), []), AppError), is_init: true)
    ExprStmt(Cast(adopt LocalRef(_4), i64))
    Loop(Items(Call(Builtin(LocalRef(_2), "iter"), []), _9, Bare), [])
    Loop(Items(adopt Call(Builtin(Call(Builtin(LocalRef(_2), "iter"), [], iterator: lent, \
heap: false), "map"), [(adopt GlobalRef(@double), Owned)], iterator: lent, heap: true), _10, Bare), \
[], in_place: false)
    Loop(Items(LocalRef(_3), _11, Bare), [], in_place: true)
    Assign(LocalRef(_12), adopt Lambda([(_13, Guaranteed)], [Return(adopt Binary(+, LocalRef(_13), \
LocalRef(_4)))], expr_body: true, symbol: "sample$lambda_0", env: [(adopt _4)]), is_init: true)
    Return(adopt EnumInit(@Result, "Ok", [adopt Lit(Blank)]))
"""


def test_a_body_prints_the_ownership_and_lift_answers_in_place() -> None:
    made = sample()
    text = print_body(made.body, made.decls, made.typeck, ownership=ownership(made),
                      lift=lift(made))
    assert text == ANSWERED


def test_a_body_with_no_table_or_an_empty_table_prints_as_before() -> None:
    made = sample()
    before = print_body(made.body, made.decls, made.typeck)
    assert "move" not in before and "adopt" not in before
    assert print_body(made.body, made.decls, made.typeck, ownership=OwnershipResults(),
                      lift=LiftResults()) == before


def test_a_lambda_with_a_symbol_alone_prints_the_symbol_alone() -> None:
    made = sample()
    text = print_body(made.body, made.decls, made.typeck, lift=lift(made, skip="environment"))
    assert 'expr_body: true, symbol: "sample$lambda_0")' in text


def test_a_capture_with_no_ownership_prints_its_local_and_its_mode() -> None:
    made = sample()
    text = print_body(made.body, made.decls, made.typeck,
                      ownership=ownership(made, skip="capture"), lift=lift(made))
    assert "env: [(_4)]" in text


def test_a_program_prints_each_body_with_its_tables() -> None:
    made = sample()
    text = print_units([("main", [(made.body, made.typeck, ownership(made), lift(made))])],
                       made.decls)
    assert text == f"unit main\n\n{ANSWERED}"
