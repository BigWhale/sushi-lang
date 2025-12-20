# Standard Library Build Process

[← Back to Documentation](../README.md) | [Architecture](architecture.md)

Documentation for building and maintaining the Sushi standard library.

## Overview

The standard library uses a different build process than user libraries:

| Aspect | Standard Library | User Libraries |
|--------|-----------------|----------------|
| Source | Python code generating LLVM IR | Sushi source code |
| Format | Raw `.bc` (LLVM bitcode) | `.slib` (bitcode + metadata) |
| Build tool | `build.py` script | `./sushic --lib` |
| Metadata | Built into compiler | Embedded in `.slib` file |

This design allows the stdlib to use low-level LLVM features not exposed in the Sushi language.

## Directory Structure

```
sushi_lang/sushi_stdlib/
├── build.py           # Build script
├── src/               # Python source modules
│   ├── collections/   # strings.py
│   ├── io/            # stdio.py, files.py
│   ├── sys/           # env.py, process.py
│   ├── math.py
│   ├── time.py
│   └── random.py
├── generics/          # Generic type emission (List, HashMap, etc.)
└── dist/              # Built bitcode files
    ├── darwin/        # macOS binaries
    │   ├── collections/strings.bc
    │   ├── core/primitives.bc
    │   ├── io/stdio.bc
    │   ├── io/files.bc
    │   ├── sys/env.bc
    │   ├── sys/process.bc
    │   ├── math.bc
    │   ├── time.bc
    │   └── random.bc
    └── linux/         # Linux binaries
        └── (same structure)
```

## Building the Standard Library

### Prerequisites

- Python 3.10+
- llvmlite (installed via `uv sync`)
- LLVM 20 (for native target support)

### Build Command

From the project root:

```bash
python sushi_lang/sushi_stdlib/build.py
```

Output:

```
============================================================
Sushi Standard Library Build Script
============================================================

Project root: /path/to/sushi
Build output: /path/to/sushi/sushi_lang/sushi_stdlib/dist

Detected platform: x86_64-apple-darwin
  Architecture: x86_64
  OS: darwin
  Vendor: apple

Building stdlib for darwin...
Output directory: /path/to/sushi/sushi_lang/sushi_stdlib/dist/darwin

Building collections/strings...
  → dist/darwin/collections/strings.bc
Building core/primitives...
  → dist/darwin/core/primitives.bc
Building io/files...
  → dist/darwin/io/files.bc
...

============================================================
Stdlib build complete!
  Platform: darwin
  Artifacts: dist/darwin
============================================================
```

### Platform-Specific Builds

The build script automatically detects the current platform and generates bitcode for it. To build for a different platform, you must run the build on that platform.

Currently supported:
- `darwin` (macOS)
- `linux`

## How It Works

### Module Structure

Each stdlib module is a Python file that generates LLVM IR using llvmlite:

```python
# Example: sushi_stdlib/src/time.py

import llvmlite.ir as ir

def generate_module_ir() -> ir.Module:
    """Generate LLVM IR for the time module."""
    module = ir.Module(name="sushi_time")

    # Declare external libc function
    nanosleep_ty = ir.FunctionType(
        ir.IntType(32),
        [ir.IntType(64).as_pointer(), ir.IntType(64).as_pointer()]
    )
    nanosleep = ir.Function(module, nanosleep_ty, name="nanosleep")

    # Create wrapper function
    sleep_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(64)])
    sleep_fn = ir.Function(module, sleep_ty, name="sleep")

    # Generate function body...

    return module
```

### Build Process

1. **Initialize LLVM** - Set up native target
2. **Create module** - Generate LLVM IR for each stdlib unit
3. **Compile to bitcode** - Convert IR to `.bc` files
4. **Write to dist** - Save platform-specific binaries

### Why Not `.slib` Format?

The stdlib doesn't use the `.slib` format because:

1. **No metadata needed** - Type information is built into the compiler
2. **Low-level access** - Direct LLVM IR generation for libc interop
3. **Monomorphization** - Generic types (List, HashMap) are emitted inline
4. **Simpler linking** - Raw bitcode links directly with clang

## Adding New Stdlib Modules

### 1. Create the Python Module

```python
# sushi_stdlib/src/mymodule.py

import llvmlite.ir as ir

def generate_module_ir() -> ir.Module:
    module = ir.Module(name="sushi_mymodule")

    # Define types, functions, etc.

    return module
```

### 2. Add to build.py

```python
def build_mymodule(platform_dir: Path):
    """Build mymodule unit."""
    print("Building mymodule...")

    from sushi_lang.sushi_stdlib.src import mymodule
    module = mymodule.generate_module_ir()

    output = platform_dir / "mymodule.bc"
    compile_module_to_bc(module, output)
```

Call it from `main()`:

```python
def main():
    # ...existing builds...
    build_mymodule(platform_dir)
```

### 3. Register in Compiler

Update `sushi_lang/backend/stdlib_loader.py` to recognize the new module for `use <mymodule>` imports.

## Generic Types

Generic stdlib types (List, HashMap, Maybe, Result) use a different mechanism:

- **Inline emission** - Code is generated at compile time, not precompiled
- **Monomorphization** - Each instantiation (e.g., `List<i32>`) gets its own code
- **Location** - `sushi_stdlib/generics/` directory

These are not built by `build.py` because they must be instantiated for user-specific type arguments.

## Troubleshooting

### "LLVM target not initialized"

Run `uv sync` to ensure llvmlite is installed, then try again.

### Platform mismatch errors

You cannot use macOS-built `.bc` files on Linux or vice versa. Build on the target platform.

### Missing stdlib module

If `use <module>` fails with "unit not found":
1. Check the module exists in `dist/{platform}/`
2. Rebuild with `python sushi_lang/sushi_stdlib/build.py`
3. Verify the module is registered in the compiler

## See Also

- [Libraries](../libraries.md) - User library creation (`.slib` format)
- [Library Format](../library-format.md) - `.slib` binary format specification
- [Architecture](architecture.md) - Compiler overview
