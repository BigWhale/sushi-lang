"""Platform-specific time function declarations."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc

if typing.TYPE_CHECKING:
    pass


def declare_nanosleep(module: ir.Module) -> ir.Function:
    """Declare nanosleep: int nanosleep(const struct timespec *req, struct timespec *rem)"""
    return declare_libc(module, "nanosleep")


def declare_clock_gettime(module: ir.Module) -> ir.Function:
    """Declare clock_gettime: int clock_gettime(clockid_t, struct timespec *)"""
    return declare_libc(module, "clock_gettime")


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for platform-specific time functions."""
    module = ir.Module(name="platform_time")
    module.triple = ""  # Use default target triple

    declare_nanosleep(module)

    return module
