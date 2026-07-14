"""
Unified generic enum method implementations for Result<T> and Maybe<T>.

This module provides common patterns shared between Result and Maybe:
- realise(default): Extract value with fallback
- Tag checking: is_ok/is_some, is_err/is_none
- Value extraction from enum data fields

These patterns were previously duplicated across results.py and maybe.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import llvmlite.ir as ir

from sushi_lang.semantics.typesys import EnumType, Type
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils
from sushi_lang.backend.destructors import (
    emit_value_destructor, needs_cleanup, resolve_named_type
)
from sushi_lang.backend.expressions.memory import emit_value_clone

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import MethodCall


def emit_enum_tag_check(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    expected_tag: int,
    check_name: str
) -> ir.Value:
    """Extract enum tag and compare to expected value.

    Generic implementation for is_ok(), is_some(), is_err(), is_none(), etc.

    Args:
        codegen: The LLVM code generator instance
        enum_value: The LLVM value of the enum (struct value, not pointer)
        expected_tag: The tag value to check against (e.g., 0 for Ok/Some)
        check_name: Name for the comparison result (e.g., "is_ok", "is_some")

    Returns:
        i1 value: true if tag matches expected_tag, false otherwise
    """
    # Extract tag and compare to expected value
    return enum_utils.check_enum_variant(
        codegen, enum_value, expected_tag, signed=False, name=check_name
    )


def emit_enum_realise(
    codegen: 'LLVMCodegen',
    call: 'MethodCall',
    enum_value: ir.Value,
    enum_type: EnumType,
    success_variant_name: str,
    enum_type_name: str
) -> ir.Value:
    """Emit LLVM code for enum.realise(default) pattern.

    Generic implementation that works for both Result<T>.realise() and Maybe<T>.realise().

    Enum layout: {i32 tag, [N x i8] data}
    - success_variant_name has tag = 0 (Ok for Result, Some for Maybe)
    - failure variant has tag = 1 (Err for Result, None for Maybe)

    Returns: tag == 0 ? unpacked_value : default

    Args:
        codegen: The LLVM code generator instance
        call: The method call AST node
        enum_value: The LLVM value of the enum
        enum_type: The enum type (Result<T> or Maybe<T>)
        success_variant_name: Name of the success variant ("Ok" or "Some")
        enum_type_name: Name for error messages ("Result" or "Maybe")

    Returns:
        The extracted value if success variant, or default if failure variant

    Raises:
        ValueError: If argument count is not exactly 1
        RuntimeError: If variant structure is invalid
        TypeError: If type mismatch occurs
    """
    if len(call.args) != 1:
        raise_internal_error("CE0023", method="realise", expected=1, got=len(call.args))

    # Extract T from generic enum
    success_variant = enum_type.get_variant(success_variant_name)
    if success_variant is None:
        raise_internal_error("CE0035", variant=success_variant_name, enum=enum_type.name)

    if len(success_variant.associated_types) != 1:
        raise_internal_error("CE0036", variant=success_variant_name, expected=1, got=len(success_variant.associated_types))

    t_type = success_variant.associated_types[0]

    # Get the LLVM type for T
    value_llvm_type = codegen.types.ll_type(t_type)

    # Extract (is_success, value) from enum using the helper on the function manager
    # This helper handles the complex unpacking of the enum's [N x i8] data field
    # Pass semantic type for accurate size calculation (critical for struct types)
    is_success, unpacked_value = codegen.functions._extract_value_from_result_enum(
        enum_value, value_llvm_type, t_type
    )

    # Emit the default value expression
    default_value = codegen.expressions.emit_expr(call.args[0])

    # A dynamic-array default arrives as a POINTER: `from([...])` (emit_dynamic_array_from) hands
    # back the array's alloca, not the {len, cap, data*} value the payload is compared and selected
    # as. Load it. Without this the coercion ladder below -- which knows only int<->int and
    # float<->float -- fell off its end and reported CE0017, an INTERNAL code, for the ordinary
    # `r.realise(from([0]))` on a Result<i32[], E> (#186).
    if (isinstance(default_value.type, ir.PointerType)
            and default_value.type.pointee == value_llvm_type):
        default_value = codegen.builder.load(default_value, name="realise_default_value")

    # Ensure default_value has the same LLVM type as unpacked_value
    # The LLVM select instruction requires both operands to have identical types
    if default_value.type != value_llvm_type:
        # Type mismatch - need to convert default_value to match
        # Handle float-to-float conversions (f32 <-> f64)
        if isinstance(default_value.type, (ir.FloatType, ir.DoubleType)) and isinstance(value_llvm_type, (ir.FloatType, ir.DoubleType)):
            if isinstance(value_llvm_type, ir.DoubleType) and isinstance(default_value.type, ir.FloatType):
                # f32 -> f64: extend precision
                default_value = codegen.builder.fpext(default_value, value_llvm_type)
            elif isinstance(value_llvm_type, ir.FloatType) and isinstance(default_value.type, ir.DoubleType):
                # f64 -> f32: truncate precision
                default_value = codegen.builder.fptrunc(default_value, value_llvm_type)
        # Handle integer-to-integer conversions (i8 <-> i32, i16 <-> i64, etc.)
        elif isinstance(default_value.type, ir.IntType) and isinstance(value_llvm_type, ir.IntType):
            src_width = default_value.type.width
            dst_width = value_llvm_type.width
            if src_width < dst_width:
                # Extend: i8 -> i32, i32 -> i64, etc.
                # Use sign extension for signed types (i32 is signed in Sushi)
                default_value = codegen.builder.sext(default_value, value_llvm_type)
            elif src_width > dst_width:
                # Truncate: i32 -> i8, i64 -> i32, etc.
                default_value = codegen.builder.trunc(default_value, value_llvm_type)
        else:
            # Type mismatch that shouldn't happen after proper semantic analysis
            raise_internal_error("CE0017", src=str(default_value.type), dst=str(value_llvm_type))

    # Select: is_success ? unpacked_value : default_value
    #
    # LLVM `select` on an aggregate whose fields are themselves aggregates (e.g. a
    # struct with a `string` field, laid out {i32, {i8*, i32}}) miscompiles: the
    # top-level scalar fields survive but the nested aggregate is corrupted, so the
    # nested string's length comes out garbage and the first use crashes. A flat
    # struct of scalars (e.g. {i32, i32}) is unaffected, which is why realise on a
    # struct without an aggregate field works. Selecting the *pointers* and loading
    # through the choice is always a scalar select and copies the whole aggregate
    # correctly, so use that for any aggregate T (mem2reg/O1+ folds the alloca away).
    if isinstance(value_llvm_type, ir.Aggregate):
        # `t_type` reaches here as a bare name for a user struct/enum; the ownership test,
        # the clone and the destructor all dispatch on the resolved class.
        owned_type = resolve_named_type(codegen, t_type)
        if needs_cleanup(owned_type):
            return _emit_owning_realise(
                codegen, call, is_success, unpacked_value, default_value,
                value_llvm_type, owned_type
            )

        value_slot = codegen.builder.alloca(value_llvm_type, name="realise_value_slot")
        codegen.builder.store(unpacked_value, value_slot)
        default_slot = codegen.builder.alloca(value_llvm_type, name="realise_default_slot")
        codegen.builder.store(default_value, default_slot)
        chosen_ptr = codegen.builder.select(is_success, value_slot, default_slot, name="realise_ptr")
        return codegen.builder.load(chosen_ptr, name="realise_result")

    result = codegen.builder.select(is_success, unpacked_value, default_value, name="realise_result")

    return result


def _expression_is_borrow(expr) -> bool:
    """Does `expr` name storage that keeps owning its heap after we read it?

    The inverse of `expression_is_temporary`, which is the single definition -- `.realise()`
    ADOPTS a temporary's payload while the non-extracting consumers DESTROY the temporary
    outright (#159), so the two must agree on every AST node or a payload is adopted and freed.
    """
    from sushi_lang.backend.expressions.memory import expression_is_temporary
    return not expression_is_temporary(expr)


def _emit_owning_realise(
    codegen: 'LLVMCodegen',
    call: 'MethodCall',
    is_success: ir.Value,
    unpacked_value: ir.Value,
    default_value: ir.Value,
    value_llvm_type: ir.Type,
    owned_type: Type
) -> ir.Value:
    """Emit `realise(default)` for a `T` that owns heap, keeping exactly one owner.

    `realise` picks one of two candidate values and returns it. Both arrive as shallow
    views, so each side is cloned when it belongs to a binding that stays live, and adopted
    when it is a temporary; the losing candidate is then destroyed. Getting this wrong fails
    in both directions: returning an alias of a live binding double-frees at scope exit,
    while never destroying the loser strands its buffers.

    The payload is only touched under `is_success`. On the failure path the enum's data
    field holds the *other* variant's bytes (`Err`'s payload, `None`'s uninitialised slot)
    reinterpreted as `T`, so cloning or destroying through it would walk a bogus pointer.
    """
    borrowed_receiver = _expression_is_borrow(call.receiver)
    borrowed_default = _expression_is_borrow(call.args[0])

    # The default is evaluated unconditionally (its expression may have side effects), so
    # park it in a slot: the success path needs a pointer to destroy it through.
    default_slot = codegen.builder.alloca(value_llvm_type, name="realise_default_slot")
    codegen.builder.store(default_value, default_slot)
    result_slot = codegen.builder.alloca(value_llvm_type, name="realise_result_slot")

    with codegen.builder.if_else(is_success) as (then_block, else_block):
        with then_block:
            payload = unpacked_value
            if borrowed_receiver:
                payload = emit_value_clone(codegen, payload, owned_type)
            codegen.builder.store(payload, result_slot)
            if not borrowed_default:
                # We adopted the default temporary and are not returning it.
                emit_value_destructor(codegen, codegen.builder, default_slot, owned_type)
        with else_block:
            fallback = codegen.builder.load(default_slot, name="realise_default")
            if borrowed_default:
                fallback = emit_value_clone(codegen, fallback, owned_type)
            codegen.builder.store(fallback, result_slot)

    return codegen.builder.load(result_slot, name="realise_result")
