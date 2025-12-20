"""Platform-specific process control declarations for Linux."""
from sushi_lang.sushi_stdlib.src._platform.posix.process import (
    declare_getcwd,
    declare_chdir,
    declare_exit,
    declare_getpid,
    declare_getuid,
)
