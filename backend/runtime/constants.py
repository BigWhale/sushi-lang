"""
Constants for runtime operations.

This module defines constants used throughout the runtime system:
- errno constants from <errno.h>
- Format string specifications for printf/sprintf
"""
from __future__ import annotations

# errno constants (from errno.h)
# These values are standard across most UNIX-like systems
# Used for mapping system errors to Sushi FileError enum variants
ERRNO_EPERM = 1           # Operation not permitted
ERRNO_ENOENT = 2          # No such file or directory
ERRNO_EIO = 5             # I/O error
ERRNO_EACCES = 13         # Permission denied
ERRNO_EEXIST = 17         # File exists
ERRNO_EISDIR = 21         # Is a directory
ERRNO_EMFILE = 24         # Too many open files
ERRNO_ENOSPC = 28         # No space left on device
ERRNO_ENAMETOOLONG = 63   # File name too long (macOS)

# errno to FileError variant mapping
# Maps errno values to FileError enum variant indices
# Must match the order defined in CollectorPass._register_predefined_enums
ERRNO_TO_FILE_ERROR = {
    ERRNO_ENOENT: 0,        # NotFound
    ERRNO_EPERM: 1,         # PermissionDenied
    ERRNO_EACCES: 1,        # PermissionDenied
    ERRNO_EEXIST: 2,        # AlreadyExists
    ERRNO_EISDIR: 3,        # IsDirectory
    ERRNO_ENOSPC: 4,        # DiskFull
    ERRNO_EMFILE: 5,        # TooManyOpen
    ERRNO_ENAMETOOLONG: 6,  # InvalidPath
    ERRNO_EIO: 7,           # IOError
    # Default: 8 (Other) - handled separately
}

# Default FileError variant for unmapped errno values
ERRNO_DEFAULT_FILE_ERROR = 8  # Other

# Format string specifications for printf/sprintf operations
# Maps type name to format string
FORMAT_STRINGS = {
    "i32": "%d",
    "i64": "%lld",
    "u32": "%u",
    "u64": "%llu",
    "str": "%s",
    "f32": "%g",
    "f64": "%g",
    "bool_true": "true",
    "bool_false": "false",
}
