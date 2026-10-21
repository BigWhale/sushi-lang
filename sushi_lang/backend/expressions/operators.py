"""Operator expression emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.operators import EQUALITY_OPS
from sushi_lang.semantics.type_predicates import is_unsigned_int

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_float_negation(codegen: 'LLVMCodegen', val: ir.Value) -> ir.Value:
    """Emit floating-point negation: `fneg` flips the sign bit, so -0.0 is negative zero."""
    return codegen.builder.fneg(val)


def emit_unary_value(codegen: 'LLVMCodegen', op: str, val: ir.Value, to_i1: bool) -> ir.Value:
    """A unary operator over one LLVM value: the AST emitter and the SLIR emitter call it."""
    if op == "neg":
        if isinstance(val.type, (ir.FloatType, ir.DoubleType)):
            return emit_float_negation(codegen, val)
        else:
            return codegen.builder.sub(ir.Constant(val.type, 0), val)

    if op == "not":
        if to_i1:
            i1v = codegen.utils.as_i1(val)
            return codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1))
        i1v = codegen.utils.as_i1(val)
        return codegen.builder.zext(codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1)), codegen.i8)

    if op == "~":
        return codegen.builder.not_(val)

    raise NotImplementedError(f"unknown UnaryOp: {op!r}")


def emit_string_relation(codegen: 'LLVMCodegen', op: str, lhs: ir.Value,
                         rhs: ir.Value) -> ir.Value:
    """The i1 answer of a comparison of two strings: `==` and `!=` read the bytes, and the
    order operators read the bytes, then the length."""
    if op in EQUALITY_OPS:
        return codegen.runtime.strings.emit_string_comparison(op, lhs, rhs)
    return codegen.runtime.strings.emit_string_order(op, lhs, rhs)


def emit_number_comparison(codegen: 'LLVMCodegen', op: str, lhs: ir.Value, rhs: ir.Value,
                           operand_type: 'Optional[Type]') -> ir.Value:
    """The i1 answer of a comparison of two numbers of one type.

    A float compares ordered, so a NaN operand answers false. An integer compares by the
    sign of `operand_type`. The operator and the array reductions read this one rule.
    """
    if isinstance(lhs.type, (ir.FloatType, ir.DoubleType)):
        return codegen.builder.fcmp_ordered(op, lhs, rhs)

    for operand in (lhs, rhs):
        if not isinstance(operand.type, ir.IntType):
            raise_internal_error("CE0017", src=str(operand.type), dst="an integer comparison operand")
    _require_one_width(op, lhs, rhs)

    if operand_type is not None and is_unsigned_int(operand_type):
        return codegen.builder.icmp_unsigned(op, lhs, rhs)
    return codegen.builder.icmp_signed(op, lhs, rhs)


def _require_one_width(op: str, left: ir.Value, right: ir.Value) -> None:
    """Stop on two operand types: CE2510 refuses a mixed pair before the backend."""
    if left.type != right.type:
        raise_internal_error("CE0139", op=op, left=str(left.type), right=str(right.type))


def emit_arithmetic(codegen: 'LLVMCodegen', op: str, left: ir.Value, right: ir.Value, left_type: 'Optional[Type]' = None) -> ir.Value:
    """Emit arithmetic operations on integer or floating-point values.

    Two constant operands are emitted as the instruction too. The backend computes no
    integer operator of its own: the constant evaluator is the one compile-time home
    of the semantics, and LLVM folds the instruction at run time's level (#681).
    """
    if isinstance(left.type, (ir.FloatType, ir.DoubleType)):
        float_ops = {
            "+": codegen.builder.fadd,
            "-": codegen.builder.fsub,
            "*": codegen.builder.fmul,
            "/": codegen.builder.fdiv,
            "%": codegen.builder.frem,
        }
        if op in float_ops:
            return float_ops[op](left, right)
    else:
        # Integer arithmetic operations. Division and modulo dispatch on
        # signedness (the same-type binop rule guarantees both operands share
        # one signedness, so the left operand's type suffices).
        unsigned = is_unsigned_int(left_type)
        int_ops = {
            "+": codegen.builder.add,
            "-": codegen.builder.sub,
            "*": codegen.builder.mul,
            "/": codegen.builder.udiv if unsigned else codegen.builder.sdiv,
            "%": codegen.builder.urem if unsigned else codegen.builder.srem,
        }
        if op in int_ops:
            return int_ops[op](left, right)

    raise NotImplementedError(f"arithmetic op not supported yet: {op!r}")


def emit_bitwise(codegen: 'LLVMCodegen', op: str, left: ir.Value, right: ir.Value, left_type: 'Optional[Type]' = None) -> ir.Value:
    """Emit bitwise operations on integer values. Two constants are an instruction too."""
    if (op in ("<<", ">>")
            and isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType)):
        return _emit_shift(codegen, op, left, right, left_type)

    _require_one_width(op, left, right)

    bitwise_ops = {
        "&": codegen.builder.and_,
        "|": codegen.builder.or_,
        "^": codegen.builder.xor,
    }

    if op in bitwise_ops:
        return bitwise_ops[op](left, right)

    raise NotImplementedError(f"bitwise op not supported yet: {op!r}")


def _emit_shift(codegen: 'LLVMCodegen', op: str, value: ir.Value, count: ir.Value,
                value_type: 'Optional[Type]' = None) -> ir.Value:
    """Emit a shift, with an answer for a count that leaves the type.

    A count at or above the width of the value moves every bit out of it, so the
    result is 0 -- or the sign bit repeated, when an arithmetic right shift fills
    from it. That is what shifting one place at a time would reach, and it is Go's
    rule. A count is never masked: masking answers `value << 8` on a u8 with the
    value itself, which reads like a working shift.

    Three details carry the rule:
    - the range is read from the count AS WRITTEN, before it is brought to the
      value's width, because a u64 count of 256 narrowed to a u8 is 0;
    - the shift instruction is given a masked count, so LLVM never emits an
      out-of-range shift, whose result is poison it may then use as it likes;
    - a count the compiler can read never arrives out of range. That is CE2512,
      and LLVM folds this whole sequence back to one shift for a constant in range.
    """

    builder = codegen.builder
    width = value.type.width

    leaves_the_type = builder.icmp_unsigned(">=", count, ir.Constant(count.type, width))

    if count.type.width > width:
        places = builder.trunc(count, value.type)
    elif count.type.width < width:
        places = builder.zext(count, value.type)
    else:
        places = count
    places = builder.and_(places, ir.Constant(value.type, width - 1))

    if op == "<<":
        shifted = builder.shl(value, places)
        emptied = ir.Constant(value.type, 0)
    elif is_unsigned_int(value_type):
        shifted = builder.lshr(value, places)
        emptied = ir.Constant(value.type, 0)
    else:
        shifted = builder.ashr(value, places)
        emptied = builder.ashr(value, ir.Constant(value.type, width - 1))

    return builder.select(leaves_the_type, emptied, shifted)


def emit_xor(codegen: 'LLVMCodegen', lhs: ir.Value, rhs: ir.Value, to_i1: bool) -> ir.Value:
    """`xor` over two bool values: it reads both operands, so it is no control flow."""
    result = codegen.builder.xor(codegen.utils.as_i1(lhs), codegen.utils.as_i1(rhs),
                                 name="xor_result")
    return result if to_i1 else codegen.builder.zext(result, codegen.i8)


