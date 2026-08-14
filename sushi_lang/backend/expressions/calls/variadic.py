"""Shared construction of the collected array for a native variadic call.

Both user-function variadic calls and the stdlib `run` builtin funnel their trailing
arguments through `build_variadic_array`, which produces the single owned `T[]` struct
value the callee receives. Two shapes are supported:

  - **Collect** (the default): individual trailing values are copied into a freshly
    synthesized, caller-owned array that is moved into the callee.
  - **Bloom** (`arr...`): an existing array is moved into the callee whole, with no
    new allocation and no element copy; the caller relinquishes ownership so its RAII
    does not free the buffer the callee now owns.

Every ownership transfer here goes through the seam (`backend/ownership.py`): each
trailing element and the bloom source are CALL_ARG consuming uses, and the synthesized
temp itself leaves through `relinquish_temp`. `tests/unit/test_consuming_use_coverage.py`
fails the build if a transfer primitive is called from this module directly.
"""
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
                         array_type, callee_name: str) -> ir.Value:
    """Produce the owned T[] struct value for a variadic callee's collected slot.

    Args:
        codegen: The LLVM code generator.
        trailing_exprs: The trailing argument expressions (post fixed prefix). Either a
            single ``Spread`` (bloom) or zero-or-more individual value expressions.
        array_type: The variadic parameter's array type (``DynamicArrayType`` or the
            element type, which is wrapped defensively).
        callee_name: Used to name the synthesized temp for readable IR.

    Returns:
        An ``ir.Value`` holding the T[] struct (fat pointer) to pass as the callee's
        single collected array argument.
    """
    if not isinstance(array_type, DynamicArrayType):
        # Defensive: callers should pass the wrapped array type.
        array_type = DynamicArrayType(base_type=array_type)

    # Bloom: `arr...` moves an existing array in whole.
    if len(trailing_exprs) == 1 and isinstance(trailing_exprs[0], Spread):
        return _bloom_move_array(codegen, trailing_exprs[0].value, array_type)

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

    codegen.dynamic_arrays.declare_dynamic_array(temp_name, array_type)
    codegen.dynamic_arrays.emit_array_constructor_from(temp_name, trailing_values)

    descriptor = codegen.dynamic_arrays._array(temp_name)
    array_struct = codegen.builder.load(descriptor.llvm_alloca, name=f"{temp_name}_val")

    # Ownership moves into the callee: the caller must not free this temp. The temp is
    # compiler-made and carries no provenance, which is exactly what relinquish_temp is for.
    relinquish_temp(codegen, temp_name)

    return array_struct


def _bloom_move_array(codegen: 'LLVMCodegen', source, array_type) -> ir.Value:
    """Move an existing array (the bloom source) into the callee.

    Loads the source's T[] struct by value and consumes the source (a CALL_ARG use of
    the whole array type) so the caller's RAII skips the buffer the callee now owns.
    Soundness depends on the source being a bare Name: validate_variadic_trailing_args
    rejects any other spread source with CE0120.
    """
    value = codegen.expressions.emit_expr(source)
    if isinstance(value.type, ir.PointerType):
        value = codegen.builder.load(value, name="bloom_src_val")
    return consume(codegen, source, value, array_type, ConsumingUse.CALL_ARG)
