"""Operator expression emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.integer_ops import EXACT_OPS
from sushi_lang.semantics.overflow_mode import OverflowMode
from sushi_lang.semantics.operators import (
    COMPLEMENT_OP, DIVISION_OPS, DIV_OP, EQUALITY_OPS, EQ_OP, NEG_OP, NE_OP, NOT_OP,
    SHIFT_OPS, SHL_OP, SUB_OP)
from sushi_lang.semantics.type_predicates import is_unsigned_int

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_float_negation(codegen: 'LLVMCodegen', val: ir.Value) -> ir.Value:
    """Emit floating-point negation: `fneg` flips the sign bit, so -0.0 is negative zero."""
    return codegen.builder.fneg(val)


def emit_unary_value(codegen: 'LLVMCodegen', op: str, val: ir.Value, to_i1: bool,
                     operand_type: 'Optional[Type]' = None, checked: bool = False) -> ir.Value:
    """A unary operator over one LLVM value: the SLIR emitter calls it for a `Unary`.
    `checked` is the user mark: an integer negation that the user wrote follows the
    overflow mode."""
    if op == NEG_OP:
        if isinstance(val.type, (ir.FloatType, ir.DoubleType)):
            return emit_float_negation(codegen, val)
        zero = ir.Constant(val.type, 0)
        if _follows(codegen, checked, OverflowMode.TRAP):
            return _emit_trapping(codegen, SUB_OP, zero, val, operand_type)
        return codegen.builder.sub(zero, val)

    if op == NOT_OP:
        if to_i1:
            i1v = codegen.utils.as_i1(val)
            return codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1))
        i1v = codegen.utils.as_i1(val)
        return codegen.builder.zext(codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1)), codegen.i8)

    if op == COMPLEMENT_OP:
        return codegen.builder.not_(val)

    raise NotImplementedError(f"unknown UnaryOp: {op!r}")


def emit_string_relation(codegen: 'LLVMCodegen', op: str, lhs: ir.Value,
                         rhs: ir.Value) -> ir.Value:
    """The i1 answer of a comparison of two strings: `==` and `!=` read the bytes, and the
    order operators read the bytes, then the length."""
    if op in EQUALITY_OPS:
        return codegen.runtime.strings.emit_string_comparison(op, lhs, rhs)
    return codegen.runtime.strings.emit_string_order(op, lhs, rhs)


def emit_contract_relation(codegen: 'LLVMCodegen', op: str, lhs: ir.Value, rhs: ir.Value,
                           ty: 'Type') -> ir.Value:
    """The i1 answer of a comparison of two values of one struct, enum or container type:
    `==` and `!=` read the `Eq` contract, the order operators read the `Ord` contract. A
    value of a type that moves by address may be its address."""
    from sushi_lang.backend.types.contracts import emit_value_compare, emit_value_eq
    if op == EQ_OP:
        return emit_value_eq(codegen, lhs, rhs, ty)
    if op == NE_OP:
        return codegen.builder.not_(emit_value_eq(codegen, lhs, rhs, ty))
    order = emit_value_compare(codegen, lhs, rhs, ty)
    return codegen.builder.icmp_signed(op, order, ir.Constant(order.type, 0))


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


def emit_arithmetic(codegen: 'LLVMCodegen', op: str, left: ir.Value, right: ir.Value,
                    left_type: 'Optional[Type]' = None, checked: bool = False) -> ir.Value:
    """Emit arithmetic operations on integer or floating-point values.

    Two constant operands are emitted as the instruction too. The backend computes no
    integer operator of its own: the constant evaluator is the one compile-time home
    of the semantics, and LLVM folds the instruction at run time's level (#681).

    `checked` is the user mark: an integer operation that the user wrote follows the
    overflow mode (docs/adr/0002-overflow-mode.md). In `trap` mode, `+ - *` stop the
    program with RE2032 on a result that the type cannot hold; SLIR lowering checks `/`
    and `%` before the operation. In `wrap` mode, a signed `/` by -1 is the negation and
    a signed `%` by -1 is 0, so the smallest value over -1 has an answer.
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
        if _follows(codegen, checked, OverflowMode.TRAP) and op in EXACT_OPS:
            return _emit_trapping(codegen, op, left, right, left_type)
        if _follows(codegen, checked, OverflowMode.WRAP) and op in DIVISION_OPS and not unsigned:
            return _emit_wrapping_division(codegen, op, left, right)
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


_OVERFLOW_INTRINSICS = {"+": "add", "-": "sub", "*": "mul"}


def _follows(codegen: 'LLVMCodegen', checked: bool, mode: OverflowMode) -> bool:
    """Whether an operation follows the overflow mode `mode`: the user wrote it
    (`checked`), and the build has that mode."""
    return checked and codegen.overflow is mode


def _emit_trapping(codegen: 'LLVMCodegen', op: str, left: ir.Value, right: ir.Value,
                   ty: 'Optional[Type]') -> ir.Value:
    """`left op right` for `+ - *` through the `with.overflow` intrinsic of the sign of
    `ty`. A result that `ty` cannot hold stops the program with RE2032, which names the
    operator `op` (a negation is `0 - x`, and its text names `-`)."""
    from sushi_lang.semantics.generics.type_display import display_type
    builder = codegen.builder
    signed = not is_unsigned_int(ty)
    kind = _OVERFLOW_INTRINSICS[op]
    name = f"llvm.{'s' if signed else 'u'}{kind}.with.overflow.{left.type.intrinsic_name}"
    intrinsic = codegen.module.globals.get(name)
    if intrinsic is None:
        answer = ir.LiteralStructType([left.type, ir.IntType(1)])
        intrinsic = ir.Function(codegen.module,
                                ir.FunctionType(answer, [left.type, left.type]), name=name)
    pair = builder.call(intrinsic, [left, right])
    trap = codegen.runtime.errors.exit_block(
        builder.function, "RE2032", op=op,
        type=display_type(ty) if ty is not None else str(left.type))
    held = builder.append_basic_block(name="no_overflow")
    builder.cbranch(builder.extract_value(pair, 1), trap, held)
    builder.position_at_end(held)
    return builder.extract_value(pair, 0)


def _emit_wrapping_division(codegen: 'LLVMCodegen', op: str, left: ir.Value,
                            right: ir.Value) -> ir.Value:
    """A signed `/` or `%` in `wrap` mode. A divisor of -1 gives `0 - left` or 0 with no
    `sdiv`, because the machine instruction has no answer for the smallest value over -1."""
    builder = codegen.builder
    minus_one = builder.icmp_signed("==", right, ir.Constant(right.type, -1))
    divisor = builder.select(minus_one, ir.Constant(right.type, 1), right)
    if op == DIV_OP:
        return builder.select(minus_one, builder.sub(ir.Constant(left.type, 0), left),
                              builder.sdiv(left, divisor))
    return builder.select(minus_one, ir.Constant(left.type, 0), builder.srem(left, divisor))


def emit_bitwise(codegen: 'LLVMCodegen', op: str, left: ir.Value, right: ir.Value,
                 left_type: 'Optional[Type]' = None, right_type: 'Optional[Type]' = None,
                 checked: bool = False) -> ir.Value:
    """Emit bitwise operations on integer values. Two constants are an instruction too.
    `checked` is the user mark: a shift that the user wrote follows the overflow mode."""
    if (op in SHIFT_OPS
            and isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType)):
        return _emit_shift(codegen, op, left, right, left_type, right_type, checked)

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
                value_type: 'Optional[Type]', count_type: 'Optional[Type]',
                checked: bool) -> ir.Value:
    """A shift; an out-of-range count follows the overflow mode (docs/design/ir.md 8.7)."""
    builder = codegen.builder
    width = value.type.width

    if _follows(codegen, checked, OverflowMode.TRAP):
        _trap_on_shift_count(codegen, op, count, width, value_type, count_type)

    if count.type.width > width:
        places = builder.trunc(count, value.type)
    elif count.type.width < width:
        places = builder.zext(count, value.type)
    else:
        places = count
    places = builder.and_(places, ir.Constant(value.type, width - 1))

    if op == SHL_OP:
        return builder.shl(value, places)
    if is_unsigned_int(value_type):
        return builder.lshr(value, places)
    return builder.ashr(value, places)


def _trap_on_shift_count(codegen: 'LLVMCodegen', op: str, count: ir.Value, width: int,
                         value_type: 'Optional[Type]', count_type: 'Optional[Type]') -> None:
    """Stop the program with RE2034 when `count` is out of `0..width`."""
    from sushi_lang.semantics.generics.type_display import display_type
    builder = codegen.builder
    high = builder.lshr(count, ir.Constant(count.type, width.bit_length() - 1))
    fail = builder.append_basic_block(name="shift_count_out_of_range")
    held = builder.append_basic_block(name="shift_count_in_range")
    switch = builder.switch(high, fail)
    switch.add_case(ir.Constant(count.type, 0), held)
    builder.position_at_end(fail)
    type_name = display_type(value_type) if value_type is not None else f"i{width}"
    codegen.runtime.errors.emit_runtime_error_with_values(
        "RE2034", _count_as_i64(codegen, count, count_type),
        *_text_operand(codegen, op), *_text_operand(codegen, type_name))
    builder.unreachable()
    builder.position_at_end(held)


def _count_as_i64(codegen: 'LLVMCodegen', count: ir.Value,
                  count_type: 'Optional[Type]') -> ir.Value:
    """The count of a shift as the i64 of `%lld`. A u64 above the i64 maximum is the i64
    maximum."""
    builder = codegen.builder
    i64 = ir.IntType(64)
    unsigned = is_unsigned_int(count_type)
    if count.type.width < 64:
        return builder.zext(count, i64) if unsigned else builder.sext(count, i64)
    if not unsigned:
        return count
    negative = builder.icmp_signed("<", count, ir.Constant(i64, 0))
    return builder.select(negative, ir.Constant(i64, (1 << 63) - 1), count)


def _text_operand(codegen: 'LLVMCodegen', text: str) -> tuple[ir.Value, ir.Value]:
    """The two operands of `%.*s` for a text that the compiler knows: its size, then its
    bytes."""
    data = codegen.string_manager.get_or_create_raw(text)
    pointer = codegen.builder.gep(data, [ir.Constant(codegen.i32, 0)] * 2)
    return ir.Constant(codegen.i32, len(text.encode("utf-8"))), pointer


def emit_xor(codegen: 'LLVMCodegen', lhs: ir.Value, rhs: ir.Value, to_i1: bool) -> ir.Value:
    """`xor` over two bool values: it reads both operands, so it is no control flow."""
    result = codegen.builder.xor(codegen.utils.as_i1(lhs), codegen.utils.as_i1(rhs),
                                 name="xor_result")
    return result if to_i1 else codegen.builder.zext(result, codegen.i8)


