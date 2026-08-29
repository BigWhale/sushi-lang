"""Platform-specific file system declarations for Linux."""
from sushi_lang.sushi_stdlib.src._platform.posix.files import (
    declare_stat,
    declare_opendir,
    declare_readdir,
    declare_closedir,
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
O_CREAT = 0x40
O_TRUNC = 0x200

# struct stat offsets, verified with an offsetof probe (2026-08-29, glibc).
# st_mode sits at 24 on x86_64 and at 16 on aarch64 (st_ino is followed by
# st_nlink on x86_64 and the order flips on aarch64); the rest agree.
from sushi_lang.backend.platform_detect import get_current_platform as _plat

ST_MODE_OFFSET = 24 if _plat().arch == "x86_64" else 16   # u32; values fit u16
ST_SIZE_OFFSET = 48    # i64
ST_MTIME_OFFSET = 88   # struct timespec {i64 sec, i64 nsec}
ST_CTIME_OFFSET = 104  # struct timespec

# errno values that differ from macOS
ENAMETOOLONG = 36
ELOOP = 40

# struct dirent: d_name offset, verified with an offsetof probe (2026-08-29, glibc).
# Linux: d_ino(8) d_off(8) d_reclen(2) d_type(1), name at 19; both arches agree.
DIRENT_NAME_OFFSET = 19

__all__ = [
    "declare_stat",
    "declare_opendir",
    "declare_readdir",
    "declare_closedir",
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
