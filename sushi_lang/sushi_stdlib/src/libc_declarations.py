"""C Library Function Declarations

`declare_extern` is the one accessor that declares an external function in a stdlib
module (#910); tests/unit/test_extern_declaration_is_one_seam.py refuses a hand-written
declaration anywhere else under sushi_stdlib/src/.
"""

from typing import Sequence

import llvmlite.ir as ir

_i8 = ir.IntType(8)
_i8_ptr = _i8.as_pointer()
_i32 = ir.IntType(32)
_i64 = ir.IntType(64)  # size_t and long are i64 on 64-bit systems
_f64 = ir.DoubleType()
_void = ir.VoidType()


def declare_extern(
    module: ir.Module,
    name: str,
    ret: ir.Type,
    args: Sequence[ir.Type],
    var_arg: bool = False,
) -> ir.Function:
    """Return the global `name` of the module, or declare it as `ret name(args...)`."""
    existing = module.globals.get(name)
    if existing is not None:
        return existing
    return ir.Function(module, ir.FunctionType(ret, list(args), var_arg=var_arg), name=name)


def declare_malloc(module: ir.Module) -> ir.Function:
    """void* malloc(size_t size)"""
    return declare_extern(module, "malloc", _i8_ptr, [_i64])


def declare_free(module: ir.Module) -> ir.Function:
    """void free(void* ptr)"""
    return declare_extern(module, "free", _void, [_i8_ptr])


def declare_realloc(module: ir.Module) -> ir.Function:
    """void* realloc(void* ptr, size_t size)"""
    return declare_extern(module, "realloc", _i8_ptr, [_i8_ptr, _i64])


def declare_memcpy(module: ir.Module) -> ir.Function:
    """The LLVM memcpy intrinsic: void llvm.memcpy.p0i8.p0i8.i64(i8*, i8*, i64, i1)."""
    operands = [_i8_ptr, _i8_ptr, _i64]
    name = ".".join(["llvm.memcpy"] + [t.intrinsic_name for t in operands])
    return declare_extern(module, name, _void, operands + [ir.IntType(1)])


def declare_strlen(module: ir.Module) -> ir.Function:
    """i32 llvm_strlen(i8*); the body is emitted during the final compilation."""
    return declare_extern(module, "llvm_strlen", _i32, [_i8_ptr])


def declare_strtol(module: ir.Module) -> ir.Function:
    """long strtol(const char* str, char** endptr, int base)"""
    return declare_extern(module, "strtol", _i64, [_i8_ptr, _i8_ptr.as_pointer(), _i32])


def declare_strtoll(module: ir.Module) -> ir.Function:
    """long long strtoll(const char* str, char** endptr, int base)"""
    return declare_extern(module, "strtoll", _i64, [_i8_ptr, _i8_ptr.as_pointer(), _i32])


def declare_strtod(module: ir.Module) -> ir.Function:
    """double strtod(const char* str, char** endptr)"""
    return declare_extern(module, "strtod", _f64, [_i8_ptr, _i8_ptr.as_pointer()])


def declare_sprintf(module: ir.Module) -> ir.Function:
    """int sprintf(char* str, const char* format, ...)"""
    return declare_extern(module, "sprintf", _i32, [_i8_ptr, _i8_ptr], var_arg=True)


def declare_fprintf(module: ir.Module) -> ir.Function:
    """int fprintf(FILE* stream, const char* format, ...)"""
    return declare_extern(module, "fprintf", _i32, [_i8_ptr, _i8_ptr], var_arg=True)


def declare_fread(module: ir.Module) -> ir.Function:
    """size_t fread(void* ptr, size_t size, size_t nmemb, FILE* stream)"""
    return declare_extern(module, "fread", _i64, [_i8_ptr, _i64, _i64, _i8_ptr])


def declare_fclose(module: ir.Module) -> ir.Function:
    """int fclose(FILE* stream)"""
    return declare_extern(module, "fclose", _i32, [_i8_ptr])


def declare_fseek(module: ir.Module) -> ir.Function:
    """int fseek(FILE* stream, long offset, int whence)"""
    return declare_extern(module, "fseek", _i32, [_i8_ptr, _i64, _i32])


def declare_ftell(module: ir.Module) -> ir.Function:
    """long ftell(FILE* stream)"""
    return declare_extern(module, "ftell", _i64, [_i8_ptr])


def declare_exit(module: ir.Module) -> ir.Function:
    """void exit(int status)"""
    return declare_extern(module, "exit", _void, [_i32])


def declare_errno_location(module: ir.Module) -> ir.Function:
    """int* __error() (macOS) / __errno_location() (Linux)."""
    from sushi_lang.backend.platform_detect import get_current_platform

    name = "__errno_location" if get_current_platform().is_linux else "__error"
    return declare_extern(module, name, _i32.as_pointer(), [])
