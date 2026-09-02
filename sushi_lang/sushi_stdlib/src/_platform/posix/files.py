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


def declare_lstat(module: ir.Module, name: str = "lstat") -> ir.Function:
    """Declare POSIX lstat() syscall; the name parameter mirrors declare_stat."""
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


def declare_pread(module: ir.Module) -> ir.Function:
    """Declare POSIX pread(): `ssize_t pread(int, void *, size_t, off_t)`.

    The POSITIONAL read. The offset is an argument, so the descriptor's own file position
    never moves and two readers of one descriptor cannot race over it -- the reason every
    language that supports concurrent file I/O converged on this primitive rather than on
    a new kind of type (HANDLES.md, Phase 8).

    `off_t` is 64-bit on both supported platforms: probe P6 measured 8 bytes on macOS
    arm64 and on Linux x86_64, where plain `pread` already IS the wide-offset entry point.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i64, [i32, i8_ptr, i64, i64])

    try:
        return module.get_global("pread")
    except KeyError:
        return ir.Function(module, func_type, name="pread")


def declare_pwrite(module: ir.Module) -> ir.Function:
    """Declare POSIX pwrite(): `ssize_t pwrite(int, const void *, size_t, off_t)`.

    The positional write, and `declare_pread`'s twin in every respect.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i64, [i32, i8_ptr, i64, i64])

    try:
        return module.get_global("pwrite")
    except KeyError:
        return ir.Function(module, func_type, name="pwrite")


def declare_lseek(module: ir.Module) -> ir.Function:
    """Declare POSIX lseek(): `off_t lseek(int, off_t, int)`.

    `off_t` is 64-bit on both supported platforms, which probe P6 measured for `pread`;
    the same answer holds here, so the offset and the result are both i64.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i64, [i32, i64, i32])

    try:
        return module.get_global("lseek")
    except KeyError:
        return ir.Function(module, func_type, name="lseek")


def declare_isatty(module: ir.Module) -> ir.Function:
    """Declare POSIX isatty(): `int isatty(int)`.

    Asking cannot fail in any way a caller can act on -- a descriptor that is not a
    terminal and a descriptor that is not open both answer 0 -- so `fd_isatty` gives a
    bare bool rather than a Result.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i32])

    try:
        return module.get_global("isatty")
    except KeyError:
        return ir.Function(module, func_type, name="isatty")


def declare_dup(module: ir.Module) -> ir.Function:
    """Declare POSIX dup(): `int dup(int)`.

    A SECOND descriptor over the SAME open file description. The offset is shared, so
    this is the shared-listener primitive and not the answer for concurrent reads of one
    file -- `pread`/`pwrite` are that. `.share()` is built on it in Phase 8.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    func_type = ir.FunctionType(i32, [i32])

    try:
        return module.get_global("dup")
    except KeyError:
        return ir.Function(module, func_type, name="dup")


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
