"""
C Library Function Declarations

All declarations for external C functions (libc) used by the stdlib.
This module provides LLVM IR function declarations for C standard library functions.

Design: Single Responsibility - only C function declarations, no logic.
"""

import llvmlite.ir as ir


# ==============================================================================
# Memory Management
# ==============================================================================

def declare_malloc(module: ir.Module) -> ir.Function:
    """Declare malloc: void* malloc(size_t size)"""
    if "malloc" in module.globals:
        return module.globals["malloc"]

    i64 = ir.IntType(64)  # size_t is i64 on 64-bit systems
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i8_ptr, [i64])
    return ir.Function(module, fn_ty, name="malloc")


def declare_free(module: ir.Module) -> ir.Function:
    """Declare free: void free(void* ptr)"""
    if "free" in module.globals:
        return module.globals["free"]

    i8_ptr = ir.IntType(8).as_pointer()
    void = ir.VoidType()
    fn_ty = ir.FunctionType(void, [i8_ptr])
    return ir.Function(module, fn_ty, name="free")


def declare_realloc(module: ir.Module) -> ir.Function:
    """Declare realloc: void* realloc(void* ptr, size_t size)"""
    if "realloc" in module.globals:
        return module.globals["realloc"]

    i64 = ir.IntType(64)  # size_t is i64 on 64-bit systems
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i8_ptr, [i8_ptr, i64])
    return ir.Function(module, fn_ty, name="realloc")


def declare_memcpy(module: ir.Module) -> ir.Function:
    """Declare LLVM memcpy intrinsic (replaces libc memcpy).

    Returns llvm.memcpy.p0i8.p0i8.i32 intrinsic for platform-independent
    memory copying with better optimization potential.

    Note: The intrinsic takes 4 parameters (dest, src, len, is_volatile),
    so callers must pass is_volatile=0 as the 4th argument.
    """
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)

    # Declare llvm.memcpy intrinsic
    # Signature: void @llvm.memcpy.p0i8.p0i8.i32(i8* dest, i8* src, i32 len, i1 is_volatile)
    return module.declare_intrinsic(
        'llvm.memcpy',
        [ir.PointerType(i8), ir.PointerType(i8), i32]
    )


# ==============================================================================
# String Operations
# ==============================================================================

def declare_strlen(module: ir.Module) -> ir.Function:
    """Declare strlen as external (implementation emitted during final compilation).

    Returns:
        The llvm_strlen function declaration: i32 llvm_strlen(i8* s)
    """
    func_name = "llvm_strlen"
    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    return ir.Function(module, fn_ty, name=func_name)


def declare_strcmp(module: ir.Module) -> ir.Function:
    """Declare strcmp: int strcmp(const char* s1, const char* s2)"""
    if "strcmp" in module.globals:
        return module.globals["strcmp"]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i8_ptr])
    return ir.Function(module, fn_ty, name="strcmp")


def declare_strtol(module: ir.Module) -> ir.Function:
    """Declare strtol: long strtol(const char* str, char** endptr, int base)

    Converts string to long integer. Returns 0 on error, sets endptr to str if no conversion.
    """
    if "strtol" in module.globals:
        return module.globals["strtol"]

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)  # long is i64 on 64-bit systems
    i8_ptr = ir.IntType(8).as_pointer()
    i8_ptr_ptr = i8_ptr.as_pointer()
    fn_ty = ir.FunctionType(i64, [i8_ptr, i8_ptr_ptr, i32])
    return ir.Function(module, fn_ty, name="strtol")


def declare_strtoll(module: ir.Module) -> ir.Function:
    """Declare strtoll: long long strtoll(const char* str, char** endptr, int base)

    Converts string to long long integer. Returns 0 on error, sets endptr to str if no conversion.
    """
    if "strtoll" in module.globals:
        return module.globals["strtoll"]

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)  # long long is i64
    i8_ptr = ir.IntType(8).as_pointer()
    i8_ptr_ptr = i8_ptr.as_pointer()
    fn_ty = ir.FunctionType(i64, [i8_ptr, i8_ptr_ptr, i32])
    return ir.Function(module, fn_ty, name="strtoll")


def declare_strtod(module: ir.Module) -> ir.Function:
    """Declare strtod: double strtod(const char* str, char** endptr)

    Converts string to double. Returns 0.0 on error, sets endptr to str if no conversion.
    """
    if "strtod" in module.globals:
        return module.globals["strtod"]

    f64 = ir.DoubleType()
    i8_ptr = ir.IntType(8).as_pointer()
    i8_ptr_ptr = i8_ptr.as_pointer()
    fn_ty = ir.FunctionType(f64, [i8_ptr, i8_ptr_ptr])
    return ir.Function(module, fn_ty, name="strtod")


# ==============================================================================
# Character Operations
# ==============================================================================

def declare_toupper(module: ir.Module) -> ir.Function:
    """Declare toupper: int toupper(int c)"""
    if "toupper" in module.globals:
        return module.globals["toupper"]

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [i32])
    return ir.Function(module, fn_ty, name="toupper")


def declare_tolower(module: ir.Module) -> ir.Function:
    """Declare tolower: int tolower(int c)"""
    if "tolower" in module.globals:
        return module.globals["tolower"]

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [i32])
    return ir.Function(module, fn_ty, name="tolower")


def declare_isspace(module: ir.Module) -> ir.Function:
    """Declare isspace: int isspace(int c)"""
    if "isspace" in module.globals:
        return module.globals["isspace"]

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [i32])
    return ir.Function(module, fn_ty, name="isspace")


def declare_isalpha(module: ir.Module) -> ir.Function:
    """Declare isalpha: int isalpha(int c)"""
    if "isalpha" in module.globals:
        return module.globals["isalpha"]

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [i32])
    return ir.Function(module, fn_ty, name="isalpha")


# ==============================================================================
# UTF-8 Helpers (from backend/c_utils/string.c)
# ==============================================================================

def declare_utf8_count(module: ir.Module) -> ir.Function:
    """Declare utf8_count: size_t utf8_count(const char* s)

    C helper function that counts UTF-8 characters.
    """
    if "utf8_count" in module.globals:
        return module.globals["utf8_count"]

    i64 = ir.IntType(64)  # size_t is i64 on 64-bit systems
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i64, [i8_ptr])
    return ir.Function(module, fn_ty, name="utf8_count")


def declare_utf8_byte_offset(module: ir.Module) -> ir.Function:
    """Declare utf8_byte_offset: size_t utf8_byte_offset(const char* s, size_t n)

    C helper function that finds the byte offset of the nth UTF-8 character.
    """
    if "utf8_byte_offset" in module.globals:
        return module.globals["utf8_byte_offset"]

    i64 = ir.IntType(64)  # size_t is i64 on 64-bit systems
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i64, [i8_ptr, i64])
    return ir.Function(module, fn_ty, name="utf8_byte_offset")


# ==============================================================================
# Formatted I/O
# ==============================================================================

def declare_sprintf(module: ir.Module) -> ir.Function:
    """Declare sprintf: int sprintf(char* str, const char* format, ...)"""
    if "sprintf" in module.globals:
        return module.globals["sprintf"]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i8_ptr], var_arg=True)
    return ir.Function(module, fn_ty, name="sprintf")


def declare_fprintf(module: ir.Module) -> ir.Function:
    """Declare fprintf: int fprintf(FILE* stream, const char* format, ...)"""
    if "fprintf" in module.globals:
        return module.globals["fprintf"]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i8_ptr], var_arg=True)
    return ir.Function(module, fn_ty, name="fprintf")


# ==============================================================================
# File I/O
# ==============================================================================

def declare_getline(module: ir.Module) -> ir.Function:
    """Declare getline: ssize_t getline(char **lineptr, size_t *n, FILE *stream)"""
    if "getline" in module.globals:
        return module.globals["getline"]

    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i64, [i8_ptr.as_pointer(), i64.as_pointer(), i8_ptr])
    return ir.Function(module, fn_ty, name="getline")


def declare_fgets(module: ir.Module) -> ir.Function:
    """Declare fgets: char* fgets(char* str, int n, FILE* stream)"""
    if "fgets" in module.globals:
        return module.globals["fgets"]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i8_ptr, [i8_ptr, i32, i8_ptr])
    return ir.Function(module, fn_ty, name="fgets")


def declare_fread(module: ir.Module) -> ir.Function:
    """Declare fread: size_t fread(void* ptr, size_t size, size_t nmemb, FILE* stream)"""
    if "fread" in module.globals:
        return module.globals["fread"]

    i64 = ir.IntType(64)  # size_t
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i64, [i8_ptr, i64, i64, i8_ptr])
    return ir.Function(module, fn_ty, name="fread")


def declare_fwrite(module: ir.Module) -> ir.Function:
    """Declare fwrite: size_t fwrite(const void* ptr, size_t size, size_t nmemb, FILE* stream)"""
    if "fwrite" in module.globals:
        return module.globals["fwrite"]

    i64 = ir.IntType(64)  # size_t
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i64, [i8_ptr, i64, i64, i8_ptr])
    return ir.Function(module, fn_ty, name="fwrite")


def declare_fgetc(module: ir.Module) -> ir.Function:
    """Declare fgetc: int fgetc(FILE* stream)"""
    if "fgetc" in module.globals:
        return module.globals["fgetc"]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    return ir.Function(module, fn_ty, name="fgetc")


def declare_fclose(module: ir.Module) -> ir.Function:
    """Declare fclose: int fclose(FILE* stream)"""
    if "fclose" in module.globals:
        return module.globals["fclose"]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    return ir.Function(module, fn_ty, name="fclose")


def declare_fseek(module: ir.Module) -> ir.Function:
    """Declare fseek: int fseek(FILE* stream, long offset, int whence)"""
    if "fseek" in module.globals:
        return module.globals["fseek"]

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)  # long is i64 on 64-bit systems
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i64, i32])
    return ir.Function(module, fn_ty, name="fseek")


def declare_ftell(module: ir.Module) -> ir.Function:
    """Declare ftell: long ftell(FILE* stream)"""
    if "ftell" in module.globals:
        return module.globals["ftell"]

    i64 = ir.IntType(64)  # long is i64 on 64-bit systems
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i64, [i8_ptr])
    return ir.Function(module, fn_ty, name="ftell")


def declare_feof(module: ir.Module) -> ir.Function:
    """Declare feof: int feof(FILE* stream)"""
    if "feof" in module.globals:
        return module.globals["feof"]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    return ir.Function(module, fn_ty, name="feof")


# ==============================================================================
# Process Control
# ==============================================================================

def declare_exit(module: ir.Module) -> ir.Function:
    """Declare exit: void exit(int status)"""
    if "exit" in module.globals:
        return module.globals["exit"]

    void = ir.VoidType()
    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(void, [i32])
    return ir.Function(module, fn_ty, name="exit")
