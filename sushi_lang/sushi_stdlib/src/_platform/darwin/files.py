"""Platform-specific file system declarations for macOS."""
from llvmlite import ir
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.sushi_stdlib.src._platform.posix.files import (
    declare_stat as _declare_stat_posix,
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

O_RDONLY = 0
O_WRONLY = 1
O_CREAT = 0x0200
O_TRUNC = 0x0400

# struct stat offsets, verified with an offsetof probe (2026-08-29). The layout
# is the 64-bit-inode struct; on x86_64 that layout lives behind stat$INODE64,
# while the bare stat symbol is the legacy 32-bit-inode entry point.
ST_MODE_OFFSET = 4     # u16
ST_SIZE_OFFSET = 96    # i64
ST_MTIME_OFFSET = 48   # struct timespec {i64 sec, i64 nsec}
ST_CTIME_OFFSET = 64   # struct timespec

# errno values that differ from Linux
ENAMETOOLONG = 63
ELOOP = 62


def declare_stat(module: ir.Module) -> ir.Function:
    """Declare stat with the 64-bit-inode symbol for the arch."""
    name = "stat$INODE64" if get_current_platform().arch == "x86_64" else "stat"
    return _declare_stat_posix(module, name)

__all__ = [
    "declare_stat",
    "declare_access",
    "declare_unlink",
    "declare_rename",
    "declare_open",
    "declare_read",
    "declare_write",
    "declare_close",
    "declare_mkdir",
    "declare_rmdir",
]
