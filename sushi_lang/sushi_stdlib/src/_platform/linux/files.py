"""Platform-specific file system declarations for Linux."""
from sushi_lang.backend.platform_detect import get_current_platform as _plat
from sushi_lang.sushi_stdlib.src._platform.posix.files import (
    declare_stat,
    declare_lstat,
    declare_opendir,
    declare_readdir,
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
O_CREAT = 0x40
O_TRUNC = 0x200
# The two the descriptor layer adds (HANDLES.md, Phase 4). A portable Sushi
# module cannot spell these -- they differ between platforms -- so the intent
# crosses the boundary and `generate_fd_open` maps it here.
O_RDWR = 0x0002
O_APPEND = 0x0400

# struct stat offsets, verified with an offsetof probe (2026-08-29, glibc).
# st_mode sits at 24 on x86_64 and at 16 on aarch64 (st_ino is followed by
# st_nlink on x86_64 and the order flips on aarch64); the rest agree.
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
