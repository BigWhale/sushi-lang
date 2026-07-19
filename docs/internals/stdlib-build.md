# Standard Library Build Process

[← Back to Documentation](../index.md) | [Architecture](architecture.md)

How the Sushi stdlib's precompiled bitcode is generated, kept fresh, and linked.

## Overview

The stdlib is not written in Sushi and does not use the `.slib` format. It is a set of
Python modules that emit LLVM IR directly (via llvmlite), compiled ahead of time into
per-platform `.bc` files.

| Aspect | Standard Library | User Libraries |
|--------|-------------------|----------------|
| Source | Python emitting LLVM IR | Sushi source code |
| Format | Raw `.bc` (LLVM bitcode) | `.slib` (bitcode + metadata) |
| Build tool | `sushi_stdlib/build.py` | `./sushic --lib` |
| Metadata | Built into the compiler (`stdlib_registry.py`) | Embedded in the `.slib` file |
| Freshness | Content-fingerprinted, auto-rebuilt by the compiler | N/A (compiled per invocation) |

Raw LLVM IR gives the stdlib access to constructs the Sushi language itself doesn't
expose (raw libc externs, manual struct layout for `string`/`Result`/`Maybe`, etc.),
and lets generic containers (`List@(T)`, `HashMap@(K, V)`) be emitted inline per
instantiation instead of precompiled for every possible type argument.

## Directory Structure

```
sushi_lang/sushi_stdlib/
├── build.py              # Build script (build_all, per-unit build_* functions)
├── src/                  # Python IR generators, one package per unit
│   ├── collections/
│   │   ├── strings/      # generate_module_ir() -> collections/strings.bc
│   │   └── strings_inline.py   # emitted directly by the backend, not via build.py
│   │                            # (is_empty()/strcmp/strlen intrinsics needed pre-`use`)
│   ├── io/
│   │   ├── stdio/        # -> io/stdio.bc (platform-specific handles)
│   │   └── files/        # -> io/files.bc
│   ├── sys/
│   │   ├── env/          # -> sys/env.bc
│   │   └── process/      # -> sys/process.bc
│   ├── math/              # -> math.bc
│   ├── random/            # -> random.bc
│   ├── time/               # -> time.bc
│   ├── _platform/         # per-OS constants (darwin/, linux/, posix/) used by
│   │                       # the generators above via common.py
│   └── common.py, ir_builders.py, libc_declarations.py, ...   # shared helpers
└── dist/                  # Build output, one directory per target platform
    ├── darwin/
    │   ├── collections/strings.bc
    │   ├── core/primitives.bc
    │   ├── io/{stdio,files}.bc
    │   ├── sys/{env,process}.bc
    │   ├── math.bc, random.bc, time.bc
    │   └── .build_fingerprint   # marker written by stdlib_builder.py
    └── linux/              # same layout; only produced by building on Linux
```

`core/primitives.bc` is generated from `sushi_lang/backend/types/primitives/`
(`to_str.py`), not from anything under `sushi_stdlib/src/` — it lives with the rest of
the primitive-type codegen because it's also used directly by the backend, not only
shipped as a linkable unit.

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
`nanosleep`/`getenv`) is resolved inside the generator via `_platform/{darwin,linux}`,
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

This calls `build_all(platform_name)`, which initializes LLVM's native target, runs
every `build_@(unit)()` function (each: import the generator, call
`generate_module_ir()`, `llvm.parse_assembly()` the IR, write `.as_bitcode()` to
`dist/<platform>/...`), then writes the freshness marker described below. `--platform`
only affects which `dist/` subdirectory the output lands in; without it, the platform
is auto-detected via `platform_detect.get_current_platform()`. Cross-compilation is not
supported — build on the target OS.

## Staying Fresh: `backend/stdlib_builder.py`

`dist/*.bc` files are prebuilt artifacts, not regenerated per compile. The compiler
does not blindly trust them: `sushi_lang/compiler/pipeline.py` calls
`ensure_stdlib_built()` before linking any stdlib unit, which:

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
- every `*.py` under `sushi_lang/backend/types/primitives/` (the package `build.py`
  generates `core/primitives` from),
- `sushi_lang/sushi_stdlib/build.py` itself.

Each path is hashed as `<path relative to sushi_lang/>:<file bytes>`, sorted, so the
digest is stable across checkouts and platform-independent (the same sources produce
both platforms' `.bc`; the digest doesn't encode which platform was built).
Granularity is whole-tree, not per-unit — editing any one generator or a shared helper
(`common.py`, `ir_builders.py`, ...) invalidates every platform's marker and triggers a
full rebuild of all units, not just the one that changed. This is deliberate: shared
helpers legitimately affect many units, and stdlib rebuilds are cheap relative to a
real compile.

The hasher skips a listed path that does not exist, which once silently dropped the
primitives generator from the digest when it became a package (edits stopped
invalidating `.build_fingerprint`). The source list is now pinned by
`tests/unit/test_fingerprint.py`, which fails if any listed path goes missing.

## Forcing a Rebuild

```bash
./sushic --build-stdlib [file.sushi]
```

`cli.py` handles this before any source file is required: it calls
`backend.stdlib_builder.detect_platform()` then `build_all(platform_name)` directly
(the *loud*, non-quiet path — full per-unit progress output), unconditionally,
bypassing the fingerprint check entirely. A `StdlibBuildError` (`CE0007`) wraps any
exception from the build. If no source file is given, the compiler exits 0 after the
build; if one is given, compilation proceeds normally afterward (using the bitcode
just rebuilt).

## Linking

At compile time, `backend/stdlib_linker.py` (`StdlibLinker`) resolves each `use
<module>` referencing a stdlib path to its `.bc` file(s) under `dist/<platform>/` and
links them into the output module. A handful of unit names are *virtual* — they
resolve to no `.bc` file because they're emitted inline instead
(`collections/hashmap`, and `collections/iter`, which is a bundled **Sushi-source**
module compiled as an ordinary unit — see `semantics/stdlib_registry.py`'s
`SOURCE_STDLIB_MODULES`, distinct from the `.bc` units this doc covers).

Module *metadata* (which functions a unit exposes, their signatures, validators) is
registered separately in `semantics/stdlib_registry.py`'s `StdlibRegistry.KNOWN_MODULES`
— a mapping of unit path to the Python module that implements it. There is no
`stdlib_loader.py`; that job is split between `stdlib_linker.py` (resolves a unit path
to `.bc` file paths for linking) and `stdlib_registry.py` (resolves a unit path to
compile-time function metadata). `compiler/loader.py` is unrelated — it handles
`.sushi` source-unit loading and `use`-statement bookkeeping, not stdlib bitcode.

## Adding a New Stdlib Module

1. Write the generator: a package/module under `src/` with `generate_module_ir()`.
2. Add a `build_@(name)()` function to `build.py` and call it from `build_all()`.
3. Register the module's function metadata in `StdlibRegistry.KNOWN_MODULES`
   (`semantics/stdlib_registry.py`) so `use <name>` type-checks calls into it.
4. If the unit needs its own `.bc` resolution logic beyond the default
   `dist/<platform>/<path>.bc` lookup (e.g. a directory import spanning several
   `.bc` files, like `io`), extend `StdlibLinker._resolve_stdlib_unit`.

`compute_stdlib_source_fingerprint()`'s whole-tree scan over `sushi_stdlib/src/`
picks up any new generator automatically — no fingerprint-list update needed unless
the generator lives outside that tree (see the `core/primitives` gap above).

## Generic Types

`List@(T)`, `HashMap@(K, V)`, `Maybe@(T)`, `Result@(T, E)`, and `Own@(T)` are not built by
`build.py` and have no `.bc` — they're monomorphized and emitted inline per
instantiation at compile time. The emitters live in `sushi_lang/backend/generics/`
(`list/`, `hashmap/`, `maybe.py`, `own.py`, `results.py`), with the IR-free half
(method validation, type-table plumbing) in the mirroring
`sushi_lang/semantics/generics/`. There is no `sushi_stdlib/generics/` directory —
it existed pre-Tier-4.2 and was deleted when the split above was introduced.

## Troubleshooting

**"LLVM target not initialized"** — run `uv sync` to ensure llvmlite is installed.

**Platform mismatch** — you cannot link macOS-built `.bc` on Linux or vice versa; the
platform directories are not interchangeable. Build on the target platform (or via CI).

**Edited a generator, nothing changed** — normal compiles rebuild automatically
(`ensure_stdlib_built`) as long as the edited file is under `sushi_stdlib/src/`, is
`sushi_stdlib/build.py`, or is one of the paths in
`compute_stdlib_source_fingerprint()`. If the file isn't covered (see the
`core/primitives` gap above), force it with `./sushic --build-stdlib`. As a last
resort, delete `dist/<platform>/.build_fingerprint` to force the next compile to treat
the directory as stale.

**"unit not found" on `use <module>`** — check the module exists in `dist/<platform>/`
(or is a virtual unit in `StdlibLinker._virtual_units`) and is registered in
`StdlibRegistry.KNOWN_MODULES`.

## See Also

- [Libraries](../libraries.md) - User library creation (`.slib` format)
- [Library Format](../library-format.md) - `.slib` binary format specification
- [Architecture](architecture.md) - Compiler overview
