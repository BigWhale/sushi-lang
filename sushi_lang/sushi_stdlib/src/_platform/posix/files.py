"""Common POSIX file system function declarations."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types


def declare_stat(module: ir.Module, name: str = "stat") -> ir.Function:
    """Declare POSIX stat() syscall. The symbol name is a parameter because
    macOS x86_64 exports the 64-bit-inode layout as stat$INODE64."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i8_ptr])

    try:
        return module.get_global(name)
    except KeyError:
        return ir.Function(module, func_type, name=name)


def declare_access(module: ir.Module) -> ir.Function:
    """Declare POSIX access() syscall."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i32])

    try:
        return module.get_global("access")
    except KeyError:
        return ir.Function(module, func_type, name="access")


def declare_unlink(module: ir.Module) -> ir.Function:
    """Declare POSIX unlink() syscall."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr])

    try:
        return module.get_global("unlink")
    except KeyError:
        return ir.Function(module, func_type, name="unlink")


def declare_rename(module: ir.Module) -> ir.Function:
    """Declare POSIX rename() syscall."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i8_ptr])

    try:
        return module.get_global("rename")
    except KeyError:
        return ir.Function(module, func_type, name="rename")


def declare_open(module: ir.Module) -> ir.Function:
    """Declare POSIX open() syscall: `int open(const char *, int, ...)`.

    VARIADIC, as C declares it. Declared with a fixed third parameter, the mode went in a
    register while Apple arm64 expects a variadic argument on the stack, so the callee read
    the mode from the wrong place -- `copy()` produced whatever happened to be there (#363).
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i32], var_arg=True)

    try:
        return module.get_global("open")
    except KeyError:
        return ir.Function(module, func_type, name="open")


def declare_read(module: ir.Module) -> ir.Function:
    """Declare POSIX read() syscall."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i64, [i32, i8_ptr, i64])

    try:
        return module.get_global("read")
    except KeyError:
        return ir.Function(module, func_type, name="read")


def declare_write(module: ir.Module) -> ir.Function:
    """Declare POSIX write() syscall."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i64, [i32, i8_ptr, i64])

    try:
        return module.get_global("write")
    except KeyError:
        return ir.Function(module, func_type, name="write")


def declare_close(module: ir.Module) -> ir.Function:
    """Declare POSIX close() syscall."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i32])

    try:
        return module.get_global("close")
    except KeyError:
        return ir.Function(module, func_type, name="close")


def declare_mkdir(module: ir.Module) -> ir.Function:
    """Declare POSIX mkdir() syscall."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr, i32])

    try:
        return module.get_global("mkdir")
    except KeyError:
        return ir.Function(module, func_type, name="mkdir")


def declare_rmdir(module: ir.Module) -> ir.Function:
    """Declare POSIX rmdir() syscall."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr])

    try:
        return module.get_global("rmdir")
    except KeyError:
        return ir.Function(module, func_type, name="rmdir")


def declare_opendir(module: ir.Module) -> ir.Function:
    """Declare POSIX opendir(): DIR* is opaque, an i8* here."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i8_ptr, [i8_ptr])

    try:
        return module.get_global("opendir")
    except KeyError:
        return ir.Function(module, func_type, name="opendir")


def declare_readdir(module: ir.Module, name: str = "readdir") -> ir.Function:
    """Declare POSIX readdir(). The symbol name is a parameter because macOS
    x86_64 exports the 64-bit-inode layout as readdir$INODE64."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i8_ptr, [i8_ptr])

    try:
        return module.get_global(name)
    except KeyError:
        return ir.Function(module, func_type, name=name)


def declare_closedir(module: ir.Module) -> ir.Function:
    """Declare POSIX closedir()."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i8_ptr])

    try:
        return module.get_global("closedir")
    except KeyError:
        return ir.Function(module, func_type, name="closedir")
