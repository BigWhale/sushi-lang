"""errno to a NetError tag for the <net/socket> generators, over `src/errno_tags.py`."""
from llvmlite import ir

from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.backend.runtime.constants import (
    ERRNO_DEFAULT_NET_ERROR,
    errno_to_net_error_table,
)
from sushi_lang.sushi_stdlib.src import errno_tags

# NetError.ResolveFailed, the one tag no errno reaches.
NET_ERROR_RESOLVE_FAILED = 8


def emit_net_error_tag(builder: ir.IRBuilder, module: ir.Module) -> ir.Value:
    """Read errno and map it to a NetError variant tag (i32)."""
    return errno_tags.emit_errno_tag(
        builder, module, errno_to_net_error_table(get_current_platform().is_linux),
        ERRNO_DEFAULT_NET_ERROR)


def emit_errno_err_result(builder: ir.IRBuilder, module: ir.Module,
                          result_type: ir.LiteralStructType) -> ir.Value:
    """The whole failure path: read errno, map it, build Result.Err(NetError)."""
    return errno_tags.emit_errno_err_result(
        builder, module, result_type,
        errno_to_net_error_table(get_current_platform().is_linux),
        ERRNO_DEFAULT_NET_ERROR)
