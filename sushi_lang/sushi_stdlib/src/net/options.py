"""Socket options: the receive and send timeouts.

A timeout is the only thing standing between a blocking socket and a hung
program, so both setters exist from the start rather than as a retrofit. When
one expires, the call answers EAGAIN, which errno_to_net_error_table maps to
NetError.TimedOut -- on a blocking socket that is the only thing EAGAIN can
mean.
"""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.net import addr
from sushi_lang.sushi_stdlib.src.net.errno import emit_errno_err_result
from sushi_lang.sushi_stdlib.src.results import emit_ok_result
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types,
    get_result_type,
    get_unit_enum_type,
)


def generate_ir(module: ir.Module) -> None:
    """Emit both timeout setters into the module."""
    platform_net = get_platform_module('net')
    _generate_timeout(module, "sushi_net_sock_set_recv_timeout",
                      platform_net.SO_RCVTIMEO)
    _generate_timeout(module, "sushi_net_sock_set_send_timeout",
                      platform_net.SO_SNDTIMEO)


def _generate_timeout(module: ir.Module, symbol: str, option: int) -> None:
    """Emit `Result<i32, NetError> <symbol>(i32 fd, i32 ms)`.

    struct timeval is 16 bytes on both platforms with tv_usec at offset 8, but
    its WIDTH differs -- an int32 on macOS with padding behind it, a full i64
    on Linux -- so the store is sized by TIMEVAL_USEC_BITS. The buffer starts
    zeroed, which is what makes the macOS padding harmless.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    platform_net = get_platform_module('net')
    setsockopt_fn = platform_net.declare_setsockopt(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, i32]), name=symbol)
    fd, ms = func.args
    fd.name, ms.name = "fd", "ms"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    tv = addr.alloca_zeroed(builder, platform_net.TIMEVAL_SIZE, "tv")
    ms64 = builder.sext(ms, i64, name="ms64")
    thousand = ir.Constant(i64, 1000)
    seconds = builder.sdiv(ms64, thousand, name="tv_sec")
    remainder = builder.srem(ms64, thousand, name="ms_rem")
    micros = builder.mul(remainder, thousand, name="tv_usec")

    sec_ptr = builder.bitcast(tv, i64.as_pointer(), name="tv_sec_ptr")
    builder.store(seconds, sec_ptr)

    usec_bits = platform_net.TIMEVAL_USEC_BITS
    usec_ty = ir.IntType(usec_bits)
    usec_raw = builder.gep(tv, [ir.Constant(i32, platform_net.TIMEVAL_USEC_OFFSET)],
                           name="tv_usec_raw")
    usec_ptr = builder.bitcast(usec_raw, usec_ty.as_pointer(), name="tv_usec_ptr")
    builder.store(builder.trunc(micros, usec_ty, name="tv_usec_sized")
                  if usec_bits < 64 else micros, usec_ptr)

    rc = builder.call(setsockopt_fn, [
        fd,
        ir.Constant(i32, platform_net.SOL_SOCKET),
        ir.Constant(i32, option),
        tv,
        ir.Constant(i32, platform_net.TIMEVAL_SIZE),
    ], name="setsockopt_rc")

    zero = ir.Constant(i32, 0)
    ok = builder.icmp_signed("==", rc, zero, name="setsockopt_ok")
    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, zero, 4))
