"""Platform-specific time function declarations."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_timespec_type, get_basic_types

if typing.TYPE_CHECKING:
    pass


def declare_nanosleep(module: ir.Module) -> ir.Function:
    """Declare nanosleep: int nanosleep(const struct timespec *req, struct timespec *rem)"""
    if "nanosleep" in module.globals:
        return module.globals["nanosleep"]

    _, _, i32, _ = get_basic_types()
    timespec = get_timespec_type()
    timespec_ptr = timespec.as_pointer()

    fn_ty = ir.FunctionType(i32, [timespec_ptr, timespec_ptr])

    func = ir.Function(module, fn_ty, name="nanosleep")

    return func


def declare_clock_gettime(module: ir.Module) -> ir.Function:
    """Declare clock_gettime: int clock_gettime(clockid_t, struct timespec *)"""
    if "clock_gettime" in module.globals:
        return module.globals["clock_gettime"]

    _, _, i32, _ = get_basic_types()
    timespec_ptr = get_timespec_type().as_pointer()
    fn_ty = ir.FunctionType(i32, [i32, timespec_ptr])
    return ir.Function(module, fn_ty, name="clock_gettime")


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for platform-specific time functions."""
    module = ir.Module(name="platform_time")
    module.triple = ""  # Use default target triple

    declare_nanosleep(module)

    return module
