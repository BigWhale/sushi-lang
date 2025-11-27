"""Platform-specific process control declarations for macOS."""
from stdlib.src._platform.posix.process import (
    declare_getcwd,
    declare_chdir,
    declare_exit,
    declare_getpid,
    declare_getuid,
)
