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
from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg, emit_cstr_arg
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

    i8_ptr = codegen.types.i8.as_pointer()
    result_i32 = get_result_type(i32, get_unit_enum_type())

    # One i32 descriptor in, Result<i32, NetError> out.
    if func_name in ("sock_close", "sock_local_port"):
        _expect_args(expr, func_name, 1)
        fd = emit_borrowed_arg(codegen, expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_i32, [i32])
        result = codegen.builder.call(stdlib_func, [fd], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    if func_name == "sock_tcp_listen":
        _expect_args(expr, func_name, 3)
        # The host is marshalled HERE and freed at scope exit, like an FFI
        # argument (#292); the callee takes i8* and frees nothing.
        host = emit_cstr_arg(codegen, expr.args[0])
        port = emit_borrowed_arg(codegen, expr.args[1])
        backlog = emit_borrowed_arg(codegen, expr.args[2])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_i32, [i8_ptr, i32, i32])
        result = codegen.builder.call(stdlib_func, [host, port, backlog],
                                      name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    raise_internal_error("CE0055", name=f"net/socket/{func_name}")


def _expect_args(expr, func_name: str, expected: int) -> None:
    """The arity the generator was built for. A mismatch here is a compiler bug:
    validate_socket_function_call already refused a wrong count with CE2009."""
    if len(expr.args) != expected:
        raise_internal_error("CE0023", method=func_name, expected=expected,
                             got=len(expr.args))
