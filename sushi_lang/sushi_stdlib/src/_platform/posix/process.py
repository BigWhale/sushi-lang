"""POSIX process control function declarations."""

from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_exit, declare_extern

_i8_ptr = ir.IntType(8).as_pointer()
_i32 = ir.IntType(32)
_i64 = ir.IntType(64)

__all__ = [
    "declare_getcwd", "declare_chdir", "declare_exit", "declare_getpid", "declare_getuid",
    "declare_tmpfile", "declare_fileno", "declare_waitpid", "declare_posix_spawnp",
    "declare_posix_spawn_file_actions_init", "declare_posix_spawn_file_actions_adddup2",
    "declare_posix_spawn_file_actions_destroy", "get_environ",
]


def declare_getcwd(module: ir.Module) -> ir.Function:
    """Declare getcwd: char* getcwd(char *buf, size_t size)"""
    return declare_extern(module, "getcwd", _i8_ptr, [_i8_ptr, _i64])


def declare_chdir(module: ir.Module) -> ir.Function:
    """Declare chdir: int chdir(const char *path)"""
    return declare_extern(module, "chdir", _i32, [_i8_ptr])


def declare_getpid(module: ir.Module) -> ir.Function:
    """Declare getpid: pid_t getpid(void)"""
    return declare_extern(module, "getpid", _i32, [])


def declare_getuid(module: ir.Module) -> ir.Function:
    """Declare getuid: uid_t getuid(void)"""
    return declare_extern(module, "getuid", _i32, [])


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
    return declare_extern(module, "tmpfile", _i8_ptr, [])


def declare_fileno(module: ir.Module) -> ir.Function:
    """Declare fileno: int fileno(FILE*)."""
    return declare_extern(module, "fileno", _i32, [_i8_ptr])


def declare_waitpid(module: ir.Module) -> ir.Function:
    """Declare waitpid: pid_t waitpid(pid_t pid, int *status, int options)."""
    return declare_extern(module, "waitpid", _i32, [_i32, _i32.as_pointer(), _i32])


def declare_posix_spawnp(module: ir.Module) -> ir.Function:
    """Declare posix_spawnp:"""
    char_pp = _i8_ptr.as_pointer()
    return declare_extern(module, "posix_spawnp", _i32,
                          [_i32.as_pointer(), _i8_ptr, _i8_ptr, _i8_ptr, char_pp, char_pp])


def declare_posix_spawn_file_actions_init(module: ir.Module) -> ir.Function:
    """Declare int posix_spawn_file_actions_init(posix_spawn_file_actions_t *)."""
    return declare_extern(module, "posix_spawn_file_actions_init", _i32, [_i8_ptr])


def declare_posix_spawn_file_actions_adddup2(module: ir.Module) -> ir.Function:
    """Declare int posix_spawn_file_actions_adddup2(posix_spawn_file_actions_t *, int fd, int newfd)."""
    return declare_extern(module, "posix_spawn_file_actions_adddup2", _i32, [_i8_ptr, _i32, _i32])


def declare_posix_spawn_file_actions_destroy(module: ir.Module) -> ir.Function:
    """Declare int posix_spawn_file_actions_destroy(posix_spawn_file_actions_t *)."""
    return declare_extern(module, "posix_spawn_file_actions_destroy", _i32, [_i8_ptr])


def get_environ(module: ir.Module) -> ir.GlobalVariable:
    """Get the external `char **environ` global (the process environment)."""
    if "environ" in module.globals:
        return module.globals["environ"]
    i8_ptr = ir.IntType(8).as_pointer()
    g = ir.GlobalVariable(module, i8_ptr.as_pointer(), name="environ")
    g.linkage = "external"
    return g
