"""Operations on a socket descriptor, whatever transport made it."""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.net.errno import emit_errno_err_result
from sushi_lang.sushi_stdlib.src.results import emit_ok_result
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types,
    get_result_type,
    get_unit_enum_type,
)


def generate_ir(module: ir.Module) -> None:
    """Emit every descriptor-level symbol into the module."""
    generate_close(module)


def generate_close(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_close(i32 fd)`.

    close() on a descriptor that was never open sets EBADF, which the table
    maps to NetError.Closed -- the condition, rather than a fall-through to
    Other.
    """
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')
    close_fn = platform_net.declare_close(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_net_sock_close")
    func.args[0].name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    rc = builder.call(close_fn, [func.args[0]], name="close_rc")
    zero = ir.Constant(i32, 0)
    ok = builder.icmp_signed("==", rc, zero, name="close_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, zero, 4))
