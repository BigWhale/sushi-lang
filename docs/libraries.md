# Libraries

[← Back to Documentation](index.md)

Sushi supports compiling code to reusable libraries and linking them into programs. This enables code sharing, modular architecture, and faster incremental builds.

**A `.slib` is Sushi source plus an index.** The consumer compiles that source as ordinary
compilation units and caches the object files, so one library file works on every platform:
text carries no target triple.

> Contributor-level design: see [design/libraries.md](design/libraries.md) for how the
> `.slib` container and the manifest work internally.

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

The `--lib` build checks each generic template of the library one time, where it is
written, also a template that the library does not call
([Checked generics](design/checked-generics.md)). So the author gets the fault of a
template, for example a `.hash()` on a `T` with no `Hashable`, and the consumer does not.
The consumer does not check a library template again; it copies the template for its own
type arguments.

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
directory when there is one, and from `--lib-version` otherwise; neither is **[CE3505](error-catalog.md#ce3505)**. See
[Versions and Compatibility](#versions-and-compatibility).

### What a Library Exports

A library exports its PUBLIC extension methods, instance and static, and a private one
stays in the library ([CE3005](error-catalog.md#ce3005) at the consumer). A consumer calls
`Vec.at(3, 4)` and `v.sum()` on a library's `Vec` as it does on the `Vec` of a unit of
its own program. The rules of `docs/design/extension-visibility.md` apply as for a unit of the
program: a public extension on a type that the library unit declares travels with the
type, and one on a type that it does not declare (`i32`, an array) is visible only in a
unit that imports the library unit. The consumer compiles each method from the source of
the library. The consumer makes its own copies of a template (`extend Box@(T)`,
`extend Box@(i32)`, `extend T[]`, `pick@(U)`). A method of an implementation of a
private perk ships as an ordinary method of the type: the contract stays hidden, and the
method stays callable. A consumer that declares the same method on a type of the library
hears [`CE0101`](error-catalog.md#ce0101); one that declares the same
method on a type that the library does not declare wins in its own unit, with the warning
[`CW3007`](error-catalog.md#cw3007).

A library can export a public native variadic (`...T`). The consumer compiles the unit as
its own, and calls the function with any count of trailing arguments or with a bloom
`arr...`. `--lib-info` prints the parameter as it is written (`...i32 nums`).

A library re-exports. A façade unit that says `public use` on each of the library's
other units is the way to give a multi-unit library one namespace. The library ships the
statement as text, and the consumer's compiler reads it. The manifest also has a
`reexports` record for each statement, which `--lib-info` prints. A `public use <io/fs>` hands the module on the same
way, and the consumer's build compiles the module on the strength of the record even
where no unit of its own wrote the import. A `public use <lib/other>` hands on another
library with the same rule: the consumer's build finds `other` on `SUSHI_LIB_PATH` as if
the consumer wrote `use <lib/other>`, and `other`'s public names are the consumer's to
write. If `other` is not on the path, the consumer gets [CE3502](error-catalog.md#ce3502) with a note that names the
`public use`.

### Unchecked Indexes and Consent

A library function may carry the marker `dont_panic because "<reason>"`, which removes the
bounds check of each `[]` in its body (see the
[language reference](language-reference.md#unchecked-indexes-dont_panic)). The consumer
compiles the source text of the library, so the consumer accepts the unchecked code: a
consumer of a library with a marked function must pass `--dont-panic`. This applies to a
concrete function and to a generic template. Without the flag the build stops with
[CE0152](error-catalog.md#ce0152), and the message names the library. Build the library
itself with `--dont-panic` too.

A library records its marked functions in the manifest, and `--lib-info` lists them (see
[Inspecting Libraries](#the---lib-info-flag)). A library author who marks a function says
so in the library's documentation. A marked function in a library unit gets no
[CW0004](error-catalog.md#cw0004) in a consumer's build.

### Library Dependencies

A library that writes `use <lib/b>` depends on `b`. Two rules apply:

1. **Loading is transitive.** The library records each `use <lib/...>` of its own units,
   plain or public, in the manifest `dependencies`, with the name and the version of the
   `b` that its build found. The consumer's build loads every library of that graph and
   compiles it with the program. So a library body always has what it calls, and the
   consumer does not write `use <lib/b>` for a library it does not call itself. A stdlib
   module that a library uses is loaded the same way.
2. **Visibility is not transitive.** A unit may write the names of what it imports
   itself, and of what a `public use` hands on. A plain `use <lib/b>` in a library loads
   `b` and gives the consumer no name of it: a bare `b_val()` at the consumer is [CE2008](error-catalog.md#ce2008)
   until the consumer writes `use <lib/b>`, or the library writes `public use <lib/b>`.

Visibility is also PER UNIT. A library's
public functions, constants, unit variables and types (concrete and generic) are names
only in a unit of the consumer that imports the library itself, or that reaches it
through a `public use` chain. If `main.sushi` writes `use <lib/a>` and `use "helper"`, a
call of `a_val()` in `helper.sushi` is [CE2008](error-catalog.md#ce2008) until `helper.sushi` writes `use <lib/a>`
too.

A generic template of a library is copied at the consumer, with the type arguments of
the call. The body of the copy resolves its names in the scope of the library unit that
declares the template: the library's own declarations, and every library and stdlib
module that THAT unit uses, plain or public. So a template that calls `b_val()` works
when its unit loads `b` with a plain `use <lib/b>`, and the consumer still cannot
call `b_val()` itself. The copy works also when the unit that holds it does not import
the library. A `use` in another unit of the same library does not count: the call gives
[CE2008](error-catalog.md#ce2008) (#1123). Each manifest `dependencies` record names the
units that write it.

A library is identified by the `library_name` stamped into its `.slib`, not by the path
that the search finds. One library that several paths reach -- the consumer and a
library, or two libraries in a diamond -- is loaded once and gives one candidate for each
name, so `use <lib/a>` beside `use <lib/b>` is not [CE3012](error-catalog.md#ce3012). Two versions of one library in
one graph are [CE3519](error-catalog.md#ce3519): for example, `a` was built against `b` 0.1.0 and the consumer's
search finds `b` 0.2.0. The diagnostic names each version and the path that reached it.
A dependency that the consumer's search cannot find is [CE3502](error-catalog.md#ce3502), with a note at the `use`
that needs it, for a plain `use` and for a `public use` alike.

Two libraries that declare one public extension method on one type can be used in one
program. A unit that imports both and calls the method gets [CE3023](error-catalog.md#ce3023) at the call, with
a note for each library: there is no qualified method call, so move the
code that needs each method into a unit of its own.

### The library name is the name of the `use`

A build stamps the name of the library into its `.slib`: the `library_name` is the stem of
the output file, so `-o mylib.slib` makes the library `mylib`. A `use` names a library with
the last part of its path: `use <lib/mylib>` and `use <lib/vendor/mylib>` both name
`mylib`. The consumer refuses a file whose stamped name is not the name of the `use` that
loaded it, with [CE3521](error-catalog.md#ce3521). This applies to each load of the graph:
the consumer's own `use`, and a `use` or a `public use` in a library.

So do not copy or rename a `.slib` file to load it under another name. Such a file hides
which library a build uses, and two files with one stamped name meet as two versions of
one library. The message names the two names and the file:

```
CE3521: `use <lib/other>` loads the library 'other', but the file 'libs/other.slib' is the library 'mylib'
```

Build the library again with the name of the `use` (`-o other.slib`), or write the `use`
with the library's own name (`use <lib/mylib>`). `--lib-info` reads a file with no `use`,
so it does not check the name.

### Public Declarations

Only declarations marked `public` are accessible from other compilation units. Seven kinds
carry the marker -- `fn`, `const`, `var`, `struct`, `enum`, `error` and `perk` -- and private is the
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
and that breaks every caller. The compiler does not enforce this.
`docs/design/error-channel.md` carries the rule.

A generic is no exception. `public fn pick@(T)(...)` is part of the API; `fn pick@(T)(...)`
is internal, and a consumer that calls it hears [`CE3005`](error-catalog.md#ce3005) exactly as it does for a concrete
function. [`CE2008`](error-catalog.md#ce2008) is left for what it is for: a name that no
unit and no library declares.

### A library that links a C library

A unit of a library can declare an `unsafe external` block with a `link` clause
([FFI](ffi.md#linking-an-external-library)). Keep the block private, and make a Sushi
wrapper public:

<!-- docs-sweep: skip (a library unit; build it with --lib) -->
```sushi
unsafe external "C" link "z" as zlib because "CRC-32 from zlib":
    fn crc32(u64 crc, string buf, u32 len) u64 = "crc32"

public fn checksum(string s) u64:
    return zlib.crc32(0, s, s.len() as u32)
```

- **The library** records nothing in its manifest. The consumer compiles the blocks of the
  library, so it reads the clauses there, and writes no `link` clause of its own.
- **The consumer's machine** must have the C library, because the consumer runs the link.
  When the library is outside the default directories, the consumer builds with
  `./sushic main.sushi -L <dir>`. A missing library is
  [`CE3008`](error-catalog.md#ce3008), and a note names the library that asked for it.
- The library build itself links nothing, so `-L` with `--lib` has no effect
  ([`CW0003`](error-catalog.md#cw0003)).

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

- **A public signature may not name a private type** ([`CE3009`](error-catalog.md#ce3009)), and a public constraint
  may not name a private perk ([`CE3010`](error-catalog.md#ce3010)). Privacy is worth nothing if the signature hands
  the type out anyway, so mark what the API returns and takes.
- **A private declaration a template body needs still travels.** A public generic's body
  may name a private type, constant or helper. The library ships the whole unit as source,
  so the consumer can monomorphize, and the consumer still cannot name them itself.
- **A public constant is API.** A consumer's own constant of that name is
  [`CE0105`](error-catalog.md#ce0105). (Between two ordinary
  units of one program, the unit's own constant wins with no diagnostic.)

A consumer that writes a library-private name hears [`CE3005`](error-catalog.md#ce3005) -- "private struct 'Cursor',
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
3. Compile the library's source units with the program

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

Use `--lib-info` to display the metadata of a library:

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
Compiler: 0.17.0
Requires compiler: ~0.14
Protocol: 2.10

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

Dependencies (7):
  <collections/strings>
  <io/contracts>
  <io/error>
  <io/files>
  <io/fs>, used by mylib
  <io/path>
  <net/error>

Source: 643 bytes
```

A documented symbol prints its doc block, indented two spaces under its own line, with a
blank line closing the block and another between one claim and the next; `multiply` above
has no block, so it prints as a bare line and the run of bare lines stays dense. Without
`--docs` no block prints at all and the whole report is that dense -- prose is what makes
a report long, and a reader asking what a library exports usually does not want ten
screens of it. A `nom` parameter shows its mode, which is the one mode a type cannot
spell, and it prints either way. `Dependencies` lists every stdlib module the library
needs, the modules that its imports bring in included: one `use <io/fs>` gives seven lines
(`<io/error>` imports `<net/error>` for the conversion `NetError as IoError`).
It also lists each library that the library's own units use, with the name and the
version that its build found: `<lib/b> (b 0.1.0)`. An enum declared with `error` prints
as `error Name:`, and a `Conversions` section lists each conversion between two error
types that the library declares, one line per pair (`extend LowError as LibError`). See
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
- Seeing the unchecked code before the first build: `Unchecked Indexes` prints one line per
  function marked `dont_panic`, as `  unit: name dont_panic because "reason"`. The unit is
  the unit that declares the function, because two units can each declare a private
  function of one name. The name is `name`
  for a function or a template, `Type.method` for an extension method, a static or a perk
  implementation method, and `Source as Target` for a conversion. The section is absent
  when nothing is marked
- Seeing what a unit hands on: `Re-exports` prints one line per `public use`, as the
  statement was written, so a façade unit's whole surface reads off the report
- Understanding library dependencies

## Library Format

### The `.slib` Container

One file holds a MessagePack index next to a payload, framed by a fixed 52-byte header:

```
[Magic: 16 bytes] [Version: 4 bytes] [Flags: 4 bytes] [Kind: 4 bytes] [Reserved: 16 bytes]
[Metadata Length: 8 bytes] [Metadata: MessagePack]
[Source Length: 8 bytes]   [Source: MessagePack map, unit name -> source text]
[Bitcode Length: 8 bytes]  [Bitcode: empty]
```

The source section holds the library. The bitcode section is empty. See
[Library Format](library-format.md) for the full specification.

### Platform Compatibility

A library is portable. It carries text, and text has no target triple, so the same `.slib`
builds on macOS and on Linux. The consumer compiles the library with its own overflow mode
(`--overflow=trap|wrap`, see [Integer Overflow](compiler-reference.md#integer-overflow)).

Portable as text is not the same as portable in behaviour. See Limitation #1 below.

## Versions and Compatibility

A `.slib` records two versions, with two different jobs.

### `library_version` — the library's own version

`major.minor.patch`, taken from the first of these that exists:

1. `[package] version` in the `nori.toml` in the current directory (never one in a parent
   directory, and never one beside the sources)
2. the `--lib-version X.Y.Z` flag

Neither present is **[CE3505](error-catalog.md#ce3505)**, and so is a `--lib-version` that contradicts the
`nori.toml` — silently preferring one would let a package ship under a version it does not
claim. A `nori.toml` that exists must be valid: a file that cannot be read is **[CE3518](error-catalog.md#ce3518)**,
and any fault that nori refuses is **[CE3517](error-catalog.md#ce3517)**. The packager stays the source of truth for a
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
schema version (`11`). A signature without `| E` is bare (`docs/design/error-channel.md`).
Every function, helper and method record in the manifest states `has_channel`, and the field
is required. A library of another container version is refused with
**[CE3509](error-catalog.md#ce3509)**. A manifest of the wrong shape, or a manifest that
states the retired `binary` kind, is **[CE3512](error-catalog.md#ce3512)**. Rebuild the
library with the current compiler. `docs/library-format.md` carries the rows.

## Symbol Resolution

### Priority Rules

When the same symbol is defined in multiple places:

| Priority    | Source           | Description                    |
|-------------|------------------|--------------------------------|
| 1 (highest) | Main program     | Your program's definitions win |
| 2           | User library     | Library definitions            |
| 3           | Standard library | Stdlib definitions             |
| 4 (lowest)  | Runtime          | Runtime helper functions       |

This means you can override library functions in your main program. The consumer's call
binds to the consumer's declaration, the library's own body keeps calling its own, and the
build warns with [CW3002](error-catalog.md#cw3002).

A symbol carries the unit that declared it -- `<unit>$<name>`, with every `/` in the unit
name becoming `$` -- so two units may each declare a private `helper` without colliding
(`docs/design/unit-namespaces.md` section 9). The priority table above is about a name two
units both offer, not about a symbol two units both take.

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
public error AddError:
    Overflow

##:
Adds two integers.

- Parameter a: The first addend.
- Parameter b: The second addend.
- Returns: The sum.
- Errors: `AddError.Overflow` when the sum does not fit an `i32`.
:##
public fn safe_add(i32 a, i32 b) i32 | AddError:
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
2. **A public signature cannot expose FFI**: a public function whose signature exposes a
   foreign `ptr` cannot be in a library (**[CE5002](error-catalog.md#ce5002)**). Wrap the
   foreign detail behind a private helper with a C-ABI-free signature.

## See Also

- [Nori Package Manager](package-manager.md) - Packaging and distributing libraries
- [Compiler Reference](compiler-reference.md) - All compiler options
- [Getting Started](getting-started.md) - Introduction to Sushi
- [Standard Library](standard-library.md) - Built-in library modules
