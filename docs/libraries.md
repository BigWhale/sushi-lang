# Libraries

[← Back to Documentation](README.md)

Sushi supports compiling code to reusable libraries and linking them into programs. This enables code sharing, modular architecture, and faster incremental builds.

## Table of Contents

- [Overview](#overview)
- [Creating Libraries](#creating-libraries)
- [Using Libraries](#using-libraries)
- [Library Search Path](#library-search-path)
- [Inspecting Libraries](#inspecting-libraries)
- [Library Format](#library-format)
- [Symbol Resolution](#symbol-resolution)
- [Best Practices](#best-practices)

## Overview

The library system has two main operations:

1. **Compile to library**: Convert Sushi source files to a binary library file (`.slib`)
2. **Use libraries**: Import precompiled libraries using `use <lib/...>` syntax

```bash
# Create a library
./sushic --lib mathutils.sushi -o mathutils.slib

# Use the library in a program (via use statement in source)
./sushic program.sushi -o program
```

## Creating Libraries

### The `--lib` Flag

Use `--lib` to compile source files into a library instead of an executable:

```bash
./sushic --lib mylib.sushi -o mylib.slib
```

This generates a single `.slib` file containing both:
- LLVM bitcode (compiled functions)
- Binary metadata (type signatures, function declarations)

### Public Functions

Only functions marked `public` are accessible from other compilation units:

```sushi
# mylib.sushi

# This function can be called from programs that use this library
public fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

# This function is internal to the library
fn helper(i32 x) i32:
    return Result.Ok(x * 2)

public fn double_add(i32 a, i32 b) i32:
    let i32 sum = add(a, b)??
    return Result.Ok(helper(sum)??)
```

### No main() Required

Libraries do not need a `main()` function. If you include one, compilation will fail.

### Structs and Enums

Structs and enums defined in libraries are automatically available to programs that use them:

```sushi
# shapes.sushi

struct Point:
    i32 x
    i32 y

enum Color:
    Red
    Green
    Blue

public fn make_point(i32 x, i32 y) Point:
    return Result.Ok(Point(x, y))
```

## Using Libraries

### The `use <lib/...>` Statement

To use a precompiled library, add a `use` statement with the `lib/` prefix:

```sushi
# program.sushi
use <lib/mathutils>

fn main() i32:
    let i32 result = add(10, 20).realise(0)
    println("10 + 20 = {result}")
    return Result.Ok(0)
```

The compiler will:
1. Search for `mathutils.slib` in the library search path
2. Read metadata and register all functions, structs, and enums
3. Link the bitcode into the final executable

### Multiple Libraries

Use multiple `use` statements:

```sushi
use <lib/math>
use <lib/utils>

fn main() i32:
    # Functions from both libraries are available
    return Result.Ok(0)
```

## Library Search Path

### Automatic Discovery via Nori

Libraries installed with the [Nori package manager](package-manager.md) are found automatically by the compiler. No environment variable configuration is needed:

```bash
nori install math-utils from ./dist/
./sushic program.sushi    # finds math-utils.slib automatically
```

### SUSHI_LIB_PATH Environment Variable

For libraries not managed by Nori, the compiler searches directories specified by `SUSHI_LIB_PATH`:

```bash
export SUSHI_LIB_PATH=/usr/local/lib/sushi:./libs:~/mylibs
./sushic program.sushi
```

The path is colon-separated on Unix (semicolon on Windows).

### Search Order

1. Each directory in `SUSHI_LIB_PATH` (in order)
2. Nori packages (`~/.sushi/bento/*/lib/`)
3. Current working directory (always searched last)

### Hierarchical Namespaces

Libraries can be organized in subdirectories:

```
libs/
  math/
    vectors.slib
    matrices.slib
  utils/
    strings.slib
```

Import with the path:

```sushi
use <lib/math/vectors>
use <lib/utils/strings>
```

## Inspecting Libraries

### The `--lib-info` Flag

Use `--lib-info` to display metadata from a compiled library:

```bash
./sushic --lib-info mylib.slib
```

Example output:

```
Library: mylib
Platform: darwin
Compiler: 0.3.0
Compiled: 2025-12-07T10:30:00+00:00
Protocol: 1.0

Public Functions (2):
  fn add(i32 a, i32 b) i32
  fn multiply(i32 a, i32 b) i32

Structs (1):
  struct Point:
    i32 x
    i32 y

Enums (1):
  enum Color:
    Red
    Green
    Blue

Dependencies (1):
  <io/stdio>

Bitcode: 5,432 bytes
```

This is useful for:
- Checking what functions a library exports
- Verifying platform compatibility
- Understanding library dependencies

## Library Format

### Binary `.slib` Format

Libraries use a binary format that combines metadata and bitcode in a single file:

```
[Magic: 16 bytes] [Version: 4 bytes] [Reserved: 24 bytes]
[Metadata Length: 8 bytes] [Metadata: MessagePack]
[Bitcode Length: 8 bytes] [Bitcode: LLVM]
```

The format uses MessagePack for efficient metadata serialization.

### Platform Compatibility

Libraries are compiled for a specific platform. The compiler warns if you use a library compiled for a different platform:

```
CW3505: platform mismatch: library compiled for 'linux', current platform is 'darwin'
```

## Symbol Resolution

### Two-Phase Linking

Sushi uses a two-phase linking process to handle symbol conflicts:

1. **Extract**: Parse all modules and build symbol tables
2. **Resolve**: Deduplicate symbols using priority rules
3. **Merge**: Build final module with resolved symbols

### Priority Rules

When the same symbol is defined in multiple places:

| Priority    | Source           | Description                    |
|-------------|------------------|--------------------------------|
| 1 (highest) | Main program     | Your program's definitions win |
| 2           | User library     | Library definitions            |
| 3           | Standard library | Stdlib definitions             |
| 4 (lowest)  | Runtime          | Runtime helper functions       |

This means you can override library functions in your main program.

### Dead Code Elimination

Only symbols reachable from `main()` are included in the final executable. Unused library functions are automatically removed, reducing binary size.

## Best Practices

### 1. Use Public Sparingly

Only mark functions as `public` if they are part of your library's API:

```sushi
# Good: Only expose the API
public fn calculate(i32 x) i32:
    return Result.Ok(internal_helper(x)??)

fn internal_helper(i32 x) i32:
    return Result.Ok(x * 2)
```

### 2. Document Your Library

Include comments explaining what each public function does:

```sushi
# Adds two integers and returns the result.
# Returns Result.Err if overflow would occur.
public fn safe_add(i32 a, i32 b) i32 | MathError:
    # ...
```

### 3. Organize with Namespaces

Use directory structure to organize related libraries:

```
myproject/
  libs/
    math/
      basic.slib
      advanced.slib
    io/
      network.slib
      files.slib
```

### 4. Version Your Libraries

Include version information in your library names:

```bash
./sushic --lib mylib.sushi -o mylib-1.0.slib
```

### 5. Test Libraries Independently

Create test programs that exercise your library functions:

```sushi
# test_mylib.sushi
use <lib/mylib>

fn main() i32:
    # Test cases
    let i32 r1 = add(1, 2).realise(-1)
    if (r1 != 3):
        println("FAIL: add(1, 2) = {r1}, expected 3")
        return Result.Ok(1)

    println("All tests passed")
    return Result.Ok(0)
```

## Limitations

Current limitations of the library system:

1. **No transitive dependencies**: If library A depends on library B, you must import both explicitly
2. **Platform-specific**: Libraries compiled on macOS cannot be used on Linux (and vice versa)
3. **No generic instantiation across libraries**: Generic types must be instantiated in the same compilation unit

These limitations may be addressed in future versions.

## See Also

- [Nori Package Manager](package-manager.md) - Packaging and distributing libraries
- [Compiler Reference](compiler-reference.md) - All compiler options
- [Getting Started](getting-started.md) - Introduction to Sushi
- [Standard Library](standard-library.md) - Built-in library modules
