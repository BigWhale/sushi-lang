"""Compile-time constant expression evaluator."""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Optional, Union

from llvmlite import ir

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    Expr, IntLit, FloatLit, BoolLit, StringLit, ArrayLiteral,
    BinaryOp, UnaryOp, Name, CastExpr, IndexAccess
)
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.semantics.passes.collect import ConstantTable
from sushi_lang.semantics.generics.type_display import display_type


@dataclass
class ConstantValue:
    """Compile-time constant value with type information."""
    value: Union[int, float, bool, str, List['ConstantValue']]  # Python value
    semantic_type: Type  # Sushi type (i32, f64, bool, string, etc.)

    def to_llvm_constant(self, types) -> ir.Constant:
        """Convert to LLVM constant for backend emission."""
        if self.semantic_type == BuiltinType.BOOL:
            return ir.Constant(types.i8, 1 if self.value else 0)
        elif self.semantic_type == BuiltinType.I8:
            return ir.Constant(types.i8, self.value)
        elif self.semantic_type == BuiltinType.I16:
            return ir.Constant(types.i16, self.value)
        elif self.semantic_type == BuiltinType.I32:
            return ir.Constant(types.i32, self.value)
        elif self.semantic_type == BuiltinType.I64:
            return ir.Constant(types.i64, self.value)
        elif self.semantic_type == BuiltinType.U8:
            return ir.Constant(types.u8, self.value)
        elif self.semantic_type == BuiltinType.U16:
            return ir.Constant(types.u16, self.value)
        elif self.semantic_type == BuiltinType.U32:
            return ir.Constant(types.u32, self.value)
        elif self.semantic_type == BuiltinType.U64:
            return ir.Constant(types.u64, self.value)
        elif self.semantic_type == BuiltinType.F32:
            return ir.Constant(types.f32, self.value)
        elif self.semantic_type == BuiltinType.F64:
            return ir.Constant(types.f64, self.value)
        elif self.semantic_type == BuiltinType.STRING:
            return None
        elif isinstance(self.value, list):
            element_constants = [elem.to_llvm_constant(types) for elem in self.value]
            if any(c is None for c in element_constants):
                return None
            element_type = element_constants[0].type
            array_type = ir.ArrayType(element_type, len(element_constants))
            return ir.Constant(array_type, element_constants)
        else:
            return None


class ConstantEvaluator:
    """Compile-time constant expression evaluator."""

    def __init__(self, reporter: Reporter, const_table: ConstantTable, ast_constants: dict):
        """Initialize the evaluator."""
        self.reporter = reporter
        self.const_table = const_table
        self.ast_constants = ast_constants
        self.evaluation_stack: List[str] = []  # For cycle detection

    def evaluate(self, expr: Expr, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate an expression to a compile-time constant."""
        if isinstance(expr, IntLit):
            return self._evaluate_int_lit(expr, expected_type)
        elif isinstance(expr, FloatLit):
            return self._evaluate_float_lit(expr, expected_type)
        elif isinstance(expr, BoolLit):
            return ConstantValue(expr.value, BuiltinType.BOOL)
        elif isinstance(expr, StringLit):
            return ConstantValue(expr.value, BuiltinType.STRING)

        elif isinstance(expr, BinaryOp):
            return self._evaluate_binary_op(expr, expected_type, span)

        elif isinstance(expr, UnaryOp):
            return self._evaluate_unary_op(expr, expected_type, span)

        elif isinstance(expr, ArrayLiteral):
            return self._evaluate_array_literal(expr, expected_type, span)

        elif isinstance(expr, Name):
            return self._evaluate_name(expr, span)

        elif isinstance(expr, CastExpr):
            return self._evaluate_cast(expr, span)

        elif isinstance(expr, IndexAccess):
            return self._evaluate_index(expr, span)

        else:
            er.emit(self.reporter, er.ERR.CE0108, span, expr_type=type(expr).__name__)
            return None

    def _evaluate_int_lit(self, expr: IntLit, expected_type: Type) -> ConstantValue:
        """Evaluate integer literal with type inference."""
        if expected_type in (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                             BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64):
            return ConstantValue(expr.value, expected_type)
        else:
            return ConstantValue(expr.value, BuiltinType.I32)

    def _evaluate_float_lit(self, expr: FloatLit, expected_type: Type) -> ConstantValue:
        """Evaluate float literal with type inference."""
        if expected_type in (BuiltinType.F32, BuiltinType.F64):
            return ConstantValue(expr.value, expected_type)
        else:
            return ConstantValue(expr.value, BuiltinType.F64)

    def _evaluate_binary_op(self, expr: BinaryOp, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate binary operation."""
        left_val = self.evaluate(expr.left, expected_type, expr.left.loc)
        right_val = self.evaluate(expr.right, expected_type, expr.right.loc)

        if left_val is None or right_val is None:
            return None

        if expr.op == '+':
            # Sushi has no concatenation operator anywhere, so a constant reports the
            # language rule and not a constant-only one (#441).
            if BuiltinType.STRING in (left_val.semantic_type, right_val.semantic_type):
                er.emit(self.reporter, er.ERR.CE2509, span)
                return None
            return self._eval_arithmetic(left_val, right_val, lambda a, b: a + b, span)
        elif expr.op == '-':
            return self._eval_arithmetic(left_val, right_val, lambda a, b: a - b, span)
        elif expr.op == '*':
            return self._eval_arithmetic(left_val, right_val, lambda a, b: a * b, span)
        elif expr.op == '/':
            return self._eval_division(left_val, right_val, span)
        elif expr.op == '%':
            return self._eval_modulo(left_val, right_val, span)

        elif expr.op == '&':
            return self._eval_bitwise(left_val, right_val, lambda a, b: a & b, '&', span)
        elif expr.op == '|':
            return self._eval_bitwise(left_val, right_val, lambda a, b: a | b, '|', span)
        elif expr.op == '^':
            return self._eval_bitwise(left_val, right_val, lambda a, b: a ^ b, '^', span)
        elif expr.op == '<<':
            return self._eval_shift_left(left_val, right_val, span)
        elif expr.op == '>>':
            return self._eval_shift_right(left_val, right_val, span)

        elif expr.op in ('and', 'or', 'xor'):
            return self._eval_logical(left_val, right_val, expr.op, span)

        elif expr.op in ('==', '!=', '<', '<=', '>', '>='):
            return self._eval_comparison(left_val, right_val, expr.op, span)

        else:
            er.emit(self.reporter, er.ERR.CE0110, span, op=expr.op)
            return None

    def _evaluate_unary_op(self, expr: UnaryOp, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate unary operation."""
        operand = self.evaluate(expr.expr, expected_type, expr.expr.loc)
        if operand is None:
            return None

        if expr.op == 'neg':
            if self._is_numeric_type(operand.semantic_type):
                return ConstantValue(-operand.value, operand.semantic_type)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='negation on non-numeric type')
                return None

        elif expr.op == '~':
            if self._is_integer_type(operand.semantic_type):
                result = ~operand.value
                return ConstantValue(result, operand.semantic_type)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='bitwise NOT on non-integer type')
                return None

        elif expr.op == 'not':
            if operand.semantic_type == BuiltinType.BOOL:
                return ConstantValue(not operand.value, BuiltinType.BOOL)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='logical NOT on non-boolean type')
                return None

        else:
            er.emit(self.reporter, er.ERR.CE0110, span, op=expr.op)
            return None

    def _evaluate_array_literal(self, expr: ArrayLiteral, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate array literal with constant elements."""
        from sushi_lang.semantics.typesys import ArrayType

        element_type = None
        if isinstance(expected_type, ArrayType):
            element_type = expected_type.base_type

        element_values = []
        for elem in expr.elements:
            elem_val = self.evaluate(elem, element_type, elem.loc)
            if elem_val is None:
                return None  # Non-constant element
            element_values.append(elem_val)

        if not element_values:
            er.emit(self.reporter, er.ERR.CE0108, span, expr_type='empty array')
            return None

        return ConstantValue(element_values, expected_type)

    def _evaluate_name(self, expr: Name, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate name reference (constant lookup)."""
        const_name = expr.id

        if const_name in self.evaluation_stack:
            chain = " -> ".join(self.evaluation_stack + [const_name])
            er.emit(self.reporter, er.ERR.CE0109, span, chain=chain)
            return None

        const_sig = self.const_table.by_name.get(const_name)
        if const_sig is None:
            er.emit(self.reporter, er.ERR.CE1002, span, name=const_name)
            return None

        const_def = self.ast_constants.get(const_name)
        if const_def is None:
            er.emit(self.reporter, er.ERR.CE1002, span, name=const_name)
            return None

        self.evaluation_stack.append(const_name)
        result = self.evaluate(const_def.value, const_sig.const_type, const_sig.loc)
        self.evaluation_stack.pop()

        return result

    def _evaluate_cast(self, expr: CastExpr, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate type cast."""
        value = self.evaluate(expr.expr, expr.target_type, expr.expr.loc)
        if value is None:
            return None

        from_type = value.semantic_type
        to_type = expr.target_type

        if self._is_integer_type(from_type) and self._is_integer_type(to_type):
            return ConstantValue(value.value, to_type)

        elif self._is_integer_type(from_type) and self._is_float_type(to_type):
            return ConstantValue(float(value.value), to_type)

        elif self._is_float_type(from_type) and self._is_integer_type(to_type):
            return ConstantValue(int(value.value), to_type)

        elif self._is_integer_type(from_type) and to_type == BuiltinType.BOOL:
            return ConstantValue(value.value != 0, BuiltinType.BOOL)

        elif from_type == BuiltinType.BOOL and self._is_integer_type(to_type):
            return ConstantValue(1 if value.value else 0, to_type)

        elif self._is_float_type(from_type) and self._is_float_type(to_type):
            return ConstantValue(value.value, to_type)

        else:
            er.emit(self.reporter, er.ERR.CE0111, span, from_type=display_type(from_type), to_type=display_type(to_type))
            return None

    def _evaluate_index(self, expr: IndexAccess, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate an index into an array constant.

        A constant cannot trap, so the bounds a body leaves to run time (RE2020) are
        compile-time diagnostics here, the same codes a constant index in a body gets.
        """
        base = self.evaluate(expr.array, None, expr.array.loc)
        if base is None:
            return None

        if not isinstance(base.value, list):
            er.emit(self.reporter, er.ERR.CE0110, span, op='index of a non-array constant')
            return None

        index = self.evaluate(expr.index, BuiltinType.I32, expr.index.loc)
        if index is None:
            return None

        if not self._is_integer_type(index.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='array index that is not an integer')
            return None

        if index.value < 0:
            er.emit(self.reporter, er.ERR.CE2056, expr.index.loc, index=index.value)
            return None

        if index.value >= len(base.value):
            er.emit(self.reporter, er.ERR.CE2012, expr.index.loc,
                    index=index.value, size=len(base.value))
            return None

        return base.value[index.value]

    def _eval_arithmetic(self, left: ConstantValue, right: ConstantValue, op, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate arithmetic operation."""
        if not self._is_numeric_type(left.semantic_type) or not self._is_numeric_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='arithmetic on non-numeric type')
            return None

        result = op(left.value, right.value)
        return ConstantValue(result, left.semantic_type)

    def _eval_division(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate division with zero check."""
        if not self._is_numeric_type(left.semantic_type) or not self._is_numeric_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='division on non-numeric type')
            return None

        if right.value == 0:
            er.emit(self.reporter, er.ERR.CE0112, span)
            return None

        if self._is_integer_type(left.semantic_type):
            result = _truncated_quotient(left.value, right.value)
        else:
            result = left.value / right.value

        return ConstantValue(result, left.semantic_type)

    def _eval_modulo(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate modulo with zero check."""
        if not self._is_numeric_type(left.semantic_type) or not self._is_numeric_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='modulo on non-numeric type')
            return None

        if right.value == 0:
            er.emit(self.reporter, er.ERR.CE0112, span)
            return None

        if self._is_integer_type(left.semantic_type):
            result = _truncated_remainder(left.value, right.value)
        else:
            result = math.fmod(left.value, right.value)

        return ConstantValue(result, left.semantic_type)

    def _eval_bitwise(self, left: ConstantValue, right: ConstantValue, op, op_name: str, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate bitwise operation."""
        if not self._is_integer_type(left.semantic_type) or not self._is_integer_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op=f'bitwise {op_name} on non-integer type')
            return None

        result = op(left.value, right.value)
        return ConstantValue(result, left.semantic_type)

    def _eval_shift_left(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate left shift."""
        if not self._is_integer_type(left.semantic_type) or not self._is_integer_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='shift on non-integer type')
            return None

        if right.value < 0:
            er.emit(self.reporter, er.ERR.CE0110, span, op='shift by negative amount')
            return None

        result = left.value << right.value
        return ConstantValue(result, left.semantic_type)

    def _eval_shift_right(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate right shift (arithmetic for signed, logical for unsigned)."""
        if not self._is_integer_type(left.semantic_type) or not self._is_integer_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='shift on non-integer type')
            return None

        if right.value < 0:
            er.emit(self.reporter, er.ERR.CE0110, span, op='shift by negative amount')
            return None

        result = left.value >> right.value
        return ConstantValue(result, left.semantic_type)

    def _eval_logical(self, left: ConstantValue, right: ConstantValue, op: str, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate logical operation (and, or, xor)."""
        if left.semantic_type != BuiltinType.BOOL or right.semantic_type != BuiltinType.BOOL:
            er.emit(self.reporter, er.ERR.CE0110, span, op=f'logical {op} on non-boolean type')
            return None

        if op == 'and':
            result = left.value and right.value
        elif op == 'or':
            result = left.value or right.value
        elif op == 'xor':
            result = left.value != right.value  # XOR for booleans
        else:
            er.emit(self.reporter, er.ERR.CE0110, span, op=op)
            return None

        return ConstantValue(result, BuiltinType.BOOL)

    def _eval_comparison(self, left: ConstantValue, right: ConstantValue, op: str, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate comparison operation.

        A bool and a string are equatable but not orderable, which is what a body gets
        too: '<' on two strings is not implemented anywhere.
        """
        if op in ('==', '!=') and self._is_equatable_pair(left, right):
            same = left.value == right.value
            return ConstantValue(same if op == '==' else not same, BuiltinType.BOOL)

        if not self._is_numeric_type(left.semantic_type) or not self._is_numeric_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op=f'comparison {op} on non-comparable types')
            return None

        if op == '==':
            result = left.value == right.value
        elif op == '!=':
            result = left.value != right.value
        elif op == '<':
            result = left.value < right.value
        elif op == '<=':
            result = left.value <= right.value
        elif op == '>':
            result = left.value > right.value
        elif op == '>=':
            result = left.value >= right.value
        else:
            er.emit(self.reporter, er.ERR.CE0110, span, op=op)
            return None

        return ConstantValue(result, BuiltinType.BOOL)

    def _is_equatable_pair(self, left: ConstantValue, right: ConstantValue) -> bool:
        """Whether two non-numeric values of the same type compare for equality."""
        return left.semantic_type == right.semantic_type and left.semantic_type in (
            BuiltinType.BOOL, BuiltinType.STRING)

    def _is_integer_type(self, ty: Type) -> bool:
        """Check if type is an integer type."""
        return ty in (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                     BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64)

    def _is_float_type(self, ty: Type) -> bool:
        """Check if type is a float type."""
        return ty in (BuiltinType.F32, BuiltinType.F64)

    def _is_numeric_type(self, ty: Type) -> bool:
        """Check if type is numeric (integer or float)."""
        return self._is_integer_type(ty) or self._is_float_type(ty)


def _truncated_quotient(left: int, right: int) -> int:
    """Integer division that truncates toward zero, as the backend's sdiv does.

    Python floors instead, so '-7 / 2' read -4 in a constant and -3 in a body (#441).
    """
    magnitude = abs(left) // abs(right)
    return -magnitude if (left < 0) != (right < 0) else magnitude


def _truncated_remainder(left: int, right: int) -> int:
    """Integer remainder whose sign follows the dividend, as the backend's srem does."""
    magnitude = abs(left) % abs(right)
    return -magnitude if left < 0 else magnitude
