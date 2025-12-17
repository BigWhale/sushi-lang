"""
Operator expression emission for the Sushi language compiler.

This module handles emission of all operator types: unary, binary, arithmetic,
bitwise, comparison, and logical operations with short-circuiting.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from semantics.ast import Expr, Name, UnaryOp, BinaryOp, Borrow
from internals.errors import raise_internal_error
from backend import enum_utils
from backend.utils import require_builder, require_both_initialized

if TYPE_CHECKING:
    from backend.interfaces import CodegenProtocol


def emit_operator(codegen: 'CodegenProtocol', expr: Expr, to_i1: bool) -> ir.Value:
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


def emit_float_negation(codegen: 'CodegenProtocol', val: ir.Value) -> ir.Value:
    """Emit floating-point negation using fsub.

    Args:
        codegen: The LLVM codegen instance.
        val: The float or double value to negate.

    Returns:
        The negated floating-point value.
    """
    return codegen.builder.fsub(ir.Constant(val.type, 0.0), val)


def emit_unary_op(codegen: 'CodegenProtocol', expr: UnaryOp, to_i1: bool) -> ir.Value:
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
            # Integer negation
            v32 = codegen.utils.as_i32(val)
            return codegen.builder.sub(ir.Constant(codegen.i32, 0), v32)

    if expr.op == "not":
        if to_i1:
            i1v = codegen.utils.as_i1(val)
            return codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1))
        i1v = codegen.utils.as_i1(val)
        return codegen.builder.zext(codegen.builder.xor(i1v, ir.Constant(codegen.i1, 1)), codegen.i8)

    if expr.op == "~":
        # Bitwise NOT (complement)
        v32 = codegen.utils.as_i32(val)
        return codegen.builder.not_(v32)

    raise NotImplementedError(f"unknown UnaryOp: {expr.op!r}")


def _ensure_i32(codegen: 'CodegenProtocol', val: ir.Value) -> ir.Value:
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


def emit_binary_op(codegen: 'CodegenProtocol', expr: BinaryOp, to_i1: bool) -> ir.Value:
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
    return emit_arithmetic(codegen, op, left, right)


def emit_comparison(codegen: 'CodegenProtocol', expr: BinaryOp, to_i1: bool) -> ir.Value:
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

    # Integer comparisons
    lhs_i32 = _ensure_i32(codegen, lhs)
    rhs_i32 = _ensure_i32(codegen, rhs)
    i1v = codegen.builder.icmp_signed(op, lhs_i32, rhs_i32)
    return i1v if to_i1 else codegen.builder.zext(i1v, ir.IntType(INT8_BIT_WIDTH))


def emit_arithmetic(codegen: 'CodegenProtocol', op: str, left: ir.Value, right: ir.Value) -> ir.Value:
    """Emit arithmetic operations on integer or floating-point values.

    Performs compile-time constant folding when both operands are constants.

    Args:
        codegen: The LLVM codegen instance.
        op: The arithmetic operator (+, -, *, /, %).
        left: Left operand (any numeric type).
        right: Right operand (any numeric type, should match left's type).

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
        # Integer arithmetic operations
        int_ops = {
            "+": codegen.builder.add,
            "-": codegen.builder.sub,
            "*": codegen.builder.mul,
            "/": codegen.builder.sdiv,
            "%": codegen.builder.srem,
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

    # Perform the operation
    result = None
    if op == "+":
        result = lval + rval
    elif op == "-":
        result = lval - rval
    elif op == "*":
        result = lval * rval
    elif op == "/" and rval != 0:
        # Integer division (signed)
        result = int(lval / rval) if lval * rval >= 0 else -(-lval // rval)
    elif op == "%" and rval != 0:
        result = lval % rval

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


def emit_bitwise(codegen: 'CodegenProtocol', op: str, left: ir.Value, right: ir.Value, left_type: 'Optional[Type]' = None) -> ir.Value:
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
        folded = _fold_bitwise_constants(op, left, right, left_type)
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
        from semantics.typesys import BuiltinType

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


def _fold_bitwise_constants(op: str, left: ir.Constant, right: ir.Constant, left_type: 'Optional[Type]' = None) -> 'Optional[ir.Constant]':
    """Fold bitwise operations on constant values at compile time.

    Args:
        op: The bitwise operator (&, |, ^, <<, >>).
        left: Left constant operand.
        right: Right constant operand.
        left_type: The semantic type of the left operand (for right shift dispatch).

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
    elif op == ">>":
        from semantics.typesys import BuiltinType
        is_unsigned = left_type in [BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64] if left_type else False
        if is_unsigned:
            # Logical right shift
            result = (lval & mask) >> rval
        else:
            # Arithmetic right shift (sign-extend)
            if lval >= (1 << (width - 1)):
                lval -= (1 << width)
            result = lval >> rval

    if result is None:
        return None

    # Mask to type width
    result = result & mask

    return ir.Constant(left.type, result)


def emit_logic(codegen: 'CodegenProtocol', op: str, left_expr: Expr, right_expr: Expr, to_i1: bool = False) -> ir.Value:
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


def emit_name(codegen: 'CodegenProtocol', expr: Name, to_i1: bool) -> ir.Value:
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
        from stdlib.src import math as math_module
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
        from backend.expressions import type_utils
        slot = codegen.memory.find_local_slot(expr.id)
        v = type_utils.load_with_reference_handling(codegen, expr.id, slot)
        return codegen.utils.as_i1(v) if to_i1 else v
    except:
        # Variable/constant not found (should be caught in semantic analysis)
        raise_internal_error("CE0055", name=expr.id)


def emit_borrow(codegen: 'CodegenProtocol', expr: Borrow) -> ir.Value:
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
    from semantics.ast import Name, MemberAccess
    from semantics.typesys import ReferenceType

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


def emit_member_access_borrow(codegen: 'CodegenProtocol', expr) -> ir.Value:
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
    from backend.expressions.structs import infer_struct_type, try_get_struct_alloca

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


def emit_try_expr(codegen: 'CodegenProtocol', expr: 'TryExpr') -> ir.Value:
    """Emit try operator (??) for error propagation with Result<T> or Maybe<T>.

    Uses AST annotations set during semantic analysis (Pass 2) when available.
    Falls back to backend inference for stdlib functions which aren't annotated
    during semantic analysis.

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
    from semantics.typesys import EnumType, ResultType

    # Try to use AST annotations from semantic analysis
    inner_type = expr.inferred_inner_type
    unwrapped_type = expr.inferred_unwrapped_type
    success_tag = expr.inferred_success_tag
    error_type = expr.inferred_error_type
    func_return_type = expr.inferred_func_return_type

    # Fall back to backend inference if annotations missing (stdlib functions)
    if inner_type is None or unwrapped_type is None or success_tag is None:
        inner_type = _infer_try_expr_type(codegen, expr.expr)

        if not isinstance(inner_type, EnumType):
            raise_internal_error("CE0039", type=str(inner_type))

        success_tag, unwrapped_type = _extract_success_variant_info(inner_type)

        err_variant = inner_type.get_variant("Err")
        if err_variant is not None:
            _, error_type = _extract_error_variant_info(inner_type)

        # Construct func_return_type from current function
        func_return_type = codegen.current_function_ast.ret
        from semantics.generics.types import GenericTypeRef
        from semantics.type_resolution import TypeResolver

        if not isinstance(func_return_type, ResultType) and not (isinstance(func_return_type, GenericTypeRef) and func_return_type.base_name == "Result"):
            if codegen.current_function_ast.err_type is not None:
                resolver = TypeResolver(codegen.struct_table.by_name, codegen.enum_table.by_name)
                err_type = resolver.resolve(codegen.current_function_ast.err_type)
            else:
                err_type = codegen.enum_table.by_name.get("StdError")
                if err_type is None:
                    raise_internal_error("CE0040", variant="Err",
                        type="StdError enum not found (implicit Result wrapping requires StdError)")
            func_return_type = ResultType(ok_type=func_return_type, err_type=err_type)
        elif isinstance(func_return_type, GenericTypeRef) and func_return_type.base_name == "Result":
            resolver = TypeResolver(codegen.struct_table.by_name, codegen.enum_table.by_name)
            func_return_type = resolver.resolve(func_return_type)

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

    from backend.statements import utils
    utils.emit_scope_cleanup(codegen, cleanup_type='all')

    err_result = _construct_result_err_variant(codegen, func_return_type, error_value)
    codegen.builder.ret(err_result)

    # 8. Success path: continue with unwrapped value
    codegen.builder.position_at_end(continue_block)
    return unwrapped_value


def _extract_variant_from_result(codegen: 'CodegenProtocol', result_value: ir.Value, variant_type: 'Type') -> ir.Value:
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


def _construct_result_err_variant(codegen: 'CodegenProtocol', return_type, error_value: ir.Value) -> ir.Value:
    """Construct an Err variant enum value for Result<T, E> with actual error data.

    Delegates to ResultBuilder for centralized Result construction.

    Args:
        codegen: The LLVM codegen instance.
        return_type: The semantic type (ResultType or GenericTypeRef with base_name "Result").
        error_value: The LLVM value containing the error data to pack into Err variant.

    Returns:
        An LLVM value representing the Err variant with error data packed.
    """
    from backend.generics.result_builder import ResultBuilder
    builder = ResultBuilder(codegen.enum_table)
    return builder.build_err_from_return_type(codegen, return_type, error_value)


def _infer_call_return_type(codegen: 'CodegenProtocol', call_expr: 'Call') -> 'EnumType':
    """Infer the return type of a Call expression (function call).

    Used by pattern matching to determine the enum type when matching on function
    call results directly (e.g., `match returns_ok(): ...`).

    Args:
        codegen: The LLVM codegen instance.
        call_expr: The Call expression.

    Returns:
        The EnumType returned by the function.

    Raises:
        RuntimeError: If function or return type cannot be determined.
    """
    from semantics.ast import Call
    from semantics.typesys import ResultType

    func_name = call_expr.callee.id

    # Special case: open() returns FileResult directly
    if func_name == "open":
        inner_type = codegen.enum_table.by_name.get("FileResult")
        if not inner_type:
            raise_internal_error("CE0033", name="FileResult")
        return inner_type

    # Check if this is a stdlib function with a known return type (check FIRST)
    stdlib_return_type = _get_stdlib_function_return_type(codegen, func_name)
    if stdlib_return_type is not None:
        return stdlib_return_type

    # Regular functions return Result<T>
    if func_name not in codegen.function_return_types:
        raise_internal_error("CE0055", name=func_name)

    result_type_obj = codegen.function_return_types[func_name]

    # Handle GenericTypeRef("Result") - resolve to ResultType then to EnumType
    from semantics.generics.types import GenericTypeRef
    from semantics.type_resolution import TypeResolver
    if isinstance(result_type_obj, GenericTypeRef) and result_type_obj.base_name == "Result":
        # Function explicitly declares Result<T, E>
        resolver = TypeResolver(codegen.struct_table.by_name, codegen.enum_table.by_name)
        resolved = resolver.resolve(result_type_obj)
        if isinstance(resolved, ResultType):
            from backend.generics.results import ensure_result_type_in_table
            result_enum = ensure_result_type_in_table(
                codegen.enum_table,
                resolved.ok_type,
                resolved.err_type
            )
            if not result_enum:
                raise_internal_error("CE0091", type=str(resolved))
            return result_enum
        else:
            raise_internal_error("CE0091", type=str(result_type_obj))

    # Handle ResultType (implicit Result wrapping)
    if isinstance(result_type_obj, ResultType):
        from backend.generics.results import ensure_result_type_in_table
        result_enum = ensure_result_type_in_table(
            codegen.enum_table,
            result_type_obj.ok_type,
            result_type_obj.err_type
        )
        if not result_enum:
            raise_internal_error("CE0091", type=str(result_type_obj))
        return result_enum

    # Old path: primitive return types (shouldn't reach here anymore)
    ret_type = result_type_obj
    result_enum_name = f"Result<{ret_type}>"
    if result_enum_name not in codegen.enum_table.by_name:
        raise_internal_error("CE0091", type=result_enum_name)
    return codegen.enum_table.by_name[result_enum_name]


def _infer_try_expr_type(codegen: 'CodegenProtocol', expr: 'Expr') -> 'EnumType':
    """Infer the enum type of an expression for the try operator (??).

    Handles Call (function calls), DotCall (method calls), and Name (variables).

    Args:
        codegen: The LLVM codegen instance.
        expr: The expression to infer type for.

    Returns:
        The EnumType of the expression.

    Raises:
        RuntimeError: If type cannot be inferred.
    """
    from semantics.ast import Call, DotCall, Name
    from semantics.typesys import EnumType, ResultType

    if isinstance(expr, Call):
        return _infer_call_return_type(codegen, expr)
    elif isinstance(expr, DotCall):
        return _infer_dotcall_return_type(codegen, expr)
    elif isinstance(expr, Name):
        # For variables, look up in memory
        inner_type = codegen.memory.find_semantic_type(expr.id)
        if not isinstance(inner_type, EnumType):
            raise_internal_error("CE0038", var=expr.id)
        return inner_type
    else:
        raise_internal_error("CE0100", expr=type(expr).__name__)


def _infer_dotcall_return_type(codegen: 'CodegenProtocol', dotcall_expr: 'DotCall') -> 'EnumType':
    """Infer the return type of a DotCall expression (method call).

    This is used by emit_try_expr to determine the enum type returned by
    extension methods like string.find() which returns Maybe<i32>.

    Args:
        codegen: The LLVM codegen instance.
        dotcall_expr: The DotCall expression.

    Returns:
        The EnumType returned by the method.

    Raises:
        RuntimeError: If the method or its return type cannot be determined.
    """
    from semantics.ast import DotCall, Name
    from semantics.typesys import EnumType, DynamicArrayType, ArrayType, ReferenceType, BuiltinType

    # Check if the type checker already inferred the return type
    if hasattr(dotcall_expr, 'inferred_return_type') and dotcall_expr.inferred_return_type is not None:
        return dotcall_expr.inferred_return_type

    method_name = dotcall_expr.method

    # Handle string.find() -> Maybe<i32>
    if method_name == "find":
        maybe_i32_name = "Maybe<i32>"
        if maybe_i32_name in codegen.enum_table.by_name:
            return codegen.enum_table.by_name[maybe_i32_name]
        else:
            raise_internal_error("CE0047", type="i32")

    # Array/List/HashMap .get() returns Maybe<T>
    if method_name == "get":
        # Get receiver type
        receiver_type = None
        if isinstance(dotcall_expr.receiver, Name):
            receiver_name = dotcall_expr.receiver.id
            receiver_type = codegen.memory.find_semantic_type(receiver_name)

        if isinstance(receiver_type, ReferenceType):
            receiver_type = receiver_type.referenced_type

        element_type = None
        if isinstance(receiver_type, DynamicArrayType):
            element_type = receiver_type.base_type
        elif isinstance(receiver_type, ArrayType):
            element_type = receiver_type.element_type
        elif hasattr(receiver_type, 'name') and receiver_type.name.startswith('List<'):
            import re
            match = re.match(r'List<(.+)>', receiver_type.name)
            if match:
                type_str = match.group(1)
                builtin_map = {
                    'i8': BuiltinType.I8, 'i16': BuiltinType.I16, 'i32': BuiltinType.I32, 'i64': BuiltinType.I64,
                    'u8': BuiltinType.U8, 'u16': BuiltinType.U16, 'u32': BuiltinType.U32, 'u64': BuiltinType.U64,
                    'f32': BuiltinType.F32, 'f64': BuiltinType.F64,
                    'bool': BuiltinType.BOOL, 'string': BuiltinType.STRING,
                }
                element_type = builtin_map.get(type_str)
                if element_type is None and type_str in codegen.struct_table.by_name:
                    element_type = codegen.struct_table.by_name[type_str]

        if element_type is not None:
            maybe_type_name = f"Maybe<{element_type}>"
            if maybe_type_name in codegen.enum_table.by_name:
                return codegen.enum_table.by_name[maybe_type_name]
            else:
                raise_internal_error("CE0047", type=str(element_type))

    raise_internal_error("CE0063", method=method_name)


def _extract_success_variant_info(inner_type: 'EnumType') -> tuple[int, 'Type']:
    """Extract success variant tag and unwrapped type from Result-like or Maybe-like enum.

    Args:
        inner_type: The enum type (must be Result-like or Maybe-like).

    Returns:
        Tuple of (success_tag, unwrapped_type).

    Raises:
        RuntimeError: If enum is not Result-like or Maybe-like, or malformed.
    """
    # Check for Result-like pattern: Ok(value) and Err(...)
    ok_variant = inner_type.get_variant("Ok")
    err_variant = inner_type.get_variant("Err")
    is_result_like = ok_variant is not None and err_variant is not None

    # Check for Maybe-like pattern: Some(value) and None()
    some_variant = inner_type.get_variant("Some")
    none_variant = inner_type.get_variant("None")
    is_maybe_like = some_variant is not None and none_variant is not None

    if not is_result_like and not is_maybe_like:
        raise_internal_error("CE0039", type=inner_type.name)

    if is_result_like:
        success_variant = ok_variant
        success_tag = inner_type.get_variant_index("Ok")
    else:  # is_maybe_like
        success_variant = some_variant
        success_tag = inner_type.get_variant_index("Some")

    if not success_variant.associated_types:
        raise_internal_error("CE0037", enum=inner_type.name, reason="success variant has no associated type")

    unwrapped_type = success_variant.associated_types[0]
    return success_tag, unwrapped_type


def _extract_error_variant_info(inner_type: 'EnumType') -> tuple[int, 'Type']:
    """Extract error variant tag and error type from Result-like enum.

    Args:
        inner_type: The enum type (must be Result-like with Err variant).

    Returns:
        Tuple of (error_tag, error_type) where error_type is E from Result<T, E>.

    Raises:
        RuntimeError: If Err variant missing or has no associated type.
    """
    err_variant = inner_type.get_variant("Err")
    if err_variant is None:
        raise_internal_error("CE0037", enum=inner_type.name, reason="Err variant not found")

    if not err_variant.associated_types:
        raise_internal_error("CE0037", enum=inner_type.name, reason="Err variant has no associated type")

    error_tag = inner_type.get_variant_index("Err")
    error_type = err_variant.associated_types[0]
    return error_tag, error_type


def _get_stdlib_function_return_type(codegen: 'CodegenProtocol', func_name: str) -> 'EnumType | None':
    """Get the return type for a stdlib function.

    Uses the function table registry to determine return type.

    Args:
        codegen: Code generator instance
        func_name: Name of the function

    Returns:
        EnumType for Result<T> or None if not a stdlib function
    """
    from semantics.typesys import ResultType, UnknownType
    from backend.generics.results import ensure_result_type_in_table
    from semantics.type_resolution import TypeResolver

    func_table = codegen.func_table
    possible_modules = ["time", "sys/env", "math", "io/files"]

    for module_path in possible_modules:
        stdlib_func = func_table.lookup_stdlib_function(module_path, func_name)
        if stdlib_func is not None:
            if stdlib_func.is_constant:
                return None

            return_type = stdlib_func.get_return_type()

            if isinstance(return_type, ResultType):
                ok_type = return_type.ok_type
                err_type = return_type.err_type

                resolver = TypeResolver(codegen.struct_table.by_name, codegen.enum_table.by_name)
                if isinstance(ok_type, UnknownType):
                    ok_type = resolver.resolve(ok_type)
                if isinstance(err_type, UnknownType):
                    err_type = resolver.resolve(err_type)

                result_enum = ensure_result_type_in_table(
                    codegen.enum_table,
                    ok_type,
                    err_type
                )
                if result_enum:
                    return result_enum
                raise_internal_error("CE0091", type=str(return_type))
            else:
                raise_internal_error("CE0091", type=f"Result<{return_type}> (missing error type)")

    return None
