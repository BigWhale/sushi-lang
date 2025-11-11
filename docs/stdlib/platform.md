# Platform Detection System

[← Back to Architecture](../internals/architecture.md)

Internal documentation for Sushi's platform detection and cross-platform support.

## Overview

The platform detection system enables the compiler and standard library to select appropriate platform-specific implementations at build time. This allows Sushi to support macOS, Linux, and Windows with a single codebase.

## Platform Detection

### Target Triple Parsing

**File:** `backend/platform_detect.py`

The compiler parses LLVM target triples to determine the target platform.

**Target Triple Format:**
```
<arch>-<vendor>-<os>-<abi>
```

**Examples:**
- `arm64-apple-darwin22.0.0` - macOS on Apple Silicon
- `x86_64-pc-linux-gnu` - Linux on x86-64 with GNU libc
- `x86_64-pc-windows-msvc` - Windows on x86-64 with MSVC

### TargetPlatform Class

```python
@dataclass
class TargetPlatform:
    arch: str      # arm64, x86_64, i686, etc.
    vendor: str    # apple, pc, unknown
    os: str        # darwin, linux, windows
    abi: str       # gnu, musl, msvc, etc. (optional)

    @property
    def is_unix(self) -> bool:
        """True for darwin and linux"""
        return self.os in ('darwin', 'linux')

    @property
    def is_darwin(self) -> bool:
        """True for macOS"""
        return self.os == 'darwin'

    @property
    def is_linux(self) -> bool:
        """True for Linux"""
        return self.os == 'linux'

    @property
    def is_windows(self) -> bool:
        """True for Windows"""
        return self.os == 'windows'
```

### Functions

#### parse_triple

```python
def parse_triple(triple: str) -> TargetPlatform:
    """
    Parse LLVM target triple into components.

    Examples:
        >>> parse_triple("arm64-apple-darwin22.0.0")
        TargetPlatform(arch='arm64', vendor='apple', os='darwin', abi='')

        >>> parse_triple("x86_64-pc-linux-gnu")
        TargetPlatform(arch='x86_64', vendor='pc', os='linux', abi='gnu')
    """
```

#### get_current_platform

```python
def get_current_platform() -> TargetPlatform:
    """
    Detect the current host platform.

    Uses llvmlite to get the default target triple.

    Returns:
        TargetPlatform: Parsed platform information
    """
    import llvmlite.binding as llvm
    llvm.initialize()
    llvm.initialize_native_target()
    triple = llvm.get_default_triple()
    return parse_triple(triple)
```

## Standard Library Platform Support

### Platform-Specific Implementations

**Directory Structure:**
```
stdlib/src/_platform/
├── __init__.py           # get_platform_module() helper
├── darwin/               # macOS implementations
│   ├── env.py           # getenv/setenv
│   ├── stdio.py         # stdin/stdout/stderr handles
│   └── time.py          # nanosleep
├── linux/               # Linux implementations
│   ├── env.py
│   ├── stdio.py
│   └── time.py
└── windows/             # Windows implementations (planned)
    ├── env.py
    ├── stdio.py
    └── time.py
```

### Platform Module Helper

**File:** `stdlib/src/_platform/__init__.py`

```python
def get_platform_module(module_name: str):
    """
    Load platform-specific implementation.

    Args:
        module_name: Name of the module (e.g., 'env', 'time')

    Returns:
        Imported platform-specific module

    Example:
        >>> platform_env = get_platform_module('env')
        >>> # Returns darwin/env.py on macOS, linux/env.py on Linux
    """
    platform = detect_platform()  # Returns 'darwin', 'linux', or 'windows'

    if platform == 'darwin':
        from ._platform.darwin import env
        return env
    elif platform == 'linux':
        from ._platform.linux import env
        return env
    elif platform == 'windows':
        from ._platform.windows import env
        return env
    else:
        raise RuntimeError(f"Unsupported platform: {platform}")
```

### Usage in Standard Library Modules

**Example:** `stdlib/src/env.py`

```python
from _platform import get_platform_module

# Load platform-specific implementation
platform_env = get_platform_module('env')

def generate_getenv_ir(module, builder):
    """Generate LLVM IR for getenv()"""
    # Use platform-specific libc declarations
    getenv_fn = platform_env.declare_getenv(module)

    # Generate IR using platform-specific function
    # ...
```

## Platform-Organized Build Outputs

### Distribution Structure

Standard library bytecode is organized by platform:

```
stdlib/dist/
├── darwin/                    # macOS builds
│   ├── collections/strings.bc
│   ├── io/stdio.bc
│   ├── io/files.bc
│   ├── math.bc
│   ├── time.bc
│   └── env.bc
├── linux/                     # Linux builds
│   ├── collections/strings.bc
│   ├── io/stdio.bc
│   ├── io/files.bc
│   ├── math.bc
│   ├── time.bc
│   └── env.bc
└── windows/                   # Windows builds (planned)
    └── ...
```

### Compiler Module Selection

At compile time, the compiler:

1. Detects target platform from target triple
2. Selects appropriate `stdlib/dist/{platform}/` directory
3. Links platform-specific `.bc` files

**Example:**
```python
def link_stdlib_module(module_name: str, target_platform: TargetPlatform) -> str:
    """
    Get path to platform-specific stdlib module.

    Args:
        module_name: Module name (e.g., 'io/stdio')
        target_platform: Target platform info

    Returns:
        Path to .bc file

    Example:
        >>> link_stdlib_module('io/stdio', darwin_platform)
        'stdlib/dist/darwin/io/stdio.bc'
    """
    platform_dir = target_platform.os  # 'darwin', 'linux', or 'windows'
    return f'stdlib/dist/{platform_dir}/{module_name}.bc'
```

## Supported Platforms

### macOS (darwin)

**Status:** Fully supported

**Architecture Support:**
- `arm64` (Apple Silicon) - Primary development platform
- `x86_64` (Intel Macs)

**Implementation Location:** `stdlib/src/_platform/darwin/`

**Features:**
- POSIX `getenv()`/`setenv()`
- POSIX `nanosleep()`
- BSD semantics for system calls

### Linux

**Status:** Fully supported

**Architecture Support:**
- `x86_64` (64-bit)
- `i686` (32-bit, planned)
- `aarch64` (ARM64, planned)

**Implementation Location:** `stdlib/src/_platform/linux/`

**Features:**
- POSIX `getenv()`/`setenv()`
- POSIX `nanosleep()`
- GNU/Linux semantics

### Windows

**Status:** Partial support (planned)

**Architecture Support:**
- `x86_64` (64-bit)

**Implementation Location:** `stdlib/src/_platform/windows/`

**Planned Features:**
- Windows API equivalents for POSIX functions
- UTF-16 string handling for Win32 APIs
- Platform-specific I/O

## Adding Platform-Specific Functionality

### Step 1: Create Platform Implementations

Create implementation for each supported platform:

```
stdlib/src/_platform/
├── darwin/
│   └── myfeature.py       # macOS implementation
├── linux/
│   └── myfeature.py       # Linux implementation
└── windows/
    └── myfeature.py       # Windows implementation
```

### Step 2: Implement Platform-Specific IR Generation

Each platform module should generate appropriate LLVM IR:

**Example:** `stdlib/src/_platform/darwin/myfeature.py`

```python
import llvmlite.ir as ir

def declare_platform_function(module):
    """Declare platform-specific libc function"""
    # Darwin-specific function signature
    func_type = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()])
    func = ir.Function(module, func_type, name="darwin_specific_func")
    return func

def generate_ir(module, builder):
    """Generate platform-specific LLVM IR"""
    func = declare_platform_function(module)
    # Generate IR using Darwin-specific APIs
    # ...
```

**Example:** `stdlib/src/_platform/linux/myfeature.py`

```python
import llvmlite.ir as ir

def declare_platform_function(module):
    """Declare platform-specific libc function"""
    # Linux-specific function signature (may differ from Darwin)
    func_type = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()])
    func = ir.Function(module, func_type, name="linux_specific_func")
    return func

def generate_ir(module, builder):
    """Generate platform-specific LLVM IR"""
    func = declare_platform_function(module)
    # Generate IR using Linux-specific APIs
    # ...
```

### Step 3: Create Unified Interface

Create top-level module that uses platform helper:

**File:** `stdlib/src/myfeature.py`

```python
from _platform import get_platform_module

# Load platform-specific implementation
platform_impl = get_platform_module('myfeature')

def generate_myfeature_ir(module, builder):
    """Generate IR using platform-specific implementation"""
    return platform_impl.generate_ir(module, builder)
```

### Step 4: Build Platform-Specific Bytecode

Build `.bc` files for each platform:

```bash
# On macOS
cd stdlib/src
python myfeature.py  # Generates ../dist/darwin/myfeature.bc

# On Linux
cd stdlib/src
python myfeature.py  # Generates ../dist/linux/myfeature.bc
```

### Step 5: Update Compiler Linkage

Ensure compiler links appropriate platform module:

```python
# In compiler
if 'use <myfeature>' in imports:
    target_platform = get_current_platform()
    bc_file = f'stdlib/dist/{target_platform.os}/myfeature.bc'
    link_module(bc_file)
```

## Cross-Compilation Considerations

### Target Triple Override

Users can specify a target triple for cross-compilation:

```bash
./sushic --target=x86_64-pc-linux-gnu program.sushi
```

The compiler will:
1. Parse the provided target triple
2. Select appropriate `stdlib/dist/linux/` modules
3. Generate code for the target architecture

### Limitations

Cross-compilation requires:
- Pre-built stdlib `.bc` files for target platform
- Compatible system linker (clang) for target
- Cross-compilation toolchain installed

## Testing Platform-Specific Code

### Platform-Specific Test Files

Tests can be organized by platform:

```
tests/
├── common/
│   └── test_basic.sushi       # Cross-platform tests
├── darwin/
│   └── test_darwin_only.sushi # macOS-only tests
└── linux/
    └── test_linux_only.sushi  # Linux-only tests
```

### Conditional Test Execution

Test runner should skip platform-specific tests on other platforms:

```bash
# Run only darwin tests on macOS
python tests/run_tests.py --platform=darwin

# Run only linux tests on Linux
python tests/run_tests.py --platform=linux
```

## See Also

- [Architecture](../internals/architecture.md) - Compiler architecture overview
- [Backend](../internals/backend.md) - LLVM code generation
- [Standard Library](../standard-library.md) - Available stdlib modules
