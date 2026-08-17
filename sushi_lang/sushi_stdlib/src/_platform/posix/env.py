"""Platform-specific environment variable function declarations."""
from __future__ import annotations
import typing
from llvmlite import ir

if typing.TYPE_CHECKING:
    pass


def declare_getenv(module: ir.Module) -> ir.Function:
    """Declare getenv: char* getenv(const char* name)"""
    # Check if already declared
    if "getenv" in module.globals:
        return module.globals["getenv"]

    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()

    # char* getenv(const char* name)
    fn_ty = ir.FunctionType(i8_ptr, [i8_ptr])

    # Declare with external linkage (resolved by linker)
    func = ir.Function(module, fn_ty, name="getenv")

    return func


def declare_setenv(module: ir.Module) -> ir.Function:
    """Declare setenv: int setenv(const char* name, const char* value, int overwrite)"""
    # Check if already declared
    if "setenv" in module.globals:
        return module.globals["setenv"]

    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i8_ptr = i8.as_pointer()

    # int setenv(const char* name, const char* value, int overwrite)
    fn_ty = ir.FunctionType(i32, [i8_ptr, i8_ptr, i32])

    # Declare with external linkage (resolved by linker)
    func = ir.Function(module, fn_ty, name="setenv")

    return func


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for platform-specific environment functions."""
    module = ir.Module(name="platform_env")
    module.triple = ""  # Use default target triple

    # Declare all platform-specific environment functions
    declare_getenv(module)
    declare_setenv(module)

    return module
