"""Platform-specific file system declarations for macOS."""
from llvmlite import ir
from stdlib.src.type_definitions import get_basic_types


def declare_stat(module: ir.Module) -> ir.Function:
    """Declare POSIX stat() syscall.

    C signature: int stat(const char *path, struct stat *buf)
    LLVM signature: i32 @stat(i8*, i8*)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i8_ptr])

    try:
        return module.get_global("stat")
    except KeyError:
        return ir.Function(module, func_type, name="stat")


def declare_access(module: ir.Module) -> ir.Function:
    """Declare POSIX access() syscall.

    C signature: int access(const char *path, int mode)
    LLVM signature: i32 @access(i8*, i32)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i32])

    try:
        return module.get_global("access")
    except KeyError:
        return ir.Function(module, func_type, name="access")


def declare_unlink(module: ir.Module) -> ir.Function:
    """Declare POSIX unlink() syscall.

    C signature: int unlink(const char *path)
    LLVM signature: i32 @unlink(i8*)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr])

    try:
        return module.get_global("unlink")
    except KeyError:
        return ir.Function(module, func_type, name="unlink")


def declare_rename(module: ir.Module) -> ir.Function:
    """Declare POSIX rename() syscall.

    C signature: int rename(const char *old, const char *new)
    LLVM signature: i32 @rename(i8*, i8*)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i8_ptr])

    try:
        return module.get_global("rename")
    except KeyError:
        return ir.Function(module, func_type, name="rename")


def declare_open(module: ir.Module) -> ir.Function:
    """Declare POSIX open() syscall.

    C signature: int open(const char *path, int flags, mode_t mode)
    LLVM signature: i32 @open(i8*, i32, i32)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i32, i32])

    try:
        return module.get_global("open")
    except KeyError:
        return ir.Function(module, func_type, name="open")


def declare_read(module: ir.Module) -> ir.Function:
    """Declare POSIX read() syscall.

    C signature: ssize_t read(int fd, void *buf, size_t count)
    LLVM signature: i64 @read(i32, i8*, i64)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i64, [i32, i8_ptr, i64])

    try:
        return module.get_global("read")
    except KeyError:
        return ir.Function(module, func_type, name="read")


def declare_write(module: ir.Module) -> ir.Function:
    """Declare POSIX write() syscall.

    C signature: ssize_t write(int fd, const void *buf, size_t count)
    LLVM signature: i64 @write(i32, i8*, i64)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i64, [i32, i8_ptr, i64])

    try:
        return module.get_global("write")
    except KeyError:
        return ir.Function(module, func_type, name="write")


def declare_close(module: ir.Module) -> ir.Function:
    """Declare POSIX close() syscall.

    C signature: int close(int fd)
    LLVM signature: i32 @close(i32)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i32])

    try:
        return module.get_global("close")
    except KeyError:
        return ir.Function(module, func_type, name="close")


def declare_mkdir(module: ir.Module) -> ir.Function:
    """Declare POSIX mkdir() syscall.

    C signature: int mkdir(const char *path, mode_t mode)
    LLVM signature: i32 @mkdir(i8*, i32)

    Note: mode_t is uint16_t on macOS, uint32_t on Linux.
    We use i32 for compatibility.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i32])

    try:
        return module.get_global("mkdir")
    except KeyError:
        return ir.Function(module, func_type, name="mkdir")


def declare_rmdir(module: ir.Module) -> ir.Function:
    """Declare POSIX rmdir() syscall.

    C signature: int rmdir(const char *path)
    LLVM signature: i32 @rmdir(i8*)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr])

    try:
        return module.get_global("rmdir")
    except KeyError:
        return ir.Function(module, func_type, name="rmdir")


# Platform-specific constants for copy() implementation
O_RDONLY = 0
O_WRONLY = 1
O_CREAT = 0x0200
O_TRUNC = 0x0400
