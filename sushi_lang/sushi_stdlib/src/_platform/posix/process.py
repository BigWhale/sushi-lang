"""POSIX process control function declarations.

Provides declarations for standard POSIX process management functions:
- getcwd(): Get current working directory
- chdir(): Change working directory
- exit(): Terminate process
- getpid(): Get process ID
- getuid(): Get user ID
"""

from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types


def declare_getcwd(module: ir.Module) -> ir.Function:
    """Declare getcwd: char* getcwd(char *buf, size_t size)

    POSIX Signature:
        char *getcwd(char *buf, size_t size);

    Returns:
        Pointer to the buffer containing the current working directory path.
        NULL on error (sets errno).

    Portability:
        ✅ macOS (via libSystem)
        ✅ Linux (via glibc, musl)
        ✅ Any POSIX.1-2001 compliant system
    """
    if "getcwd" in module.globals:
        return module.globals["getcwd"]

    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i8_ptr, [i8_ptr, i64])
    func = ir.Function(module, fn_ty, name="getcwd")
    return func


def declare_chdir(module: ir.Module) -> ir.Function:
    """Declare chdir: int chdir(const char *path)

    POSIX Signature:
        int chdir(const char *path);

    Returns:
        0 on success, -1 on error (sets errno).

    Portability:
        ✅ macOS (via libSystem)
        ✅ Linux (via glibc, musl)
        ✅ Any POSIX.1-2001 compliant system
    """
    if "chdir" in module.globals:
        return module.globals["chdir"]

    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    func = ir.Function(module, fn_ty, name="chdir")
    return func


def declare_exit(module: ir.Module) -> ir.Function:
    """Declare exit: void exit(int status)

    C Standard Library Signature:
        void exit(int status);

    Terminates the process immediately with the given exit code.
    Never returns.

    Portability:
        ✅ macOS (via libSystem)
        ✅ Linux (via glibc, musl)
        ✅ Any C standard library
    """
    if "exit" in module.globals:
        return module.globals["exit"]

    i8, i8_ptr, i32, i64 = get_basic_types()
    void = ir.VoidType()
    fn_ty = ir.FunctionType(void, [i32])
    func = ir.Function(module, fn_ty, name="exit")
    return func


def declare_getpid(module: ir.Module) -> ir.Function:
    """Declare getpid: pid_t getpid(void)

    POSIX Signature:
        pid_t getpid(void);

    Returns:
        The process ID of the calling process (always successful).

    Note:
        pid_t is typically int (i32) on all platforms.

    Portability:
        ✅ macOS (via libSystem)
        ✅ Linux (via glibc, musl)
        ✅ Any POSIX.1-2001 compliant system
    """
    if "getpid" in module.globals:
        return module.globals["getpid"]

    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i32, [])
    func = ir.Function(module, fn_ty, name="getpid")
    return func


def declare_getuid(module: ir.Module) -> ir.Function:
    """Declare getuid: uid_t getuid(void)

    POSIX Signature:
        uid_t getuid(void);

    Returns:
        The real user ID of the calling process (always successful).

    Note:
        uid_t is typically unsigned int (u32), but we use i32 for simplicity.

    Portability:
        ✅ macOS (via libSystem)
        ✅ Linux (via glibc, musl)
        ✅ Any POSIX.1-2001 compliant system
    """
    if "getuid" in module.globals:
        return module.globals["getuid"]

    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i32, [])
    func = ir.Function(module, fn_ty, name="getuid")
    return func
