"""flush module - the `flush()` push, for the two writing streams."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_fflush
from sushi_lang.sushi_stdlib.src.io.stdio.common import (
    declare_stdout_handle, declare_stderr_handle,
)

_HANDLES = {"stdout": declare_stdout_handle, "stderr": declare_stderr_handle}


def generate_flush(module: ir.Module, stream_name: str) -> None:
    """Generate IR for <stream>.flush() -> ~.

    One generator for stdout and stderr: they differ by their handle and by
    nothing else. stdin has no flush; discarding buffered input is POSIX-only
    behaviour and not a thing a Sushi program asks for.
    """
    fflush_fn = declare_fflush(module)
    handle = _HANDLES[stream_name](module)

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [])
    func = ir.Function(module, fn_ty, name=f"sushi_{stream_name}_flush")

    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    stream_ptr = builder.load(handle, name=stream_name)
    builder.call(fflush_fn, [stream_ptr])
    builder.ret(ir.Constant(i32, 0))
