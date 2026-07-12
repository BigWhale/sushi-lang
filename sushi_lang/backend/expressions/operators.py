"""
Operator expression emission for the Sushi language compiler.

This module handles emission of all operator types: unary, binary, arithmetic,
bitwise, comparison, and logical operations with short-circuiting.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH
from sushi_lang.semantics.ast import Expr, Name, UnaryOp, BinaryOp, Borrow
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils
from sushi_lang.backend.utils import require_both_initialized

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_operator(codegen: 'LLVMCodegen', expr: Expr, to_i1: bool) -> ir.Value:
    """Dispatch operator emission to appropriate handler.

    Args:
        codegen: The LLVM codegen instance.
        expr: The operator expression.
        to_i1: Whether to convert result to i1 for boolean contexts.

    Returns:
        The LLVM value representing the operator result.

    Raises:
        TypeError: If expression is not an operator type.
    """
    match expr:
        case UnaryOp():
            return emit_unary_op(codegen, expr, to_i1)
        case BinaryOp():
            return emit_binary_op(codegen, expr, to_i1)
        case _:
            raise_internal_error("CE0099", type=type(expr).__name__)


def emit_float_negation(codegen: 'LLVMCodegen', val: ir.Value) -> ir.Value:
    """Emit floating-point negation using fsub.

    Args:
        codegen: The LLVM codegen instance.
        val: The float or double value to negate.

    Returns:
        The negated floating-point value.
    """
    return codegen.builder.fsub(ir.Constant(val.type, 0.0), val)


def emit_unary_op(codegen: 'LLVMCodegen', expr: UnaryOp, to_i1: bool) -> ir.Value:
    """Emit unary operation (negation, logical not).

    Args:
        codegen: The LLVM codegen instance.
        expr: The unary operation expression.
        to_i1: Whether to convert result to i1.

    Returns:
        The LLVM value representing the unary operation result.

    Raises:
        NotImplementedError: If the unary operator is not supported.
    """
    # Use codegen.expressions for recursive call
    val = codegen.expressions.emit_expr(expr.expr)

    if expr.op == "neg":
        # Handle both integer and float negation
        if isinstance(val.type, (ir.FloatType, ir.DoubleType)):
            return emit_float_negation(codegen, val)
        else:
            # Integer negation at the operand's own width (no i32 squeeze)
            return codegen.builder.sub(ir.Constant(val.type, 0), val)

    if expr.op == "not":
        if to_i1:
            i1v = codegen.utils.as_i1(val)
            return codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1))
        i1v = codegen.utils.as_i1(val)
        return codegen.builder.zext(codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1)), codegen.i8)

    if expr.op == "~":
        # Bitwise NOT (complement) at the operand's own width
        return codegen.builder.not_(val)

    raise NotImplementedError(f"unknown UnaryOp: {expr.op!r}")


def _ensure_i32(codegen: 'LLVMCodegen', val: ir.Value) -> ir.Value:
    """Efficiently convert value to i32, avoiding redundant conversions.

    Checks if value is already i32 before calling conversion function,
    avoiding unnecessary function call overhead.

    Args:
        codegen: The LLVM codegen instance.
        val: The value to convert.

    Returns:
        An i32 value (converted if needed, or original if already i32).
    """
    # Fast path: already i32, return directly
    if isinstance(val.type, ir.IntType) and val.type.width == 32:
        return val
    # Slow path: needs conversion
    return codegen.utils.as_i32(val)


def emit_binary_op(codegen: 'LLVMCodegen', expr: BinaryOp, to_i1: bool) -> ir.Value:
    """Emit binary operation with proper type handling.

    Args:
        codegen: The LLVM codegen instance.
        expr: The binary operation expression.
        to_i1: Whether to convert comparison results to i1.

    Returns:
        The LLVM value representing the binary operation result.
    """
    op = expr.op

    if op in ("and", "or", "xor"):
        return emit_logic(codegen, op, expr.left, expr.right, to_i1=to_i1)

    if op in ("==", "!=", "<", "<=", ">", ">="):
        return emit_comparison(codegen, expr, to_i1)

    # Use codegen.expressions for recursive calls
    if op in ("&", "|", "^", "<<", ">>"):
        left = codegen.expressions.emit_expr(expr.left)
        right = codegen.expressions.emit_expr(expr.right)
        # Infer semantic type for right shift signed/unsigned dispatch
        from .type_utils import infer_expr_semantic_type
        left_type = infer_expr_semantic_type(codegen, expr.left)
        return emit_bitwise(codegen, op, left, right, left_type)

    # Arithmetic operations: preserve operand types (don't force i32)
    left = codegen.expressions.emit_expr(expr.left)
    right = codegen.expressions.emit_expr(expr.right)
    # Infer semantic type for signed/unsigned dispatch of / and %
    from .type_utils import infer_expr_semantic_type
    left_type = infer_expr_semantic_type(codegen, expr.left)
    if left_type is None:
        left_type = infer_expr_semantic_type(codegen, expr.right)
    return emit_arithmetic(codegen, op, left, right, left_type)


def emit_comparison(codegen: 'LLVMCodegen', expr: BinaryOp, to_i1: bool) -> ir.Value:
    """Emit comparison operations with string support.

    Args:
        codegen: The LLVM codegen instance.
        expr: The binary comparison expression.
        to_i1: Whether to return i1 or i8 result.

    Returns:
        The comparison result as i1 or i8.

    Raises:
        NotImplementedError: If string comparison operator is not supported.
    """
    # Use codegen.expressions for recursive calls
    lhs = codegen.expressions.emit_expr(expr.left)
    rhs = codegen.expressions.emit_expr(expr.right)
    op = expr.op

    # String comparisons
    if (codegen.types.is_string_type(lhs.type) and
        codegen.types.is_string_type(rhs.type)):
        if op in ("==", "!="):
            i1v = codegen.runtime.strings.emit_string_comparison(op, lhs, rhs)
            return i1v if to_i1 else codegen.builder.zext(i1v, ir.IntType(INT8_BIT_WIDTH))
        else:
            raise NotImplementedError(f"String comparison '{op}' not yet supported")

    # Floating-point comparisons
    is_float = str(lhs.type) in ('double', 'float')
    if is_float:
        # Use fcmp with ordered comparisons (oeq, one, olt, ole, ogt, oge)
        i1v = codegen.builder.fcmp_ordered(op, lhs, rhs)
        return i1v if to_i1 else codegen.builder.zext(i1v, ir.IntType(INT8_BIT_WIDTH))

    # Integer comparisons at operand width. Pass 2's strict same-type rule
    # (CE2510) guarantees equal widths; the i32 squeeze is kept only as a
    # defensive fallback for mismatched widths (it would truncate i64).
    if (isinstance(lhs.type, ir.IntType) and isinstance(rhs.type, ir.IntType)
            and lhs.type.width == rhs.type.width):
        from .type_utils import infer_expr_semantic_type, is_unsigned_type
        sem = infer_expr_semantic_type(codegen, expr.left)
        if sem is None:
            sem = infer_expr_semantic_type(codegen, expr.right)
        if sem is not None and is_unsigned_type(sem):
            i1v = codegen.builder.icmp_unsigned(op, lhs, rhs)
        else:
            i1v = codegen.builder.icmp_signed(op, lhs, rhs)
        return i1v if to_i1 else codegen.builder.zext(i1v, ir.IntType(INT8_BIT_WIDTH))

    lhs_i32 = _ensure_i32(codegen, lhs)
    rhs_i32 = _ensure_i32(codegen, rhs)
    i1v = codegen.builder.icmp_signed(op, lhs_i32, rhs_i32)
    return i1v if to_i1 else codegen.builder.zext(i1v, ir.IntType(INT8_BIT_WIDTH))


def emit_arithmetic(codegen: 'LLVMCodegen', op: str, left: ir.Value, right: ir.Value, left_type: 'Optional[Type]' = None) -> ir.Value:
    """Emit arithmetic operations on integer or floating-point values.

    Performs compile-time constant folding when both operands are constants.

    For integer division (/) and modulo (%), chooses between signed and unsigned
    LLVM instructions based on operand type:
    - Signed integers (i8, i16, i32, i64): sdiv / srem
    - Unsigned integers (u8, u16, u32, u64): udiv / urem

    The other operators (+, -, *) are sign-agnostic in two's-complement.

    Args:
        codegen: The LLVM codegen instance.
        op: The arithmetic operator (+, -, *, /, %).
        left: Left operand (any numeric type).
        right: Right operand (any numeric type, should match left's type).
        left_type: The semantic type of the left operand (for / and % dispatch).

    Returns:
        The arithmetic result (same type as operands).

    Raises:
        NotImplementedError: If the arithmetic operator is not supported.
    """
    # Constant folding: if both operands are constants, compute at compile time
    if isinstance(left, ir.Constant) and isinstance(right, ir.Constant):
        folded = _fold_arithmetic_constants(op, left, right)
        if folded is not None:
            return folded

    # Check if operands are floating-point types
    is_float = str(left.type) in ('double', 'float')

    if is_float:
        # Floating-point arithmetic operations
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


def _fold_arithmetic_constants(op: str, left: ir.Constant, right: ir.Constant) -> 'Optional[ir.Constant]':
    """Fold arithmetic operations on constant values at compile time.

    Args:
        op: The arithmetic operator (+, -, *, /, %).
        left: Left constant operand.
        right: Right constant operand.

    Note:
        Only sign-agnostic operators (+, -, *) are folded here. Division (/) and
        modulo (%) are sign-dependent and are deferred to instruction selection in
        emit_arithmetic (sdiv/udiv, srem/urem); LLVM constant-folds the result.

    Returns:
        Folded constant result, or None if folding not possible.
    """
    # Only fold integer constants for now (float constant folding is more complex)
    if not isinstance(left.type, ir.IntType) or not isinstance(right.type, ir.IntType):
        return None

    # Extract constant values
    try:
        lval = left.constant
        rval = right.constant
    except (AttributeError, TypeError):
        return None

    # Perform the operation (sign-agnostic operators only)
    result = None
    if op == "+":
        result = lval + rval
    elif op == "-":
        result = lval - rval
    elif op == "*":
        result = lval * rval

    if result is None:
        return None

    # Handle overflow by masking to type width
    width = left.type.width
    mask = (1 << width) - 1
    result = result & mask

    # Handle signed representation
    if result >= (1 << (width - 1)):
        result -= (1 << width)

    return ir.Constant(left.type, result)


def emit_bitwise(codegen: 'LLVMCodegen', op: str, left: ir.Value, right: ir.Value, left_type: 'Optional[Type]' = None) -> ir.Value:
    """Emit bitwise operations on integer values.

    Performs compile-time constant folding when both operands are constants.

    For right shift (>>), chooses between arithmetic and logical shift based on operand type:
    - Signed integers (i8, i16, i32, i64): Arithmetic right shift (ashr) - sign-extends
    - Unsigned integers (u8, u16, u32, u64): Logical right shift (lshr) - zero-fills

    This matches the behavior of Go and Rust.

    Args:
        codegen: The LLVM codegen instance.
        op: The bitwise operator (&, |, ^, <<, >>).
        left: Left operand (preserves original type).
        right: Right operand (preserves original type).
        left_type: The semantic type of the left operand (for right shift dispatch).

    Returns:
        The bitwise operation result (same type as left operand).

    Raises:
        NotImplementedError: If the bitwise operator is not supported.
    """
    # Constant folding: if both operands are constants, compute at compile time
    if isinstance(left, ir.Constant) and isinstance(right, ir.Constant):
        folded = _fold_bitwise_constants(op, left, right)
        if folded is not None:
            return folded

    # Ensure right operand matches left operand's type for shift/bitwise operations
    # LLVM requires both operands to have the same type
    if left.type != right.type:
        if isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType):
            if left.type.width > right.type.width:
                # Zero-extend right to match left
                right = codegen.builder.zext(right, left.type)
            elif left.type.width < right.type.width:
                # Truncate right to match left
                right = codegen.builder.trunc(right, left.type)

    # For right shift, determine whether to use arithmetic or logical shift based on type
    if op == ">>":
        from sushi_lang.semantics.typesys import BuiltinType

        # Determine if the type is unsigned using the semantic type
        is_unsigned = False
        if left_type is not None:
            is_unsigned = left_type in [
                BuiltinType.U8,
                BuiltinType.U16,
                BuiltinType.U32,
                BuiltinType.U64
            ]

        if is_unsigned:
            # Logical right shift (zero-fill) for unsigned types
            return codegen.builder.lshr(left, right)
        else:
            # Arithmetic right shift (sign-extend) for signed types
            return codegen.builder.ashr(left, right)

    # Other bitwise operations
    bitwise_ops = {
        "&": codegen.builder.and_,
        "|": codegen.builder.or_,
        "^": codegen.builder.xor,
        "<<": codegen.builder.shl,
    }

    if op in bitwise_ops:
        return bitwise_ops[op](left, right)

    raise NotImplementedError(f"bitwise op not supported yet: {op!r}")


def _fold_bitwise_constants(op: str, left: ir.Constant, right: ir.Constant) -> 'Optional[ir.Constant]':
    """Fold bitwise operations on constant values at compile time.

    Note:
        Only sign-agnostic operators (&, |, ^, <<) are folded here. Right shift (>>)
        is sign-dependent (arithmetic vs logical) and is deferred to instruction
        selection in emit_bitwise (ashr/lshr); LLVM constant-folds the result.

    Args:
        op: The bitwise operator (&, |, ^, <<).
        left: Left constant operand.
        right: Right constant operand.

    Returns:
        Folded constant result, or None if folding not possible.
    """
    if not isinstance(left.type, ir.IntType) or not isinstance(right.type, ir.IntType):
        return None

    try:
        lval = left.constant
        rval = right.constant
    except (AttributeError, TypeError):
        return None

    width = left.type.width
    mask = (1 << width) - 1

    result = None
    if op == "&":
        result = lval & rval
    elif op == "|":
        result = lval | rval
    elif op == "^":
        result = lval ^ rval
    elif op == "<<":
        result = (lval << rval) & mask

    if result is None:
        return None

    # Mask to type width
    result = result & mask

    return ir.Constant(left.type, result)


def emit_logic(codegen: 'LLVMCodegen', op: str, left_expr: Expr, right_expr: Expr, to_i1: bool = False) -> ir.Value:
    """Emit short-circuit boolean logic for 'and', 'or', and 'xor' operations.

    Creates proper basic block structure for short-circuiting with phi nodes
    to merge the results from different execution paths.

    Args:
        codegen: The LLVM codegen instance.
        op: The logical operator ("and", "or", or "xor").
        left_expr: Left side expression.
        right_expr: Right side expression.
        to_i1: Whether to produce i1 or i8 result.

    Returns:
        The logical operation result as i1 or i8.
    """
    builder, func = require_both_initialized(codegen)

    # For xor, we need to evaluate both sides (no short-circuiting)
    if op == "xor":
        lhs_i1 = codegen.utils.as_i1(codegen.expressions.emit_expr(left_expr, to_i1=True))
        rhs_i1 = codegen.utils.as_i1(codegen.expressions.emit_expr(right_expr, to_i1=True))
        result = codegen.builder.xor(lhs_i1, rhs_i1, name="xor_result")
        return result if to_i1 else codegen.builder.zext(result, codegen.i8)

    # Use codegen.expressions for recursive calls
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

    codegen.builder.position_at_end(rhs_bb)
    rhs_i1 = codegen.utils.as_i1(codegen.expressions.emit_expr(right_expr, to_i1=True))
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


def emit_name(codegen: 'LLVMCodegen', expr: Name, to_i1: bool) -> ir.Value:
    """Emit variable or constant reference.

    First checks for global constants, then string constants, then local variables.
    For reference parameters, automatically dereferences them when used as values.

    Args:
        codegen: The LLVM codegen instance.
        expr: The name expression.
        to_i1: Whether to convert result to i1.

    Returns:
        The loaded variable or constant value.
    """
    # Check for math module constants (PI, E, TAU)
    if expr.id in {'PI', 'E', 'TAU'}:
        from sushi_lang.sushi_stdlib.src import math as math_module
        if math_module.is_builtin_math_constant(expr.id):
            _, value = math_module.get_builtin_math_constant_value(expr.id)
            f64_value = ir.Constant(ir.DoubleType(), value)
            return codegen.utils.as_i1(f64_value) if to_i1 else f64_value

    # Check for global constants (including string constants stored as fat pointer structs)
    if expr.id in codegen.constants:
        global_const = codegen.constants[expr.id]
        v = codegen.builder.load(global_const, name=expr.id)
        return codegen.utils.as_i1(v) if to_i1 else v

    # Try local variables
    try:
        from sushi_lang.backend.expressions import type_utils
        slot = codegen.memory.find_local_slot(expr.id)
        v = type_utils.load_with_reference_handling(codegen, expr.id, slot)
        return codegen.utils.as_i1(v) if to_i1 else v
    except Exception:
        # Not a local: a bare reference to a top-level function is a first-class
        # function value -> a non-capturing fat pointer {thunk, null, null}. The
        # thunk bridges the bare fn into the uniform env-passing indirect ABI.
        llvm_fn = codegen.funcs.get(expr.id)
        if llvm_fn is not None:
            from sushi_lang.backend.runtime import closures
            return closures.materialize_function_ref(codegen, llvm_fn)
        # Variable/constant not found (should be caught in semantic analysis)
        raise_internal_error("CE0055", name=expr.id)


def emit_borrow(codegen: 'LLVMCodegen', expr: Borrow) -> ir.Value:
    """Emit borrow expression (&peek expr or &poke expr) as pointer to expression.

    Supports:
    - Variables: &peek x, &poke x -> slot pointer
    - Member access: &peek obj.field, &poke obj.field -> GEP to field
    - Nested member access: &peek obj.a.b, &poke obj.a.b -> nested GEP

    References are zero-cost abstractions in Sushi. Both &peek (read-only) and
    &poke (read-write) borrows emit identical LLVM IR - they simply return the
    pointer to the memory location. The semantic differences are enforced at
    compile time by the borrow checker.

    The borrow checker (Pass 3 in semantic analysis) has already verified:
    - The borrowed expression is borrowable (variable or member access)
    - &poke borrows are exclusive (only one at a time)
    - &peek borrows allow multiple simultaneous reads
    - Cannot mix &peek and &poke borrows of the same variable
    - Cannot move/rebind/destroy while borrowed
    - Cannot borrow moved variables

    Args:
        codegen: The LLVM codegen instance.
        expr: The Borrow expression containing the expression to borrow.

    Returns:
        Pointer to the expression (LLVM alloca instruction, loaded reference, or GEP).

    Raises:
        RuntimeError: If borrowing an unsupported expression (should be caught by semantic analysis).
    """
    from sushi_lang.semantics.ast import Name, MemberAccess
    from sushi_lang.semantics.typesys import ReferenceType

    if isinstance(expr.expr, Name):
        # Original logic: borrow a variable
        var_name = expr.expr.id
        try:
            slot = codegen.memory.find_local_slot(var_name)

            # Check if this variable is itself a reference parameter
            if hasattr(codegen, 'variable_types') and var_name in codegen.variable_types:
                semantic_type = codegen.variable_types[var_name]
                if isinstance(semantic_type, ReferenceType):
                    # For reference parameters, the slot stores a pointer to the actual variable
                    # We need to load that pointer to get the actual variable's address
                    return codegen.builder.load(slot, name=f"{var_name}_ref_ptr")

            # For regular variables, just return the slot
            return slot  # Return the pointer directly (zero-cost)
        except KeyError:
            raise_internal_error("CE0055", name=var_name)

    elif isinstance(expr.expr, MemberAccess):
        # New logic: borrow a struct field
        return emit_member_access_borrow(codegen, expr.expr)

    else:
        # Should never reach here (borrow checker validates this)
        raise_internal_error("CE0100", expr=type(expr.expr).__name__)


def emit_member_access_borrow(codegen: 'LLVMCodegen', expr) -> ir.Value:
    """Emit borrow of struct field access using GEP.

    Returns a pointer to the field within the struct.

    Example:
        &cfg.port -> GEP(cfg_alloca, [0, port_field_index])

    This function leverages the existing `try_get_struct_alloca()` infrastructure
    which already handles:
    - Regular variables
    - Reference parameters (loads pointer from slot)
    - Nested member access (recursive GEP)

    Args:
        codegen: The LLVM codegen instance.
        expr: The MemberAccess expression.

    Returns:
        Pointer to the field (GEP instruction).

    Raises:
        TypeError: If struct type or field cannot be resolved.
        RuntimeError: If cannot get address of struct.
    """
    from sushi_lang.backend.expressions.structs import infer_struct_type, try_get_struct_alloca

    # Get the struct type and field information
    struct_type = infer_struct_type(codegen, expr.receiver)
    field_index = struct_type.get_field_index(expr.member)

    if field_index is None:
        raise_internal_error("CE0029", struct=struct_type.name, field=expr.member)

    # Get pointer to the struct (either alloca or loaded reference)
    # This function already handles reference parameters correctly
    struct_ptr = try_get_struct_alloca(codegen, expr.receiver)

    if struct_ptr is None:
        raise_internal_error("CE0030")

    # Use GEP to get pointer to the field
    zero = ir.Constant(codegen.types.i32, 0)
    field_idx = ir.Constant(codegen.types.i32, field_index)
    field_ptr = codegen.builder.gep(
        struct_ptr,
        [zero, field_idx],
        name=f"{expr.member}_ptr"
    )

    return field_ptr


def emit_try_expr(codegen: 'LLVMCodegen', expr: 'TryExpr') -> ir.Value:
    """Emit try operator (??) for error propagation with Result<T> or Maybe<T>.

    Reads the AST annotations set during semantic analysis (Pass 2). An unannotated
    TryExpr is a Pass 2 gap, not a user error, and raises CE0124.

    The ?? operator unwraps result-like or maybe-like enums and propagates errors:
    - Result<T>: Ok(value) -> value, Err(...) -> propagate Err
    - Maybe<T>: Some(value) -> value, None() -> propagate as Err

    LLVM IR Structure:
        %result = <evaluate inner expression>
        %tag = extractvalue %result, 0
        %is_success = icmp eq %tag, <success_tag>
        br %is_success, label %continue, label %propagate_err

    propagate_err:
        <RAII cleanup>
        %err_result = <construct Err value>
        ret %err_result

    continue:
        %value = <extract value from success variant>
        <continue execution with %value>

    Args:
        codegen: The LLVM codegen instance.
        expr: The TryExpr containing the enum expression to unwrap.

    Returns:
        The unwrapped value of type T from Ok(value) or Some(value).
    """
    # Pass 2 annotates every TryExpr it validates; the backend does not re-infer types.
    inner_type = expr.inferred_inner_type
    unwrapped_type = expr.inferred_unwrapped_type
    success_tag = expr.inferred_success_tag
    error_type = expr.inferred_error_type
    func_return_type = expr.inferred_func_return_type

    if inner_type is None or unwrapped_type is None or success_tag is None:
        raise_internal_error("CE0124")

    # 1. Emit the inner expression
    result_value = codegen.expressions.emit_expr(expr.expr)

    # 2. Check if success variant (tag matches success_tag)
    is_success = enum_utils.check_enum_variant(
        codegen, result_value, success_tag, signed=True, name="is_success"
    )

    # 3. Extract the unwrapped Ok/Some value from the enum
    unwrapped_value = _extract_variant_from_result(codegen, result_value, unwrapped_type)

    # 4. Extract error value if Result-like (has Err variant)
    error_value = None
    if error_type is not None:
        error_value = _extract_variant_from_result(codegen, result_value, error_type)

    # 5. Create basic blocks for error propagation and continuation
    propagate_block = codegen.func.append_basic_block(name="try_propagate_err")
    continue_block = codegen.func.append_basic_block(name="try_continue")

    # 6. Branch based on enum tag
    codegen.builder.cbranch(is_success, continue_block, propagate_block)

    # 7. Error path: RAII cleanup and early return with Err variant
    codegen.builder.position_at_end(propagate_block)

    from sushi_lang.backend.statements import utils
    utils.emit_scope_cleanup(codegen, cleanup_type='all')

    err_result = _construct_result_err_variant(codegen, func_return_type, error_value)
    codegen.builder.ret(err_result)

    # 8. Success path: continue with unwrapped value
    codegen.builder.position_at_end(continue_block)
    return unwrapped_value


def _extract_variant_from_result(codegen: 'LLVMCodegen', result_value: ir.Value, variant_type: 'Type') -> ir.Value:
    """Extract variant data from Result/Maybe enum value.

    Args:
        codegen: The LLVM codegen instance.
        result_value: The Result/Maybe enum LLVM value.
        variant_type: The semantic type of the variant data to extract.

    Returns:
        The extracted value with the specified type.
    """
    variant_llvm_type = codegen.types.ll_type(variant_type)
    _, extracted_value = codegen.functions._extract_value_from_result_enum(
        result_value,
        variant_llvm_type,
        variant_type
    )
    return extracted_value


def _construct_result_err_variant(codegen: 'LLVMCodegen', return_type, error_value: ir.Value) -> ir.Value:
    """Construct an Err variant enum value for Result<T, E> with actual error data.

    Args:
        codegen: The LLVM codegen instance.
        return_type: The semantic type (ResultType or GenericTypeRef with base_name "Result").
        error_value: The LLVM value containing the error data to pack into Err variant.

    Returns:
        An LLVM value representing the Err variant with error data packed.
    """
    from sushi_lang.backend.generics.result_builder import build_err_from_return_type
    return build_err_from_return_type(codegen, return_type, error_value)
