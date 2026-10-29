"""Unified generic enum method implementations for Result<T> and Maybe<T>."""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence
import llvmlite.ir as ir

from sushi_lang.semantics.typesys import EnumType, Type
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils
from sushi_lang.backend.destructors import needs_cleanup, resolve_named_type
from sushi_lang.backend.ownership import copy_out
from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


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
    enum_value: ir.Value,
    enum_type: EnumType,
    success_variant_name: str,
    args: Sequence[ir.Value],
) -> ir.Value:
    """`realise(default)`: the payload of the success variant, or the default. The receiver
    and the default are reads (docs/design/ir.md 8.3), so an answer that owns heap is a copy
    of the chosen value."""
    if len(args) != 1:
        raise_internal_error("CE0023", method="realise", expected=1, got=len(args))

    success_variant = enum_type.get_variant(success_variant_name)
    if success_variant is None:
        raise_internal_error("CE0035", variant=success_variant_name, enum=enum_type.name)

    if len(success_variant.associated_types) != 1:
        raise_internal_error("CE0036", variant=success_variant_name, expected=1, got=len(success_variant.associated_types))

    t_type = success_variant.associated_types[0]
    value_llvm_type = codegen.types.ll_type(t_type)
    (default_value,) = args
    if default_value.type != value_llvm_type:
        raise_internal_error("CE0017", src=str(default_value.type), dst=str(value_llvm_type))

    is_success, unpacked_value = codegen.functions.extract_value_from_result_enum(
        enum_value, value_llvm_type, t_type
    )

    owned_type = resolve_named_type(codegen, t_type)
    if needs_cleanup(codegen, owned_type):
        return _emit_owning_realise(codegen, is_success, unpacked_value, default_value,
                                    value_llvm_type, owned_type)

    # LLVM `select` on an aggregate whose fields are themselves aggregates miscompiles:
    # the top-level scalars survive but the nested aggregate is corrupted. So for any
    # aggregate T, select the POINTERS and load through the choice -- always a scalar
    # select, and mem2reg folds the alloca away.
    if isinstance(value_llvm_type, ir.Aggregate):
        value_slot = entry_alloca(codegen.builder, value_llvm_type, name="realise_value_slot")
        codegen.builder.store(unpacked_value, value_slot)
        default_slot = entry_alloca(codegen.builder, value_llvm_type, name="realise_default_slot")
        codegen.builder.store(default_value, default_slot)
        chosen_ptr = codegen.builder.select(is_success, value_slot, default_slot, name="realise_ptr")
        return codegen.builder.load(chosen_ptr, name="realise_result")

    return codegen.builder.select(is_success, unpacked_value, default_value, name="realise_result")


def _emit_owning_realise(
    codegen: 'LLVMCodegen',
    is_success: ir.Value,
    unpacked_value: ir.Value,
    default_value: ir.Value,
    value_llvm_type: ir.Type,
    owned_type: Type
) -> ir.Value:
    """`realise(default)` for a `T` that owns heap: a copy of the payload or of the default.
    The copy is made INSIDE its path: on the other path the data field holds the bytes of
    the other variant, and a copy through them would walk a bogus pointer."""
    result_slot = entry_alloca(codegen.builder, value_llvm_type, name="realise_result_slot")
    with codegen.builder.if_else(is_success) as (then_block, else_block):
        with then_block:
            codegen.builder.store(copy_out(codegen, unpacked_value, owned_type), result_slot)
        with else_block:
            codegen.builder.store(copy_out(codegen, default_value, owned_type), result_slot)
    return codegen.builder.load(result_slot, name="realise_result")
