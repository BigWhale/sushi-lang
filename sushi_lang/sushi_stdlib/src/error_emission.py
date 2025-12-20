"""
Runtime Error Emission

Functions for emitting runtime error messages and handling error conditions in generated IR.

Design: Single Responsibility - only runtime error handling.
"""

import llvmlite.ir as ir
from .libc_declarations import declare_fprintf, declare_exit
from .string_helpers import create_string_constant
from .io.stdio.common import get_stderr_handle_name


# ==============================================================================
# Runtime Error Emission
# ==============================================================================

def emit_runtime_error(
    module: ir.Module,
    builder: ir.IRBuilder,
    error_code: str,
    error_message: str
) -> None:
    """Emit a runtime error and exit.

    Prints an error message to stderr and exits with code 1.

    Args:
        module: The LLVM module (for declaring functions).
        builder: The IR builder for creating instructions.
        error_code: Error code (e.g., "RE2020", "RE2021").
        error_message: Human-readable error message.
    """
    # Declare required functions
    fprintf_fn = declare_fprintf(module)
    exit_fn = declare_exit(module)

    # Get stderr as FILE* pointer
    # Platform-specific: uses __stderrp on macOS/Darwin, stderr on Linux
    stderr_handle_name = get_stderr_handle_name()
    i8_ptr = ir.IntType(8).as_pointer()
    i8_ptr_ptr = i8_ptr.as_pointer()

    # Check if stderr handle is already declared
    if stderr_handle_name not in module.globals:
        # Declare platform-specific stderr handle as external global pointer to FILE*
        stderr_global = ir.GlobalVariable(module, i8_ptr, name=stderr_handle_name)
        stderr_global.linkage = 'external'
    else:
        stderr_global = module.globals[stderr_handle_name]

    # Load stderr pointer (FILE*)
    stderr_ptr = builder.load(stderr_global, name="stderr_file")

    # Create error message string: "[error_code] error_message\n"
    full_message = f"[{error_code}] {error_message}\\n"
    msg_str = create_string_constant(module, builder, full_message, name=f"err_{error_code}")

    # Print error message to stderr
    builder.call(fprintf_fn, [stderr_ptr, msg_str])

    # Exit with code 1
    i32 = ir.IntType(32)
    exit_code = ir.Constant(i32, 1)
    builder.call(exit_fn, [exit_code])

    # Mark as unreachable (control flow never continues)
    builder.unreachable()
