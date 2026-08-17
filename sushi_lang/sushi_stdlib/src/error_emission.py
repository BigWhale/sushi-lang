"""Runtime Error Emission"""

import llvmlite.ir as ir
from sushi_lang.internals.errors import message_for

from .libc_declarations import declare_fprintf, declare_exit
from .string_helpers import create_string_constant
from .io.stdio.common import get_stderr_handle_name


def emit_runtime_error(
    module: ir.Module,
    builder: ir.IRBuilder,
    error_code: str,
    **params
) -> None:
    """Emit a runtime error and exit."""
    fprintf_fn = declare_fprintf(module)
    exit_fn = declare_exit(module)

    stderr_handle_name = get_stderr_handle_name()
    i8_ptr = ir.IntType(8).as_pointer()
    i8_ptr.as_pointer()

    if stderr_handle_name not in module.globals:
        stderr_global = ir.GlobalVariable(module, i8_ptr, name=stderr_handle_name)
        stderr_global.linkage = 'external'
    else:
        stderr_global = module.globals[stderr_handle_name]

    stderr_ptr = builder.load(stderr_global, name="stderr_file")

    # Same form the backend emits: "Runtime Error RE2021: message\n".
    # create_string_constant does NOT process escapes, so this must be a real
    # newline -- the old "\\n" here emitted a literal backslash-n.
    full_message = f"Runtime Error {error_code}: {message_for(error_code, **params)}\n"
    msg_str = create_string_constant(module, builder, full_message, name=f"err_{error_code}")

    builder.call(fprintf_fn, [stderr_ptr, msg_str])

    i32 = ir.IntType(32)
    exit_code = ir.Constant(i32, 1)
    builder.call(exit_fn, [exit_code])

    # Mark as unreachable (control flow never continues)
    builder.unreachable()
