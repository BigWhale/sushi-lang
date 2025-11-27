"""
Platform-specific time function declarations.

This module declares external time functions from the system's C library.
These declarations work across all POSIX-compliant systems (macOS, Linux, BSD).

Functions:
    - nanosleep: High-precision sleep (POSIX standard)

Implementation notes:
    - Uses external linkage (resolved by system linker)
    - Compatible with libSystem (macOS), glibc (Linux), musl (Alpine)
    - No platform-specific code needed - POSIX guarantees compatibility
"""
from __future__ import annotations
import typing
from llvmlite import ir
from stdlib.src.type_definitions import get_timespec_type, get_basic_types

if typing.TYPE_CHECKING:
    pass


def declare_nanosleep(module: ir.Module) -> ir.Function:
    """Declare nanosleep: int nanosleep(const struct timespec *req, struct timespec *rem)

    Suspends execution of the calling thread until either:
    - The time interval specified in *req has elapsed, OR
    - A signal is delivered that causes the thread to terminate or call a signal handler

    If interrupted by a signal, returns -1 and stores remaining time in *rem (if not NULL).

    POSIX Signature:
        int nanosleep(const struct timespec *req, struct timespec *rem);

        struct timespec {
            time_t tv_sec;   /* seconds (i64 on 64-bit systems) */
            long   tv_nsec;  /* nanoseconds [0, 999999999] */
        };

    Returns:
        0 on success (slept for full duration)
        -1 on failure (usually EINTR - interrupted by signal)

    Portability:
        ✅ macOS (via libSystem)
        ✅ Linux (via glibc, musl)
        ✅ FreeBSD, OpenBSD, NetBSD
        ✅ Any POSIX.1-2001 compliant system
    """
    # Check if already declared
    if "nanosleep" in module.globals:
        return module.globals["nanosleep"]

    # Get common types
    _, _, i32, _ = get_basic_types()
    timespec = get_timespec_type()
    timespec_ptr = timespec.as_pointer()

    # int nanosleep(const struct timespec *req, struct timespec *rem)
    fn_ty = ir.FunctionType(i32, [timespec_ptr, timespec_ptr])

    # Declare with external linkage (resolved by linker)
    func = ir.Function(module, fn_ty, name="nanosleep")

    return func


def generate_module_ir() -> ir.Module:
    """
    Generate LLVM IR module for platform-specific time functions.

    This module only contains external declarations, not implementations.
    The actual implementations are provided by the system's C library.
    """
    module = ir.Module(name="platform_time")
    module.triple = ""  # Use default target triple

    # Declare all platform-specific time functions
    declare_nanosleep(module)

    return module
