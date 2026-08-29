"""Call emission for the <net/socket> module functions.

The declared LLVM signature here must byte-match what the generator in
sushi_stdlib/src/net emitted, which is why both sides build their Result types
through get_result_type rather than spelling the struct out.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.constants import INT32_BIT_WIDTH
from sushi_lang.backend.utils import require_builder
from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_net_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a net/socket module function."""
    require_builder(codegen)

    from sushi_lang.backend.functions import declare_stdlib_function
    from sushi_lang.sushi_stdlib.src.type_definitions import (
        get_result_type, get_unit_enum_type,
    )

    i32 = ir.IntType(INT32_BIT_WIDTH)
    stdlib_func_name = f"sushi_net_{func_name}"

    if func_name == "sock_close":
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))
        fd = emit_borrowed_arg(codegen, expr.args[0])
        result_type = get_result_type(i32, get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_type, [i32])
        result = codegen.builder.call(stdlib_func, [fd], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    raise_internal_error("CE0055", name=f"net/socket/{func_name}")
