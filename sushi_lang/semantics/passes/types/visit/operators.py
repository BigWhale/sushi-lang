"""The operator and condition arms of the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.4).

An operator arm takes the type of its position and answers the type of its node, as every
expression arm does. A numeric position gives its type to each operand that has the type
of the operation: both operands of `+ - * / % & | ^`, the value of a shift, the operand
of `-` and `~`. That type reaches a literal, and an operator that passes it on, and no
other operand. A numeric literal operand that gets no type from its position takes the
type of the other operand, which is checked first; of two such literals, a number beside
a byte literal takes its `u8`.

A comparison, `and`, `or`, `xor` and `not` answer a `bool`. A `Maybe` or a `Result`
variant that is an operand of a comparison takes the type of the other operand, which is
checked first (`m == Maybe.None`). Each operand of `and`, `or`,
`xor` and `not` is a condition, as the condition of an `if` and of a `while` is
(`check_condition`). A negated literal is one value: the negation types the literal with
its sign. An overflow-checked operation reads itself through the fold when its operands
have no error (`reject_overflow`). The rules of the operands are in
`passes/types/expressions.py`. A node whose own rule reports answers no type.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.const_eval import emit_string_plus
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.operators import (
    ARITHMETIC_OPS, BITWISE_OPS, COMPARISON_OPS, DIVISION_OPS, SHIFT_OPS)
from sushi_lang.semantics.passes.types.expressions import (
    is_string_plus, reject_impossible_shift_count,
    reject_mixed_numeric_operands, reject_non_bool_condition, reject_non_integer_operands,
    reject_non_numeric_arithmetic, reject_overflow, reject_uncomparable_operands,
    reject_zero_divisor)
from sushi_lang.semantics.passes.types.visit.expressions import (
    type_float_literal, type_int_literal)
from sushi_lang.semantics.passes.types.visit.results import WRAPPERS
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.type_predicates import is_numeric_type
from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

_LOGICAL = ("and", "or", "xor")
#: The binary operators whose two operands have the type of the operation.
_SAME_TYPE = ARITHMETIC_OPS + BITWISE_OPS
#: The unary operators whose operand has the type of the operation.
_TYPE_KEEPING = ("neg", "~")


def check_condition(validator: TypeValidator, cond: e.ShirExpr) -> bool:
    """Check a condition: a `bool` and nothing else. Answers whether it is one."""
    ty = visit.check_expr(validator, cond, BuiltinType.BOOL)
    if reject_non_bool_condition(validator, cond, ty):
        return False
    return ty == BuiltinType.BOOL


def check_unary(validator: TypeValidator, node: e.Unary,
                expected: Optional[Type]) -> Optional[Type]:
    operand = node.operand
    if node.op == "not":
        ty = visit.check_expr(validator, operand, BuiltinType.BOOL)
        if _rejects_a_pointer(validator, node, ty):
            return None
        if ty is None or reject_non_bool_condition(validator, operand, ty):
            return None
        return BuiltinType.BOOL
    position = _numeric(expected)
    if node.op == "neg" and isinstance(operand, e.Lit):
        negated = _type_negated_literal(validator, node, operand, position)
        if negated is not None:
            return visit.answer(validator, operand, negated, wraps=True)
    ty = visit.check_expr(validator, operand, _given(operand, position))
    if ty is None or _rejects_a_pointer(validator, node, ty):
        return None
    if node.op == "~":
        reject_non_integer_operands(validator, "~", [(operand, ty)])
        return ty
    if reject_non_numeric_arithmetic(validator, "-", [(operand, ty)]):
        return None
    return None if reject_overflow(validator, node, ty) else ty


def _type_negated_literal(validator: TypeValidator, node: e.Unary, lit: e.Lit,
                          position: Optional[Type]) -> Optional[Type]:
    """The type of a negated number literal, with its sign; None for any other literal."""
    match lit.value:
        case e.IntConst() as const:
            return type_int_literal(validator, const, position, lit.loc, negated=node.loc)
        case e.FloatConst(value):
            return type_float_literal(validator, -value, position, lit.loc)
    return None


def check_binary(validator: TypeValidator, node: e.Binary,
                 expected: Optional[Type]) -> Optional[Type]:
    left, right = _check_operands(validator, node, expected)
    if _rejects_a_pointer(validator, node, left) or _rejects_a_pointer(validator, node, right):
        return None
    op = node.op
    if op in _LOGICAL:
        refused = reject_non_bool_condition(validator, node.lhs, left)
        refused = reject_non_bool_condition(validator, node.rhs, right) or refused
        return None if refused or left is None or right is None else BuiltinType.BOOL
    if op in COMPARISON_OPS:
        refused = reject_mixed_numeric_operands(validator, node.loc, left, right)
        refused = reject_uncomparable_operands(validator, node, left, right) or refused
        return None if refused or left is None or right is None else BuiltinType.BOOL
    if op in ARITHMETIC_OPS:
        return _check_arithmetic(validator, node, left, right)
    return _check_bitwise(validator, node, left, right)


def _check_arithmetic(validator: TypeValidator, node: e.Binary, left: Optional[Type],
                      right: Optional[Type]) -> Optional[Type]:
    if is_string_plus(node.op, left, right):
        emit_string_plus(validator.reporter, node.loc)
        return None
    refused = reject_mixed_numeric_operands(validator, node.loc, left, right)
    refused = reject_non_numeric_arithmetic(
        validator, node.op, [(node.lhs, left), (node.rhs, right)]) or refused
    if node.op in DIVISION_OPS:
        refused = reject_zero_divisor(validator, node, left) or refused
    if refused or left is None or right is None:
        return None
    return None if reject_overflow(validator, node, left) else left


def _check_bitwise(validator: TypeValidator, node: e.Binary, left: Optional[Type],
                   right: Optional[Type]) -> Optional[Type]:
    """`& | ^` and the two shifts: integers, and a shift count that the compiler can read
    fits the value. A bitwise operator answers the type of its left operand also after an
    error of its operands (CE2004, CE2510, CE2512), so the position reports its own fault."""
    if reject_non_integer_operands(validator, node.op, [(node.lhs, left), (node.rhs, right)]):
        return left
    if node.op in SHIFT_OPS:
        reject_impossible_shift_count(validator, node, left, right)
    else:
        reject_mixed_numeric_operands(validator, node.loc, left, right)
    return None if left is None or right is None else left


def _check_operands(validator: TypeValidator, node: e.Binary,
                    expected: Optional[Type]) -> tuple[Optional[Type], Optional[Type]]:
    """The types of the two operands, each checked at the type of its position."""
    lhs, rhs = node.lhs, node.rhs
    if node.op in _LOGICAL:
        return (visit.check_expr(validator, lhs, BuiltinType.BOOL),
                visit.check_expr(validator, rhs, BuiltinType.BOOL))
    if node.op in COMPARISON_OPS:
        wrapped = _takes_the_other(validator, lhs), _takes_the_other(validator, rhs)
        if wrapped == (False, True):
            left = visit.check_expr(validator, lhs)
            return left, visit.check_expr(validator, rhs, left)
        if wrapped == (True, False):
            right = visit.check_expr(validator, rhs)
            return visit.check_expr(validator, lhs, right), right
    position = _numeric(expected)
    left_at = _given(lhs, position) if node.op in _SAME_TYPE + SHIFT_OPS else None
    right_at = _given(rhs, position) if node.op in _SAME_TYPE else None
    return check_operand_pair(validator, lhs, rhs, left_at, right_at)


def check_operand_pair(validator: TypeValidator, lhs: e.ShirExpr, rhs: e.ShirExpr,
                       left_at: Optional[Type] = None, right_at: Optional[Type] = None,
                       ) -> tuple[Optional[Type], Optional[Type]]:
    """The types of two values of one type, each checked at the type that its position
    gives it, if any: a lone number literal takes the type of the other value, and of two
    literals, a number beside a byte literal takes `u8`. The operands of a binary operator
    and the two arguments of a `<math>` family (`min(x, 2)`) read it."""
    left_open = left_at is None and _literal_under(lhs) is not None
    right_open = right_at is None and _literal_under(rhs) is not None
    if left_open and right_open:
        left_byte, right_byte = _is_byte_literal(lhs), _is_byte_literal(rhs)
        if left_byte != right_byte:
            left_at, right_at = (None, BuiltinType.U8) if left_byte else (BuiltinType.U8, None)
    elif right_open:
        left = visit.check_expr(validator, lhs, left_at)
        return left, visit.check_expr(validator, rhs, _builtin(left))
    elif left_open:
        right = visit.check_expr(validator, rhs, right_at)
        return visit.check_expr(validator, lhs, _builtin(right)), right
    return visit.check_expr(validator, lhs, left_at), visit.check_expr(validator, rhs, right_at)


def _takes_the_other(validator: TypeValidator, operand: e.ShirExpr) -> bool:
    """Is the operand a `Maybe` or a `Result` variant, which takes the instance of its
    position: in a comparison, the type of the other operand (`m == Maybe.None`)?"""
    return isinstance(operand, e.EnumInit) and validator.decl_name(operand.decl) in WRAPPERS


def _rejects_a_pointer(validator: TypeValidator, node: e.Unary | e.Binary,
                       ty: Optional[Type]) -> bool:
    """CE5010: a foreign `ptr` is an opaque handle, and no operator takes one."""
    if not isinstance(ty, ForeignPtrType):
        return False
    er.emit(validator.reporter, er.ERR.CE5010, node.loc, op=node.op)
    return True


def _numeric(ty: Optional[Type]) -> Optional[Type]:
    return ty if isinstance(ty, BuiltinType) and is_numeric_type(ty) else None


def _builtin(ty: Optional[Type]) -> Optional[Type]:
    return ty if isinstance(ty, BuiltinType) else None


def _given(operand: e.ShirExpr, position: Optional[Type]) -> Optional[Type]:
    """The type that a numeric position gives to this operand: a literal and an operator
    take it, and no other operand does."""
    return position if isinstance(operand, (e.Lit, e.Unary, e.Binary)) else None


def _literal_under(node: e.ShirExpr) -> Optional[e.Lit]:
    """The number literal under a run of operators that keep the type of their operand."""
    while isinstance(node, e.Unary) and node.op in _TYPE_KEEPING:
        node = node.operand
    if isinstance(node, e.Lit) and isinstance(node.value, (e.IntConst, e.FloatConst)):
        return node
    return None


def _is_byte_literal(node: e.ShirExpr) -> bool:
    lit = _literal_under(node)
    return (lit is not None and isinstance(lit.value, e.IntConst)
            and lit.value.byte_spelling is not None)
