"""Shared construction of the collected array for a native variadic call."""
from __future__ import annotations
from typing import TYPE_CHECKING, List

from llvmlite import ir
from sushi_lang.semantics.ast import Spread
from sushi_lang.semantics.typesys import DynamicArrayType
from sushi_lang.backend.ownership import ConsumingUse, consume, relinquish_temp

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


_variadic_temp_counter = [0]


def build_variadic_array(codegen: 'LLVMCodegen', trailing_exprs: List,
                         array_type, callee_name: str,
                         callee_owns: bool = True) -> ir.Value:
    """Produce the T[] struct value for a variadic callee's collected slot."""
    if not isinstance(array_type, DynamicArrayType):
        array_type = DynamicArrayType(base_type=array_type)

    if len(trailing_exprs) == 1 and isinstance(trailing_exprs[0], Spread):
        return _bloom_move_array(codegen, trailing_exprs[0].value, array_type,
                                 callee_owns)

    # Collect: synthesize an owned T[] from the individual trailing values. Each
    # trailing element is a CALL_ARG consuming use of the ELEMENT type: the synthesized
    # array stores it shallowly and the callee recursively destroys it at scope exit,
    # so an owning Name source moves (its own RAII is skipped) and a fresh temp adopts
    # in. A plain-typed element is an ADOPT no-op.
    trailing_values = [codegen.expressions.emit_expr(a) for a in trailing_exprs]
    trailing_values = [
        consume(codegen, arg_expr, value, array_type.base_type, ConsumingUse.CALL_ARG)
        for arg_expr, value in zip(trailing_exprs, trailing_values, strict=True)
    ]

    _variadic_temp_counter[0] += 1
    temp_name = f"__variadic_{callee_name}_{_variadic_temp_counter[0]}"

    from sushi_lang.backend.types.arrays import runs

    codegen.dynamic_arrays.declare_dynamic_array(temp_name, array_type)
    codegen.dynamic_arrays.emit_array_constructor_from(
        temp_name, runs.single_runs(trailing_values))

    descriptor = codegen.dynamic_arrays._array(temp_name)
    array_struct = codegen.builder.load(descriptor.llvm_alloca, name=f"{temp_name}_val")

    # Ownership moves into the callee: the caller must not free this temp. The temp is
    # compiler-made and carries no provenance, which is exactly what relinquish_temp is
    # for. When the callee does NOT own it, say nothing -- the temp is registered, so
    # the caller's scope exit frees it and its elements exactly once.
    if callee_owns:
        relinquish_temp(codegen, temp_name)

    return array_struct


def _bloom_move_array(codegen: 'LLVMCodegen', source, array_type,
                      callee_owns: bool = True) -> ir.Value:
    """Move an existing array (the bloom source) into the callee."""
    value = codegen.expressions.emit_expr(source)
    if isinstance(value.type, ir.PointerType):
        value = codegen.builder.load(value, name="bloom_src_val")
    if not callee_owns:
        return value
    return consume(codegen, source, value, array_type, ConsumingUse.CALL_ARG)
