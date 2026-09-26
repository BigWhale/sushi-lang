"""The one errno-to-tag emitter; the ordering rule is in `docs/design/stdlib-syscall-layer.md`."""
from collections.abc import Mapping

from llvmlite import ir

from sushi_lang.sushi_stdlib.src.libc_declarations import declare_errno_location
from sushi_lang.sushi_stdlib.src.results import emit_err_result
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types


def emit_errno_tag(builder: ir.IRBuilder, module: ir.Module,
                   table: Mapping[int, int], default: int) -> ir.Value:
    """Read errno and map it through `table` to a variant tag (i32), else `default`.

    Call this directly after the failed libc call: a later `close` or `free` can
    overwrite errno.
    """
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    errno_ptr = builder.call(declare_errno_location(module), [], name="errno_ptr")
    errno_value = builder.load(errno_ptr, name="errno_value")

    result = ir.Constant(i32, default)
    for errno_val, tag in reversed(list(table.items())):
        is_match = builder.icmp_signed("==", errno_value, ir.Constant(i32, errno_val))
        result = builder.select(is_match, ir.Constant(i32, tag), result)
    return result


def emit_errno_err_result(builder: ir.IRBuilder, module: ir.Module,
                          result_type: ir.LiteralStructType,
                          table: Mapping[int, int], default: int) -> ir.Value:
    """The whole failure path: read errno, map it, build `Result.Err(tag)`."""
    return emit_err_result(builder, result_type,
                           emit_errno_tag(builder, module, table, default))
