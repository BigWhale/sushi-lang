# Libraries

[← Back to Documentation](index.md)

Sushi supports compiling code to reusable libraries and linking them into programs. This enables code sharing, modular architecture, and faster incremental builds.

**A `.slib` is Sushi source plus an index.** The consumer compiles that source as ordinary
compilation units and caches the object files, so one library file works on every platform:
text carries no target triple. Binary distribution stays available as an opt-in.

> Contributor-level design: see [design/libraries.md](design/libraries.md) for how the
> `.slib` container, manifest, and export-closure machinery work internally.

## Table of Contents

- [Overview](#overview)
- [Creating Libraries](#creating-libraries)
- [Using Libraries](#using-libraries)
- [Library Search Path](#library-search-path)
- [Inspecting Libraries](#inspecting-libraries)
- [Library Format](#library-format)
- [Versions and Compatibility](#versions-and-compatibility)
- [Symbol Resolution](#symbol-resolution)
- [Best Practices](#best-practices)

## Overview

The library system has two operations:

1. **Compile to a library**: turn Sushi source files into one `.slib` file
2. **Use a library**: import it with `use <lib/...>`

```bash
# Create a library
./sushic --lib --lib-version 1.0.0 mathutils.sushi -o mathutils.slib

# Use the library in a program (via use statement in source)
./sushic program.sushi -o program
```

The consumer does the compiling. A library's units enter the build as ordinary units, they
are type-checked and borrow-checked with everything else, and each one caches its own object
file in `__sushi_cache__/`. The first build against a library pays for it; later builds do
not.

## Creating Libraries

### The `--lib` Flag

Use `--lib` to compile source files into a library instead of an executable:

```bash
./sushic --lib --lib-version 1.0.0 mylib.sushi -o mylib.slib
```

This writes one `.slib` file containing:
- the complete source text of every unit in the library
- a MessagePack index of everything it declares, which `--lib-info` and the consumer read

Every library states its own version. The value comes from a `nori.toml` beside the sources
when there is one, and from `--lib-version` otherwise; neither is **CE3505**. See
[Versions and Compatibility](#versions-and-compatibility).

### Library Kinds

`--lib-kind` chooses what the file carries. The default is `source`.

| Kind | Ships | Portable | Notes |
|--------|--------------------------|-----|--------------------------------------------|
| `source` | unit source text | yes | the default; the consumer compiles it |
| `binary` | LLVM bitcode | no | platform-bound (**CE3504** elsewhere) |
| `hybrid` | both | no | the bitcode still binds it to one platform |

```bash
# The default: one artifact for every platform
./sushic --lib --lib-version 1.0.0 mylib.sushi -o mylib.slib

# The opt-in: compiled bitcode, for this platform only
./sushic --lib --lib-kind binary --lib-version 1.0.0 mylib.sushi -o mylib.slib
```

Choose `binary` when you want to ship a library without shipping its source. Note what that
does **not** buy: a generic cannot be pre-compiled, because monomorphization needs the
consumer's concrete type arguments, so a binary library carries the source text of its
generics in the index regardless. Binary distribution hides concrete bodies only.

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

A generic is no exception. `public fn pick@(T)(...)` is part of the API; `fn pick@(T)(...)`
is internal, and a consumer that calls it hears `CE3005` on the source path exactly as it
does for a concrete function. Only a public generic ships as a template, so on the binary
path the symbol is not there at all and the answer is `CE2008`.

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

To use a library, add a `use` statement with the `lib/` prefix:

<!-- docs-sweep: skip (needs a .slib library built from the page's earlier example) -->
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

<!-- docs-sweep: skip (needs a .slib library built from the page's earlier example) -->
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
Version: 1.0.0
Kind: source
Compiler: 0.11.1
Requires compiler: ~0.11
Compiled: 2026-08-23T10:30:00+00:00
Protocol: 2.0

Units (1):
  mylib
    Arithmetic that reports its own failures.

Public Functions (3):
  fn add(i32 a, i32 b) i32
    Adds two numbers.
    - Parameter a: The first addend.
    - Parameter b: The second addend.
    - Returns: The sum.
  fn multiply(i32 a, i32 b) i32
  fn shout(nom string s) string
    Hands the string back, and takes it over.

Structs (1):
  struct Point:
    A point in the plane.
    i32 x
      The distance along x.
    i32 y
      The distance along y.

Enums (1):
  enum Color:
    Red
    Green
    Blue

Dependencies (1):
  <io/stdio>

Source: 1,204 bytes
```

A documented symbol prints its doc block, indented two spaces under its own line;
`multiply` above has no block and prints as it always did. A `nom` parameter shows its
mode, which is the one mode a type cannot spell. See
[Documentation Blocks](documentation-blocks.md#what-travels-in-a-slib) for the record and
for the few things that do not travel in it.

This is useful for:
- Checking what functions a library exports, and what each one is for
- Verifying platform compatibility
- Understanding library dependencies

## Library Format

### The `.slib` Container

One file holds a MessagePack index next to a payload, framed by a fixed 52-byte header:

```
[Magic: 16 bytes] [Version: 4 bytes] [Flags: 4 bytes] [Kind: 4 bytes] [Reserved: 16 bytes]
[Metadata Length: 8 bytes] [Metadata: MessagePack]
[Source Length: 8 bytes]   [Source: MessagePack map, unit name -> source text]
[Bitcode Length: 8 bytes]  [Bitcode: LLVM]
```

The `Kind` field states which payload is present, so a reader can branch before it unpacks
anything. A source library has an empty bitcode section, a binary one an empty source
section, and a hybrid carries both. See [Library Format](library-format.md) for the full
specification.

### Platform Compatibility

**A source library is portable.** It carries text, and text has no target triple, so the
same `.slib` builds on macOS and on Linux.

**A binary library is not.** LLVM bitcode looks target-neutral and is not: it carries a
target triple and a data layout, and the C ABI is already lowered into it. Loading a binary
library built for another platform is a hard error:

```
CE3504: platform mismatch: library compiled for 'linux', current platform is 'darwin'
```

The check is skipped entirely for a source library, and `--lib-info` prints no `Platform`
line for one, because the field means nothing there.

Portable as text is not the same as portable in behaviour. See Limitation #2 below.

## Versions and Compatibility

A `.slib` records two versions, with two different jobs.

### `library_version` — the library's own version

`major.minor.patch`, taken from the first of these that exists:

1. `[package] version` in a `nori.toml` beside the sources
2. the `--lib-version X.Y.Z` flag

Neither present is **CE3505**, and so is a `--lib-version` that contradicts the
`nori.toml` — silently preferring one would let a package ship under a version it does not
claim. The packager stays the source of truth for a real package, without forcing a manifest
on a bare `./sushic --lib` build.

### `requires_compiler` — which compilers can build it

A source library is compiled by **the consumer's** compiler, not the author's. So a library
that built cleanly under one compiler can fail under a later one. That is the standard cost
of source distribution, and it is not fixable — only declarable.

Every build stamps a constraint. The default is `~<major>.<minor>` of the building compiler,
so a compiler at 0.11.1 writes `~0.11`: every `0.11.z` is accepted and `0.12.0` is not.
Pre-1.0 semver makes the minor the breaking unit, which is how Sushi's 0.x releases already
behave.

A library the running compiler does not satisfy is a hard error:

```
CE3503: library 'mylib' accepts compiler ~0.11, this is 0.12.0
```

Not a warning. A real incompatibility that is only warned about surfaces later as a
confusing error deep inside library source you never wrote.

The escape is `--ignore-compiler-version`, for an author testing a library forward against a
new compiler. It is build-wide and obviously temporary, on purpose.

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

Write a `##: ... :##` doc block on every public symbol. A block is part of the declaration,
so the library carries it and `--lib-info` prints it; a `#` comment is dropped at the
boundary and reaches nobody.

An `- Example:` is worth writing on a public symbol: the code travels in the index, and
`python tests/docs_sweep.py` compiles and runs it against the library's own source, so an
example that drifts out of date says so. `--lib-info` does not print one -- a fenced program
inside a plain dump would bury the signature.

```sushi
##:
Adds two integers.

- Parameter a: The first addend.
- Parameter b: The second addend.
- Returns: The sum.
- Errors: `MathError.Overflow` when the sum does not fit an `i32`.
:##
public fn safe_add(i32 a, i32 b) i32 | MathError:
    return Result.Ok(a + b)
```

A block first in the file documents the unit itself, which is the right place for what the
library as a whole is for. [Documentation Blocks](documentation-blocks.md) is the guide.

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

### 4. State a Version

Every library records its own version, so it does not belong in the filename. Let a
`nori.toml` supply it for a real package, and pass `--lib-version` for a one-off build:

```bash
./sushic --lib --lib-version 1.0.0 mylib.sushi -o mylib.slib
```

### 5. Test Libraries Independently

Create test programs that exercise your library functions:

<!-- docs-sweep: skip (needs a .slib library built from the page's earlier example) -->
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

1. **No transitive dependencies**: If library A depends on library B, you must import both
   explicitly. A library's own `use <lib/...>` is not followed.
2. **Portable as text, not automatically in behaviour**: a source library compiles anywhere,
   but Sushi has no conditional compilation — no `cfg`, no build tags, no per-platform source
   files. A library that binds a platform-specific C function through `unsafe external` still
   only builds where that function exists, and it cannot yet say so.
3. **A binary library is platform-bound**: `--lib-kind binary` or `hybrid` ships bitcode,
   which is bound to the platform that produced it (**CE3504**).
4. **A public generic cannot reach FFI**: a public generic whose body (transitively)
   references an `unsafe external` namespace, or a private helper whose signature exposes a
   foreign `ptr`, cannot be exported (**CE5006**; see also **CE5002**). Wrap the foreign
   detail behind a private helper with a C-ABI-free signature. This applies to every kind,
   source included.
5. **A public native variadic cannot be exported**: a `...T` variadic collects into a runtime
   `T[]` inside one concrete function, so there is no template to monomorphize and public
   export is **CE0116**. A type pack (`...Ts`) is different: it exports as a template. This
   applies to every kind, source included.
6. **Generic instantiation across a BINARY boundary**: the notes below describe how generics
   cross a `--lib-kind binary` library. A source library needs none of this machinery — its
   generics are ordinary source in ordinary units, so they monomorphize exactly as they would
   in a multi-file program. Regular generic *functions*, *variadic-generic pack* functions
   (`...Ts`), and generic *structs*/*enums* can be instantiated across `.slib` boundaries.

   The library producer ships a re-parsable source template in the `.slib` `templates`
   section (templates version 4); the consumer re-parses it, registers it alongside its own
   definitions, and monomorphizes it at consumer call sites using the standard `instantiate`/`monomorphize`
   machinery. A pack function carries `type_params` (the `...Ts` is recorded with `is_pack`), so it
   ships as a template and is monomorphized per call site exactly like a regular generic. Perk
   *definitions* are also shipped so consumers do not need to redeclare a perk contract that
   originates in the library. Constraint re-checking uses `CE4006` against the consumer's
   perk-impl table.

   **Perk implementations also ship** (concrete impls only): a library's own
   `extend <ConcreteType> with <Perk>:` block for a shipped perk crosses the boundary, so a
   consumer can instantiate e.g. `pick_bigger@(T: Doubler)` at `i32` without writing
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
   are not advertised in the public API, but they are not hidden either. None of this can
   arise on the source path: library units are namespaced, so there is no shared namespace to
   clash in, and nothing has to be shipped ahead of need.

   Remaining restriction on the binary path:
   - **Generic-target perk impls do not ship**: `extend <Generic@(T)> with <Perk>` is not supported
     in-program, so only concrete-target impls cross the boundary.

These limitations may be addressed in future versions.

## See Also

- [Nori Package Manager](package-manager.md) - Packaging and distributing libraries
- [Compiler Reference](compiler-reference.md) - All compiler options
- [Getting Started](getting-started.md) - Introduction to Sushi
- [Standard Library](standard-library.md) - Built-in library modules
