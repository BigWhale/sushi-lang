"""Platform-specific file system declarations for Linux."""
from sushi_lang.sushi_stdlib.src._platform.posix.files import (
    declare_stat,
    declare_access,
    declare_unlink,
    declare_rename,
    declare_open,
    declare_read,
    declare_write,
    declare_close,
    declare_mkdir,
    declare_rmdir,
)

# Linux-specific constants for open() flags
O_RDONLY = 0
O_WRONLY = 1
O_CREAT = 0x40
O_TRUNC = 0x200
