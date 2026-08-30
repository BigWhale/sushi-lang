"""Platform-specific file system declarations for macOS."""
from llvmlite import ir
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.sushi_stdlib.src._platform.posix.files import (
    declare_stat as _declare_stat_posix,
    declare_readdir as _declare_readdir_posix,
    declare_lstat as _declare_lstat_posix,
    declare_opendir,
    declare_closedir,
    declare_access,
    declare_unlink,
    declare_rename,
    declare_open,
    declare_read,
    declare_write,
    declare_pread,
    declare_pwrite,
    declare_dup,
    declare_close,
    declare_mkdir,
    declare_rmdir,
)

O_RDONLY = 0
O_WRONLY = 1
O_CREAT = 0x0200
O_TRUNC = 0x0400
# The two the descriptor layer adds (HANDLES.md, Phase 4). A portable Sushi
# module cannot spell these -- they differ between platforms -- so the intent
# crosses the boundary and `generate_fd_open` maps it here.
O_RDWR = 0x0002
O_APPEND = 0x0008

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


# struct dirent: d_name offset, verified with an offsetof probe (2026-08-29).
# macOS: d_ino(8) d_seekoff(8) d_reclen(2) d_namlen(2) d_type(1), name at 21.
DIRENT_NAME_OFFSET = 21


def declare_stat(module: ir.Module) -> ir.Function:
    """Declare stat with the 64-bit-inode symbol for the arch."""
    name = "stat$INODE64" if get_current_platform().arch == "x86_64" else "stat"
    return _declare_stat_posix(module, name)


def declare_readdir(module: ir.Module) -> ir.Function:
    """Declare readdir with the 64-bit-inode symbol for the arch."""
    name = "readdir$INODE64" if get_current_platform().arch == "x86_64" else "readdir"
    return _declare_readdir_posix(module, name)


def declare_lstat(module: ir.Module) -> ir.Function:
    """Declare lstat with the 64-bit-inode symbol for the arch."""
    name = "lstat$INODE64" if get_current_platform().arch == "x86_64" else "lstat"
    return _declare_lstat_posix(module, name)

__all__ = [
    "declare_stat",
    "declare_lstat",
    "declare_opendir",
    "declare_readdir",
    "declare_closedir",
    "declare_access",
    "declare_unlink",
    "declare_rename",
    "declare_open",
    "declare_read",
    "declare_write",
    "declare_pread",
    "declare_pwrite",
    "declare_dup",
    "declare_close",
    "declare_mkdir",
    "declare_rmdir",
]
