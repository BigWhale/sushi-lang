"""Runtime Error Emission"""

import llvmlite.ir as ir
from sushi_lang.backend.string_constants import pct_s_format, runtime_error_text

from .libc_declarations import declare_fprintf, declare_exit
from ._platform import get_platform_module


def emit_runtime_error(
    module: ir.Module,
    builder: ir.IRBuilder,
    error_code: str,
    **params
) -> None:
    """Emit a runtime error and exit."""
    fprintf_fn = declare_fprintf(module)
    exit_fn = declare_exit(module)

    stderr_handle_name = get_platform_module('stdio').get_stderr_handle_name()
    i8_ptr = ir.IntType(8).as_pointer()
    i8_ptr.as_pointer()

    if stderr_handle_name not in module.globals:
        stderr_global = ir.GlobalVariable(module, i8_ptr, name=stderr_handle_name)
        stderr_global.linkage = 'external'
    else:
        stderr_global = module.globals[stderr_handle_name]

    stderr_ptr = builder.load(stderr_global, name="stderr_file")

    zero = ir.Constant(ir.IntType(32), 0)
    msg_str = runtime_error_text(module, error_code, **params).gep([zero, zero])
    pct_s = pct_s_format(module).gep([zero, zero])

    builder.call(fprintf_fn, [stderr_ptr, pct_s, msg_str])

    i32 = ir.IntType(32)
    exit_code = ir.Constant(i32, 1)
    builder.call(exit_fn, [exit_code])

    # Mark as unreachable (control flow never continues)
    builder.unreachable()
