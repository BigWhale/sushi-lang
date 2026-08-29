"""Compile-time constant expression evaluator."""
from __future__ import annotations
import math
import operator
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Union

from llvmlite import ir

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    ConstDef, Expr, IntLit, FloatLit, BoolLit, StringLit, ArrayLiteral,
    BinaryOp, UnaryOp, Name, CastExpr, IndexAccess, InterpolatedString
)
from sushi_lang.semantics.unit_symbols import UnitKeyedSymbols
from sushi_lang.semantics.integer_width import (
    fits_integer_type, integer_bit_width, wrap_to_integer_type)
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.semantics import array_runs
from sushi_lang.semantics.passes.collect import ConstantTable
from sushi_lang.semantics.generics.type_display import display_type

# The exact arithmetic of an overflow-checked operator. Unary minus is the fourth one
# and is applied where it is read, because it has no right operand.
_ARITHMETIC: Mapping[str, Callable[[object, object], object]] = {
    "+": operator.add, "-": operator.sub, "*": operator.mul,
}

# A width-defined operator: every bit of the result the width holds is kept, and the
# rest are lost. None of these can leave its type, so none of them reports.
_BITWISE: Mapping[str, Callable[[int, int], int]] = {
    "&": operator.and_, "|": operator.or_, "^": operator.xor,
}


@dataclass
class ConstOverflow:
    """An operation whose result its type cannot hold.

    The node is kept so a caller that reads an expression with a silent reporter can
    tell its own overflow from one inside a constant it named: only the node that
    computed the value reports it (CE2077).
    """
    node: Expr
    op: str
    value: int
    semantic_type: Type
    span: Optional[Span]


def emit_overflow(reporter: Reporter, overflow: ConstOverflow) -> None:
    """Raise a recorded overflow as CE2077, whoever read the expression."""
    er.emit_with(reporter, er.ERR.CE2077, overflow.span, op=overflow.op,
                 value=overflow.value, type=display_type(overflow.semantic_type)) \
        .help("use a wider type, or compute in one and cast the result with 'as'").emit()


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

    def __init__(self, reporter: Reporter, const_table: ConstantTable,
                 ast_constants: 'UnitKeyedSymbols[ConstDef]',
                 unit_name: Optional[str] = None, scope: object = None):
        """Initialize the evaluator.

        `unit_name` is the unit whose constant expression is being evaluated. Two units
        may each declare a private `SCRATCH`, so a name is only an answer once the asking
        unit is known (`docs/design/unit-namespaces.md` section 9). `scope` is the rest
        of that answer: which OTHER units' constants this one may read at all (section 6).
        """
        self.reporter = reporter
        self.const_table = const_table
        self.ast_constants = ast_constants
        self.unit_name = unit_name
        self.scope = scope
        self.evaluation_stack: List[str] = []  # For cycle detection
        # The FIRST operation that left its type, for a caller whose reporter is silent.
        self.overflow: Optional[ConstOverflow] = None

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

        elif isinstance(expr, InterpolatedString):
            return self._evaluate_interpolation(expr, span)

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

    def _format_hole(self, value: ConstantValue,
                     span: Optional[Span]) -> Optional[str]:
        """One hole, rendered as the run-time formatter prints it.

        The runtime prints an integer with printf %d/%u at its width, a float
        with %g, and a bool through the select over "1"/"0" the integer path
        shares (`backend/runtime/formatting.py`). The evaluator must not
        drift, or a constant would print differently than the same
        expression written in a body. An integer needs no truncation here:
        every constant operation is range-checked (CE2070/CE2077), so the
        held value is the printed value.
        """
        t = value.semantic_type
        if t == BuiltinType.STRING:
            return value.value
        if t == BuiltinType.BOOL:
            return "1" if value.value else "0"
        if t == BuiltinType.F32:
            import struct
            return "%g" % struct.unpack("f", struct.pack("f", value.value))[0]
        if t == BuiltinType.F64:
            return "%g" % value.value
        if self._is_integer_type(t):
            return str(value.value)
        er.emit(self.reporter, er.ERR.CE0108, span,
                expr_type=f"interpolation of {display_type(t)}")
        return None

    def _evaluate_interpolation(self, expr: InterpolatedString,
                                span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate an interpolated string constant (#447).

        A hole is a constant expression like any other; it carries no declared
        type, so a bare literal in one takes the same default it takes in a
        body.
        """
        rendered: List[str] = []
        for part in expr.parts:
            if isinstance(part, str):
                rendered.append(part)
                continue
            part_span = getattr(part, "loc", None) or span
            value = self.evaluate(part, BuiltinType.STRING, part_span)
            if value is None:
                return None
            text = self._format_hole(value, part_span)
            if text is None:
                return None
            rendered.append(text)
        return ConstantValue("".join(rendered), BuiltinType.STRING)

    def _evaluate_binary_op(self, expr: BinaryOp, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate binary operation."""
        left_val = self.evaluate(expr.left, expected_type, expr.left.loc)
        right_val = self.evaluate(expr.right, expected_type, expr.right.loc)

        if left_val is None or right_val is None:
            return None

        op_span = expr.loc or span

        if expr.op == '+' and BuiltinType.STRING in (left_val.semantic_type,
                                                     right_val.semantic_type):
            # Sushi has no concatenation operator anywhere, so a constant reports the
            # language rule and not a constant-only one (#441).
            er.emit(self.reporter, er.ERR.CE2509, span)
            return None

        if expr.op in _ARITHMETIC:
            return self._eval_arithmetic(expr, left_val, right_val, op_span)
        elif expr.op == '/':
            return self._eval_division(expr, left_val, right_val, op_span)
        elif expr.op == '%':
            return self._eval_modulo(expr, left_val, right_val, op_span)

        elif expr.op in _BITWISE:
            return self._eval_bitwise(left_val, right_val, expr.op, span)
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
                # A negated literal is ONE leaf, and the range of a leaf is CE2073's
                # question -- that is what makes -128 an i8 while 128 is not.
                if isinstance(expr.expr, (IntLit, FloatLit)):
                    return ConstantValue(-operand.value, operand.semantic_type)
                return self._checked(expr, '-', -operand.value,
                                     operand.semantic_type, expr.loc or span)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='negation on non-numeric type')
                return None

        elif expr.op == '~':
            if self._is_integer_type(operand.semantic_type):
                result = wrap_to_integer_type(~operand.value, operand.semantic_type)
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

        # A run is evaluated ONCE and its result repeated. This is the speaker for CE2017
        # on a constant: `validate_constant` returns on the error, so the typecheck pass
        # never reaches the same literal twice.
        runs = array_runs.read_runs(
            expr.elements,
            array_runs.const_int_reader(self.const_table, self.ast_constants,
                                        self.unit_name),
            self.reporter)
        if runs is None:
            return None

        # A constant's evaluator needs the VALUES and not only the count, so a bound or a
        # count it cannot read is CE2019 or CE2017 here (Ruling 3, #478).
        if array_runs.require_readable_length(runs, self.reporter) is None:
            return None

        element_values = []
        for run in runs:
            if run.plan is not None:
                # A readable range expands to literals, so the table lands in .rodata with
                # no arithmetic behind it.
                element_values.extend(
                    ConstantValue(value, element_type) for value in run.plan.values())
                continue
            run_val = self.evaluate(run.value, element_type, run.value.loc)
            if run_val is None:
                return None  # Non-constant element
            element_values.extend([run_val] * run.count)

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

        const_sig = self.const_table.lookup(const_name, self.unit_name, self.scope)
        if const_sig is None:
            er.emit(self.reporter, er.ERR.CE1002, span, name=const_name)
            return None

        const_def = self.ast_constants.lookup(const_name, self.unit_name, self.scope)
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

        # A cast asks for the bit pattern, so it truncates and never reports: it is the
        # escape from the overflow rule and cannot be subject to it.
        if self._is_integer_type(from_type) and self._is_integer_type(to_type):
            return ConstantValue(wrap_to_integer_type(value.value, to_type), to_type)

        elif self._is_integer_type(from_type) and self._is_float_type(to_type):
            return ConstantValue(float(value.value), to_type)

        elif self._is_float_type(from_type) and self._is_integer_type(to_type):
            return ConstantValue(wrap_to_integer_type(int(value.value), to_type), to_type)

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

    def _checked(self, node: Expr, op: str, value: Union[int, float], semantic_type: Type,
                 span: Optional[Span]) -> Optional[ConstantValue]:
        """The result of an overflow-checked operation, or CE2077 when it left its type.

        A float has no width to leave, and a value the operands already made a lie --
        a mixed pair, which CE2510 owns -- is not this diagnostic's to report.
        """
        if (self._is_integer_type(semantic_type)
                and isinstance(value, int) and not isinstance(value, bool)
                and not fits_integer_type(value, semantic_type)):
            record = ConstOverflow(node=node, op=op, value=value,
                                   semantic_type=semantic_type, span=span)
            if self.overflow is None:
                self.overflow = record
            emit_overflow(self.reporter, record)
            return None

        return ConstantValue(value, semantic_type)

    def _eval_arithmetic(self, node: BinaryOp, left: ConstantValue, right: ConstantValue,
                         span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate arithmetic operation."""
        if not self._is_numeric_type(left.semantic_type) or not self._is_numeric_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='arithmetic on non-numeric type')
            return None

        result = _ARITHMETIC[node.op](left.value, right.value)
        return self._checked(node, node.op, result, left.semantic_type, span)

    def _eval_division(self, node: BinaryOp, left: ConstantValue, right: ConstantValue,
                       span: Optional[Span]) -> Optional[ConstantValue]:
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

        return self._checked(node, node.op, result, left.semantic_type, span)

    def _eval_modulo(self, node: BinaryOp, left: ConstantValue, right: ConstantValue,
                     span: Optional[Span]) -> Optional[ConstantValue]:
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

        return self._checked(node, node.op, result, left.semantic_type, span)

    def _eval_bitwise(self, left: ConstantValue, right: ConstantValue, op: str, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate bitwise operation."""
        if not self._is_integer_type(left.semantic_type) or not self._is_integer_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op=f'bitwise {op} on non-integer type')
            return None

        result = _BITWISE[op](left.value, right.value)
        return ConstantValue(wrap_to_integer_type(result, left.semantic_type),
                             left.semantic_type)

    def _eval_shift_left(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate left shift."""
        shift = self._shift_count(left, right, span)
        if shift is None:
            return None
        count, width = shift

        result = 0 if count >= width else left.value << count
        return ConstantValue(wrap_to_integer_type(result, left.semantic_type),
                             left.semantic_type)

    def _eval_shift_right(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate right shift (arithmetic for signed, logical for unsigned)."""
        shift = self._shift_count(left, right, span)
        if shift is None:
            return None
        count, width = shift

        # A held value is in its own range, so Python's shift IS the machine's: it fills
        # from the sign bit of a negative value, and a value of an unsigned type has none.
        return ConstantValue(left.value >> min(count, width), left.semantic_type)

    def _shift_count(self, left: ConstantValue, right: ConstantValue,
                     span: Optional[Span]) -> Optional[tuple]:
        """A shift's count and the width it moves bits in, or None with the reason reported.

        A count past the width is defined and not checked (Go's rule, and CE2512 covers
        the one a body writes), so the width is handed back to clamp with: a Python shift
        by a count of millions builds the number it names.
        """
        if not self._is_integer_type(left.semantic_type) or not self._is_integer_type(right.semantic_type):
            er.emit(self.reporter, er.ERR.CE0110, span, op='shift on non-integer type')
            return None

        if right.value < 0:
            er.emit(self.reporter, er.ERR.CE0110, span, op='shift by negative amount')
            return None

        return right.value, integer_bit_width(left.semantic_type)

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

        A bool is equatable but not orderable, and a string is both, which is exactly
        what a body gets (#449). The typecheck pass owns that rule for a body; this
        evaluator has to agree with it, or a constant refuses what a local accepts.
        """
        if op in ('==', '!=') and self._is_equatable_pair(left, right):
            same = left.value == right.value
            return ConstantValue(same if op == '==' else not same, BuiltinType.BOOL)

        if op in ('<', '<=', '>', '>=') and self._is_orderable_pair(left, right):
            # Compare the UTF-8 bytes, which is what emit_string_order does at run time.
            # Python's str orders by code point and UTF-8 keeps code points in numerical
            # order, so the two agree; encoding first makes them agree by construction.
            lhs = left.value.encode('utf-8')
            rhs = right.value.encode('utf-8')
            ordered = {'<': lhs < rhs, '<=': lhs <= rhs,
                       '>': lhs > rhs, '>=': lhs >= rhs}[op]
            return ConstantValue(ordered, BuiltinType.BOOL)

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

    def _is_orderable_pair(self, left: ConstantValue, right: ConstantValue) -> bool:
        """Whether two non-numeric values of the same type carry an order.

        A string does and a bool does not, which is the rule a body follows.
        """
        return (left.semantic_type == right.semantic_type
                and left.semantic_type == BuiltinType.STRING)

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
