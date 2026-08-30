"""errno and getaddrinfo mapping for the <net/socket> generators.

Read errno after a failed libc call and map it to a NetError variant tag. The
mapping table is backend/runtime/constants.py, the Result byte layout is
src/results.py, and the errno accessor is src/libc_declarations.py -- this
module is only the socket half of the seam.

The ORDER of the calls matters and is the single largest source of a wrong
variant: close(), free() and freeaddrinfo() all overwrite errno. Every failure
edge reads the tag FIRST and parks it, then cleans up, then builds the Err.
"""
from llvmlite import ir

from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.backend.runtime.constants import (
    ERRNO_DEFAULT_NET_ERROR,
    errno_to_net_error_table,
)
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_errno_location
from sushi_lang.sushi_stdlib.src.results import emit_err_result
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types

# NetError.ResolveFailed, the one tag no errno reaches.
NET_ERROR_RESOLVE_FAILED = 8


def emit_net_error_tag(builder: ir.IRBuilder, module: ir.Module) -> ir.Value:
    """Read errno and map it to a NetError variant tag (i32).

    Call this directly after the failed libc call; anything that runs in
    between can overwrite errno.
    """
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    errno_fn = declare_errno_location(module)
    errno_ptr = builder.call(errno_fn, [], name="errno_ptr")
    errno_value = builder.load(errno_ptr, name="errno_value")

    result = ir.Constant(i32, ERRNO_DEFAULT_NET_ERROR)
    table = errno_to_net_error_table(get_current_platform().is_linux)
    for errno_val, tag in reversed(list(table.items())):
        is_match = builder.icmp_signed("==", errno_value, ir.Constant(i32, errno_val))
        result = builder.select(is_match, ir.Constant(i32, tag), result)
    return result


def emit_errno_err_result(builder: ir.IRBuilder, module: ir.Module,
                          result_type: ir.LiteralStructType) -> ir.Value:
    """The whole failure path: read errno, map it, build Result.Err(NetError)."""
    return emit_err_result(builder, result_type, emit_net_error_tag(builder, module))
