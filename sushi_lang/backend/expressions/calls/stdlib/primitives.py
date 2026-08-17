"""Standard library primitive method call emission."""
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
    """Emit a call to a stdlib primitive method."""
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
