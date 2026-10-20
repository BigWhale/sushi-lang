"""The readable fold of the typecheck pass (docs/design/ir.md 7.4, ruling R-E).

The pass needs the value of some sub-expressions that the compiler can read: a divisor, a
shift count, the operands of an overflow check, an index, a count, a range bound. The
value depends on the type of the position, so the pass folds the SHIR sub-expression at
that type. `Folder.read` is the one entry point.

The fold has no arithmetic of its own. The integer operators are the kernel's
(`semantics/integer_ops.py`), and the float and logical tables, the cast rules and the text
of a hole are the constant evaluator's (`semantics/const_eval.py`), so a body and a
constant cannot disagree. A constant is read from the constant table through the reader
that the pass gives (ruling Q5).

ONE table, `FOLDS`, holds the arm of each class that can be readable, and `UNREADABLE`
holds the classes that make a sub-expression unreadable (gate:
`tests/unit/test_typeck_fold_is_total.py`). An arm answers None when its node has no
value: an operand that is not readable, a division by zero, an index out of range. An
operation that leaves its type has no value either, and the fold records it with its node
in `overflows`, for the check of the position to report.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeAlias

from sushi_lang.semantics import integer_ops
from sushi_lang.semantics.const_eval import (
    FLOAT_ARITHMETIC, LOGICAL, AggregateConstant, ConstantValue, ScalarConstant,
    cast_constant, hole_text, is_bool_constant, is_integer_constant, is_numeric_constant,
    is_string_constant)
from sushi_lang.semantics.passes.types.inference import int_literal_default
from sushi_lang.semantics.ranges import RangePlan, plan_range
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import DeclId
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.type_predicates import is_float_type, is_integer_type
from sushi_lang.semantics.typesys import ArrayType, BuiltinType, Type

#: A value that the fold reads.
Readable: TypeAlias = ScalarConstant | AggregateConstant | RangePlan

#: The value of a constant by its declaration, or None when it has no value.
ConstantReader: TypeAlias = Callable[[DeclId], Optional[ConstantValue]]


@dataclass(frozen=True, slots=True)
class Overflow:
    """An operation whose exact result `value` its type `ty` cannot hold."""
    node: e.ExprNode
    op: str
    value: int
    ty: Type


class Folder:
    """The fold of the sub-expressions of one body, with the reader of its constants."""

    def __init__(self, constants: ConstantReader) -> None:
        self.constants = constants
        self.overflows: list[Overflow] = []
        self._memo: dict[tuple[Any, Optional[Type]], Optional[Readable]] = {}

    def read(self, node: e.ShirExpr, ty: Optional[Type]) -> Optional[Readable]:
        """The value of `node` at the type `ty` of its position, or None.

        A node is folded one time for each type: a second read answers from the memo and
        records no overflow again.
        """
        arm = FOLDS.get(type(node))
        if arm is None:
            return None
        if ty is not None and not isinstance(ty, BuiltinType):
            return arm(self, node, ty)
        key = (node.shir_id, ty)
        if key not in self._memo:
            self._memo[key] = arm(self, node, ty)
        return self._memo[key]

    def scalar(self, node: e.ShirExpr, ty: Optional[Type]) -> Optional[ScalarConstant]:
        found = self.read(node, ty)
        return found if isinstance(found, ScalarConstant) else None

    def checked(self, node: e.ExprNode, op: str, folded: integer_ops.Answer,
                ty: Type) -> Optional[ScalarConstant]:
        """The value of an overflow-checked operation; an overflow is recorded."""
        if isinstance(folded, integer_ops.Refusal):
            return None
        if folded.overflows:
            self.overflows.append(Overflow(node, op, folded.value, ty))
            return None
        return ScalarConstant(folded.value, ty)


Arm: TypeAlias = Callable[[Folder, Any, Optional[Type]], Optional[Readable]]


def _lit(_folder: Folder, node: e.Lit, ty: Optional[Type]) -> Optional[Readable]:
    match node.value:
        case e.IntConst() as const:
            at = ty if ty is not None and is_integer_type(ty) else int_literal_default(const)
            return ScalarConstant(const.value, at)
        case e.FloatConst(value):
            return ScalarConstant(value, ty if ty is not None and is_float_type(ty)
                                  else BuiltinType.F64)
        case e.BoolConst(value):
            return ScalarConstant(value, BuiltinType.BOOL)
        case e.StrConst(value):
            return ScalarConstant(value, BuiltinType.STRING)
    return None


def _const_ref(folder: Folder, node: e.ConstRef, _ty: Optional[Type]) -> Optional[Readable]:
    return folder.constants(node.const_id)


def _unary(folder: Folder, node: e.Unary, ty: Optional[Type]) -> Optional[Readable]:
    operand = folder.scalar(node.operand, ty)
    if operand is None:
        return None
    if node.op == "neg" and is_integer_constant(operand):
        negated = integer_ops.negate(int(operand.value), operand.semantic_type)
        # A negated literal is ONE leaf: its range is the question of CE2073.
        if isinstance(node.operand, e.Lit):
            return ScalarConstant(negated.value, operand.semantic_type)
        return folder.checked(node, "-", negated, operand.semantic_type)
    if node.op == "neg" and is_numeric_constant(operand):
        return ScalarConstant(-operand.value, operand.semantic_type)
    if node.op == "~" and is_integer_constant(operand):
        return ScalarConstant(integer_ops.complement(int(operand.value), operand.semantic_type),
                              operand.semantic_type)
    if node.op == "not" and is_bool_constant(operand):
        return ScalarConstant(not operand.value, BuiltinType.BOOL)
    return None


def _binary(folder: Folder, node: e.Binary, ty: Optional[Type]) -> Optional[Readable]:
    left = folder.scalar(node.lhs, ty)
    right = folder.scalar(node.rhs, ty)
    if left is None or right is None:
        return None
    op = node.op
    if is_integer_constant(left) and is_integer_constant(right) and (
            op in integer_ops.OVERFLOW_CHECKED or op in integer_ops.WIDTH_DEFINED):
        answer = integer_ops.binary(op, int(left.value), int(right.value), left.semantic_type)
        if op in integer_ops.OVERFLOW_CHECKED:
            return folder.checked(node, op, answer, left.semantic_type)
        return None if isinstance(answer, integer_ops.Refusal) else ScalarConstant(
            answer.value, left.semantic_type)
    if op in FLOAT_ARITHMETIC and is_numeric_constant(left) and is_numeric_constant(right):
        if op in ("/", "%") and right.value == 0:
            return None
        return ScalarConstant(FLOAT_ARITHMETIC[op](float(left.value), float(right.value)),
                              left.semantic_type)
    if op in LOGICAL and is_bool_constant(left) and is_bool_constant(right):
        return ScalarConstant(LOGICAL[op](bool(left.value), bool(right.value)), BuiltinType.BOOL)
    if op in integer_ops.COMPARISONS:
        return _compare(op, left, right)
    return None


def _compare(op: str, left: ScalarConstant, right: ScalarConstant) -> Optional[Readable]:
    """The rule of the constant evaluator: a bool is equatable and not ordered, a string
    is ordered by its UTF-8 bytes, and a number by its value."""
    if is_string_constant(left) and is_string_constant(right):
        lhs, rhs = str(left.value).encode("utf-8"), str(right.value).encode("utf-8")
        return ScalarConstant(integer_ops.compare(op, lhs, rhs), BuiltinType.BOOL)
    if (is_bool_constant(left) and is_bool_constant(right)
            and op not in integer_ops.ORDERINGS):
        return ScalarConstant(integer_ops.compare(op, left.value, right.value), BuiltinType.BOOL)
    if is_numeric_constant(left) and is_numeric_constant(right):
        return ScalarConstant(integer_ops.compare(op, left.value, right.value), BuiltinType.BOOL)
    return None


def _cast(folder: Folder, node: e.Cast, _ty: Optional[Type]) -> Optional[Readable]:
    operand = folder.read(node.operand, node.target)
    if not isinstance(operand, (ScalarConstant, AggregateConstant)):
        return None
    return cast_constant(operand, node.target)


def _index(folder: Folder, node: e.Index, _ty: Optional[Type]) -> Optional[Readable]:
    base = (_fixed_array(folder, node.base) if isinstance(node.base, e.ArrayInit)
            else folder.read(node.base, None))
    at = folder.scalar(node.index, BuiltinType.I32)
    if (not isinstance(base, AggregateConstant) or not isinstance(base.semantic_type, ArrayType)
            or at is None or not is_integer_constant(at)):
        return None
    index = int(at.value)
    return base.elements[index] if 0 <= index < len(base.elements) else None


def _fixed_array(folder: Folder, node: e.ArrayInit) -> Optional[AggregateConstant]:
    """A fixed array literal whose runs are readable, with its elements at their default."""
    if not isinstance(node.kind, e.Fixed):
        return None
    values: list[ConstantValue] = []
    for run in node.runs:
        if isinstance(run, e.RangeRun):
            plan = folder.read(run.range, None)
            if not isinstance(plan, RangePlan) or not plan.count:
                return None
            values.extend(ScalarConstant(value, BuiltinType.I32) for value in plan.values())
            continue
        value = folder.read(run.value, None)
        if not isinstance(value, (ScalarConstant, AggregateConstant)):
            return None
        count = 1
        if isinstance(run, e.Repeat):
            read = folder.scalar(run.count, BuiltinType.I32)
            if read is None or not is_integer_constant(read) or int(read.value) < 1:
                return None
            count = int(read.value)
        values.extend([value] * count)
    if not values:
        return None
    return AggregateConstant(values, ArrayType(values[0].semantic_type, len(values)))


def _range(folder: Folder, node: e.Range, _ty: Optional[Type]) -> Optional[Readable]:
    start = folder.scalar(node.start, BuiltinType.I32)
    end = folder.scalar(node.end, BuiltinType.I32)
    if start is None or end is None or not (
            is_integer_constant(start) and is_integer_constant(end)):
        return None
    return plan_range(int(start.value), int(end.value), node.inclusive, node.reverse, node.loc)


def _interp(folder: Folder, node: e.Interp, _ty: Optional[Type]) -> Optional[Readable]:
    rendered: list[str] = []
    for part in node.parts:
        if isinstance(part, str):
            rendered.append(part)
            continue
        value = folder.read(part, BuiltinType.STRING)
        text = None if not isinstance(value, ScalarConstant) else hole_text(value)
        if text is None:
            return None
        rendered.append(text)
    return ScalarConstant("".join(rendered), BuiltinType.STRING)


FOLDS: dict[type, Arm] = {
    e.Lit: _lit,
    e.ConstRef: _const_ref,
    e.Unary: _unary,
    e.Binary: _binary,
    e.Cast: _cast,
    e.Index: _index,
    e.Range: _range,
    e.Interp: _interp,
}

#: The classes whose value the compiler cannot read (ir.md 7.4). An `ArrayInit` is read
#: only as the base of an `Index`.
UNREADABLE: frozenset[type] = frozenset({
    e.LocalRef, e.StorageRef, e.GlobalRef, e.Field, e.Slice, e.Call, e.Borrow, e.Take,
    e.Try, e.Lambda, e.StructInit, e.EnumInit, e.TupleInit, e.ArrayInit, Hole,
})
