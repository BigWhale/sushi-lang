"""Runtime Error Emission"""

import hashlib

import llvmlite.ir as ir
from sushi_lang.internals.errors import message_for

from .libc_declarations import declare_fprintf, declare_exit
from ._platform import get_platform_module

PCT_S_FORMAT_NAME = ".runtime_err_pct_s"


def _private_constant(module: ir.Module, name: str, text: str) -> ir.Constant:
    """An i8* constant to a private, NUL-terminated global; one global for each name."""
    existing = module.globals.get(name)
    if isinstance(existing, ir.GlobalVariable):
        global_str = existing
    else:
        data = bytearray(text.encode("utf-8") + b"\0")
        str_ty = ir.ArrayType(ir.IntType(8), len(data))
        global_str = ir.GlobalVariable(module, str_ty, name=name)
        global_str.linkage = 'private'
        global_str.global_constant = True
        global_str.initializer = ir.Constant(str_ty, data)
    zero = ir.Constant(ir.IntType(32), 0)
    return global_str.gep([zero, zero])


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

    # Same form the backend emits: "Runtime Error RE2021: message\n".
    # The global holds the text as it is, so this must be a real newline. The message
    # goes to fprintf after a "%s" format: a "%" in it is text, not a conversion.
    full_message = f"Runtime Error {error_code}: {message_for(error_code, **params)}\n"
    digest = hashlib.sha1(full_message.encode("utf-8")).hexdigest()[:8]
    msg_str = _private_constant(module, f"err_{error_code}_{digest}", full_message)
    pct_s = _private_constant(module, PCT_S_FORMAT_NAME, "%s")

    builder.call(fprintf_fn, [stderr_ptr, pct_s, msg_str])

    i32 = ir.IntType(32)
    exit_code = ir.Constant(i32, 1)
    builder.call(exit_fn, [exit_code])

    # Mark as unreachable (control flow never continues)
    builder.unreachable()
