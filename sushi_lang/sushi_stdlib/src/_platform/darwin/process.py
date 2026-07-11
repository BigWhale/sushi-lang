"""Platform-specific process control declarations for macOS."""
from sushi_lang.sushi_stdlib.src._platform.posix.process import (
    declare_getcwd,
    declare_chdir,
    declare_exit,
    declare_getpid,
    declare_getuid,
    declare_tmpfile,
    declare_fileno,
    declare_waitpid,
    declare_posix_spawnp,
    declare_posix_spawn_file_actions_init,
    declare_posix_spawn_file_actions_adddup2,
    declare_posix_spawn_file_actions_destroy,
    get_environ,
)

# Re-exported for the platform dispatcher (get_platform_module); referenced dynamically.
__all__ = [
    "declare_getcwd",
    "declare_chdir",
    "declare_exit",
    "declare_getpid",
    "declare_getuid",
    "declare_tmpfile",
    "declare_fileno",
    "declare_waitpid",
    "declare_posix_spawnp",
    "declare_posix_spawn_file_actions_init",
    "declare_posix_spawn_file_actions_adddup2",
    "declare_posix_spawn_file_actions_destroy",
    "get_environ",
]
