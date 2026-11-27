"""The bitcode half of <io/files>: `fd_write_str`, and nothing else.

The other functions are Sushi source (`src_sushi/io/files.sushi`).
"""

import llvmlite.ir as ir


def generate_module_ir() -> ir.Module:
    """Generate standalone LLVM IR module for file methods."""
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
    from sushi_lang.sushi_stdlib.src.io.files import sequential

    module = create_stdlib_module("io.files")

    sequential.generate_ir(module)

    return module
