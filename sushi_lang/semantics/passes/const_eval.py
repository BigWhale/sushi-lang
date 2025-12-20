# semantics/passes/const_eval.py
"""Compile-time constant expression evaluator.

This module provides compile-time evaluation of constant expressions during
semantic analysis. Constants are folded to Python values (int, float, bool, str)
during Pass 2, enabling error detection before code generation.

Design:
- Stateless evaluation (no builder, no runtime context)
- Returns Python values or None for non-evaluable expressions
- Tracks constant dependencies for cycle detection
- Produces clear error messages for invalid expressions

Allowed Operations:
- Literals: IntLit, FloatLit, BoolLit, StringLit
- Arithmetic: +, -, *, /, % (for numeric types)
- Bitwise: &, |, ^, ~, <<, >> (for integer types only)
- Logical: and, or, xor, not (for bool type only)
- Comparison: ==, !=, <, <=, >, >= (for compatible types)
- Type Casts: as (between compatible types)
- Array Literals: Fixed-size arrays with constant elements
- Name References: References to other constants (with cycle detection)

Forbidden Operations:
- Function calls (including constructors)
- Method calls
- Variable references (only constants allowed)
- Dynamic arrays
- Struct/Enum construction
- Borrow/TryExpr (runtime-only)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Union

from llvmlite import ir

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    Expr, IntLit, FloatLit, BoolLit, StringLit, ArrayLiteral,
    BinaryOp, UnaryOp, Name, CastExpr
)
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.semantics.passes.collect import ConstantTable


@dataclass
class ConstantValue:
    """Compile-time constant value with type information."""
    value: Union[int, float, bool, str, List['ConstantValue']]  # Python value
    semantic_type: Type  # Sushi type (i32, f64, bool, string, etc.)

    def to_llvm_constant(self, types) -> ir.Constant:
        """Convert to LLVM constant for backend emission.

        Args:
            types: LLVMTypeSystem instance for type lookups

        Returns:
            LLVM constant value
        """
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
            # Strings require special handling - return None to trigger fallback
            return None
        elif isinstance(self.value, list):
            # Array constant
            element_constants = [elem.to_llvm_constant(types) for elem in self.value]
            if any(c is None for c in element_constants):
                return None
            element_type = element_constants[0].type
            array_type = ir.ArrayType(element_type, len(element_constants))
            return ir.Constant(array_type, element_constants)
        else:
            return None


class ConstantEvaluator:
    """Compile-time constant expression evaluator.

    Evaluates constant expressions to concrete values during compilation.
    Used in Pass 2 for constant definitions and during type checking.

    Design:
    - Stateless evaluation (no builder, no runtime context)
    - Returns Python values (int, float, bool, str) or None for non-evaluable expressions
    - Tracks constant dependencies for cycle detection
    - Produces clear error messages for invalid expressions
    """

    def __init__(self, reporter: Reporter, const_table: ConstantTable, ast_constants: dict):
        """Initialize the evaluator.

        Args:
            reporter: Error reporter for diagnostics
            const_table: Table of constant signatures
            ast_constants: Dict mapping constant names to ConstDef AST nodes
        """
        self.reporter = reporter
        self.const_table = const_table
        self.ast_constants = ast_constants
        self.evaluation_stack: List[str] = []  # For cycle detection

    def evaluate(self, expr: Expr, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate an expression to a compile-time constant.

        Args:
            expr: Expression to evaluate
            expected_type: Expected type of the constant
            span: Source span for error reporting

        Returns:
            ConstantValue with Python value and type, or None if not evaluable

        Emits:
            CE0108: Expression is not a compile-time constant
            CE0109: Circular constant dependency detected
            CE0110: Unsupported operation in constant expression
        """
        # Literals
        if isinstance(expr, IntLit):
            return self._evaluate_int_lit(expr, expected_type)
        elif isinstance(expr, FloatLit):
            return self._evaluate_float_lit(expr, expected_type)
        elif isinstance(expr, BoolLit):
            return ConstantValue(expr.value, BuiltinType.BOOL)
        elif isinstance(expr, StringLit):
            return ConstantValue(expr.value, BuiltinType.STRING)

        # Binary operations
        elif isinstance(expr, BinaryOp):
            return self._evaluate_binary_op(expr, expected_type, span)

        # Unary operations
        elif isinstance(expr, UnaryOp):
            return self._evaluate_unary_op(expr, expected_type, span)

        # Array literals
        elif isinstance(expr, ArrayLiteral):
            return self._evaluate_array_literal(expr, expected_type, span)

        # Name references (other constants)
        elif isinstance(expr, Name):
            return self._evaluate_name(expr, span)

        # Type casts
        elif isinstance(expr, CastExpr):
            return self._evaluate_cast(expr, span)

        # Everything else is not a compile-time constant
        else:
            er.emit(self.reporter, er.ERR.CE0108, span, expr_type=type(expr).__name__)
            return None

    def _evaluate_int_lit(self, expr: IntLit, expected_type: Type) -> ConstantValue:
        """Evaluate integer literal with type inference."""
        # Use expected type if provided, otherwise default to i32
        if expected_type in (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                             BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64):
            return ConstantValue(expr.value, expected_type)
        else:
            # Default to i32 for integer literals
            return ConstantValue(expr.value, BuiltinType.I32)

    def _evaluate_float_lit(self, expr: FloatLit, expected_type: Type) -> ConstantValue:
        """Evaluate float literal with type inference."""
        # Use expected type if provided, otherwise default to f64
        if expected_type in (BuiltinType.F32, BuiltinType.F64):
            return ConstantValue(expr.value, expected_type)
        else:
            # Default to f64 for float literals
            return ConstantValue(expr.value, BuiltinType.F64)

    def _evaluate_binary_op(self, expr: BinaryOp, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate binary operation."""
        # Evaluate operands
        left_val = self.evaluate(expr.left, expected_type, expr.left.loc)
        right_val = self.evaluate(expr.right, expected_type, expr.right.loc)

        if left_val is None or right_val is None:
            return None

        # Arithmetic operations
        if expr.op == '+':
            return self._eval_arithmetic(left_val, right_val, lambda a, b: a + b, span)
        elif expr.op == '-':
            return self._eval_arithmetic(left_val, right_val, lambda a, b: a - b, span)
        elif expr.op == '*':
            return self._eval_arithmetic(left_val, right_val, lambda a, b: a * b, span)
        elif expr.op == '/':
            return self._eval_division(left_val, right_val, span)
        elif expr.op == '%':
            return self._eval_modulo(left_val, right_val, span)

        # Bitwise operations (integers only)
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

        # Logical operations (booleans only)
        elif expr.op in ('and', 'or', 'xor'):
            return self._eval_logical(left_val, right_val, expr.op, span)

        # Comparison operations
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
            # Negation for numeric types
            if self._is_numeric_type(operand.semantic_type):
                return ConstantValue(-operand.value, operand.semantic_type)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='negation on non-numeric type')
                return None

        elif expr.op == '~':
            # Bitwise NOT for integers only
            if self._is_integer_type(operand.semantic_type):
                # Python bitwise NOT with type-aware masking
                result = ~operand.value
                return ConstantValue(result, operand.semantic_type)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='bitwise NOT on non-integer type')
                return None

        elif expr.op == 'not':
            # Logical NOT for booleans only
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

        # Determine element type
        element_type = None
        if isinstance(expected_type, ArrayType):
            element_type = expected_type.base_type

        # Evaluate all elements
        element_values = []
        for elem in expr.elements:
            elem_val = self.evaluate(elem, element_type, elem.loc)
            if elem_val is None:
                return None  # Non-constant element
            element_values.append(elem_val)

        if not element_values:
            er.emit(self.reporter, er.ERR.CE0108, span, expr_type='empty array')
            return None

        # Create array constant
        return ConstantValue(element_values, expected_type)

    def _evaluate_name(self, expr: Name, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate name reference (constant lookup)."""
        const_name = expr.id

        # Check for circular dependency
        if const_name in self.evaluation_stack:
            chain = " -> ".join(self.evaluation_stack + [const_name])
            er.emit(self.reporter, er.ERR.CE0109, span, chain=chain)
            return None

        # Lookup constant
        const_sig = self.const_table.by_name.get(const_name)
        if const_sig is None:
            er.emit(self.reporter, er.ERR.CE1002, span, name=const_name)
            return None

        # Get AST node for the constant
        const_def = self.ast_constants.get(const_name)
        if const_def is None:
            er.emit(self.reporter, er.ERR.CE1002, span, name=const_name)
            return None

        # Push to stack and evaluate recursively
        self.evaluation_stack.append(const_name)
        result = self.evaluate(const_def.value, const_sig.const_type, const_sig.loc)
        self.evaluation_stack.pop()

        return result

    def _evaluate_cast(self, expr: CastExpr, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate type cast."""
        # Evaluate the expression being cast
        value = self.evaluate(expr.expr, expr.target_type, expr.expr.loc)
        if value is None:
            return None

        # Perform cast based on types
        from_type = value.semantic_type
        to_type = expr.target_type

        # Integer to integer
        if self._is_integer_type(from_type) and self._is_integer_type(to_type):
            return ConstantValue(value.value, to_type)

        # Integer to float
        elif self._is_integer_type(from_type) and self._is_float_type(to_type):
            return ConstantValue(float(value.value), to_type)

        # Float to integer (truncation)
        elif self._is_float_type(from_type) and self._is_integer_type(to_type):
            return ConstantValue(int(value.value), to_type)

        # Integer to bool
        elif self._is_integer_type(from_type) and to_type == BuiltinType.BOOL:
            return ConstantValue(value.value != 0, BuiltinType.BOOL)

        # Bool to integer
        elif from_type == BuiltinType.BOOL and self._is_integer_type(to_type):
            return ConstantValue(1 if value.value else 0, to_type)

        # Float to float
        elif self._is_float_type(from_type) and self._is_float_type(to_type):
            return ConstantValue(value.value, to_type)

        else:
            er.emit(self.reporter, er.ERR.CE0111, span, from_type=str(from_type), to_type=str(to_type))
            return None

    # Helper methods for arithmetic/bitwise operations

    def _eval_arithmetic(self, left: ConstantValue, right: ConstantValue, op, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate arithmetic operation."""
        if not self._is_numeric_type(left.semantic_type) or not self._is_numeric_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='arithmetic on non-numeric type')
            return None

        result = op(left.value, right.value)
        # Result type is the left operand's type (may need refinement)
        return ConstantValue(result, left.semantic_type)

    def _eval_division(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate division with zero check."""
        if not self._is_numeric_type(left.semantic_type) or not self._is_numeric_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='division on non-numeric type')
            return None

        if right.value == 0:
            er.emit(self.reporter, er.ERR.CE0112, span)
            return None

        # Integer division for integers, float division for floats
        if self._is_integer_type(left.semantic_type):
            result = left.value // right.value
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

        result = left.value % right.value
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

        # Python's >> is arithmetic shift (sign-extends for negative numbers)
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
        """Evaluate comparison operation."""
        # Comparisons work on numeric types and booleans
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

    # Type checking helpers

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
