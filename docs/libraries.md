# Libraries

[← Back to Documentation](README.md)

Sushi supports compiling code to reusable libraries and linking them into programs. This enables code sharing, modular architecture, and faster incremental builds.

## Table of Contents

- [Overview](#overview)
- [Creating Libraries](#creating-libraries)
- [Using Libraries](#using-libraries)
- [Library Search Path](#library-search-path)
- [Manifest Files](#manifest-files)
- [Symbol Resolution](#symbol-resolution)
- [Best Practices](#best-practices)

## Overview

The library system has two main operations:

1. **Compile to library**: Convert Sushi source files to reusable bitcode (`.bc`) with metadata (`.sushilib`)
2. **Use libraries**: Import precompiled libraries using `use <lib/...>` syntax

```bash
# Create a library
./sushic --lib mathutils.sushi -o mathutils.bc

# Use the library in a program (via use statement in source)
./sushic program.sushi -o program
```

## Creating Libraries

### The `--lib` Flag

Use `--lib` to compile source files into a library instead of an executable:

```bash
./sushic --lib mylib.sushi -o mylib.bc
```

This generates two files:
- `mylib.bc` - LLVM bitcode containing compiled functions
- `mylib.sushilib` - JSON manifest with type signatures and metadata

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
1. Search for `mathutils.bc` and `mathutils.sushilib` in the library search path
2. Register all functions, structs, and enums from the manifest
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

### SUSHI_LIB_PATH Environment Variable

The compiler searches for `.bc` and `.sushilib` files in directories specified by `SUSHI_LIB_PATH`:

```bash
export SUSHI_LIB_PATH=/usr/local/lib/sushi:./libs:~/mylibs
./sushic program.sushi
```

The path is colon-separated on Unix (semicolon on Windows).

### Search Order

1. Each directory in `SUSHI_LIB_PATH` (in order)
2. Current working directory (always searched last)

### Hierarchical Namespaces

Libraries can be organized in subdirectories:

```
libs/
  math/
    vectors.bc
    vectors.sushilib
    matrices.bc
    matrices.sushilib
  utils/
    strings.bc
    strings.sushilib
```

Import with the path:

```sushi
use <lib/math/vectors>
use <lib/utils/strings>
```

## Manifest Files

The `.sushilib` manifest is a JSON file containing library metadata:

```json
{
  "sushi_lib_version": "1.0",
  "library_name": "mylib",
  "compiled_at": "2025-12-06T10:30:00Z",
  "platform": "darwin",
  "compiler_version": "0.1.0",
  "public_functions": [
    {
      "name": "add",
      "params": [
        {"name": "a", "type": "i32"},
        {"name": "b", "type": "i32"}
      ],
      "return_type": "Result<i32, StdError>",
      "is_generic": false
    }
  ],
  "structs": [
    {
      "name": "Point",
      "fields": [
        {"name": "x", "type": "i32"},
        {"name": "y", "type": "i32"}
      ]
    }
  ],
  "enums": [
    {
      "name": "Color",
      "variants": [
        {"name": "Red", "has_data": false},
        {"name": "Green", "has_data": false},
        {"name": "Blue", "has_data": false}
      ]
    }
  ],
  "dependencies": ["io/stdio"]
}
```

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
      basic.bc
      advanced.bc
    io/
      network.bc
      files.bc
```

### 4. Version Your Libraries

Include version information in your library names or use the manifest:

```bash
./sushic --lib mylib.sushi -o mylib-1.0.bc
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

- [Compiler Reference](compiler-reference.md) - All compiler options
- [Getting Started](getting-started.md) - Introduction to Sushi
- [Standard Library](standard-library.md) - Built-in library modules
