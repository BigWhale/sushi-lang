"""Shared construction of the collected array for a native variadic call.

Both user-function variadic calls and the stdlib `run` builtin funnel their trailing
arguments through `build_variadic_array`, which produces the single owned `T[]` struct
value the callee receives. Two shapes are supported:

  - **Collect** (the default): individual trailing values are copied into a freshly
    synthesized, caller-owned array that is moved into the callee.
  - **Bloom** (`arr...`): an existing array is moved into the callee whole, with no
    new allocation and no element copy; the caller relinquishes ownership so its RAII
    does not free the buffer the callee now owns.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List

from llvmlite import ir
from sushi_lang.semantics.ast import Spread, Name
from sushi_lang.semantics.typesys import DynamicArrayType

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
        return _bloom_move_array(codegen, trailing_exprs[0].value)

    # Collect: synthesize an owned T[] from the individual trailing values.
    trailing_values = [codegen.expressions.emit_expr(a) for a in trailing_exprs]

    _variadic_temp_counter[0] += 1
    temp_name = f"__variadic_{callee_name}_{_variadic_temp_counter[0]}"

    codegen.dynamic_arrays.declare_dynamic_array(temp_name, array_type)
    codegen.dynamic_arrays.emit_array_constructor_from(temp_name, trailing_values)

    # Move-managed element types (a dynamic-array element `...T[]`) are stored into the
    # synthesized array by a shallow struct copy that shares the underlying buffer. The
    # callee recursively destroys each element at scope exit, so a Name-bound source must
    # relinquish ownership (move) or it would be freed twice. Primitive/string/copy-type
    # elements are unaffected.
    if isinstance(array_type.base_type, DynamicArrayType):
        for arg in trailing_exprs:
            if isinstance(arg, Name):
                codegen.memory.mark_struct_as_moved(arg.id)

    descriptor = codegen.dynamic_arrays._array(temp_name)
    array_struct = codegen.builder.load(descriptor.llvm_alloca, name=f"{temp_name}_val")

    # Ownership moves into the callee: the caller must not free this temp.
    codegen.dynamic_arrays.mark_as_moved(temp_name)

    return array_struct


def _bloom_move_array(codegen: 'LLVMCodegen', source) -> ir.Value:
    """Move an existing array (the bloom source) into the callee.

    Loads the source's T[] struct by value and marks the source moved so the
    caller's RAII skips the buffer the callee now owns. Soundness depends on the
    source being a bare Name: validate_variadic_trailing_args rejects any other
    spread source with CE0120, so the `isinstance(source, Name)` move below always
    fires for a spread that reached codegen.
    """
    value = codegen.expressions.emit_expr(source)
    if isinstance(value.type, ir.PointerType):
        value = codegen.builder.load(value, name="bloom_src_val")
    if isinstance(source, Name):
        codegen.memory.mark_struct_as_moved(source.id)
    return value
