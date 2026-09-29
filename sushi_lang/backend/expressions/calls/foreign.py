"""The C boundary of a foreign call: each argument in, the return value out.

The rule of what crosses is `semantics/ffi_boundary.py`; this module is its backend half
(docs/design/ffi-memory.md). A null is a state of the boundary and never a Sushi value:
a nullable pointer is a `Maybe` on the Sushi side, and a plain one asserts non-null.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.expressions.calls.utils import marshal_cstr
from sushi_lang.backend.expressions.memory import own_temporary
from sushi_lang.semantics.ffi_boundary import is_pointer_value, nullable_payload
from sushi_lang.semantics.typesys import BuiltinType

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def marshal_argument(codegen: 'LLVMCodegen', arg, param_ty) -> ir.Value:
    """The C value of one fixed argument."""
    value = codegen.expressions.emit_expr(arg)
    if param_ty == BuiltinType.STRING:
        return marshal_cstr(codegen, value)
    payload = nullable_payload(param_ty)
    if payload is not None:
        own_temporary(codegen, arg, value, param_ty)
        return _marshal_nullable(codegen, value, param_ty, payload)
    return value


def _marshal_nullable(codegen: 'LLVMCodegen', maybe: ir.Value, maybe_ty, payload) -> ir.Value:
    """`Maybe.None` crosses as NULL, `Maybe.Some(v)` as the marshalled `v`."""
    builder = codegen.builder
    null = ir.Constant(ir.PointerType(codegen.i8), None)
    some_index = maybe_ty.get_variant_index("Some")
    is_some = builder.icmp_unsigned("==", builder.extract_value(maybe, 0),
                                    ir.Constant(codegen.i32, some_index), name="ffi_is_some")
    origin = builder.block
    some_block = builder.append_basic_block("ffi_arg_some")
    merge_block = builder.append_basic_block("ffi_arg_merge")
    builder.cbranch(is_some, some_block, merge_block)

    builder.position_at_end(some_block)
    value = _maybe_payload(codegen, maybe, payload)
    if payload == BuiltinType.STRING:
        value = codegen.runtime.strings.emit_to_cstr(value)
    value = builder.bitcast(value, null.type)
    some_end = builder.block
    builder.branch(merge_block)

    builder.position_at_end(merge_block)
    pointer = builder.phi(null.type, name="ffi_nullable_arg")
    pointer.add_incoming(null, origin)
    pointer.add_incoming(value, some_end)
    if payload == BuiltinType.STRING:
        # `free(NULL)` is a no-op, so the one register covers both arms.
        codegen.memory.register_cstr(pointer)
    return pointer


def _maybe_payload(codegen: 'LLVMCodegen', maybe: ir.Value, payload) -> ir.Value:
    """The `Some` payload of a Maybe value, which sits at offset 0 of the data."""
    builder = codegen.builder
    data = builder.extract_value(maybe, 1)
    slot = codegen.memory.entry_alloca(data.type, "ffi_maybe_data")
    builder.store(data, slot)
    typed = builder.bitcast(slot, ir.PointerType(codegen.types.ll_type(payload)))
    return builder.load(typed, name="ffi_maybe_payload")


def unmarshal_return(codegen: 'LLVMCodegen', raw: ir.Value, ret_ty) -> ir.Value:
    """The Sushi value of a foreign return."""
    payload = nullable_payload(ret_ty)
    if payload is not None:
        return _unmarshal_nullable(codegen, raw, payload)
    if is_pointer_value(ret_ty):
        _trap_on_null(codegen, raw)
        return _pointer_to_sushi(codegen, raw, ret_ty)
    return raw


def _pointer_to_sushi(codegen: 'LLVMCodegen', raw: ir.Value, ty) -> ir.Value:
    # `string`: COPY the C char* into a fresh Sushi-owned buffer (#147). Sushi never
    # frees the foreign pointer; the owned copy is RAII-freed at scope exit.
    if ty == BuiltinType.STRING:
        return codegen.runtime.strings.emit_cstr_to_owned_fat_pointer(raw)
    return raw


def _trap_on_null(codegen: 'LLVMCodegen', raw: ir.Value) -> None:
    """RE2025: a declaration that says non-null met a NULL."""
    builder = codegen.builder
    is_null = builder.icmp_unsigned("==", raw, ir.Constant(raw.type, None), name="ffi_is_null")
    trap_block = builder.append_basic_block("ffi_null_trap")
    ok_block = builder.append_basic_block("ffi_non_null")
    builder.cbranch(is_null, trap_block, ok_block)
    builder.position_at_end(trap_block)
    codegen.runtime.errors.emit_runtime_error("RE2025")
    builder.unreachable()
    builder.position_at_end(ok_block)


def _unmarshal_nullable(codegen: 'LLVMCodegen', raw: ir.Value, payload) -> ir.Value:
    """NULL is `Maybe.None`; anything else is `Maybe.Some` of the marshalled value."""
    from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some

    builder = codegen.builder
    is_null = builder.icmp_unsigned("==", raw, ir.Constant(raw.type, None), name="ffi_is_null")
    none_block = builder.append_basic_block("ffi_ret_none")
    some_block = builder.append_basic_block("ffi_ret_some")
    merge_block = builder.append_basic_block("ffi_ret_merge")
    builder.cbranch(is_null, none_block, some_block)

    builder.position_at_end(none_block)
    none_value = emit_maybe_none(codegen, payload)
    none_end = builder.block
    builder.branch(merge_block)

    builder.position_at_end(some_block)
    some_value = emit_maybe_some(codegen, payload, _pointer_to_sushi(codegen, raw, payload))
    some_end = builder.block
    builder.branch(merge_block)

    builder.position_at_end(merge_block)
    result = builder.phi(none_value.type, name="ffi_nullable_ret")
    result.add_incoming(none_value, none_end)
    result.add_incoming(some_value, some_end)
    return result
