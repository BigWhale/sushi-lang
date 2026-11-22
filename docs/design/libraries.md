# Design: `.slib` Libraries — source-first distribution

**Status: BUILT.** This document describes the code that runs today.

Written for a compiler contributor. The user-facing guide is
[`docs/libraries.md`](../libraries.md), and the on-disk byte format is specified in
[`docs/library-format.md`](../library-format.md).

## 1. Purpose and mental model

**A `.slib` is Sushi source plus an index.** The consumer compiles that source as
ordinary compilation units and caches the object files. One artifact works on every
platform, because text has no target triple.

This follows every other AOT-compiled language. Rust, Go and Zig all ship source and
build at the consumer. Apple ships a bundle of per-platform binary slices
(XCFramework). Only bytecode ecosystems — the JVM, .NET, WASM — have truly portable
binary libraries, and they win by not shipping machine code at all. No AOT-compiled
language ships a portable binary library, because none exists.

A generic cannot be pre-compiled, because monomorphization needs the consumer's
concrete type arguments. The compiler also compiles whole `.sushi` files that arrive as
text: `_inject_source_stdlib_units` (`compiler/pipeline.py`) parses every module that
`SOURCE_STDLIB_MODULES` (`semantics/stdlib_registry.py`) names -- `collections/iter`,
the `io/*` and `net/*` modules, `encoding/msgpack`, `toolchain/slib` and others -- and
puts them in the unit table as ordinary units. Source libraries use the same mechanism.

**There is one kind, `source`.** Two other kinds existed, and both are retired:

- **`hybrid`** carried the whole source AND the bitcode. It came from #428 as the third
  value of `KIND`, and it was never given a purpose. The consumer read it through the
  binary path, so the source section was never read. Its one possible value was a faster
  build on a matching host, and the per-user stdlib front-end cache gives that value to
  every source library. The header value 3 is retired.
- **`binary`** carried the bodies of the concrete functions as LLVM bitcode, and the
  generics as source slices. It is removed with no backward compatibility (2026-11-22).
  The reason is in [Rejected alternatives](#rejected-alternatives).

`./sushic --lib` builds a source library, and there is no flag to select a kind. A
manifest that states `binary` or `hybrid` (from an older compiler) is refused with
[CE3512](../error-catalog.md#ce3512). Build such a library again with this compiler.

### Why bitcode is not portable

LLVM bitcode looks target-neutral and is not. It carries a target triple and a data
layout, and the C ABI is already lowered into it: `sret`, `byval` and struct-by-value
rules differ between x86-64 SysV, AArch64 and Windows x64. Apple removed App Store
bitcode submission in Xcode 14 for the same reason. For our purposes bitcode is as
platform-bound as an object file.

## 2. The container (VERSION 5)

`sushi_lang/backend/library_format.py` defines `LibraryFormat`, a flat binary
container — no LLVM in it, no linking logic, just framing. The fixed header is 52 bytes.
A file of any other version is refused ([CE3509](../error-catalog.md#ce3509)):

```
MAGIC (16B) 🍣SUSHILIB🍣
VERSION       (u32 LE) = 5
FLAGS         (u32 LE)   bit 0 = source section compressed
KIND          (u32 LE)   1 = source (2, the binary kind, and 3, the hybrid kind, are retired)
SPARE_3       (u64 LE)   zero
SPARE_4       (u64 LE)   zero
METADATA_LENGTH (u64 LE) | METADATA_BLOB (msgpack dict)
SOURCE_LENGTH   (u64 LE) | SOURCE_BLOB   (msgpack map: unit name -> source text)
BITCODE_LENGTH  (u64 LE) | BITCODE_BLOB  (always empty)
```

The bitcode section is always empty: `BITCODE_LENGTH` is zero. The header `KIND` mirrors
the manifest `kind`; no reader reads it.

Entry points: `write()`, `read()`, `read_metadata_only()`, `read_source_only()` so the
consumer can take the index and the source, and `read_section_sizes()` so `--lib-info`
can report how big each section is without holding it.

Integrity is checked strictly in order, one code per failure mode, all in
`sushi_lang/internals/errors/library.py`:

- **[CE3508](../error-catalog.md#ce3508)** — bad magic (not a `.slib` at all)
- **[CE3509](../error-catalog.md#ce3509)** — version mismatch. The reader accepts `VERSION == 5` only. There is no
  backward-compat shim and none is needed: Sushi has no users in the wild, so an
  older `.slib` is rejected, not upgraded.
- **[CE3510](../error-catalog.md#ce3510)** / **[CE3506](../error-catalog.md#ce3506)** / **[CE3511](../error-catalog.md#ce3511)** — the metadata, source or bitcode section is
  truncated (`f.read(n)` returned fewer bytes than the length prefix promised). One
  code per section rather than one shared code: the text names which section is short,
  which is what tells a reader where the file was cut.
- **[CE3512](../error-catalog.md#ce3512)** — the metadata is not valid MessagePack, or it does not have the shape
  that `MANIFEST_SCHEMA` (`backend/library_format.py`) states: a field is missing or has
  the wrong type, or the manifest states a kind that this compiler does not read
  (`binary`, `hybrid`).
- **[CE3513](../error-catalog.md#ce3513)** — total file size exceeds the 1 GiB sanity limit
- **[CE3515](../error-catalog.md#ce3515)** — the operating system refuses to open or to read the file (a missing file,
  a directory, no read permission, an I/O failure)

`--lib-info` also refuses a file whose name does not end in `.slib`, before it opens the
file (**[CE3516](../error-catalog.md#ce3516)**).

### Compression

`FLAGS` bit 0 is reserved for source-section compression and is **always written as
zero** today. **Compression is planned** (ruled 2026-08-25). The self-hosted reader
(`sushi_stdlib/src_sushi/toolchain/slib.sushi` plus `encoding/msgpack`) needs an
inflate written in Sushi, and `compression/zlib.sushi` is in the stdlib. For the source
section there is a reason to wait: Nori archives are `tar.gz` (`packager/archive.py`),
so distribution is compressed regardless.

The reason to compress is the **metadata blob**, not the source section. The index is
where doc text lives (`docs/design/documentation.md` section 8, R8), and a library must
not carry thinner documentation to keep the index small.

So the blob is uncompressed today and is not meant to stay that way. Whoever takes it
owns three things: the flag, a read side in both readers, and a rule for when an index
is still cheap to take.

## 3. The manifest — an index, not the authority

`LibraryManifestGenerator.generate()` in `sushi_lang/compiler/library_manifest.py`
builds a plain dict. `docs/library-format.md` carries the full schema.

**The rule: everything in the library must be knowable from the manifest alone.**
`--lib-info` must never parse source to answer what a library contains. The index is
*derived* from the units at build time; the source section is the authority, and the
index is a cache of it.

| Field | Notes |
|---|---|
| `sushi_lib_version` | `"2.10"`. A protocol string, unrelated to `VERSION` or to `templates.version` |
| `library_name` | from the output filename; see §6.4 |
| `library_version` | see §6 |
| `requires_compiler` | see §6 |
| `kind` | `"source"`, matching the `KIND` header field. Optional: a manifest with no `kind` is a source library. Any other value is [CE3512](../error-catalog.md#ce3512) in every reader; `library_kind` (`backend/library_format.py`) and its Sushi twin (`toolchain/slib.sushi`) are the one answer |
| `units` | ordered unit names present in the source section |
| `compiler_version` | exactly which compiler built the file. Informational: the consumer compiles the source, so only `requires_compiler` is checked |
| `platform` | the host key `<os>_<arch>` (`darwin_arm64`), from `host_key()`. Informational: a source library is not platform-bound |
| `public_functions`, `public_constants`, `public_variables`, `structs`, `enums` | the index |
| `templates` | the generic functions, structs and enums, the perks, the perk implementations and the extension methods, as re-parsable source slices (`semantics/library_templates.py`). It carries its own `version` (`TEMPLATES_SCHEMA_VERSION`, `11`). It is redundant -- the generics are in the source section as well -- but it is what lets `--lib-info` list a library's generic functions without parsing anything. The consumer does not read it |
| `conversions` | one record per public conversion `extend A as B:` (`source`, `target`, `unit`). Absent when the library declares no conversion |
| `reexports` | one record per `public use`: the target, the unit that wrote it, and which producer the target is. Absent when no unit re-exports |
| `dependencies` | one record per stdlib module the build uses (`kind: "stdlib"`) and per `use <lib/...>` of the library's own units, plain or public (`kind: "library"`, with the `library_name` and the `library_version` that the build found). `--lib-info` lists them (#1120). Each record names in `units` the library's own units that write the `use` (#1123) |

**A library indexes its own units and nothing else.** A `use <io/fs>` injects the
bundled module as an ordinary compilation unit, and a `use <lib/other>` over a source
library injects its units the same way, so both reach the manifest generator beside the
library's own files. `own_units` filters them out of every index -- one predicate,
`Unit.provenance`, the field that also answers "did the author write this unit" for a
`public use` record.

`structs` / `enums` / `public_functions` carry **only concrete, non-generic**
declarations. `_extract_public_functions` and `_extract_public_types` (structs and enums) both
explicitly `continue` past anything with `type_params`: a generic function is not a
concrete callable, so it goes to `templates`. `_extract_public_types` also reads the
visibility marker: only a `public struct` or a `public enum` is in `structs` / `enums`.
A public native `...T` variadic function is in the index, and the record marks the
parameter `variadic` (protocol 2.10), so `--lib-info` prints it as written.

## 4. The source path

### 4.1 Production

The source section is a msgpack map from unit name to the unit's complete source text.
Whole files, not slices.

Everything ships, private declarations included. There is no closure of private
dependencies to compute, because there is nothing to leave out.

The index is generated from the same units, so nothing about `--lib-info` has to parse
source. The report states the library version, the compiler constraint, the protocol,
the sections and the unit list.

### 4.2 Consumption

One shared unit injector in `compiler/pipeline.py` serves both the bundled source
stdlib and source libraries. It does the work: read text, `parse_to_ast`, build a `Unit`, and loop until no new `use`
appears.

These rules make this sound:

- **Namespacing.** A library unit enters `unit_manager.units` as
  `lib/<library_name>/<unit>`, so it can never collide with a consumer unit name.
- **Privacy is the existing unit mechanism.** `semantics/units.py` gates
  exports on `func.is_public`. Library privates stay private with no new machinery.
- **Nothing special registers a library.** A library unit is an ordinary unit, so the
  ordinary passes handle it.
- **Library units are COLLECTED first, and the order says why.** A consumer's
  `extend i32 with Render` is checked against the perks visible when its own unit is
  collected, so a perk the library declares has to be in the table already. The
  compilation order yields every unit after the units it depends on
  (`docs/design/unit-namespaces.md` section 6.2), and `build_dependency_graph` records
  the edge a `use <lib/...>` creates, so a source library's units come first without a
  hand-patch.
- **A consumer definition SHADOWS a library one.** §7 states the rule, and
  `passes/collect/` enforces it for functions, generics and perk impls. A shadowed
  PUBLIC function gives [CW3002](../error-catalog.md#cw3002); a shadowed private one
  says nothing. A shadowed perk impl is also dropped from its unit's AST: both
  bodies are ordinary Sushi in ordinary units, so leaving it defines the method symbol
  twice.
- **A library re-exports** (`docs/design/unit-namespaces.md` section 8.1, rule 3).
  `public use X` makes X's public names the unit's own. The statement is text in the
  library unit, and the consumer parses it as any other. A re-exported library is loaded
  as if the consumer wrote the import (#1106). A library that is not on the path is
  [CE3502](../error-catalog.md#ce3502) with a note that names the `public use`.
- **A monomorphized instance of a library generic goes home to the library unit.**
  `register_synthesized_function` stores it in the unit that declared the generic
  rather than the first unit, so a library generic calling a library-private helper is
  an intra-unit call. The instance is folded into that unit's fingerprint
  (`SYNTHESIZED:`), because which instances a library unit carries depends on what the
  consumer asked for, and its own source hash cannot say so. The rule is not special to
  libraries -- see §4.5.

Two consequences follow, and they are the point of the design:

1. **No closure of private dependencies.** A private helper is reachable because it is
   compiled, not because the producer predicted that an exported generic would need it.
2. **No platform check.** Nothing in a source library is platform-bound. The consumer
   compiles the text for its own target.

### 4.3 Caching

Library units are ordinary units, so `__sushi_cache__` caches one `.o` for each of
them, keyed as any other unit is. The first build pays; later builds do not.

A `.slib` is reproducible: two builds of one source give one file, byte for byte. The
manifest holds no field that changes from build to build (no timestamp). So a library
that is built again with no change rebuilds nothing at the consumer.

The key of a library unit is the key of any other unit, plus the digest of the bytes of
its `.slib` (`compute_lib_fingerprint`, `compiler/fingerprint.py`) and the label
`lib/<name>/<unit>.sushi` (not the path of the materialized file). A change of the
library rebuilds the units of the library and what depends on their interfaces. A
library unit that holds no template copy is also REUSED: a later build skips its body
passes and takes its object and the record of its body from `__sushi_cache__/reuse/`
(`semantics/unit_reuse.py`, as for a bundled stdlib unit, #1343). `--lib` builds do not
reuse.

### 4.4 Diagnostics from source the consumer did not write

This is the one real cost of the design, and it needs deliberate handling. An error
inside a library must be shown, and it must be attributable.

A failure in a library unit renders as a **tier-3 relational diagnostic**: the error at
its real location inside the library unit, plus a `note` naming the library, its
version, and the consumer `use` statement that pulled it in. No new error code — the
existing ladder in `internals/report.py` covers it. A library unit arrives as a `Unit`
with a `provenance`, and every per-unit pass runs against `_unit_reporter(unit)`.

The user-visible contract is that a consumer must never see a bare error with no
explanation of why code they did not write is being compiled.

**A warning belongs to the author of the code**. The consumer reports an error
in library code, with the note above, because it cannot build without a fix. It does
not report a WARNING whose location is in code it did not write: a unit of a source
library or a bundled stdlib unit. Such a warning does not change the exit status. This
is the rule of Cargo, which compiles a dependency with `--cap-lints allow`. The author
sees the warning when the library is built, because the library's own units carry no
provenance in that build.

The decision is made at ONE place, from provenance, and not per warning code.
`Reporter._record` drops a warning when the reporter's `keeps_warnings` is false.
`keeps_warnings` defaults to "the reporter has no provenance", and
`SemanticAnalyzer._unit_reporter` sets it from `_lint_checks`, the same predicate
that decides whether a lint pass reads a unit at all. The one exception it holds is the
test runner's stdlib gates: while `SUSHI_STDLIB_DOC_GATE` or `SUSHI_STDLIB_DEAD_GATE`
is set, the `docs` or the `unused` pass keeps the warnings of a bundled stdlib unit, so
the gates still see them. A source library stays silent under both gates.

**A generic template is checked at the author's build.** The `typecheck` pass checks
every template one time, where it is written, with each type parameter opaque (#1070,
`docs/design/checked-generics.md`). This includes a template that no code calls, and it
holds in a `--lib` build as in a program. So the author of a library sees
the errors and the lints of each template (for example [CW1001](../error-catalog.md#cw1001) and an operation that
no constraint promises) in the library's own build. This is the position of Rust, Swift
and Go; before #1070 it was the position of C++ and Zig, where a template body is checked
at its instantiation, and a template's warning reached nobody when the author never
instantiated it.

The consumer does not check the template of a consumed library unit again: the author's
build did it. The rule above stays: the consumer drops a warning whose location is in
library code. A fault that only an instance can show (such as E3 for the type
argument of an opaque `E`) is still reported at the consumer, at the site that names the
instance, with a note at the template. A library that an older compiler built did not
get the author-side check. The compiler-version check refuses it at a later minor version
([CE3503](../error-catalog.md#ce3503)). With `--ignore-compiler-version`, its templates are not checked at the
consumer, and each copy takes the full check of a copy, as before #1070: a template fault
shows in the copy for the type argument that has it, with the note that names the library,
and a copy for a type argument with no fault compiles.

### 4.5 One rule for every unit

"A monomorphized instance belongs to the unit that declared the generic" applies to
every unit in the build: a source-library unit, a unit of the bundled source stdlib and
an ordinary unit of a multi-unit program. `register_synthesized_function`
(`semantics/generics/synthesis.py`) appends the instance to that unit's AST, and the
backend gives it that unit's symbol prefix. A monomorphized extension copy follows the
same rule (`ufcs-combinators.md`, "The home unit of a copy").

### 4.6 What the source path does not solve

A source library is portable as **text**. It is not automatically portable in
**behaviour**.

FFI is the case that matters. `stat` differs between macOS and Linux, and the `open`
flags differ. FFI stays a private unit detail ([CE5002](../error-catalog.md#ce5002), [CE5008](../error-catalog.md#ce5008) keep `ptr` out of every
public signature), so the FFI travels inside the library and must compile on the
consumer's platform. Sushi has no conditional compilation today: no `cfg`, no build
tags, no per-platform source files.

So an FFI-heavy library is still platform-specific, and cannot yet say so. That gap is
a language feature (conditional compilation), and it is the next thing that blocks
genuinely portable FFI-heavy libraries. It is out of scope here.

## 5. The binary path (retired)

This section described the binary kind. The kind is removed (2026-11-22), and the
section number stays so that the numbers of §6 to §8 do not change. The reason for the
removal is in [Rejected alternatives](#rejected-alternatives).

## 6. Versions and compatibility

Two version fields, with two different jobs.

### 6.1 `library_version`

The library's own version, `major.minor.patch`.

Source of the value, in order:

1. `[package] version` from the `nori.toml` in the current working directory, when one
   exists. The compiler does not look in a parent directory
2. otherwise the explicit `--lib-version X.Y.Z` flag

Neither present is **[CE3505](../error-catalog.md#ce3505)** at build time. A `nori.toml` that exists must be valid: a
file that nori's manifest reader refuses stops the build with **[CE3517](../error-catalog.md#ce3517)**, and a file
that cannot be opened or read stops it with **[CE3518](../error-catalog.md#ce3518)**. This keeps the packager as the source of
truth for a real package, without forcing a manifest on a bare `./sushic --lib` build.

### 6.2 `requires_compiler`

A source library is compiled by *the consumer's* compiler, not the author's. So a
library that built cleanly under 0.11 can fail under 0.12. This is the standard cost of
source distribution, and Rust lives with the same problem. It is not fixable; it is
declarable.

`requires_compiler` holds a constraint string. The default written at build time is
`~<major>.<minor>` of the building compiler — so a compiler at 0.11.1 writes `~0.11`,
which accepts every `0.11.z` and rejects `0.12.0`.

**Pre-1.0 semver makes the minor the breaking unit**, which matches how Sushi's 0.x
releases already behave.

A mismatch is a **hard error, [CE3503](../error-catalog.md#ce3503)**, raised in the library load loop in
`compiler/pipeline.py` (`_check_library_compiler_version`). The text names the library,
its `requires_compiler`, and the running compiler.

The escape is `--ignore-compiler-version`, for an author testing a library forward
against a new compiler. It is deliberately not a per-library setting: it is a
build-wide, obviously-temporary override.

A warning was considered and rejected. A real incompatibility that is only warned about
surfaces later as a confusing error deep inside library source the consumer did not
write — exactly the diagnostic problem §4.4 exists to avoid.

### 6.3 Version comparison

The `semver` module under `sushi_lang/internals/`: `Version` (parse, compare, order) and a constraint
matcher covering exact, `~X.Y`, caret and comparator ranges.

It lives in `internals` rather than `packager` because the compiler needs it on the
library-load path, and `internals` is the shared bottom layer. The Nori resolver reuses
this module rather than growing a second implementation. Both packages are in the blocking mypy set
(`MYPY_PKGS` in `.githooks/pre-push`), so the module must be mypy-clean.

### 6.4 The library name is the name of the `use`

**The rule.** A build stamps `library_name`, the stem of its output file. A
`use <lib/...>` names a library with the last part of its path, and the search finds a
file of that name (`LibraryResolver.resolve_library`). The consumer refuses a file whose
stamped name is not the name of the `use` that loaded it:
**[CE3521](../error-catalog.md#ce3521)**. The check is `_check_library_name`
(`compiler/pipeline.py`), in the library load loop beside the compiler-version check. It
runs on each load of the graph: the consumer's own `use`, and a `use` or a `public use`
of a source library. It runs before the loop looks the stamped name up, so a copy beside
its original is CE3521 and not [CE3519](../error-catalog.md#ce3519). The message names
the two names and the file, and a note points at the `use`. `--lib-info` reads a file
with no `use`, so it does not check the name.

**The reason.** The stamped name identifies a library in the graph, and it gives the
names of the units (`lib/<library>/<unit>`). A copied or renamed file loads under a name
that is not its own: the build then uses a library that no `use` names, and two files with
one stamped name meet the one-version rule as two versions of one library, with text that
names neither file's `use`. A warning was not enough: the load is wrong, not only unusual.

## 7. Conflict and safety rules — summary

| Situation | Rule | Why |
|---|---|---|
| Consumer FUNCTION name == public library function | local wins, and **[CW3002](../error-catalog.md#cw3002)** says so | the two are separate symbols, because each carries the unit that declared it: the consumer's call binds to its own and the library's body to its own. Legal, and rarely intended, so it warns. Both public is legal too and warns the same way. `docs/design/visibility.md` §9.1 |
| Consumer TYPE name == public library type | **[CE0004](../error-catalog.md#ce0004)** / **[CE2046](../error-catalog.md#ce2046)**, hard error | type identity is nominal, so one name is one shape; the consumer cannot have its own. The library's units are compilation units here, so the collect pass answers at the declaration and a note points at the library's |
| Consumer TYPE name == library-PRIVATE type or private generic template | **[CE3011](../error-catalog.md#ce3011)**, hard error, and the analysis stops after it | type identity is nominal, so one name is one shape even where the consumer cannot see the library's declaration. Renaming is the only move, and `docs/design/type-identity.md` phase 2 is what would lift it |
| Consumer FUNCTION name == library-PRIVATE function | legal, and silent | each declaration carries the unit that declared it, so the two coexist and each body calls its own (`docs/design/unit-namespaces.md` sections 6 and 9). A GENERIC function is the same shape, and a CONSTANT takes the same answer; a library's PUBLIC constant stays **[CE0105](../error-catalog.md#ce0105)**, because the consumer can see and read that name |
| Consumer extension method == library extension method on one target | **[CE0101](../error-catalog.md#ce0101)**, hard error | the two bodies are one symbol |
| A library unit fails to compile | the real diagnostic, plus a `note` naming the library and the `use` that pulled it in (§4.4) | the consumer must never see a bare error about code they did not write |
| Public function exposes `ptr` in its own signature | **[CE5002](../error-catalog.md#ce5002)** | `ptr` cannot appear in any public library signature |
| Public `fn` (non-library, ordinary unit boundary) exposes `ptr` | **[CE5008](../error-catalog.md#ce5008)** | the general unit-boundary form of the same rule; [CE5002](../error-catalog.md#ce5002) is its library-specific sibling |
| An `unsafe external "C"` link-name == a symbol this build defines | **[CE5013](../error-catalog.md#ce5013)** | one module, so a declaration and a definition of one name unify: the declaration would enter the program's own body with its own signature, unchecked, and could reach a library-PRIVATE body from consumer code. The rule reads the func table and the constant table. A GENERATED stdlib symbol is in neither, so the stdlib build writes a manifest of what it defines beside its bitcode, and a reserved set in `semantics/externs_manifest.py` covers the ones the backend emits inline |
| Container version is not 5 | **[CE3509](../error-catalog.md#ce3509)** | no backward-compat shim; there are no users in the wild |
| The manifest states a kind other than `source` | **[CE3512](../error-catalog.md#ce3512)** | the binary and the hybrid kinds are retired (§1). Build the library again |
| `requires_compiler` not satisfied | **[CE3503](../error-catalog.md#ce3503)** | see §6.2; the escape is `--ignore-compiler-version` |
| The `library_name` of the file is not the name of the `use` that loaded it | **[CE3521](../error-catalog.md#ce3521)** | see §6.4; a copied or renamed file hides which library a build uses. Build the library again under the name of the `use`, or write the `use` with the library's own name |
| Two versions of one library in one build | **[CE3519](../error-catalog.md#ce3519)** | one library has one version in a build |
| No `library_version` available at build time | **[CE3505](../error-catalog.md#ce3505)** | see §6.1 |

## 8. Reading the code: file → responsibility

| File | Responsibility |
|---|---|
| `sushi_lang/backend/library_format.py` | The `.slib` byte container: magic/version/flags/kind/length framing, msgpack (de)serialization, `MANIFEST_SCHEMA`, `library_kind`. No LLVM, no linking, no path resolution. |
| `sushi_lang/compiler/library_manifest.py` | `LibraryManifestGenerator` — the **producer**. Builds every manifest section, and refuses a public `ptr` signature ([CE5002](../error-catalog.md#ce5002)). |
| `sushi_lang/semantics/library_templates.py` | The source slices of the `templates` index: `serialize_generic_function/struct/enum`, `serialize_perk`, `serialize_perk_impl`, `serialize_extension`, `slice_decl_source` (the line-span slicing algorithm), and `impl_method_symbol` (perk-impl symbol mangling, kept in lockstep with `extension_decl_symbol`). |
| `sushi_lang/backend/library_errors.py` | `LibraryError(SushiError)` — the one exception type every `.slib` read/resolve failure raises, rendered through the normal reporter. |
| `sushi_lang/backend/library_paths.py` | `LibraryResolver` — filesystem discovery only (`SUSHI_LIB_PATH`, project deps, Nori bento, cwd) and manifest caching (`loaded_libraries`). Deliberately *not* a linker. |
| `semver` (under `sushi_lang/internals/`) | `Version` + constraint matching. Shared by the library-load path and the Nori resolver (§6.3). |
| `sushi_lang/compiler/pipeline.py` | Library resolution and the gates: `_check_library_name`, `_check_library_compiler_version`, and the shared source-unit injector that both the bundled source stdlib and source libraries use. |
| `sushi_lang/compiler/fingerprint.py` | `compute_lib_fingerprint` — content hash of a `.slib`, folded into the key of each of its units (§4.3). |
| `sushi_lang/internals/errors/library.py` | CE35xx: the container/version family. |
| `sushi_lang/internals/errors/ffi.py` | CE50xx: [CE5002](../error-catalog.md#ce5002)/[CE5008](../error-catalog.md#ce5008) — `ptr` confinement at a public signature. |

## Rejected alternatives

**The binary kind.** Removed on 2026-11-22, with no backward
compatibility (David's ruling). A binary `.slib` carried the bodies of the concrete
functions as LLVM bitcode, and the generics as source slices. The kind cost much and gave
little:

- About 3,500 lines of compiler code: a second front end that built the tables from the
  records of the manifest, and a second link path for the bitcode.
- Its own rules: CE5007 (a consumer name that clashes with a private symbol the library
  ships), the export closure of private dependencies with its error CE5006, and CE0116 (a
  public native variadic function that the binary path could not describe). These codes
  are deleted.
- A cache fault: a change to a binary library rebuilt every unit of the consumer.
- A bundled copy of each stdlib module that the library imported. A fix in the stdlib did
  not reach the library, and a compiler stamp had to refuse it.
- About 195 test fixtures.

It hid only the bodies of the concrete functions. The generics, the perks and the
signatures were source text in the manifest, and the bodies were LLVM bitcode, which
`llvm-dis` makes readable again. So the binary kind did not protect the code of an
author. Its one other value was a faster build, and unit reuse (#1343) gives a source
library the same build speed. A binary form can come back later, from a clean design.

**Fat `.slib` (per-platform binary slices).** Add a slice table to the reserved fields:
N × (target triple, bitcode). This is the XCFramework model and it works. It was
rejected because the container change is the easy part and everything before it is not:
Sushi has no `--target` flag (the triple always comes from `llvm.get_default_triple()`),
the stdlib builds for the host only, and linking shells out to the host `cc`.
Cross-compilation is the real cost. It also makes every publisher own the platform
matrix by hand.

**Server-side per-platform builds in Omakase.** The repository builds and caches
binaries keyed by (package, version, target, compiler version), so publishers ship
source and consumers download binaries — the Homebrew bottle model. Strictly better
than a fat file if binary delivery is ever wanted, and it needs no format change at
all. Parked, not rejected: it is a repository feature, not a language feature.

**Binary libraries restricted to concrete functions only.** Would have deleted the
template codec, the export closure, CE5007 and the weak perk-impl path.
Rejected at the time: binary libraries would lose generics entirely. The whole binary
kind is now removed (see above).
