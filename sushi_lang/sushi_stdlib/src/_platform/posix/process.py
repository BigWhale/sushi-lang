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


# ==============================================================================
# Subprocess spawning (used by run())
# ==============================================================================
# These back the safe `run(cmd, args) -> Result<ProcessOutput, ProcessError>`
# primitive. Implementation uses posix_spawnp (PATH-searched, no shell) with the
# child's stdout/stderr redirected to tmpfile() handles via posix_spawn file
# actions, then waitpid to collect the exit status. posix_spawnp reports an exec
# failure (e.g. command not found -> ENOENT) directly in its return value on both
# macOS (libSystem) and Linux (glibc/musl), so no self-pipe is needed.


def declare_tmpfile(module: ir.Module) -> ir.Function:
    """Declare tmpfile: FILE* tmpfile(void). Auto-unlinked on fclose."""
    if "tmpfile" in module.globals:
        return module.globals["tmpfile"]
    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i8_ptr, [])
    return ir.Function(module, fn_ty, name="tmpfile")


def declare_fileno(module: ir.Module) -> ir.Function:
    """Declare fileno: int fileno(FILE*)."""
    if "fileno" in module.globals:
        return module.globals["fileno"]
    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    return ir.Function(module, fn_ty, name="fileno")


def declare_waitpid(module: ir.Module) -> ir.Function:
    """Declare waitpid: pid_t waitpid(pid_t pid, int *status, int options)."""
    if "waitpid" in module.globals:
        return module.globals["waitpid"]
    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i32, [i32, i32.as_pointer(), i32])
    return ir.Function(module, fn_ty, name="waitpid")


def declare_posix_spawnp(module: ir.Module) -> ir.Function:
    """Declare posix_spawnp:

        int posix_spawnp(pid_t *pid, const char *file,
                         const posix_spawn_file_actions_t *file_actions,
                         const posix_spawnattr_t *attrp,
                         char *const argv[], char *const envp[]);

    The opaque file_actions/attr pointers are passed as i8*. Returns 0 on success
    (and sets *pid); an errno on failure (e.g. ENOENT).
    """
    if "posix_spawnp" in module.globals:
        return module.globals["posix_spawnp"]
    i8, i8_ptr, i32, i64 = get_basic_types()
    char_pp = i8_ptr.as_pointer()
    fn_ty = ir.FunctionType(i32, [i32.as_pointer(), i8_ptr, i8_ptr, i8_ptr, char_pp, char_pp])
    return ir.Function(module, fn_ty, name="posix_spawnp")


def declare_posix_spawn_file_actions_init(module: ir.Module) -> ir.Function:
    """Declare int posix_spawn_file_actions_init(posix_spawn_file_actions_t *)."""
    if "posix_spawn_file_actions_init" in module.globals:
        return module.globals["posix_spawn_file_actions_init"]
    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    return ir.Function(module, fn_ty, name="posix_spawn_file_actions_init")


def declare_posix_spawn_file_actions_adddup2(module: ir.Module) -> ir.Function:
    """Declare int posix_spawn_file_actions_adddup2(posix_spawn_file_actions_t *, int fd, int newfd)."""
    if "posix_spawn_file_actions_adddup2" in module.globals:
        return module.globals["posix_spawn_file_actions_adddup2"]
    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i32, i32])
    return ir.Function(module, fn_ty, name="posix_spawn_file_actions_adddup2")


def declare_posix_spawn_file_actions_destroy(module: ir.Module) -> ir.Function:
    """Declare int posix_spawn_file_actions_destroy(posix_spawn_file_actions_t *)."""
    if "posix_spawn_file_actions_destroy" in module.globals:
        return module.globals["posix_spawn_file_actions_destroy"]
    i8, i8_ptr, i32, i64 = get_basic_types()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    return ir.Function(module, fn_ty, name="posix_spawn_file_actions_destroy")


def get_environ(module: ir.Module) -> ir.GlobalVariable:
    """Get the external `char **environ` global (the process environment).

    Needed so posix_spawnp inherits PATH etc. for the child. Links against the
    always-present libc/libSystem symbol on both macOS and Linux.
    """
    if "environ" in module.globals:
        return module.globals["environ"]
    i8_ptr = ir.IntType(8).as_pointer()
    g = ir.GlobalVariable(module, i8_ptr.as_pointer(), name="environ")
    g.linkage = "external"
    return g
