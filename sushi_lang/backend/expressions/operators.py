"""Operator expression emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH
from sushi_lang.semantics.ast import Expr, UnaryOp, BinaryOp
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_both_initialized

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_operator(codegen: 'LLVMCodegen', expr: Expr, to_i1: bool) -> ir.Value:
    """Dispatch operator emission to appropriate handler."""
    match expr:
        case UnaryOp():
            return emit_unary_op(codegen, expr, to_i1)
        case BinaryOp():
            return emit_binary_op(codegen, expr, to_i1)
        case _:
            raise_internal_error("CE0099", type=type(expr).__name__)


def emit_float_negation(codegen: 'LLVMCodegen', val: ir.Value) -> ir.Value:
    """Emit floating-point negation using fsub."""
    return codegen.builder.fsub(ir.Constant(val.type, 0.0), val)


def emit_unary_op(codegen: 'LLVMCodegen', expr: UnaryOp, to_i1: bool) -> ir.Value:
    """Emit unary operation (negation, logical not)."""
    val = codegen.expressions.emit_expr(expr.expr)

    if expr.op == "neg":
        if isinstance(val.type, (ir.FloatType, ir.DoubleType)):
            return emit_float_negation(codegen, val)
        else:
            return codegen.builder.sub(ir.Constant(val.type, 0), val)

    if expr.op == "not":
        if to_i1:
            i1v = codegen.utils.as_i1(val)
            return codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1))
        i1v = codegen.utils.as_i1(val)
        return codegen.builder.zext(codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1)), codegen.i8)

    if expr.op == "~":
        return codegen.builder.not_(val)

    raise NotImplementedError(f"unknown UnaryOp: {expr.op!r}")


def emit_binary_op(codegen: 'LLVMCodegen', expr: BinaryOp, to_i1: bool) -> ir.Value:
    """Emit binary operation with proper type handling."""
    op = expr.op

    if op in ("and", "or", "xor"):
        return emit_logic(codegen, op, expr.left, expr.right, to_i1=to_i1)

    if op in ("==", "!=", "<", "<=", ">", ">="):
        return emit_comparison(codegen, expr, to_i1)

    if op in ("&", "|", "^", "<<", ">>"):
        left = codegen.expressions.emit_expr(expr.left)
        right = codegen.expressions.emit_expr(expr.right)
        from .type_utils import infer_expr_semantic_type
        left_type = infer_expr_semantic_type(codegen, expr.left)
        return emit_bitwise(codegen, op, left, right, left_type)

    left = codegen.expressions.emit_expr(expr.left)
    right = codegen.expressions.emit_expr(expr.right)
    from .type_utils import infer_expr_semantic_type
    left_type = infer_expr_semantic_type(codegen, expr.left)
    if left_type is None:
        left_type = infer_expr_semantic_type(codegen, expr.right)
    return emit_arithmetic(codegen, op, left, right, left_type)


def emit_comparison(codegen: 'LLVMCodegen', expr: BinaryOp, to_i1: bool) -> ir.Value:
    """Emit comparison operations with string support."""
    lhs = codegen.expressions.emit_expr(expr.left)
    rhs = codegen.expressions.emit_expr(expr.right)
    op = expr.op

    if (codegen.types.is_string_type(lhs.type) and
        codegen.types.is_string_type(rhs.type)):
        if op in ("==", "!="):
            i1v = codegen.runtime.strings.emit_string_comparison(op, lhs, rhs)
        else:
            i1v = codegen.runtime.strings.emit_string_order(op, lhs, rhs)
        return i1v if to_i1 else codegen.builder.zext(i1v, ir.IntType(INT8_BIT_WIDTH))

    if isinstance(lhs.type, (ir.FloatType, ir.DoubleType)):
        i1v = codegen.builder.fcmp_ordered(op, lhs, rhs)
        return i1v if to_i1 else codegen.builder.zext(i1v, ir.IntType(INT8_BIT_WIDTH))

    for operand in (lhs, rhs):
        if not isinstance(operand.type, ir.IntType):
            raise_internal_error("CE0017", src=str(operand.type), dst="an integer comparison operand")
    _require_one_width(op, lhs, rhs)

    from .type_utils import infer_expr_semantic_type, is_unsigned_type
    sem = infer_expr_semantic_type(codegen, expr.left)
    if sem is None:
        sem = infer_expr_semantic_type(codegen, expr.right)
    if sem is not None and is_unsigned_type(sem):
        i1v = codegen.builder.icmp_unsigned(op, lhs, rhs)
    else:
        i1v = codegen.builder.icmp_signed(op, lhs, rhs)
    return i1v if to_i1 else codegen.builder.zext(i1v, ir.IntType(INT8_BIT_WIDTH))


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
        from .type_utils import is_unsigned_type
        unsigned = is_unsigned_type(left_type)
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
    from .type_utils import is_unsigned_type

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
    elif is_unsigned_type(value_type):
        shifted = builder.lshr(value, places)
        emptied = ir.Constant(value.type, 0)
    else:
        shifted = builder.ashr(value, places)
        emptied = builder.ashr(value, ir.Constant(value.type, width - 1))

    return builder.select(leaves_the_type, emptied, shifted)


def emit_logic(codegen: 'LLVMCodegen', op: str, left_expr: Expr, right_expr: Expr, to_i1: bool = False) -> ir.Value:
    """Emit short-circuit boolean logic for 'and', 'or', and 'xor' operations."""
    builder, func = require_both_initialized(codegen)

    if op == "xor":
        lhs_i1 = codegen.utils.as_i1(codegen.expressions.emit_expr(left_expr, to_i1=True))
        rhs_i1 = codegen.utils.as_i1(codegen.expressions.emit_expr(right_expr, to_i1=True))
        result = codegen.builder.xor(lhs_i1, rhs_i1, name="xor_result")
        return result if to_i1 else codegen.builder.zext(result, codegen.i8)

    lhs_i1 = codegen.utils.as_i1(codegen.expressions.emit_expr(left_expr, to_i1=True))
    lhs_branch_bb = codegen.builder.block

    rhs_bb = codegen.func.append_basic_block(name=f"logic.{op}.rhs")
    end_bb = codegen.func.append_basic_block(name=f"logic.{op}.end")

    if op == "and":
        codegen.builder.cbranch(lhs_i1, rhs_bb, end_bb)
        lhs_false_pred = lhs_branch_bb
    else:  # "or"
        codegen.builder.cbranch(lhs_i1, end_bb, rhs_bb)
        lhs_true_pred = lhs_branch_bb

    # The right operand is evaluated on ONE path, so an owning temporary it builds gets a
    # scope of its own and is freed inside that block. Registered in the enclosing scope,
    # the free also ran on the short-circuit path, where the slot was never stored (#497).
    codegen.builder.position_at_end(rhs_bb)
    codegen.memory.push_scope()
    rhs_i1 = codegen.utils.as_i1(codegen.expressions.emit_expr(right_expr, to_i1=True))
    codegen.memory.pop_scope()
    rhs_pred = codegen.builder.block
    codegen.builder.branch(end_bb)

    codegen.builder.position_at_end(end_bb)
    phi = codegen.builder.phi(codegen.i1)

    if op == "and":
        phi.add_incoming(ir.Constant(codegen.i1, 0), lhs_false_pred)
        phi.add_incoming(rhs_i1, rhs_pred)
    else:  # "or"
        phi.add_incoming(ir.Constant(codegen.i1, 1), lhs_true_pred)
        phi.add_incoming(rhs_i1, rhs_pred)

    return phi if to_i1 else codegen.builder.zext(phi, codegen.i8)

