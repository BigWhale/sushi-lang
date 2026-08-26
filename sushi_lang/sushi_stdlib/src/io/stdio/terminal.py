"""terminal module - the `is_terminal()` query, shared by all three streams."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_isatty

# The three standard descriptors, fixed by POSIX. A Sushi program cannot obtain a
# descriptor for any other stream -- the language hides them completely -- so this
# table is the whole surface `is_terminal()` has.
STANDARD_DESCRIPTORS = {"stdin": 0, "stdout": 1, "stderr": 2}


def generate_is_terminal(module: ir.Module, stream_name: str) -> None:
    """Generate IR for <stream>.is_terminal() -> bool.

    One generator for the three streams: they differ by their descriptor and by
    nothing else. `isatty` answers a non-zero int for a terminal and 0 otherwise, and
    sets ENOTTY on the second, which is the same answer -- so there is nothing to
    report and the method returns a bare `bool`.
    """
    isatty_fn = declare_isatty(module)

    i8 = ir.IntType(8)
    i32 = ir.IntType(32)

    fn_ty = ir.FunctionType(i8, [])
    func = ir.Function(module, fn_ty, name=f"sushi_{stream_name}_is_terminal")

    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    fd = ir.Constant(i32, STANDARD_DESCRIPTORS[stream_name])
    answer = builder.call(isatty_fn, [fd], name="isatty_result")

    # A `bool` is an i8 holding 0 or 1, and `isatty` promises only non-zero. Normalise,
    # or a comparison against `true` would read a 1 the C library never guaranteed.
    is_tty = builder.icmp_signed("!=", answer, ir.Constant(i32, 0), name="is_tty")
    builder.ret(builder.zext(is_tty, i8, name="is_tty_i8"))
