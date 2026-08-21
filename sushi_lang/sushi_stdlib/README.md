# sushi_stdlib

Standard library implementation. Two unrelated delivery mechanisms live here —
know which one your change belongs to before you start.

## 1. Compiled IR generators (`src/`)

Most of the stdlib is Python code under `src/` that builds LLVM IR directly with
`llvmlite.ir` (see e.g. `src/time/sleep.py`, `src/math/operations.py`). These are
NOT interpreted at compile time per-program — `build.py` runs them once to produce
per-platform bitcode:

```
sushi_stdlib/
  src/            generators: time/, math/, random/, sys/, io/, collections/, _platform/
  src_sushi/      bundled real-Sushi source modules (see section 2)
  dist/<platform>/  precompiled .bc, organized by module (e.g. dist/darwin/math.bc)
  build.py        drives src/ -> dist/<platform>/*.bc
```

`dist/` is a build artifact (not authoritative — regenerate, don't hand-edit).
`sushi_lang/backend/stdlib_builder.py` keeps it fresh automatically: it hashes all
generator sources via `compute_stdlib_source_fingerprint()`
(`sushi_lang/compiler/fingerprint.py`) against a per-platform marker and rebuilds
on mismatch, so editing a generator is picked up on the next `./sushic` invocation
with no manual step. `./sushic --build-stdlib` forces a rebuild regardless of the
fingerprint.

Only `darwin` and `linux` are built today (`dist/` currently has only `darwin/`
present locally). `src/_platform/` splits OS-specific declarations into
`posix/`, `darwin/`, `linux/`, dispatched via `get_platform_module()`; no
`windows/` yet (future work).

## 2. Bundled Sushi-source modules (`src_sushi/`)

A small second class of stdlib module ships as plain `.sushi` source, parsed and
compiled like user code instead of precompiled to bitcode. Two exist today:

- `collections/iter` (`src_sushi/collections/iter.sushi`) — generic combinators
  (`map`/`filter`/`fold`); nothing is emitted unless a program instantiates one.
- `encoding/msgpack` (`src_sushi/encoding/msgpack.sushi`) — a MessagePack
  decoder, and the first source module with CONCRETE public functions: an
  injected module is an ordinary compilation unit, so `public fn` exports work
  exactly as they do between user units.

Registration is two entries per module: `SOURCE_STDLIB_MODULES`
(`sushi_lang/semantics/stdlib_registry.py`) maps the import path to the bundled
file, and `_virtual_units` (`sushi_lang/backend/stdlib_linker.py`) tells the
linker the module has no `.bc`. Use this path for stdlib code that is generic
(an IR generator would hand-emit a monomorphization scheme the generic pipeline
does for free) or that is simply better written in Sushi than in llvmlite calls.

## Registry (`sushi_lang/semantics/stdlib_registry.py`)

`use <module_path>` becomes importable in one of two ways:

- **Compiled module**: add an entry to `StdlibRegistry.KNOWN_MODULES` mapping the
  Sushi module path (`"sys/env"`) to its Python import path. The target module
  must expose the naming-convention triple `is_builtin_<name>_function`,
  `get_builtin_<name>_function_return_type`, `validate_<name>_function_call` —
  discovery (`_discover_module`) `getattr`s these by name rather than reading an
  explicit function table, so a module that doesn't follow the convention is
  silently skipped.
- **Source module**: add an entry to `SOURCE_STDLIB_MODULES` pointing at the
  `.sushi` file.

Validators/type-resolvers throughout `semantics/` locate stdlib functions the
same `is_builtin_*_function`-style way (e.g. `math_module.is_builtin_math_function`
in `semantics/passes/types/visitor.py`) — grep for `is_builtin_.*_function` to
find all call sites before changing the convention.
