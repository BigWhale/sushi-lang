"""The readable fold of the typecheck pass, over SHIR made by hand (docs/design/ir.md 7.4).

The typecheck pass folds a readable SHIR sub-expression at the type of its position
(ruling R-E): a literal, a constant, an operator, a cast, an index of a fixed array
literal, a range and an interpolation of such parts. The integer operator kernel
(`semantics/integer_ops.py`) does the arithmetic, and the value of a constant comes from
the constant table through a reader. Each test builds the nodes and calls the fold. No
test runs the compiler.
"""
from __future__ import annotations

from typing import Optional

from sushi_lang.internals.report import Span
from sushi_lang.semantics.const_eval import ConstantValue, ScalarConstant
from sushi_lang.semantics.passes.types.fold import Folder, Overflow
from sushi_lang.semantics.ranges import RangePlan
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import DeclId, LocalId, ShirId
from sushi_lang.semantics.typesys import BuiltinType

I8 = BuiltinType.I8
I32 = BuiltinType.I32
I64 = BuiltinType.I64
U8 = BuiltinType.U8
U64 = BuiltinType.U64
F32 = BuiltinType.F32
F64 = BuiltinType.F64
BOOL = BuiltinType.BOOL
STRING = BuiltinType.STRING

SPAN = Span(1, 1, 1, 2)
_ids = iter(range(10_000))


def _id() -> ShirId:
    return ShirId(next(_ids))


def lit(value: object) -> e.Lit:
    if isinstance(value, bool):
        return e.Lit(_id(), SPAN, e.BoolConst(value))
    if isinstance(value, int):
        return e.Lit(_id(), SPAN, e.IntConst(value))
    if isinstance(value, float):
        return e.Lit(_id(), SPAN, e.FloatConst(value))
    return e.Lit(_id(), SPAN, e.StrConst(str(value)))


def binary(op: str, lhs: e.ShirExpr, rhs: e.ShirExpr) -> e.Binary:
    return e.Binary(_id(), SPAN, op, lhs, rhs)  # type: ignore[arg-type]


def unary(op: str, operand: e.ShirExpr) -> e.Unary:
    return e.Unary(_id(), SPAN, op, operand)  # type: ignore[arg-type]


def no_constants(_decl: DeclId) -> Optional[ConstantValue]:
    return None


def read(node: e.ShirExpr, ty: Optional[BuiltinType]) -> object:
    answer = Folder(no_constants).read(node, ty)
    if isinstance(answer, ScalarConstant):
        return answer.value, answer.semantic_type
    return answer


def test_a_literal_takes_the_type_of_its_position() -> None:
    assert read(lit(200), U8) == (200, U8)
    assert read(lit(4.5), F32) == (4.5, F32)
    assert read(lit(True), I32) == (True, BOOL)
    assert read(lit("Mostly Harmless"), None) == ("Mostly Harmless", STRING)


def test_a_literal_with_no_type_takes_its_default() -> None:
    assert read(lit(7), None) == (7, I32)
    assert read(lit(7), F64) == (7, I32)
    assert read(e.Lit(_id(), SPAN, e.IntConst(120, byte_spelling="a'x'")), None) == (120, U8)
    assert read(lit(4.5), None) == (4.5, F64)


def test_the_blank_value_is_not_readable() -> None:
    assert read(e.Lit(_id(), SPAN, e.BlankConst()), None) is None


def test_a_complement_keeps_the_bits_of_the_type_of_its_position() -> None:
    assert read(unary("~", lit(255)), U8) == (0, U8)
    assert read(unary("~", lit(255)), I64) == (-256, I64)


def test_an_unsigned_value_above_the_i64_maximum_shifts_as_unsigned() -> None:
    assert read(lit(2 ** 63), U64) == (2 ** 63, U64)
    assert read(binary(">>", lit(2 ** 64 - 1), lit(60)), U64) == (15, U64)


def test_an_operation_that_leaves_its_type_is_recorded_at_its_node() -> None:
    folder = Folder(no_constants)
    node = binary("+", lit(200), lit(100))
    assert folder.read(node, U8) is None
    assert folder.overflows == [Overflow(node, "+", 300, U8)]


def test_a_negated_literal_is_one_leaf() -> None:
    folder = Folder(no_constants)
    assert folder.read(unary("neg", lit(128)), I8) == ScalarConstant(-128, I8)
    assert folder.overflows == []


def test_a_division_by_zero_has_no_value() -> None:
    folder = Folder(no_constants)
    assert folder.read(binary("/", lit(1), lit(0)), I32) is None
    assert folder.overflows == []


def test_a_constant_is_read_from_the_constant_table() -> None:
    def constants(decl: DeclId) -> Optional[ConstantValue]:
        return ScalarConstant(7, I32) if decl == DeclId(3) else None

    known = e.ConstRef(_id(), SPAN, DeclId(3))
    unknown = e.ConstRef(_id(), SPAN, DeclId(4))
    assert Folder(constants).read(binary("*", known, lit(6)), I32) == ScalarConstant(42, I32)
    assert Folder(constants).read(binary("*", unknown, lit(6)), I32) is None


def test_a_cast_reads_its_operand_at_the_target_type() -> None:
    assert read(e.Cast(_id(), SPAN, lit(300), U8), None) == (44, U8)
    assert read(e.Cast(_id(), SPAN, lit(1), BOOL), None) == (True, BOOL)
    assert read(e.Cast(_id(), SPAN, lit(2.75), I32), None) == (2, I32)


def test_an_index_of_a_fixed_array_literal_reads_its_element() -> None:
    def array(kind: e.ArrayKind) -> e.ArrayInit:
        runs: list[e.ArrayRun] = [e.Single(lit(10)), e.Single(lit(20)),
                                  e.Repeat(lit(7), lit(2))]
        return e.ArrayInit(_id(), SPAN, kind, runs)

    def index(base: e.ShirExpr, at: int) -> e.Index:
        return e.Index(_id(), SPAN, base, lit(at), unchecked=False)

    assert read(index(array(e.Fixed()), 3), None) == (7, I32)
    assert read(index(array(e.Fixed()), 4), None) is None
    assert read(index(array(e.Dynamic()), 0), None) is None
    assert read(array(e.Fixed()), None) is None


def test_a_range_reads_as_the_values_that_it_yields() -> None:
    node = e.Range(_id(), SPAN, lit(0), lit(5), inclusive=True, reverse=True)
    plan = Folder(no_constants).read(node, None)
    assert isinstance(plan, RangePlan)
    assert plan.values() == [5, 4, 3, 2, 1, 0]


def test_an_interpolation_of_readable_parts_is_a_string() -> None:
    node = e.Interp(_id(), SPAN, ["n=", lit(4), " ok=", lit(True)])
    assert read(node, None) == ("n=4 ok=true", STRING)


def test_a_comparison_and_a_logical_operator_answer_a_bool() -> None:
    assert read(binary("<", lit(1), lit(2)), BOOL) == (True, BOOL)
    assert read(binary("and", lit(True), lit(False)), BOOL) == (False, BOOL)
    assert read(unary("not", lit(False)), BOOL) == (True, BOOL)


def test_a_node_that_is_not_readable_makes_the_whole_value_unreadable() -> None:
    local = e.LocalRef(_id(), SPAN, LocalId(0))
    call = e.Call(_id(), SPAN, e.BuiltinFn("println"), [])
    assert read(local, I32) is None
    assert read(binary("+", lit(1), local), I32) is None
    assert read(e.Interp(_id(), SPAN, ["x", call]), None) is None
