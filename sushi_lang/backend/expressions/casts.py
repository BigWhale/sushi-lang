"""
Type casting operations for the Sushi language compiler.

This module handles explicit type casting between numeric types following Rust-style
explicit casting semantics. Supports integer-to-integer, integer-to-float, float-to-integer,
and float-to-float conversions with appropriate extension, truncation, or precision changes.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import CastExpr, IntLit, UnaryOp
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def classify_type_for_cast(llvm_type: ir.Type) -> str:
    """Classify LLVM type into category for cast operation dispatch.

    Args:
        llvm_type: The LLVM type to classify.

    Returns:
        Type category: 'int', 'float', or 'unknown'.
    """
    if isinstance(llvm_type, ir.IntType):
        return 'int'
    elif isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
        return 'float'
    else:
        return 'unknown'


def cast_int_to_int(codegen: 'LLVMCodegen', source: ir.Value, target_type: ir.IntType,
                    source_unsigned: bool = False) -> ir.Value:
    """Cast between integer types with appropriate extension or truncation.

    Widening uses zero-extension for an unsigned source and sign-extension for a
    signed source; narrowing always truncates.

    Args:
        codegen: The LLVM codegen instance.
        source: The source integer value.
        target_type: The target integer type.
        source_unsigned: Whether the source semantic type is unsigned.

    Returns:
        The casted value.
    """
    source_width = source.type.width
    target_width = target_type.width

    if source_width == target_width:
        return source
    elif source_width < target_width:
        # Zero-extend an unsigned source, sign-extend a signed source.
        if source_unsigned:
            return codegen.builder.zext(source, target_type)
        return codegen.builder.sext(source, target_type)
    else:
        # Truncate for larger to smaller widths
        return codegen.builder.trunc(source, target_type)


def cast_int_to_float(codegen: 'LLVMCodegen', source: ir.Value, target_type: ir.Type,
                      source_unsigned: bool = False) -> ir.Value:
    """Cast integer to floating-point type.

    Args:
        codegen: The LLVM codegen instance.
        source: The source integer value.
        target_type: The target float/double type.
        source_unsigned: Whether the source semantic type is unsigned.

    Returns:
        The casted value.
    """
    if source_unsigned:
        return codegen.builder.uitofp(source, target_type)
    return codegen.builder.sitofp(source, target_type)


def cast_float_to_int(codegen: 'LLVMCodegen', source: ir.Value, target_type: ir.IntType,
                      target_unsigned: bool = False) -> ir.Value:
    """Cast floating-point to integer type (truncates toward zero).

    Args:
        codegen: The LLVM codegen instance.
        source: The source float/double value.
        target_type: The target integer type.
        target_unsigned: Whether the target semantic type is unsigned.

    Returns:
        The casted value.
    """
    if target_unsigned:
        return codegen.builder.fptoui(source, target_type)
    return codegen.builder.fptosi(source, target_type)


def cast_float_to_float(codegen: 'LLVMCodegen', source: ir.Value, target_type: ir.Type) -> ir.Value:
    """Cast between floating-point types (extend or truncate precision).

    Args:
        codegen: The LLVM codegen instance.
        source: The source float/double value.
        target_type: The target float/double type.

    Returns:
        The casted value.
    """
    # Determine operation based on type sizes
    if isinstance(source.type, ir.FloatType) and isinstance(target_type, ir.DoubleType):
        return codegen.builder.fpext(source, target_type)
    elif isinstance(source.type, ir.DoubleType) and isinstance(target_type, ir.FloatType):
        return codegen.builder.fptrunc(source, target_type)
    else:
        # Same type, return as-is
        return source


def emit_cast_expression(codegen: 'LLVMCodegen', expr: CastExpr) -> ir.Value:
    """Emit LLVM IR for type casting expressions.

    Performs explicit type casting between numeric types following Rust-style
    explicit casting semantics. Uses dispatch table for O(1) operation lookup.

    Args:
        codegen: The LLVM codegen instance.
        expr: The cast expression to emit.

    Returns:
        The LLVM value after casting to the target type.

    Raises:
        NotImplementedError: If the cast operation is not supported.
    """
    builder = require_builder(codegen)

    # An integer literal cast directly to an integer type materializes at the
    # TARGET width, so values above i32 range (e.g. 40000000000 as i64) are
    # exact instead of pre-wrapped by the default 32-bit literal emission.
    target_ll = codegen.types.ll_type(expr.target_type)
    if isinstance(target_ll, ir.IntType):
        literal = None
        if isinstance(expr.expr, IntLit):
            literal = int(expr.expr.value)
        elif (isinstance(expr.expr, UnaryOp) and expr.expr.op == "neg"
              and isinstance(expr.expr.expr, IntLit)):
            literal = -int(expr.expr.expr.value)
        if literal is not None:
            mask = (1 << target_ll.width) - 1
            return ir.Constant(target_ll, literal & mask)

    # Emit the source expression
    source_value = codegen.expressions.emit_expr(expr.expr)

    # Get LLVM types for source and target
    source_llvm_type = source_value.type
    target_llvm_type = codegen.types.ll_type(expr.target_type)

    # Fast path: If types are the same, no casting needed
    if source_llvm_type == target_llvm_type:
        return source_value

    # Classify types and dispatch to appropriate cast handler
    src_category = classify_type_for_cast(source_llvm_type)
    dst_category = classify_type_for_cast(target_llvm_type)

    # Signedness is invisible in the signless LLVM integer type, so recover it
    # from the semantic types stamped in Pass 2 (source_type / target_type).
    from sushi_lang.semantics.type_predicates import is_unsigned_int
    source_unsigned = is_unsigned_int(expr.source_type)
    target_unsigned = is_unsigned_int(expr.target_type)

    # Dispatch table for cast operations
    cast_ops = {
        ('int', 'int'): lambda src, dst: cast_int_to_int(codegen, src, dst, source_unsigned),
        ('int', 'float'): lambda src, dst: cast_int_to_float(codegen, src, dst, source_unsigned),
        ('float', 'int'): lambda src, dst: cast_float_to_int(codegen, src, dst, target_unsigned),
        ('float', 'float'): lambda src, dst: cast_float_to_float(codegen, src, dst),
    }

    cast_func = cast_ops.get((src_category, dst_category))
    if cast_func:
        return cast_func(source_value, target_llvm_type)

    raise NotImplementedError(f"cast from {source_llvm_type} to {target_llvm_type} not supported")
