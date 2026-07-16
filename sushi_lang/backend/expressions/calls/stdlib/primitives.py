"""
Standard library primitive method call emission.

This module handles external calls to precompiled stdlib functions for
primitive type conversions (e.g. to_str()).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_stdlib_primitive_call(
    codegen: 'LLVMCodegen',
    method: str,
    receiver_value: ir.Value,
    receiver_type: ir.Type,
    semantic_type_str: str
) -> ir.Value:
    """Emit a call to a stdlib primitive method.

    This function emits an external call to a precompiled stdlib function
    instead of emitting inline IR. Used when the appropriate stdlib module
    is imported via use <module> syntax.

    Args:
        codegen: The LLVM code generator
        method: The method name (e.g., "to_str")
        receiver_value: The LLVM value of the receiver
        receiver_type: The LLVM type of the receiver
        semantic_type_str: String representation of semantic type (e.g., "i32")

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the method is not implemented in stdlib
    """
    require_builder(codegen)
    # For now, only handle to_str()
    if method != "to_str":
        raise_internal_error("CE0028", method=method)

    # Build function name: sushi_{type}_to_str
    func_name = f"sushi_{semantic_type_str}_to_str"

    # Return type is always string fat pointer struct {i8*, i32} for to_str()
    string_struct_type = codegen.types.string_struct

    # Declare the external function
    from sushi_lang.backend.functions import declare_stdlib_function
    stdlib_func = declare_stdlib_function(
        codegen.module,
        func_name,
        string_struct_type,
        [receiver_type]
    )

    # Emit the call
    result = codegen.builder.call(
        stdlib_func,
        [receiver_value],
        name=f"{semantic_type_str}_to_str_result"
    )

    return result
