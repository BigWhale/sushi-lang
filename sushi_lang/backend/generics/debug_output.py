"""What a container's `debug()` writes, and where it writes it.

`List@(T).debug()` and `HashMap@(K, V).debug()` each carried a private copy of these
two helpers, and both copies called libc `printf`. That is a SECOND route to
descriptor 1 beside the one `print` and `println` take, and stdio buffers when the
stream is a pipe -- so a `debug()` line and a `println` line arrived in flush order
rather than in call order (HANDLES.md, Phase 5). One copy now, and it writes through
`emit_console_write`, the one seam.

A generated method body owns its builder, which is why every helper here takes one.
"""

from typing import Any

import llvmlite.ir as ir

from sushi_lang.backend.constants.llvm_values import ZERO_I32


def _text_global(codegen: Any, name: str, text: str) -> ir.GlobalVariable:
    """The private constant holding `text`, made once per module."""
    raw = (text + '\0').encode('utf-8')
    ty = ir.ArrayType(ir.IntType(8), len(raw))
    try:
        return codegen.builder.module.get_global(name)
    except KeyError:
        const = ir.GlobalVariable(codegen.builder.module, ty, name=name)
        const.linkage = 'internal'
        const.global_constant = True
        const.initializer = ir.Constant(ty, bytearray(raw))
        return const


def emit_debug_string(codegen: Any, builder: Any, text: str) -> None:
    """Write a literal to the console. The count is known here, so nothing formats."""
    const = _text_global(codegen, f".str_debug_{abs(hash(text)) % 1000000}", text)
    data = builder.gep(const, [ZERO_I32, ZERO_I32], name="str_ptr")
    length = ir.Constant(codegen.i32, len(text.encode('utf-8')))
    codegen.runtime.formatting.emit_console_write(data, length, builder=builder)


def emit_debug_i32(codegen: Any, builder: Any, value: ir.Value) -> None:
    """Write one i32 to the console, formatted into a frame buffer first."""
    fmt = builder.gep(_text_global(codegen, ".fmt_i32_debug", "%d"),
                      [ZERO_I32, ZERO_I32], name="fmt_ptr")
    slot = builder.alloca(ir.ArrayType(codegen.i8, 24), name="debug_buf")
    buf = builder.bitcast(slot, codegen.i8.as_pointer(), name="debug_buf_ptr")
    written = builder.call(codegen.runtime.libc_strings.sprintf, [buf, fmt, value],
                           name="debug_len")
    codegen.runtime.formatting.emit_console_write(buf, written, builder=builder)
