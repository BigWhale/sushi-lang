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

An error in a library unit is reported to the consumer. A warning in a library unit is not: it
does not print, and it does not change the exit status of the consumer's build. The library's
author sees those warnings when the library is built.

## Creating Libraries

### The `--lib` Flag

Use `--lib` to compile source files into a library instead of an executable:

```bash
./sushic --lib --lib-version 1.0.0 mylib.sushi -o mylib.slib
```

This writes one `.slib` file containing:
- the complete source text of every unit in the library
- a MessagePack index of everything it declares, which `--lib-info` and the consumer read

Every library states its own version. The value comes from the `nori.toml` in the current
directory when there is one, and from `--lib-version` otherwise; neither is **CE3505**. See
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

Every kind exports extension methods, instance and static. A consumer calls
`Vec.at(3, 4)` and `v.sum()` on a binary library's `Vec` as it does on a source
library's. A concrete method ships as a signature, and its body links from the bitcode.
A template (`extend Box@(T)`, `extend Box@(i32)`, `extend T[]`, `pick@(U)`) ships as
source, and the consumer makes its own copies. A method of an implementation of a
private perk ships as an ordinary method of the type: the contract stays hidden, and the
method stays callable. A consumer that declares the same method on the same type hears
`CE0101`, on every kind.

Every kind re-exports. A façade unit that says `public use` on each of the library's
other units is the way to give a multi-unit library one namespace, and it works whichever
kind you build: a source library ships the statement as text and the consumer's compiler
reads it, a compiled one ships a `reexports` record per statement and the consumer
composes the namespace from that. A `public use <io/fs>` hands the module on the same
way, and the consumer's build compiles the module on the strength of the record even
where no unit of its own wrote the import. A `public use <lib/other>` hands on another
library with the same rule: the consumer's build finds `other` on `SUSHI_LIB_PATH` as if
the consumer wrote `use <lib/other>`, and `other`'s public names are the consumer's to
write. If `other` is not on the path, the consumer gets CE3502 with a note that names the
`public use`.

### Library Dependencies

A library that writes `use <lib/b>` depends on `b`. Two rules apply, the same for every
kind of library:

1. **Loading is transitive.** The library records each `use <lib/...>` of its own units,
   plain or public, in the manifest `dependencies`, with the name and the version of the
   `b` that its build found. The consumer's build loads every library of that graph: a
   source dependency is compiled with the program, and the bitcode of a compiled one is
   linked once. So a library body always has what it calls, and the consumer does not
   write `use <lib/b>` for a library it does not call itself. A stdlib module that a
   compiled library uses is loaded the same way.
2. **Visibility is not transitive.** A unit may write the names of what it imports
   itself, and of what a `public use` hands on. A plain `use <lib/b>` in a library loads
   `b` and gives the consumer no name of it: a bare `b_val()` at the consumer is CE2008
   until the consumer writes `use <lib/b>`, or the library writes `public use <lib/b>`.

Visibility is also PER UNIT, and it is the same for every kind of library. A library's
public functions, constants, unit variables and types (concrete and generic) are names
only in a unit of the consumer that imports the library itself, or that reaches it
through a `public use` chain. If `main.sushi` writes `use <lib/a>` and `use "helper"`, a
call of `a_val()` in `helper.sushi` is CE2008 until `helper.sushi` writes `use <lib/a>`
too. A binary or hybrid library follows the rule of a source library here.

A generic template of a library is copied at the consumer, with the type arguments of
the call. The body of the copy resolves its names in the scope of the library unit that
declares the template: the library's own declarations, and every library and stdlib
module that the library uses, plain or public. So a template that calls `b_val()` works
when the library loads `b` with a plain `use <lib/b>`, and the consumer still cannot
call `b_val()` itself. The copy works also when the unit that holds it does not import
the library.

A library is identified by the `library_name` stamped into its `.slib`, not by the path
that the search finds. One library that several paths reach -- the consumer and a
library, or two libraries in a diamond -- is loaded once and gives one candidate for each
name, so `use <lib/a>` beside `use <lib/b>` is not CE3012. Two versions of one library in
one graph are CE3519: for example, `a` was built against `b` 0.1.0 and the consumer's
search finds `b` 0.2.0. The diagnostic names each version and the path that reached it.
A dependency that the consumer's search cannot find is CE3502, with a note at the `use`
that needs it, for a plain `use` and for a `public use` alike.

Two libraries that declare one extension method on one type are CE0101 at the consumer,
with a note for each library, for every kind: a method is found on its receiver's type,
so no import can choose between the two bodies.

### Public Declarations

Only declarations marked `public` are accessible from other compilation units. Six kinds
carry the marker -- `fn`, `const`, `var`, `struct`, `enum` and `perk` -- and private is the
default for all of them. A `public var` is storage that a consumer can read and write:

```sushi
# mylib.sushi

# This function can be called from programs that use this library
public fn add(i32 a, i32 b) i32:
    return a + b

# This function is internal to the library
fn helper(i32 x) i32:
    return x * 2

public fn double_add(i32 a, i32 b) i32:
    let i32 sum = add(a, b)
    return helper(sum)

# Storage that a consumer can read and write
public var i32 calls = 0
```

`add`, `helper` and `double_add` are BARE: they write no `| E`, so a call yields the `i32`
and takes no `??` or `.realise`. A bare function is the exception, not the default style. It
is correct here because the functions are pure arithmetic and will stay so. A PUBLIC function
keeps a channel (`| E`) when there is any doubt: a channel added later changes the signature,
and that breaks every caller and every binary `.slib`. The compiler does not enforce this.
`docs/design/error-channel.md` carries the rule.

A generic is no exception. `public fn pick@(T)(...)` is part of the API; `fn pick@(T)(...)`
is internal, and a consumer that calls it hears `CE3005` exactly as it does for a concrete
function. Only a public generic ships as a template, so on the binary path the symbol is
not in the consumer's tables at all -- but the manifest names what the library declares and
keeps, so the answer is `CE3005` there too, naming the library instead of a unit. `CE2008`
is left for what it is for: a name that no unit and no linked library declares.

### No main() Required

Libraries do not need a `main()` function. If you include one, compilation will fail.

### Structs, Enums, Constants and Perks

A type, a constant and a perk are private to the library unless they say `public`. Only a
marked one is in the manifest, so only a marked one is API a consumer can name -- and only
a marked one is frozen: an unmarked declaration can change shape in the next version
without breaking anybody.

```sushi
# shapes.sushi

public struct Point:                # API: a consumer may name the type
    i32 x
    i32 y

public enum Color:
    Red
    Green
    Blue

public const i32 MAX_SHAPES = 64

struct Cursor:                      # a decoder detail; not in the manifest
    i32 at

public fn make_point(i32 x, i32 y) Point:
    return Point(x, y)
```

Three consequences worth knowing:

- **A public signature may not name a private type** (`CE3009`), and a public constraint
  may not name a private perk (`CE3010`). Privacy is worth nothing if the signature hands
  the type out anyway, so mark what the API returns and takes.
- **A private declaration a template body needs still travels.** A public generic's body
  may name a private type, constant or helper; the export closure ships them so the
  consumer can monomorphize, and the consumer still cannot name them itself.
- **A public constant is API, on both library kinds.** A constant has no body to link, so
  the manifest carries the declaration's own source and the consumer registers it under
  its own name. A consumer's own constant of that name is `CE0105`. (Between two ordinary
  units of one program, the unit's own constant wins with no diagnostic.)

A consumer that writes a library-private name hears `CE3005` -- "private struct 'Cursor',
defined in that library" -- and not "unknown type".

## Using Libraries

### The `use <lib/...>` Statement

To use a library, add a `use` statement with the `lib/` prefix:

<!-- docs-sweep: skip (needs a .slib library built from the page's earlier example) -->
```sushi
# program.sushi
use <lib/mathutils>

fn main() i32:
    let i32 result = add(10, 20)     # add is bare: the call yields the i32
    println("10 + 20 = {result}")
    return 0
```

The compiler will:
1. Search for `mathutils.slib` in the library search path
2. Read the index and register the public declarations
3. Compile the library's source units with the program (a source library), or link the
   shipped bitcode (a binary library)

### Multiple Libraries

Use multiple `use` statements:

<!-- docs-sweep: skip (needs a .slib library built from the page's earlier example) -->
```sushi
use <lib/math>
use <lib/utils>

fn main() i32:
    # Functions from both libraries are available
    return 0
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

The plain report is the API surface: one line per symbol. Add `--docs` to print each
symbol's documentation block under its own line:

```bash
./sushic --lib-info mylib.slib --docs
```

Example output, with `--docs`:

```
Library: mylib
Version: 1.0.0
Kind: source
Compiler: 0.13.0
Requires compiler: ~0.13
Compiled: 2026-09-28T19:18:00+00:00
Protocol: 2.3

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

Public Variables (1):
  var i32 counter

Public Structs (1):
  struct Point:
    A point in the plane.

    i32 x
      The distance along x.

    i32 y
      The distance along y.

Public Enums (1):
  enum Color:
    Red
    Green
    Blue

Dependencies (6):
  <collections/strings>
  <io/contracts>
  <io/error>
  <io/files>
  <io/fs>
  <io/path>

Source: 643 bytes
```

A documented symbol prints its doc block, indented two spaces under its own line, with a
blank line closing the block and another between one claim and the next; `multiply` above
has no block, so it prints as a bare line and the run of bare lines stays dense. Without
`--docs` no block prints at all and the whole report is that dense -- prose is what makes
a report long, and a reader asking what a library exports usually does not want ten
screens of it. A `nom` parameter shows its mode, which is the one mode a type cannot
spell, and it prints either way. `Dependencies` lists every stdlib module the library
needs, the modules that its imports bring in included: one `use <io/fs>` gives six lines.
It also lists each library that the library's own units use, with the name and the
version that its build found: `<lib/b> (b 0.1.0)`. See
[Documentation Blocks](documentation-blocks.md#what-travels-in-a-slib) for the record and
for the few things that do not travel in it.

This is useful for:
- Checking what functions a library exports, and what each one is for
- Reading a library's contracts: every public perk prints with its method signatures, and
  `Perk Implementations` lists which types satisfy each one -- a concrete implementation
  and a generic-target template (`extend Box@(T) with Show`) alike
- Reading the methods a library adds to a type: `Extension Methods` prints one line per
  method, as it was declared (`extend Vec static at(i32 x, i32 y) Vec`), a template
  (`extend Box@(T) tag() i32`) beside the concrete ones
- Seeing what a unit hands on: `Re-exports` prints one line per `public use`, as the
  statement was written, so a façade unit's whole surface reads off the report
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

Portable as text is not the same as portable in behaviour. See Limitation #1 below.

## Versions and Compatibility

A `.slib` records two versions, with two different jobs.

### `library_version` — the library's own version

`major.minor.patch`, taken from the first of these that exists:

1. `[package] version` in the `nori.toml` in the current directory (never one in a parent
   directory, and never one beside the sources)
2. the `--lib-version X.Y.Z` flag

Neither present is **CE3505**, and so is a `--lib-version` that contradicts the
`nori.toml` — silently preferring one would let a package ship under a version it does not
claim. A `nori.toml` that exists must be valid: a file that cannot be read is **CE3518**,
and any fault that nori refuses is **CE3517**. The packager stays the source of truth for a
real package, without forcing a manifest on a bare `./sushic --lib` build.

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

### The format versions

The file itself carries two more versions: the container version (`5`) and the templates
schema version (`8`). A signature without `| E` is bare (`docs/design/error-channel.md`).
Every function, helper and method record in the manifest states `has_channel`, and the field
is required. A library of another version is refused: its container version is **CE3509**,
and the templates schema of a binary or hybrid library is **CE3512**. Rebuild the library
with the current compiler. `docs/library-format.md` carries the rows.

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

A symbol carries the unit that declared it -- `<unit>$<name>`, with every `/` in the unit
name becoming `$` -- so two units may each declare a private `helper` without colliding
(`docs/design/unit-namespaces.md` section 9). The priority table above is about a name two
units both offer, not about a symbol two units both take.

### Which Symbol a Binary Library's Body Calls

A **source** library recompiles at the consumer, and its units are renamed to
`lib/<library>/<unit>` on the way in, so the consumer derives every symbol itself. A
**binary** library links: its bodies are in the shipped bitcode, and the symbols in them
were named by the PRODUCER's compiler. The manifest records those names in `link_symbol`,
one per record that has a symbol, and the binary path is the only reader. The manifest also
records the declaring `unit` on every record, which is a different question and is answered
for both kinds. `docs/library-format.md` carries the schema and the reasoning.

### Unused Library Functions

The compiler does not remove unused library functions. A public function of a library that the
program never calls is still in the executable, also at `--opt O3`.

## Best Practices

### 1. Use Public Sparingly

Mark a declaration `public` only if it is part of your library's API. Everything you mark
is API you have to keep; everything you leave unmarked you can change:

```sushi
# Good: only the API is marked
public const i32 SCALE = 2

public fn calculate(i32 x) i32:
    return internal_helper(x)

struct Work:
    i32 at

fn internal_helper(i32 x) i32:
    return x * SCALE
```

`zlib` is the example the rule is for: 38 functions and 13 types and constants, of which 6
functions and one enum belong in the API. It exports 7 names instead of 19.

### 2. Document Your Library

Write a `##: ... :##` doc block on every public symbol. A block is part of the declaration,
so the library carries it and `--lib-info` prints it; a `#` comment is dropped at the
boundary and reaches nobody.

An `- Example:` is worth writing on a public symbol: the code travels in the index, and
`python tests/docs_sweep.py` compiles and runs it against the library's own source, so an
example that drifts out of date says so. `--lib-info --docs` prints the example last, under its
caption; the plain `--lib-info` report prints no doc block.

```sushi
use <math>

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
    let i32 r1 = add(1, 2)
    if (r1 != 3):
        println("FAIL: add(1, 2) = {r1}, expected 3")
        return 1

    println("All tests passed")
    return 0
```

## Limitations

Current limitations of the library system:

1. **Portable as text, not automatically in behaviour**: a source library compiles anywhere,
   but Sushi has no conditional compilation — no `cfg`, no build tags, no per-platform source
   files. A library that binds a platform-specific C function through `unsafe external` still
   only builds where that function exists, and it cannot yet say so.
2. **A binary library is platform-bound**: `--lib-kind binary` or `hybrid` ships bitcode,
   which is bound to the platform that produced it (**CE3504**).
3. **A public generic cannot reach FFI**: a public generic whose body (transitively)
   references an `unsafe external` namespace, or a private helper whose signature exposes a
   foreign `ptr`, cannot be exported (**CE5006**; see also **CE5002**). Wrap the foreign
   detail behind a private helper with a C-ABI-free signature. This applies to every kind,
   source included.
4. **A public native variadic cannot be exported**: a `...T` variadic collects into a runtime
   `T[]` inside one concrete function, so there is no template to monomorphize and public
   export is **CE0116**. A type pack (`...Ts`) is different: it exports as a template. This
   applies to every kind, source included.
5. **Generic instantiation across a BINARY boundary**: the notes below describe how generics
   cross a `--lib-kind binary` library. A source library needs none of this machinery — its
   generics are ordinary source in ordinary units, so they monomorphize exactly as they would
   in a multi-file program. Regular generic *functions*, *variadic-generic pack* functions
   (`...Ts`), and generic *structs*/*enums* can be instantiated across `.slib` boundaries.

   The library producer ships a re-parsable source template in the `.slib` `templates`
   section (templates version 8); the consumer re-parses it, registers it alongside its own
   definitions, and monomorphizes it at consumer call sites using the standard `instantiate`/`monomorphize`
   machinery. A pack function carries `type_params` (the `...Ts` is recorded with `is_pack`), so it
   ships as a template and is monomorphized per call site exactly like a regular generic. Perk
   *definitions* are also shipped so consumers do not need to redeclare a perk contract that
   originates in the library. Constraint re-checking uses `CE4006` against the consumer's
   perk-impl table.

   **Perk implementations also ship**: a library's own `extend <ConcreteType> with <Perk>:`
   block for a shipped perk crosses the boundary, so a
   consumer can instantiate e.g. `pick_bigger@(T: Doubler)` at `i32` without writing
   `extend i32 with Doubler` itself. The impl's bodies are not re-compiled at the consumer - its
   signatures register for constraint checking and dispatch, the method symbols are declared, and
   the definitions link from the library bitcode (where they carry weak linkage). Precedence:
   a consumer's own impl of the same `(type, perk)` always wins, both semantically and at link
   time; across multiple libraries shipping the same impl, the first registered wins; if a local
   extension method on the target type already uses one of the impl's method names, the library
   impl is skipped entirely (write your own `extend` to opt in, which surfaces the normal
   `CE4007` conflict diagnostics). Only impls of perks referenced by an exported generic's
   constraints ship; impls of library-internal perks stay internal as contracts, and
   their methods ship as ordinary extension methods (below). An impl of a predefined perk
   (`Drop`, `Hashable`, `Eq`, `Ord`, `Display`) always ships. A shipped `Drop` makes the
   type own a resource at the consumer: it moves, and scope exit calls the library's
   compiled `drop()`. A consumer cannot add a `Drop` to a library type (`CE4012`), so the
   first precedence rule does not apply to `Drop`. A generic-target
   implementation (`extend Box@(T) with Show`) ships as a template in
   `templates.generic_perk_impls`, and the consumer makes a copy for each instantiation.

   **Extension methods also ship**: a concrete extension method (instance or static) is a
   signature record in `templates.extensions`, with the symbol that the library bitcode
   defines. The consumer registers the method and declares the symbol. An extension
   template is a source record in `templates.generic_extensions`, and the consumer makes
   a copy for each instance it names. A template body may call a private function of the
   library, through the export closure below.

   **Private helpers ship automatically (the export closure)**: a public generic whose body
   references library-private symbols exports: the producer walks the
   transitive closure of everything the generic depends on and ships it: private *generic*
   helpers as source templates (flagged `private`), private *concrete* helpers as signature
   records (their definitions carry external linkage in the library bitcode and link at the
   consumer), and *constants* with their source (the consumer needs the value for compile-time
   evaluation). The manifest's `templates.closure_summary` lists what shipped, by kind. At the
   consumer, a local symbol with the same name as a shipped private is an error (**CE5007**,
   not local-wins): shadowing it would silently change what the library's monomorphized bodies
   call. A shipped private helper is callable by the library's own bodies and by nothing
   else: consumer code that names one is `CE3005`, like any other private function. A shipped
   private constant is the same: a consumer that reads it hears `CE3005`. None of this can arise on the source path: library units are namespaced, so
   there is no shared namespace to clash in, and nothing has to be shipped ahead of need.

   **A private the closure does not ship is named too.** The closure only walks what a
   public *generic* needs, so a private a concrete function calls -- or one nothing public
   calls -- ships nowhere. The manifest's `not_exported` key carries those names and their
   kind, and nothing else: no signature, no body, no source. It is what lets the consumer
   hear `CE3005` for them. A name in that list is not shipped,
   so it clashes with nothing: a consumer may declare a function of the same name and it is
   the consumer's own.

## See Also

- [Nori Package Manager](package-manager.md) - Packaging and distributing libraries
- [Compiler Reference](compiler-reference.md) - All compiler options
- [Getting Started](getting-started.md) - Introduction to Sushi
- [Standard Library](standard-library.md) - Built-in library modules
