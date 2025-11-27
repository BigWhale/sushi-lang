"""Platform-specific file system declarations for macOS."""
from stdlib.src._platform.posix.files import (
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

# macOS-specific constants for open() flags
O_RDONLY = 0
O_WRONLY = 1
O_CREAT = 0x0200
O_TRUNC = 0x0400
