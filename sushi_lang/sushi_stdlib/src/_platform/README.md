# Platform-Specific Implementations (Internal)

This directory contains internal platform-specific code used by stdlib modules. User code should never import from `_platform` directly - these are implementation details.

The `_` prefix indicates this is an internal package not part of the public stdlib API.

## Philosophy

We follow the **"libc first"** principle:

1. **Use POSIX/libc when available** - Most Unix-like systems (macOS, Linux, BSD) provide compatible libc implementations
2. **External declarations** - Declare functions as external, let the linker resolve them from system libraries
3. **Avoid direct syscalls** - Syscall numbers are unstable (especially on macOS); use stable libc ABI
4. **Platform-specific only when necessary** - 95% of code should be portable

## Why Not Direct Syscalls?

### On macOS
- Apple explicitly states: "We do not make any guarantees" about syscall stability
- Syscall numbers are private and change between OS versions
- macOS forces dynamic linking against libSystem anyway
- Functions like `nanosleep` use complex Mach kernel primitives, not simple syscalls

### On Linux
- Direct syscalls ARE stable, but libc wrappers provide:
  - Type safety
  - Error handling
  - Compatibility across kernel versions
  - Same approach as macOS (consistent)

## Directory Structure

```
_platform/
├── __init__.py         # Dynamic platform module loader (get_platform_module)
├── README.md           # This file
├── darwin/             # macOS platform implementations
│   ├── __init__.py
│   └── time.py         # POSIX time declarations (nanosleep)
├── linux/              # Linux platform implementations (future)
│   ├── __init__.py
│   └── time.py         # POSIX time declarations
└── windows/            # Windows platform implementations (future)
    ├── __init__.py
    └── time.py         # Windows time declarations (kernel32 Sleep)
```

## Usage

Stdlib modules use `get_platform_module()` to dynamically load the correct platform implementation:

```python
from stdlib.src._platform import get_platform_module

# Automatically selects darwin/linux/windows based on build platform
platform_time = get_platform_module('time')
declare_nanosleep = platform_time.declare_nanosleep
```

This ensures the correct platform-specific declarations are used at build time without hardcoding platform names.

## References

- Rust's approach: `std/sys/unix/`, `std/sys/windows/`
- Zig's macOS strategy: "libSystem is macOS's syscall ABI"
- Go's runtime: Uses pthread APIs on Darwin
- Apple's position: "The stable ABI boundary is libSystem.dylib"
