"""The <io/files> module: the descriptor primitives, and nothing else.

Every file METHOD is an ordinary extension in `src_sushi/io/fs.sushi`, written over
these primitives. `lines()` was the last builtin and Phase 7d removed it: line
iteration is `BufReader.lines()` in `src_sushi/io/buf.sushi`, walked by the `foreach`
`next()` protocol (HANDLES.md ruling R21), so the compiler defines no File method at all.
"""

import llvmlite.ir as ir


def generate_module_ir() -> ir.Module:
    """Generate standalone LLVM IR module for file methods."""
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
    from sushi_lang.sushi_stdlib.src.io.files import (
        syscalls, stat, read_dir, copy, positional, sequential)

    module = create_stdlib_module("io.files")

    syscalls.generate_ir(module)
    stat.generate_ir(module)
    read_dir.generate_ir(module)
    copy.generate_ir(module)
    positional.generate_ir(module)
    sequential.generate_ir(module)

    return module
