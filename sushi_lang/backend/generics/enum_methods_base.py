"""Unified generic enum method implementations for Result<T> and Maybe<T>."""
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
    """Extract enum tag and compare to expected value."""
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
    """Emit LLVM code for enum.realise(default) pattern."""
    if len(call.args) != 1:
        raise_internal_error("CE0023", method="realise", expected=1, got=len(call.args))

    success_variant = enum_type.get_variant(success_variant_name)
    if success_variant is None:
        raise_internal_error("CE0035", variant=success_variant_name, enum=enum_type.name)

    if len(success_variant.associated_types) != 1:
        raise_internal_error("CE0036", variant=success_variant_name, expected=1, got=len(success_variant.associated_types))

    t_type = success_variant.associated_types[0]

    value_llvm_type = codegen.types.ll_type(t_type)

    # Extract (is_success, value) from enum using the helper on the function manager
    # This helper handles the complex unpacking of the enum's [N x i8] data field
    # Pass semantic type for accurate size calculation (critical for struct types)
    is_success, unpacked_value = codegen.functions._extract_value_from_result_enum(
        enum_value, value_llvm_type, t_type
    )

    default_value = codegen.expressions.emit_expr(call.args[0])

    # A dynamic-array default arrives as a POINTER: `from([...])` (emit_dynamic_array_from) hands
    # back the array's alloca, not the {len, cap, data*} value the payload is compared and selected
    # as. Load it. Without this the coercion ladder below -- which knows only int<->int and
    # float<->float -- fell off its end and reported CE0017, an INTERNAL code, for the ordinary
    # `r.realise(from([0]))` on a Result<i32[], E> (#186).
    if (isinstance(default_value.type, ir.PointerType)
            and default_value.type.pointee == value_llvm_type):
        default_value = codegen.builder.load(default_value, name="realise_default_value")

    if default_value.type != value_llvm_type:
        if isinstance(default_value.type, (ir.FloatType, ir.DoubleType)) and isinstance(value_llvm_type, (ir.FloatType, ir.DoubleType)):
            if isinstance(value_llvm_type, ir.DoubleType) and isinstance(default_value.type, ir.FloatType):
                default_value = codegen.builder.fpext(default_value, value_llvm_type)
            elif isinstance(value_llvm_type, ir.FloatType) and isinstance(default_value.type, ir.DoubleType):
                default_value = codegen.builder.fptrunc(default_value, value_llvm_type)
        elif isinstance(default_value.type, ir.IntType) and isinstance(value_llvm_type, ir.IntType):
            src_width = default_value.type.width
            dst_width = value_llvm_type.width
            if src_width < dst_width:
                default_value = codegen.builder.sext(default_value, value_llvm_type)
            elif src_width > dst_width:
                default_value = codegen.builder.trunc(default_value, value_llvm_type)
        else:
            raise_internal_error("CE0017", src=str(default_value.type), dst=str(value_llvm_type))

    # LLVM `select` on an aggregate whose fields are themselves aggregates miscompiles:
    # the top-level scalars survive but the nested aggregate is corrupted. So for any
    # aggregate T, select the POINTERS and load through the choice -- always a scalar
    # select, and mem2reg folds the alloca away.
    if isinstance(value_llvm_type, ir.Aggregate):
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


def emit_enum_expect(
    codegen: 'LLVMCodegen',
    call: 'MethodCall',
    enum_value: ir.Value,
    enum_type: EnumType,
    success_variant_name: str,
    label: str,
    missing_variant_code: str,
    assoc_count_code: str,
) -> ir.Value:
    """Emit LLVM code for enum.expect(message), for Result<T, E> and Maybe<T> alike."""
    from sushi_lang.backend.constants.llvm_values import ONE_I64, ONE_I32

    if len(call.args) != 1:
        raise_internal_error("CE0095", got=len(call.args))

    success_variant = enum_type.get_variant(success_variant_name)
    if success_variant is None:
        raise_internal_error(missing_variant_code, enum=enum_type.name)
    if len(success_variant.associated_types) != 1:
        raise_internal_error(assoc_count_code, got=len(success_variant.associated_types))

    t_type = success_variant.associated_types[0]
    value_llvm_type = codegen.types.ll_type(t_type)

    is_success, unpacked_value = codegen.functions._extract_value_from_result_enum(
        enum_value, value_llvm_type, t_type
    )

    ok_block = codegen.builder.append_basic_block(name=f"{label}_expect_ok")
    fail_block = codegen.builder.append_basic_block(name=f"{label}_expect_fail")
    continue_block = codegen.builder.append_basic_block(name=f"{label}_expect_continue")

    codegen.builder.cbranch(is_success, ok_block, fail_block)

    # Success: detach an owning payload from a receiver that stays live. Done INSIDE the
    # success block -- on the failure path the data field holds the other variant's bytes
    # reinterpreted as T, so cloning through it would walk a bogus pointer.
    codegen.builder.position_at_end(ok_block)
    payload = unpacked_value
    owned_type = resolve_named_type(codegen, t_type)
    if needs_cleanup(owned_type) and _expression_is_borrow(codegen, call.receiver):
        payload = emit_value_clone(codegen, payload, owned_type)
    ok_exit_block = codegen.builder.block
    codegen.builder.branch(continue_block)

    codegen.builder.position_at_end(fail_block)
    error_message = codegen.expressions.emit_expr(call.args[0])
    stderr_ptr = codegen.builder.load(codegen.runtime.libc_stdio.stderr_handle)
    fwrite_fn = codegen.runtime.libc_stdio.fwrite

    for fat in (codegen.runtime.strings.emit_string_literal("ERROR: "),
                error_message,
                codegen.runtime.strings.emit_string_literal("\n")):
        data = codegen.builder.extract_value(fat, 0, name="expect_msg_ptr")
        size = codegen.builder.extract_value(fat, 1, name="expect_msg_len")
        size_i64 = codegen.builder.zext(size, ir.IntType(64), name="expect_msg_len_i64")
        codegen.builder.call(fwrite_fn, [data, ONE_I64, size_i64, stderr_ptr])

    codegen.builder.call(codegen.runtime.libc_process.exit, [ONE_I32])
    codegen.builder.unreachable()  # exit() does not return, but LLVM wants a terminator

    codegen.builder.position_at_end(continue_block)
    phi = codegen.builder.phi(value_llvm_type, name="expect_result")
    phi.add_incoming(payload, ok_exit_block)
    return phi


def _expression_is_borrow(codegen: 'LLVMCodegen', expr) -> bool:
    """Does `expr` name storage that keeps owning its heap after we read it?"""
    from sushi_lang.backend.expressions.memory import expression_is_temporary
    return not expression_is_temporary(codegen, expr)


def _emit_owning_realise(
    codegen: 'LLVMCodegen',
    call: 'MethodCall',
    is_success: ir.Value,
    unpacked_value: ir.Value,
    default_value: ir.Value,
    value_llvm_type: ir.Type,
    owned_type: Type
) -> ir.Value:
    """Emit `realise(default)` for a `T` that owns heap, keeping exactly one owner."""
    borrowed_receiver = _expression_is_borrow(codegen, call.receiver)
    borrowed_default = _expression_is_borrow(codegen, call.args[0])

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
                emit_value_destructor(codegen, default_slot, owned_type)
        with else_block:
            fallback = codegen.builder.load(default_slot, name="realise_default")
            if borrowed_default:
                fallback = emit_value_clone(codegen, fallback, owned_type)
            codegen.builder.store(fallback, result_slot)

    return codegen.builder.load(result_slot, name="realise_result")
