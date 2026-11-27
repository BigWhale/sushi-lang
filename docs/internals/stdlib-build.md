# Standard Library Build Process

[← Back to Documentation](../index.md) | [Architecture](architecture.md)

How the Sushi stdlib is made: the precompiled bitcode units, how they stay fresh and how
they are linked, and the stdlib modules that are written in Sushi.

## Overview

The code that a Sushi program runs, other than its own, comes from three layers. None of
them uses the `.slib` format.

| Layer | What it is | Can it be Sushi? |
|---|---|---|
| A. The runtime | The IR that the backend emits into every program: interpolation, `print`, runtime errors, `List@(T)`, `HashMap@(K, V)`, `Own@(T)`, `Maybe@(T)`, `Result@(T, E)`, clone, drop, hash and bounds checks. No `use` reaches it | No. It is the compiler's own definition of the value layouts ([Generic Types](#generic-types)) |
| B. The Sushi-source modules | Stdlib modules written in Sushi, under `sushi_stdlib/src_sushi/` | They are Sushi |
| C. The bitcode units | Python generators that emit LLVM IR (with llvmlite), compiled ahead of time into one `.bc` file for each unit and platform | Yes. They move to layer B one unit at a time |

"The stdlib in Sushi" means layers B and C. Layer A stays in the compiler.

- **Sushi-source modules (layer B).** 27 modules, and `<sys/platform>`, which has one file
  for each host ([Per-platform source modules](#per-platform-source-modules)). The list is
  `SOURCE_STDLIB_MODULES` (`semantics/stdlib_registry.py`). The compiler injects each one as
  an ordinary compilation unit when a program imports it. A module that calls C declares an
  `unsafe external` block (for example `math`, `time`, `sys/process`), and a module can sit
  on a bitcode unit (for example `io/fs` over `io/files`, `net/tcp` over `net/socket`).
- **Bitcode units (layer C).** Two units are left: `collections/strings` and `net/socket`
  (`STDLIB_BITCODE_UNITS` in `build.py`). `collections/strings` has two halves: the bitcode,
  and a Sushi file in layer B. `<io/files>` is fully in layer B (`io/files.sushi`).
  `tests/stdlib_python_lines.baseline` holds the count of Python lines under
  `sushi_stdlib/src/` (6449 on 2026-11-27: about 3800 for `collections/strings`, 1600 for
  `net/socket`, and the shared helpers); the test runner fails when the count goes up or
  goes down without a change of the baseline.

| Aspect | Bitcode units | Sushi-source modules | User libraries |
|--------|---------------|----------------------|----------------|
| Source | Python emitting LLVM IR | Sushi source code | Sushi source code |
| Format | Raw `.bc` (LLVM bitcode) | `.sushi` files in the package | `.slib` (source text and a manifest) |
| Build tool | `sushi_stdlib/build.py` | none (compiled with the program) | `./sushic --lib` |
| Metadata | Built into the compiler (`stdlib_registry.py`) | The declarations in the source | Embedded in the `.slib` file |
| Freshness | Content-fingerprinted, auto-rebuilt by the compiler | In the unit cache key through the dependency graph | In the unit cache key through the dependency graph |
| Reuse on a warm build | The `.bc` is linked | The object is in the per-user cache, and the body passes are skipped | The object is in the project cache, and the body passes are skipped |

The reuse of a Sushi-source module and of a library unit has one predicate,
`UnitReuse.is_reused` (`semantics/unit_reuse.py`). A reused unit is still parsed and
collected, because the program reads its interface. A unit with a template copy is not
reused. The rules are in [Semantic passes](semantic-passes.md#reuse-of-a-bundled-stdlib-unit-or-a-source-library-unit-the-skip-the-replay-the-fall-back).

## Directory Structure

```
sushi_lang/sushi_stdlib/
├── build.py              # Build script: build_all() over the STDLIB_BITCODE_UNITS table
├── src_sushi/            # Stdlib modules written in Sushi (see Overview)
├── src/                  # Python IR generators, one package per unit
│   ├── collections/
│   │   ├── strings/      # generate_module_ir() -> collections/strings.bc
│   │   └── strings_inline.py   # emitted directly by the backend, not via build.py
│   │                            # (is_empty()/strcmp/strlen intrinsics needed pre-`use`)
│   ├── net/              # -> net/socket.bc
│   ├── _platform/         # per-OS constants (darwin/, linux/, posix/) used by
│   │                       # the generators above via common.py
│   └── common.py, ir_builders.py, libc_declarations.py, ...   # shared helpers
└── dist/                  # Build output, one directory per target platform
    ├── darwin/
    │   ├── collections/strings.bc
    │   ├── net/socket.bc
    │   ├── symbols.json         # the symbols that the .bc files define
    │   └── .build_fingerprint   # marker written by stdlib_builder.py
    └── linux/              # same layout; only produced by building on Linux
```

No bitcode unit holds the methods of the primitive types. The backend emits `to_str()` of
a primitive at the call (`backend/types/display.py`), and the code of the primitive
methods (`backend/types/primitives/`) is not a stdlib generator. After a build, `build.py`
deletes each `.bc` that no row makes, and each empty directory (`prune_stale_bitcode`,
#1324). The linker finds the units on disk, so a `.bc` of a removed row must not stay.

`dist/` is a build-artifact directory, not tracked in git. Only the platform(s) you've
actually built on will have a subdirectory; on a fresh checkout `dist/` may be empty
until the compiler auto-builds it (see below). Supported platform directory names are
`darwin` and `linux` — there is no `windows` target; `sushi_stdlib/build.py --platform`
and `backend/stdlib_builder.detect_platform()` both reject anything else.

## The Generators

Each unit is a Python package (or module) under `src/` exposing a single entry point:

```python
def generate_module_ir() -> ir.Module:
    ...
```

`build.py` imports the package, calls `generate_module_ir()`, and writes the result as
`.bc`. Platform-specific behavior (e.g. which libc symbols to declare for
`open`/`socket`) is resolved inside the generator via `_platform/{darwin,linux}`,
not by branching in `build.py` — the *emitted* IR reflects the platform Python is
running on, `build.py`'s `--platform` argument only picks the output subdirectory.

Two units are conspicuously absent from `dist/`: `core/results` and `core/maybe`.
`Result@(T, E)`/`Maybe@(T)` are monomorphized per type argument and emitted inline at
compile time (see `sushi_lang/backend/generics/`), so there is nothing fixed to
precompile.

## Building

```bash
python sushi_lang/sushi_stdlib/build.py [--platform darwin|linux]
```

This calls `build_all(platform_name)`, which initializes LLVM's native target, loops
over the rows of `STDLIB_BITCODE_UNITS` (for each row: import the generator, call
`generate_module_ir()`, `llvm.parse_assembly()` the IR, write `.as_bitcode()` to
`dist/<platform>/<unit>.bc`), then writes the symbol manifest `symbols.json` (every symbol
that the `.bc` files define) and, last, the freshness marker described below. The manifest
comes first: a build that stops between the two leaves no marker, so the next compile
builds again. `--platform`
only affects which `dist/` subdirectory the output lands in; without it, the platform
is auto-detected via `platform_detect.get_current_platform()`. Cross-compilation is not
supported — build on the target OS.

## Staying Fresh: `backend/stdlib_builder.py`

`dist/*.bc` files are prebuilt artifacts, not regenerated per compile. The compiler
does not blindly trust them. Before it links a stdlib unit, `sushi_lang/compiler/pipeline.py`
calls its one entry point, `build_stdlib()`, which calls `ensure_stdlib_built()`
(`backend/stdlib_builder.py`). A failure of a generator is **[CE0007](../error-catalog.md#ce0007)**.
`ensure_stdlib_built()`:

1. Detects the current platform.
2. Compares `compute_stdlib_source_fingerprint()` (see below) against the digest
   stored in `dist/<platform>/.build_fingerprint`.
3. If the dist directory is missing, the marker is missing, or the digests differ:
   prints a one-line notice, runs `build_all(platform_name, quiet=True)`, and
   rewrites the marker.
4. Memoizes per platform for the life of the process (`_checked`), so this runs at
   most once per compile even with several `use <stdlib>` statements.

### What the fingerprint covers

`compute_stdlib_source_fingerprint()` (`sushi_lang/compiler/fingerprint.py`) is a
SHA-256 over:

- every `*.py` under `sushi_stdlib/src/` (recursive — generators and shared helpers
  alike),
- `sushi_lang/sushi_stdlib/build.py` itself,
- `sushi_lang/backend/types/core/mapping.py` and `sizing.py` (the enum layout, which the
  `.bc` files must match byte for byte),
- `sushi_lang/backend/runtime/constants.py` (the errno tables, which are baked into the
  `.bc` files).

Each path is hashed as `<path relative to sushi_lang/>:<file bytes>`, sorted, so the
digest is stable across checkouts and platform-independent (the same sources produce
both platforms' `.bc`; the digest doesn't encode which platform was built).
Granularity is whole-tree, not per-unit — editing any one generator or a shared helper
(`common.py`, `ir_builders.py`, ...) invalidates every platform's marker and triggers a
full rebuild of all units, not just the one that changed. This is deliberate: shared
helpers legitimately affect many units, and stdlib rebuilds are cheap relative to a
real compile.

The hasher skips a listed path that does not exist. `tests/unit/test_fingerprint.py` pins
the source list and fails when a listed path is missing, so a moved generator cannot
drop out of the digest.

## Forcing a Rebuild

```bash
./sushic --build-stdlib [file.sushi]
```

`cli.py` handles this before any source file is required: it calls
`pipeline.build_stdlib(rebuild=True)`, which calls `build_all(detect_platform())` (the
*loud*, non-quiet path — full per-unit progress output), unconditionally, and bypasses
the fingerprint check. A `StdlibBuildError` ([`CE0007`](../error-catalog.md#ce0007)) wraps any exception from the
build. If no source file is given, the compiler exits 0 after the
build; if one is given, compilation proceeds normally afterward (using the bitcode
just rebuilt).

## Linking

At compile time, `backend/stdlib_linker.py` (`StdlibLinker`) resolves each `use
<module>` referencing a stdlib path to its `.bc` file(s) under `dist/<platform>/` and
links them into the output module. Some unit names are *virtual*
(`StdlibLinker._virtual_units`): they resolve to no `.bc` file. `collections/hashmap` is
virtual because the HashMap is emitted inline for each instantiation. Every Sushi-source
module (layer B in the Overview) is virtual too, because it is compiled as an ordinary unit
of the program. `collections/strings` is the one exception: it is not virtual, because its
bitcode half must link.

Module *metadata* (which functions a unit exposes, their signatures, validators) is
registered separately in `semantics/stdlib_registry.py`'s `StdlibRegistry.KNOWN_MODULES`
— a mapping of unit path to the Python module that implements it. There is no
`stdlib_loader.py`; that job is split between `stdlib_linker.py` (resolves a unit path
to `.bc` file paths for linking) and `stdlib_registry.py` (resolves a unit path to
compile-time function metadata). `compiler/loader.py` is unrelated — it handles
`.sushi` source-unit loading and `use`-statement bookkeeping, not stdlib bitcode.

## Per-platform source modules

A source module may have one file per platform and architecture (#1089). The first one
is `<sys/platform>`. `PLATFORM_SOURCE_MODULES` (`semantics/stdlib_registry.py`) maps the
module name to a file for each host key (`darwin_arm64`, `linux_x86_64`), and `host_key()` (`sushi_lang/internals/host.py`) spells the host from Python's `platform` module.
`SOURCE_STDLIB_MODULES` holds the host's file under the module name, so every other
reader (the injector, the namespaces pass, the doc-block gate and the dead-code gate)
sees an ordinary source module. On a host with no file the name stays known, and the
injector refuses the import with [`CE3021`](../error-catalog.md#ce3021).

This is the whole of the platform selection for Sushi source. There is no conditional
compilation: a platform file holds `public const` declarations only, and a module that
needs a value writes `use <sys/platform>` and names the constant. A symbol that differs
per platform is a `public const string` too, because the link name after `=` in an
`unsafe external` declaration accepts a string constant (`docs/ffi.md`, "Link-name
separation"). `docs/stdlib/system/platform.md` lists what the files hold, how the probe makes
them, and how the fixtures under `tests/stdlib/platform/` test each value against the
host's C library.

## Adding a New Stdlib Module

A module written in Sushi (the usual choice when the module can be written in Sushi):

1. Write the module as a `.sushi` file under `src_sushi/`. Mark each concrete export
   `public`, and give each declaration a documentation block (the runner's stdlib
   doc-block gate checks them).
2. Add one entry to `SOURCE_STDLIB_MODULES` (`semantics/stdlib_registry.py`). A module
   with one file per platform is an entry in `PLATFORM_SOURCE_MODULES` instead
   ([Per-platform source modules](#per-platform-source-modules)).
3. Add the module name to `StdlibLinker._virtual_units` (`backend/stdlib_linker.py`).

A bitcode unit. Do not add a new one: a new stdlib function is written in Sushi, because the
FFI reaches C (`docs/ffi.md`). The Python line ratchet (`tests/stdlib_python_lines.baseline`)
refuses new generator code without a change of the baseline and a written reason. The steps,
for a change to a unit that is left:

1. Write the generator: a package/module under `src/` with `generate_module_ir()`.
2. Add one `StdlibBitcodeUnit(unit, generator)` row to `STDLIB_BITCODE_UNITS` in
   `build.py`. `build_all()` builds every row.
3. Register the module's function metadata in `StdlibRegistry.KNOWN_MODULES`
   (`semantics/stdlib_registry.py`) so `use <name>` type-checks calls into it.
4. If the unit needs its own `.bc` resolution logic beyond the default
   `dist/<platform>/<path>.bc` lookup (e.g. a directory import spanning several
   `.bc` files, like `io`), extend `StdlibLinker._resolve_stdlib_unit`.

`compute_stdlib_source_fingerprint()`'s whole-tree scan over `sushi_stdlib/src/`
picks up any new generator automatically. A generator that lives outside that tree must
be added to `_stdlib_generator_sources()` (`compiler/fingerprint.py`).

## Generic Types

`List@(T)`, `HashMap@(K, V)`, `Maybe@(T)`, `Result@(T, E)`, and `Own@(T)` are not built by
`build.py` and have no `.bc` — they're monomorphized and emitted inline per
instantiation at compile time. The emitters live in `sushi_lang/backend/generics/`
(`list/`, `hashmap/`, `maybe.py`, `own.py`, `results.py`), with the IR-free half
(method validation, type-table plumbing) in the mirroring
`sushi_lang/semantics/generics/`.

## Troubleshooting

**"LLVM target not initialized"** — run `uv sync` to ensure llvmlite is installed.

**Platform mismatch** — you cannot link macOS-built `.bc` on Linux or vice versa; the
platform directories are not interchangeable. Build on the target platform (or via CI).

**Edited a generator, nothing changed** — normal compiles rebuild automatically
(`ensure_stdlib_built`) as long as the edited file is under `sushi_stdlib/src/`, is
`sushi_stdlib/build.py`, or is one of the paths in
`_stdlib_generator_sources()`. If the file isn't covered, force it with
`./sushic --build-stdlib`. As a last
resort, delete `dist/<platform>/.build_fingerprint` to force the next compile to treat
the directory as stale.

**"unit not found" on `use <module>`** — check the module exists in `dist/<platform>/`
(or is a virtual unit in `StdlibLinker._virtual_units`) and is registered in
`StdlibRegistry.KNOWN_MODULES`.

## See Also

- [Libraries](../libraries.md) - User library creation (`.slib` format)
- [Library Format](../library-format.md) - `.slib` file format specification
- [Architecture](architecture.md) - Compiler overview
