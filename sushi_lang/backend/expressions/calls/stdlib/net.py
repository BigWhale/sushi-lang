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
    if func_name in ("sock_close", "sock_local_port", "sock_tcp_accept",
                     "sock_peer_port"):
        _expect_args(expr, func_name, 1)
        fd = emit_borrowed_arg(codegen, expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_i32, [i32])
        result = codegen.builder.call(stdlib_func, [fd], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    # Two i32s in, Result<i32, NetError> out.
    if func_name in ("sock_set_recv_timeout", "sock_set_send_timeout"):
        _expect_args(expr, func_name, 2)
        fd = emit_borrowed_arg(codegen, expr.args[0])
        ms = emit_borrowed_arg(codegen, expr.args[1])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_i32, [i32, i32])
        result = codegen.builder.call(stdlib_func, [fd, ms], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    # A host name and a port. The host is marshalled HERE and freed at scope
    # exit, like an FFI argument (#292); the callee takes i8* and frees nothing.
    if func_name in ("sock_tcp_connect", "sock_tcp_listen"):
        count = 2 if func_name == "sock_tcp_connect" else 3
        _expect_args(expr, func_name, count)
        args = [emit_cstr_arg(codegen, expr.args[0])]
        args.extend(emit_borrowed_arg(codegen, a) for a in expr.args[1:])
        params = [i8_ptr] + [i32] * (count - 1)
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_i32, params)
        result = codegen.builder.call(stdlib_func, args, name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    if func_name == "sock_send":
        _expect_args(expr, func_name, 2)
        fd = emit_borrowed_arg(codegen, expr.args[0])
        data = _as_array_value(codegen, emit_borrowed_arg(codegen, expr.args[1]))
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_i32, [i32, _byte_array_type()])
        result = codegen.builder.call(stdlib_func, [fd, data], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    if func_name == "sock_peer_ip":
        _expect_args(expr, func_name, 1)
        fd = emit_borrowed_arg(codegen, expr.args[0])
        from sushi_lang.sushi_stdlib.src.type_definitions import get_string_type
        result_type = get_result_type(get_string_type(), get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_type, [i32])
        result = codegen.builder.call(stdlib_func, [fd], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    if func_name == "sock_recv":
        _expect_args(expr, func_name, 2)
        fd = emit_borrowed_arg(codegen, expr.args[0])
        maximum = emit_borrowed_arg(codegen, expr.args[1])
        result_type = get_result_type(_byte_array_type(), get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                              result_type, [i32, i32])
        result = codegen.builder.call(stdlib_func, [fd, maximum],
                                      name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    raise_internal_error("CE0055", name=f"net/socket/{func_name}")


def _byte_array_type() -> ir.LiteralStructType:
    """The u8[] descriptor, from the same helper the generator used."""
    from sushi_lang.sushi_stdlib.src.type_definitions import get_byte_array_type
    return get_byte_array_type()


def _as_array_value(codegen: 'LLVMCodegen', value: ir.Value) -> ir.Value:
    """A u8[] BY VALUE, whatever shape the argument arrived in.

    A named local yields the descriptor already; a `peek u8[]` parameter is a
    reference and arrives as a pointer to one. The registry emitters build
    their calls by hand and so do not pass through cast_for_param, which is
    where emit_named_call normalizes this.
    """
    array_ty = _byte_array_type()
    if isinstance(value.type, ir.PointerType) and value.type.pointee == array_ty:
        return codegen.builder.load(value, name="bytes_by_value")
    return value


def _expect_args(expr, func_name: str, expected: int) -> None:
    """The arity the generator was built for. A mismatch here is a compiler bug:
    validate_socket_function_call already refused a wrong count with CE2009."""
    if len(expr.args) != expected:
        raise_internal_error("CE0023", method=func_name, expected=expected,
                             got=len(expr.args))
