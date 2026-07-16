# Libraries

[← Back to Documentation](index.md)

Sushi supports compiling code to reusable libraries and linking them into programs. This enables code sharing, modular architecture, and faster incremental builds.

> Contributor-level design: see [design/libraries.md](design/libraries.md) for how the
> `.slib` container, manifest, and export-closure machinery work internally.

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
2. Project-local Nori packages (`.sushi_bento/*/lib/`)
3. Global Nori packages (`~/.sushi/bento/*/lib/`)
4. Current working directory (always searched last)

Project-local packages take precedence over global ones, so a version pinned in `.sushi_bento/` always wins. See [Project Environments](package-manager.md#project-environments) for details on how `.sushi_bento/` is populated.

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
3. **Generic instantiation across libraries**: Regular generic *functions*, *variadic-generic
   pack* functions (`...Ts`), and generic *structs*/*enums* can be instantiated across `.slib`
   boundaries. The library producer ships a re-parsable source template in the `.slib` `templates`
   section (templates version 4); the consumer re-parses it, registers it alongside its own
   definitions, and monomorphizes it at consumer call sites using the standard Pass 1.5/1.6
   machinery. A pack function carries `type_params` (the `...Ts` is recorded with `is_pack`), so it
   ships as a template and is monomorphized per call site exactly like a regular generic. Perk
   *definitions* are also shipped so consumers do not need to redeclare a perk contract that
   originates in the library. Constraint re-checking uses `CE4006` against the consumer's
   perk-impl table.

   **Perk implementations also ship** (concrete impls only): a library's own
   `extend <ConcreteType> with <Perk>:` block for a shipped perk crosses the boundary, so a
   consumer can instantiate e.g. `pick_bigger<T: Doubler>` at `i32` without writing
   `extend i32 with Doubler` itself. The impl's bodies are not re-compiled at the consumer - its
   signatures register for constraint checking and dispatch, the method symbols are declared, and
   the definitions link from the library bitcode (where they carry weak linkage). Precedence:
   a consumer's own impl of the same `(type, perk)` always wins, both semantically and at link
   time; across multiple libraries shipping the same impl, the first registered wins; if a local
   extension method on the target type already uses one of the impl's method names, the library
   impl is skipped entirely (write your own `extend` to opt in, which surfaces the normal
   `CE4007` conflict diagnostics). Only impls of perks referenced by an exported generic's
   constraints ship; impls of library-internal perks stay internal.

   **Private helpers ship automatically (the export closure)**: a public generic whose body
   references library-private symbols no longer fails to export - the producer walks the
   transitive closure of everything the generic depends on and ships it: private *generic*
   helpers as source templates (flagged `private`), private *concrete* helpers as signature
   records (their definitions carry external linkage in the library bitcode and link at the
   consumer), and *constants* with their source (the consumer needs the value for compile-time
   evaluation). The manifest's `templates.closure_summary` lists what shipped, by kind. At the
   consumer, a local symbol with the same name as a shipped private is an error (**CE5007**,
   not local-wins): shadowing it would silently change what the library's monomorphized bodies
   call. Note that shipped private helpers become callable by name from consumer code - they
   are not advertised in the public API, but they are not hidden either.

   Remaining restrictions:
   - **CE5006 (narrowed)**: a generic that (transitively) references an `unsafe external`
     namespace, or a private helper whose signature exposes a foreign `ptr`, still cannot be
     exported - foreign bindings cannot be re-declared at the consumer (see CE5002). Wrap the
     foreign detail behind a private helper with a C-ABI-free signature.
   - **Generic-target perk impls do not ship**: `extend <Generic<T>> with <Perk>` is not supported
     in-program, so only concrete-target impls cross the boundary.
   - **Native variadics (`...T`) are not exportable**: a v1 native variadic collects into a runtime
     `T[]` inside one concrete function (no template to monomorphize), so public export is rejected
     with **CE0116**. This is distinct from a v2 type pack (`...Ts`), which exports as a template.

These limitations may be addressed in future versions.

## See Also

- [Nori Package Manager](package-manager.md) - Packaging and distributing libraries
- [Compiler Reference](compiler-reference.md) - All compiler options
- [Getting Started](getting-started.md) - Introduction to Sushi
- [Standard Library](standard-library.md) - Built-in library modules
