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
concrete type arguments, so a generic travels as **re-parsable Sushi source text** in
every kind. The compiler also compiles whole `.sushi` files that arrive as text:
`_inject_source_stdlib_units` (`compiler/pipeline.py`) parses every module that
`SOURCE_STDLIB_MODULES` (`semantics/stdlib_registry.py`) names -- `collections/iter`,
the `io/*` and `net/*` modules, `encoding/msgpack`, `toolchain/slib` and others -- and
puts them in the unit table as ordinary units. Source libraries use the same mechanism.

**Binary distribution stays available as an opt-in** (`--lib-kind binary`), and keeps
the per-declaration behaviour: concrete bodies as bitcode, generics as
source slices. §5 specifies it. A binary `.slib` is platform-bound and is rejected on
a foreign platform (CE3504); a source `.slib` is not.

Note that a binary `.slib` does **not** hide a library's generics. Their
source text is in the manifest, because that is the only way a generic can cross a
boundary at all.

### Why bitcode is not portable

LLVM bitcode looks target-neutral and is not. It carries a target triple and a data
layout, and the C ABI is already lowered into it: `sret`, `byval` and struct-by-value
rules differ between x86-64 SysV, AArch64 and Windows x64. Apple removed App Store
bitcode submission in Xcode 14 for the same reason. For our purposes bitcode is as
platform-bound as an object file.

## 2. The container (VERSION 5)

`sushi_lang/backend/library_format.py` defines `LibraryFormat`, a flat binary
container — no LLVM in it, no linking logic, just framing. The fixed header is 52 bytes.
A signature without `| E` is bare (`docs/design/error-channel.md`), and the bitcode of a
version-5 library follows that ABI. A file of any other version is refused (CE3509), so
no function is called with the wrong ABI:

```
MAGIC (16B) 🍣SUSHILIB🍣
VERSION       (u32 LE) = 5
FLAGS         (u32 LE)   bit 0 = source section compressed
KIND          (u32 LE)   1 = source, 2 = binary, 3 = hybrid
SPARE_3       (u64 LE)   zero
SPARE_4       (u64 LE)   zero
METADATA_LENGTH (u64 LE) | METADATA_BLOB (msgpack dict)
SOURCE_LENGTH   (u64 LE) | SOURCE_BLOB   (msgpack map: unit name -> source text)
BITCODE_LENGTH  (u64 LE) | BITCODE_BLOB  (LLVM bitcode)
```

`SOURCE_LENGTH` is zero when `KIND = binary`. `BITCODE_LENGTH` is zero when
`KIND = source`. A hybrid carries both.

Entry points: `write()`, `read()`, `read_metadata_only()`, `read_source_only()` so the
consumer can take the index and the source without touching the bitcode, and
`read_section_sizes()` so `--lib-info` can report how big each payload is without holding
either one.

Integrity is checked strictly in order, one code per failure mode, all in
`sushi_lang/internals/errors/library.py`:

- **CE3508** — bad magic (not a `.slib` at all)
- **CE3509** — version mismatch. The reader accepts `VERSION == 5` only. There is no
  backward-compat shim and none is needed: Sushi has no users in the wild, so an
  older `.slib` is rejected, not upgraded.
- **CE3510** / **CE3506** / **CE3511** — the metadata, source or bitcode section is
  truncated (`f.read(n)` returned fewer bytes than the length prefix promised). One
  code per section rather than one shared code: the text names which section is short,
  which is what tells a reader where the file was cut.
- **CE3512** — the metadata is not valid MessagePack, or it does not have the shape
  that `MANIFEST_SCHEMA` (`backend/library_format.py`) states: a field is missing or has
  the wrong type. A template source that does not parse is also CE3512, and it names the
  library file, not the consumer's `use` line
- **CE3513** — total file size exceeds the 1 GiB sanity limit
- **CE3515** — the operating system refuses to open or to read the file (a missing file,
  a directory, no read permission, an I/O failure)

`--lib-info` also refuses a file whose name does not end in `.slib`, before it opens the
file (**CE3516**).

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

`LibraryManifestGenerator.generate()` in `sushi_lang/backend/library_manifest.py`
builds a plain dict. `docs/library-format.md` carries the full schema.

**The rule: everything in the library must be knowable from the manifest alone.**
`--lib-info` must never parse source to answer what a library contains. For a source
library the index is *derived* from the units at build time; the source section is the
authority, and the index is a cache of it.

| Field | Notes |
|---|---|
| `sushi_lib_version` | `"2.3"`. A protocol string, unrelated to `VERSION` or to `templates.version` |
| `library_name` | from the output filename |
| `library_version` | see §6 |
| `requires_compiler` | see §6 |
| `kind` | `"source" / `"binary"` / `"hybrid"`, matching the `KIND` header field |
| `units` | ordered unit names present in the source section |
| `compiler_version` | informational: exactly which compiler built the file |
| `platform` | meaningful only when `kind != "source"` |
| `compiled_at` | ISO-8601 UTC |
| `public_functions`, `public_constants`, `structs`, `enums` | the index |
| `templates` | written for EVERY kind. It is redundant on the source path -- the generics are in the source section as well -- but it is what lets `--lib-info` list a source library's generic functions without parsing anything (§5) |
| `not_exported` | what the library declares and keeps: a name, its kind and the unit that keeps it, one record for each (unit, name), and nothing else (#1112). The complement of `templates.closure_summary`, and absent when a library keeps nothing (§5.5) |
| `reexports` | one record per `public use`: the target, the unit that wrote it, and which producer the target is. Absent when no unit re-exports |
| `dependencies` | one record per stdlib module the build uses (`kind: "stdlib"`) and per `use <lib/...>` of the library's own units, plain or public (`kind: "library"`, with the `library_name` and the `library_version` that the build found). The consumer's build loads every library of the graph from these records, and `--lib-info` lists them (§5.8, #1120). Each record names in `units` the library's own units that write the `use`: at the consumer, the namespace table of a unit of the library reads only the records that name it, so a template copy sees the imports of its own unit and not the imports of another unit (#1123) |

`structs` / `enums` / `public_functions` carry **only concrete, non-generic**
declarations. `_extract_public_functions` and `_extract_public_types` (structs and enums) both
explicitly `continue` past anything with `type_params`: a generic function is not a
concrete callable, so listing it here would hand the consumer a bogus `FuncSig` with
unresolved type parameters. `_extract_public_types` also reads the visibility marker:
only a `public struct` or a `public enum` ships in `structs` / `enums`. A private type
that a shipped generic body names travels as source in the export closure (§5.5).

## 4. The source path

This is the default. `--lib-kind source` is what `./sushic --lib` does unless the
author asks for something else.

### 4.1 Production

The source section is a msgpack map from unit name to the unit's complete source text.
Whole files, not slices — `slice_decl_source` and the template codec are not used on
this path.

Everything ships, private declarations included. There is no export closure to
compute, because there is nothing to leave out.

The index is generated from the same units, so nothing about `--lib-info` has to parse
source. The report states the kind, the library version, the compiler constraint and the
unit list, and it prints only the lines the kind can answer for: no `Platform` and no
`Bitcode` for a source library, no `Source` for a binary one.

### 4.2 Consumption

One shared unit injector in `compiler/pipeline.py` serves both the bundled source
stdlib and source libraries. It does the work: read text, `parse_to_ast`, build a `Unit`, and loop until no new `use`
appears.

Three rules make this sound:

- **Namespacing.** A library unit enters `unit_manager.units` as
  `lib/<library_name>/<unit>`, so it can never collide with a consumer unit name.
- **Privacy is the existing unit mechanism.** `semantics/units.py` gates
  exports on `func.is_public`. Library privates stay private with no new machinery.
- **The registry is skipped.** For `kind = "source"`, nothing in
  `semantics/library_registration.py` runs. A library unit is an ordinary unit, so the
  ordinary passes handle it.
- **Library units are COLLECTED first, and the order says why.** A consumer's
  `extend i32 with Render` is checked against the perks visible when its own unit is
  collected, so a perk the library declares has to be in the table already. The
  compilation order yields every unit after the units it depends on
  (`docs/design/unit-namespaces.md` section 6.2), and `build_dependency_graph` records
  the edge a `use <lib/...>` creates, so a source library's units come first without a
  hand-patch. Seeding them ahead of the loop the way `LibraryRegistration.seed_perks`
  does for a binary library would register the same perk twice (CE4001), because this
  one also arrives in a real unit.
- **A consumer definition SHADOWS a library one.** Same rule the binary path
  documents in §7, enforced for functions, generics and perk impls by
  `passes/collect/`. A shadowed PUBLIC function gives CW3002; a shadowed private one
  says nothing. Without it, `--lib-kind` would change program semantics instead of
  just distribution. A shadowed perk impl is also dropped from its unit's AST: both
  bodies are ordinary Sushi in ordinary units, so leaving it defines the method symbol
  twice. (A binary library needs no such step -- its body is `weak_odr` and the linker
  discards it.)
- **A monomorphized instance of a library generic goes home to the library unit.**
  `register_synthesized_function` stores it in the unit that declared the generic
  rather than the first unit, so a library generic calling a library-private helper is
  an intra-unit call. This is what replaces the export closure. The instance is folded
  into that unit's fingerprint (`SYNTHESIZED:`), because which instances a library unit
  carries depends on what the consumer asked for, and its own source hash cannot say
  so. The rule is not special to libraries -- see §4.6.

Three consequences follow, and they are the point of the design:

1. **No export closure.** A private helper is reachable because it is compiled, not
   because the producer predicted that an exported generic would need it.
2. **CE5007 cannot fire.** That code exists because export-closure private symbols
   share the consumer's namespace, so shadowing one would change what the library's
   own shipped bodies call. Namespaced units remove the shared namespace.
3. **CE3504 cannot fire.** Nothing in a source library is platform-bound.

`_check_library_platform` is conditional on `kind != "source"`. That single
condition is the whole cross-platform fix.

### 4.3 Caching

Library units are ordinary units, so `__sushi_cache__` caches one `.o` for each of
them, keyed as any other unit is. The first build pays; later builds do not.

`compute_lib_fingerprint` (`compiler/fingerprint.py`) invalidates every
consumer unit when a `.slib` changes. That is correct, and it rebuilds more than it
must: a consumer unit that does not use the library is rebuilt too.

### 4.4 Diagnostics from source the consumer did not write

This is the one real cost of the design, and it needs deliberate handling.

On the binary path, `LibraryRegistration` collects every template snippet against a
**throwaway `Reporter`**, so a snippet's diagnostics never leak into the consumer's own
compile. A snippet that does not parse stops the build with CE3512, which names the
library file. That trick cannot serve a source library. When the
whole library is source, an error inside it must be shown, and it must be
attributable.

A failure in a library unit renders as a **tier-3 relational diagnostic**: the error at
its real location inside the library unit, plus a `note` naming the library, its
version, and the consumer `use` statement that pulled it in. No new error code — the
existing ladder in `internals/report.py` covers it.

The user-visible contract is that a consumer must never see a bare error with no
explanation of why code they did not write is being compiled.

**The binary path meets the same contract**; §5.2 has the mechanism. A source library gets this for free because it arrives as a `Unit` with a
`provenance`, and every per-unit pass runs against `_unit_reporter(unit)`.

**A warning belongs to the author of the code**. The consumer reports an error
in library code, with the note above, because it cannot build without a fix. It does
not report a WARNING whose location is in code it did not write: a unit of a source
library, a bundled stdlib unit, or the instance of a binary library's template. Such a
warning does not change the exit status. This is the rule of Cargo, which compiles a
dependency with `--cap-lints allow`. The author sees the warning when the library is
built, because the library's own units carry no provenance in that build.

The decision is made at ONE place, from provenance, and not per warning code.
`Reporter._record` drops a warning when the reporter's `keeps_warnings` is false, or
when the body under report has an `Origin` with a provenance (a binary template).
`keeps_warnings` defaults to "the reporter has no provenance", and
`SemanticAnalyzer._unit_reporter` sets it from `_lint_checks`, the same predicate
that decides whether a lint pass reads a unit at all. The one exception it holds is the
test runner's stdlib gates: while `SUSHI_STDLIB_DOC_GATE` or `SUSHI_STDLIB_DEAD_GATE`
is set, the `docs` or the `unused` pass keeps the warnings of a bundled stdlib unit, so
the gates still see them. A source library stays silent under both gates.

**The limit: a generic template is checked only when code instantiates it.** A generic
function that no code calls is never checked, in a program and in a `--lib` build
alike. So the author of a library sees the lints of a template only when the library's
own code, or the author's own tests, instantiate it. There is NO author-side template
check at `--lib`; this is the position of C++ and Zig, where a template body is checked
at its instantiation. The consequence is that a template's warning reaches nobody when
the author never instantiates it: the consumer drops it by the rule above.

### 4.5 Why CE5007 does not fire on the source path

The binary path needs CE5007 because an export-closure private shares the consumer's
flat namespace, so a consumer symbol of the same name would silently change what the
library's own shipped bodies call. Namespaced units remove the shared namespace, and
the instance-goes-home rule above removes the need to ship privates at all, so the
clash cannot occur. `tests/libs/helpers/private_closure_lib.sushi` is therefore built
as a BINARY library by the test runner (`BINARY_ONLY_HELPERS`): the code it guards is
binary-path machinery, and `test_err_lib_private_clash` asserts a rule that only exists
there.

### 4.6 One rule for every unit

"A monomorphized instance belongs to the unit that declared the generic" applies to
every unit in the build: a source-library unit, a unit of the bundled source stdlib and
an ordinary unit of a multi-unit program. `register_synthesized_function`
(`semantics/generics/synthesis.py`) appends the instance to that unit's AST, and the
backend gives it that unit's symbol prefix. A monomorphized extension copy follows the
same rule (`ufcs-combinators.md`, "The home unit of a copy"). The one exception is a
BINARY library's template: its units exist only at the producer, so its instance lands
in the entry unit with no unit identity. The instance keeps the library unit as
`scope_unit` (#1120): the names of its body resolve in the scope of that unit, never in
the scope of the entry unit (`docs/design/unit-namespaces.md` section 8.1).

### 4.7 What the source path does not solve

A source library is portable as **text**. It is not automatically portable in
**behaviour**.

FFI is the case that matters. `stat` differs between macOS and Linux, and the `open`
flags differ. FFI stays a private unit detail (CE5002, CE5008 keep `ptr` out of every
public signature), so the FFI travels inside the library and must compile on the
consumer's platform. Sushi has no conditional compilation today: no `cfg`, no build
tags, no per-platform source files.

So an FFI-heavy library is still platform-specific, and cannot yet say so. That gap is
a language feature (conditional compilation), and it is the next thing that blocks
genuinely portable FFI-heavy libraries. It is out of scope here.

## 5. The binary path (opt-in)

This section applies when `kind` is `binary` or `hybrid`.

A binary `.slib` is **bitcode plus a manifest**. Concrete symbols (plain functions,
concrete structs/enums, concrete perk-impl method bodies) are already machine code in
the bitcode — the consumer only declares them and lets the linker resolve the call.
Generic symbols cannot be pre-compiled, so they travel as re-parsable source text and
are instantiated by the ordinary `instantiate`/`monomorphize` machinery at the
consumer.

That distinction — concrete ships as machine code and links, generic ships as source
and monomorphizes locally — is the whole binary system. The export closure, perk-impl
shipping, the two link paths and CE5007 are all consequences of making it sound: a
generic template's body can reference things the consumer has never seen (a private
helper, a private constant, a perk the library itself implements), and those
references have to resolve to *the library's* symbols, not to a same-named consumer
symbol, without the consumer writing any glue.

**A compiled library declares its own units and nothing else**. A
`use <io/fs>` injects the bundled module as an ordinary compilation unit, and a
`use <lib/other>` over a source library injects its units the same way, so both reach
the manifest generator beside the library's own files. `own_units` filters them out of
every index -- one predicate, `Unit.provenance`, the field that also answers "did
the author write this unit" for a `public use` record. So a consumer that imports
`<io/fs>` for itself does not read a second definition of each name.

The bitcode is the other half, and the answer there is different: a compiled library is
**self-contained**, so the module's compiled code stays in it and a consumer that names
nothing of the module still links. That copy is a second definition for a consumer that
does import the module. It is weakened rather than dropped -- `weak_odr` on every
definition a foreign unit contributed and on every symbol a stdlib `.bc` brought in
(`backend/library_linkage.py`), which is the same answer the perk-impl seam
takes. `ld` then keeps one copy and a consumer's own
strong definition wins. The monolithic consumer path needs no weakening, because
`TwoPhaseLinker` resolves a duplicate by symbol source; the per-unit incremental path
hands `ld` both objects and has no such rule.

**Every kind of library re-exports** (`docs/design/unit-namespaces.md` section 8.1,
rule 3). `public use X` makes X's public names the unit's
own. A source library ships the statement as text and the consumer re-parses it; a
compiled library ships one `reexports` record per statement -- the target, and the unit
that wrote it -- and the consumer's `_binary_unit_provider` composes the unit's namespace
from its own records plus the providers of what those name, exactly as `_unit_provider`
does for a source unit. `LibraryMetadata.reexports` is the one reader of the key.

Two consequences beyond the namespace. The `units` index, not the record lists, is what
answers "which unit does this import name": a façade unit whose every public name is
re-exported declares nothing of its own, so no `public_functions` record can name it. And
a re-exported STDLIB module has to reach the consumer's build at all -- a source
library's `use <io/fs>` does that by being text in the build, and a compiled one has only
the record, so `_compiled_stdlib_modules` reads it and both the source-module injection
and the bitcode link line take it. A re-exported LIBRARY follows the same rule (#1106):
`_resolve_library_imports` loads it as if the consumer wrote the import -- from a source
library's re-parsed statement and from a compiled library's `kind: "library"` record --
and the link line takes it (`reexported_libraries`). A library that is not on the path is
CE3502 with a note that names the `public use`.

The `templates` section carries its own `"version"` (`TEMPLATES_SCHEMA_VERSION`,
`8`, in `backend/library_format.py`), which is independent of the container version.
The section holds:

- generic function templates (source slices)
- generic struct/enum templates
- concrete perk-impl records
- the export closure: private-symbol records and `closure_summary`
- every closure record keyed by its unit, and a source-shipped template's `bindings`
- every public perk, and generic-target perk implementations as templates.
   `extend Box@(T) with Show` names no instantiation, so it cannot be a concrete
   record: it ships as source in `generic_perk_impls`, the consumer files it through
   the collect pass's own perk collector under the producer's unit, and
   `_monomorphize_generic_perk_impls` cuts one copy per instantiation of `Box` the
   consumer names -- the library's own (`make_box` answers `Box@(i32)`) and the
   consumer's own (`Box@(string)`) alike. The library's monomorphized copies stay OUT
   of `perk_impls`: a copy's source slice is the template's. And a public function's
   signature is read for instantiations too -- `fn make_box(i32 v) Box@(i32)` reaches
   the consumer as a manifest record no unit walk sees, so the consumer interns
   `Box<i32>` from that record.
- the signature and the receiver mode of every perk method record.
- `has_channel` (required) on every function, helper and method record. A signature
   without `| E` is bare, and there is no default error type, so the flag states the
   channel. A binary or hybrid library with a different schema version is refused
   (CE3512); a source library recompiles from its units and reads none of this.

### 5.1 Concrete functions

A non-generic `public fn` becomes a `public_functions` record: `name`, `params`
(`name`+`type` strings), `return_type`, `has_channel` (a bool, required) and, when the
declaration spells `| E`, `error_type` (all strings — types are serialized via `str(ty)` and re-parsed with
`parse_type_string` at the consumer, not pickled). The consumer reads the record in ONE
place, `LibraryRegistry._parse_functions`, and the typecheck pass and the backend derive
the call's `Result` from that signature through one function, `signature_result_arms`:
the spelled channel is the Err arm, an explicit `Result@(T, E)` return is not wrapped
again, and a signature with neither is bare: the call yields the value. Two checks gate the
record, both raising and aborting the `.slib` write (no partial artifact):

- **CE0116** — a native `...T` variadic function cannot appear here. The registry
  gives the reason as a serialization gap: the variadic flag is not written into the
  library format. (A type-pack `...Ts` function is unaffected — it carries
  `type_params` and is filtered into `generic_functions` before this check is reached;
  the discriminator is `is_pack` vs `is_variadic`, not the shared `...` spelling.)
  This check does not apply on the source path, where the declaration ships whole.
- **CE5002** — a public function whose signature (param or return, recursively
  including inside `Result`/`Maybe`) exposes a foreign `ptr` cannot appear in a public
  API at all. FFI is a private unit detail (`contains_foreign_ptr`, shared with the
  in-program unit-boundary check that raises CE5008).

At the consumer, `LibraryRegistry._parse_functions` turns each record back into a
`FuncSig` (`sushi_lang/semantics/library_registry.py`), and codegen
(`_declare_library_functions` in `codegen_llvm.py`) emits an `external`
LLVM declaration with no body — the definition resolves at link time from the library
bitcode.

### 5.2 Generic functions, structs, enums

Ships as **re-parsable source**, not a typed-AST codec and not IR — the locked design
in `sushi_lang/semantics/library_templates.py`. The rationale in the module docstring:
a typed-AST codec has to solve spans, cross-references, and cycles; re-parsing
sidesteps all three by reusing the frontend that already exists.

`slice_decl_source(node, source_text)` is the crux: `node.loc` is a **line-based**
`Span` from `propagate_positions=True` (not a char offset), whose `line` is the
declaration keyword's line and whose `end_line` overshoots into the blank-line gap
before the next top-level declaration (or one past EOF for the last one). The slice
is `[loc.line, loc.end_line)`, trailing-blank-stripped, newline-terminated —
self-contained enough to re-parse standalone.

Record shape (`generic_functions` / `generic_structs` / `generic_enums`, same schema):
`name`, `type_params` (`[{name, constraints, is_pack}]` — authoritative; reconciled
onto the re-parsed node after parsing, since the record is the source of truth against
future drift), `source`, `free_perks` (sorted perk names referenced by the bounds).

At the consumer, `LibraryRegistration._register_generic_functions` /
`_register_generic_types` (`semantics/library_registration.py`) re-parse each record's
`source` through `parse_to_ast`, run ONE **throwaway** `CollectorPass`, shared by every
snippet of the analysis, against a throwaway `Reporter` (so
a template snippet can never leak a diagnostic into the consumer's own compile; a
snippet that does not parse is CE3512 against the library file), pull the resulting
`GenericFuncDef`/generic type out, and register it into the consumer's own generic
table under its original name — indistinguishable, from that point on, from a generic
the consumer wrote itself. **Local definitions win silently**: a template is
registered only if its name is not already present.

**A diagnostic raised in a transplanted template body belongs to the library**.
The throwaway reporter above covers the collect pass only. What the consumer's per-unit
passes check is the MONOMORPHIZED INSTANCE, which is a `FuncDef` in one of the
consumer's own unit ASTs (`register_synthesized_function` puts a binary library's
template instance in the entry unit, because the library's units do not exist at the
consumer -- §4.6). Its spans came from
parsing the SLICE, where the declaration is on line 1, so rendering them against the
consumer's file would name a line the consumer never wrote: an error would land on a blank
line and a caret could mark unrelated consumer text.

`report.Origin` is the answer, and it carries exactly three things -- what to call the
body, what text the caret marks, and why it is being compiled here:

| field | value |
|---|---|
| `filename` | `<template:<library>:<name>>`, the same shape the throwaway reporter uses |
| `source` | the record's `source` slice, so the caret marks the template's own line |
| `provenance` | `'<library>' <version> ships this template; it is monomorphized here because of \`use <lib/<library>>\`` |

It is set on the `GenericFuncDef` beside `is_library_template` and copied onto every
instance and onto every lambda lifted out of one -- the two travel together, and the
mark answers *who may be called* while the origin answers *how a failure reads*. The
per-function entry of each per-unit pass (`scope`, `typecheck`, `borrow`) stamps
`Reporter.origin`, and `Reporter._record` -- the one place every diagnostic passes
through -- applies it. A diagnostic that names a file of its own keeps it.

`Diagnostic.source` carries the slice text rather than a source map, so it survives the
per-unit reporters being merged into the top-level one, and no lookup can go stale.

Structs are registered before enums (an enum variant payload may reference a struct).
Generic *function* registration happens before generic struct/enum registration, and
all of it happens before the instantiate pass, so the consumer's own `Box@(i32)` usages
are discovered and monomorphized in the normal pass.

### 5.3 Type-pack (`...Ts`) functions

No special case at all: a pack function carries `type_params` (with `is_pack=True` on
the pack parameter) exactly like any other generic function, so it is collected into
`generic_functions` and monomorphized per (arity, type-tuple) at the consumer's call
site through the same path as §5.2. `tests/libs/helpers/format_lib.sushi` +
`tests/libs/consumer_generics/test_lib_pack.sushi` exercise this: the library ships `perk Render` and
`show_all@(...Ts: Render)`; the consumer supplies `Render` impls for `i32`/`string`
and calls with zero and two arguments.

### 5.4 Concrete perk implementations

A library's own `extend <ConcreteType> with <Perk>:` block, for a perk the library
ships or a predefined perk (`Drop`, `Hashable`, `Eq`, `Ord`, `Display`), ships as a
`perk_impls` record: `type`, `perk`,
`source` (the whole `extend` block, for signatures), `methods: [{name, symbol}]`. The
method symbol name is computed by `impl_method_symbol()` — mirrors
`backend/functions/helpers.py:get_extension_method_name`'s mangling (`<` → `__`, `>`
dropped, `", "` → `_`) — deliberately duplicated into the manifest rather than
re-derived at the consumer, so producer and consumer never drift.

The **bodies are not re-emitted**. In the library's own bitcode, every perk-impl
method gets `weak_odr` linkage (`_set_weak_odr_on_perk_impls` in `backend/driver.py`,
called from `LLVMDriver.compile_to_bitcode` right after the module is generated) — `weak_odr`, not
`linkonce_odr`, specifically because it must **survive** LLVM's optimizer even though
nothing inside the library module itself calls it (an unreferenced `linkonce_odr`
definition can be dropped as dead code; `weak_odr` cannot). At the consumer,
`LibraryRegistration._register_perk_impls` rebuilds an `ExtendWithDef` via `deserialize_perk_impl`
and registers it in the perk-impl table for constraint checking (CE4006) and dispatch;
codegen's `_declare_library_perk_impl_methods` declares (never defines) the method —
the definition resolves from the library object/bitcode at link time.

Precedence, all silent (mirrors every other library-registration helper):

1. A consumer's own impl of the same `(type, perk)` wins outright — checked first.
2. Across multiple libraries shipping the same impl, the first one registered wins.
3. If a local **extension method** on the target type already uses one of the impl's
   method names, the library impl is skipped entirely — registering it would recreate
   the exact dispatch ambiguity **CE4007** exists to prevent, but *erroring* here would
   make adding an impl to a library a breaking change for every consumer that happens
   to have a same-named extension method. If the consumer genuinely needs the perk, it
   writes its own `extend`, which raises the normal in-program CE4007 with a real span.

A `Drop` record is what makes a library handle own its resource at the consumer
(#1118): the registered implementation puts the type in the consumer's Drop set, so
`owns_resource` answers True (the type moves, and a use after a `nom` is CE2405), and
scope exit calls the library's compiled `drop()`, linked from the bitcode. A generic
`extend Sink@(T) with Drop` ships in `generic_perk_impls`, and the consumer cuts its own
copy, as for any template. The orphan rule holds across the boundary: a consumer `Drop`
on a type that a binary library declares, concrete or generic, is CE4012. The collect
pass cannot see that, because a public library type has a visibility record with no
declaring unit, so `LibraryRegistration._reject_foreign_drops` reads the rule after the
library's types and implementations are registered. Rule 1 of the precedence list thus
does not apply to `Drop`.

A perk-impl record that fails to re-parse is not fatal to the consumer's build: it is
skipped with **CW3506** (a warning, not an error) — "methods it provides will be
unavailable unless the consumer supplies its own."

Only perk *implementations* ship this way; perk *definitions* (the method-signature
contract) ship separately and unconditionally for every perk named in an exported
generic's constraints, via `LibraryRegistration.seed_perks`, which runs **before** the consumer's
own units are collected (perk-impl collection validates against CE4003, so a consumer
implementing a library-shipped perk needs the contract present at collection time, not
after).

### 5.5 The export closure of private dependencies

An exported generic whose body calls a library-private helper ships that helper with it.
`LibraryManifestGenerator._compute_export_closure()` walks every exported generic's
body (`_scan_referenced_symbols` for call/name/constructor references,
`_scan_referenced_type_names` for `UnknownType`/`GenericTypeRef` names in field/variant
positions — both deliberately **over-collect**, since a false positive here only means
over-*shipping* a symbol, never a spurious rejection) as a **visited-set worklist** over
the unit's private symbol tables, so recursive and mutually-recursive private helpers
terminate (`tests/libs/helpers/private_closure_lib.sushi`'s `countdown` function exists
specifically to prove this).

Three private kinds ship, each a different way:

- **private concrete function** → a `templates.private_functions` signature record
  (name/params/return_type, no source) — its body is already in the bitcode; codegen
  promotes it from `internal` to `external` linkage via the `exported_private_functions`
  set threaded into `compile_to_bitcode` (computed *before* bitcode compilation, since
  the closure walk itself can raise CE5006 and there is no point compiling bitcode for
  a library that is about to fail to export).
- **private generic function** → rides the *same* `generic_functions` list as public
  generics (§5.2), flagged `"private": True`.
- **constant** → ships with its `source` (re-parsable), because the consumer needs the
  compile-time *value*, not a link-time symbol. `LibraryRegistration._register_constants` re-parses
  it, appends the reconstructed `ConstDef` onto the first consumer unit's AST (constant
  globals get internal linkage per module, so appending a duplicate-content const to a
  different module never collides).
- **concrete struct/enum types**: a PUBLIC type already ships in `structs`/`enums`
  (§3), so the closure does not ship it again and only recurses through it for further
  transitive references. A PRIVATE type that a shipped body names ships in the closure
  as source.

Only two reference shapes abort the export with **CE5006**, attributed to the
exported generic at the root of the dependency chain (not the private helper itself —
the generic is what the consumer sees fail): a reference into an `unsafe external`
namespace (foreign bindings cannot be re-declared at a consumer that never saw the
`unsafe external` block), and a private helper whose signature exposes a foreign `ptr`
(same rationale as CE5002).

`closure_summary` (`{private_functions: [name...], private_generic_functions:
[name...], constants: [name...]}`, sorted) is not consumed by any code path — it exists
purely for observability (inspectable via `--lib-info` / direct manifest reads) so a
library author can see what their public API is quietly dragging along.

**The closure is for the library's own bodies, and the CALL SITE is what says so**.
A shipped private has to be resolvable at the consumer, because a monomorphized
template body lands there and still calls what it called at home; consumer code that
names the same symbol is `CE3005`. The two are told apart by whose body the call is in,
never by the symbol: `FuncDef.is_library_template` marks an instance of a `.slib`
template (stamped in `register_synthesized_function`, and carried onto a lambda lifted
out of such a body), the typecheck pass reads it as `in_library_body`, and the gate in
`passes/types/visibility.py` exempts that and nothing else. An instance of the
*consumer's* generic is synthesized the same way and is not exempt — it is the user's
code. The same gate covers a private constant: `const` is private by default, and a
shipped body that reads a private constant reads it through the exemption.

**A private the closure does not ship is named, not hidden**. The walk starts at
the public *generics*, so a private that only a concrete public function calls -- or one
nothing public calls -- ships nowhere and reaches the consumer's tables not at all. The
consumer must still answer `CE3005` for it, as the source path does, and not
`CE2008: undefined function`. The manifest's `not_exported` key gives that answer: `_extract_not_exported` lists the name and the kind of every
private of the library's OWN units that the closure did not ship, `LibraryRegistry` reads it
into `SymbolTables.library_not_exported`, and the `CE2008` site in
`passes/types/calls/user_defined.py` asks it before it emits, routing the answer through the
one CE3005 gate with the library in place of a unit.

The two lists are one piece of bookkeeping: a private is named in the closure, WITH a
signature, or in `not_exported`, with nothing but its kind. Which is why a `not_exported`
name is registered in no function table -- there is no signature to register, the callee
stays unresolved so the borrow pass judges no argument against an invented mode, and
`CE5007` must not fire for a symbol that ships nowhere and so can clash with nothing.

**None of this machinery exists on the source path** (§4.2), which is the largest
structural difference between the two. Both paths print the same `CE3005`: a source
library's units are ordinary units at the consumer, so its private resolves and the same
gate refuses it, naming the injected unit where the binary path names the library.

### 5.6 Who frees an argument at the boundary

A library's public functions are called from code the library never saw, so the boundary
has to carry the **parameter mode** — the answer to "who frees this argument?" (the spec
is `docs/design/borrow-model.md`). Each parameter record in the manifest is

```
{"name": "path", "type": "string", "mode": "borrow"}
```

and `mode` is one of `borrow`, `nom`, `peek`, `poke`. The consumer restores it when it
re-registers the signature (`semantics/library_registry.py`), so a call across the
boundary obeys the same rule as a call within one unit: unmarked borrows, `nom`
transfers, and the marker is required at the call site if and only if it is declared.

**The mode is its own field, not part of the type string,** and both halves of that
matter:

- **`nom` cannot be spelled in a type at all.** It is a property of the parameter, not
  of the value, so there is nowhere in `"string"` to put it.
- **`peek` / `poke` can be spelled in a type**, as `"peek string"`, because
  `str(ReferenceType)` produces it. `parse_type_string` has a reference arm, so the
  consumer reads the spelling back as a reference type. A parser with no such arm reads
  `UnknownType("peek string")`: a type that names nothing, with the mode silently gone.

`tests/unit/test_lib_param_modes.py` is the gate: it builds a library declaring one
function per mode, asserts what the manifest records, asserts what the consumer reads
back, and asserts that an unsupported container version is rejected with CE3509 rather
than guessed at.

On the source path this question does not arise at the manifest level: the declaration
ships whole, so the mode is in the text and the ordinary passes read it.

A `nom self` receiver is a receiver mode like `peek self` and `poke self`: a method
record carries it in `self_mode`.

**Not at the boundary:** a consuming variadic — a public v1 `...T` cannot ship in a binary
library (CE0116, §5.1).

### 5.7 Two link paths

A consumer resolves a library's *call sites* the same way regardless of build mode, but
the **bitcode itself** is merged into the final binary through one of two genuinely
different mechanisms, chosen by `_compile_monolithic` vs `_compile_incremental` in
`sushi_lang/compiler/pipeline.py`:

**Monolithic** (single-unit builds, or any build with `--no-incremental`): the
consumer's IR and every library's bitcode (parsed straight from the `.slib`, via
`llvm.parse_bitcode`) are fed into `TwoPhaseLinker` (`backend/module_linker.py`) as
in-memory LLVM modules, tagged `SymbolSource.MAIN` / `LIBRARY` / `STDLIB`. It computes
a transitive-closure reachable set from `main` (plus any `@llvm.global_ctors` entries)
across all modules, and `SymbolResolver._choose_definition`
(`backend/symbol_resolver.py`) picks a winner for every multiply-defined symbol by a
**fixed priority order**: `MAIN > LIBRARY > STDLIB > RUNTIME`. This is where "local
wins" is implemented for the monolithic path — at the level of one merged IR module,
before any object file exists.

**Incremental** (the default for multi-unit builds — per-unit `.o` caching in
`__sushi_cache__/`): each library used in the build is compiled to its **own** native
object file once (`compile_library_to_object` — parse bitcode, optimize, emit object,
cached by `compute_lib_fingerprint(slib_path)`), and that `.o` is handed alongside
every other unit's `.o` to a plain `cc` invocation (`link_object_files`). There is no
LLVM-level merge and no `SymbolResolver` on this path — "local wins" instead falls out
of the **system linker's own weak-symbol semantics**: a library perk-impl method
carries `weak_odr` in its own object file (§5.4), so a consumer's ordinary (strong)
definition of the same symbol is what the platform linker picks, with no Sushi-side
logic involved at all. This is why a perk-impl body uses `weak_odr` and not
`linkonce_odr` — `linkonce_odr` symbols never even survive as
*declarations* other object files can override; the point of `weak_odr` is to be a
retained-but-overridable definition that a plain `cc` link resolves correctly.

A `.slib`'s fingerprint also feeds `compute_unit_fingerprint` for every *consumer* unit
(`library_fingerprints` in `_compile_incremental`), so a rebuilt library invalidates
the cache of any consumer unit that depends on it, even though the unit's own source
did not change. This applies to both paths.

**A source library uses neither mechanism.** Its units are compiled and linked exactly
as the consumer's own units are.

### 5.8 What does NOT cross a binary boundary

- **Generic-target perk impls** (`extend Box@(T) with SomePerk:`) do not ship as
  CONCRETE perk impls: the concrete list skips them
  (`isinstance(impl.target_type, GenericTypeRef)` in `library_manifest.py`). They ship as
  TEMPLATES instead (`_generic_perk_impl_templates`), and the
  consumer instantiates them like any other template.
- **v1 native `...T` variadics** as public functions — CE0116, §5.1.
- **The NAMES of a plain `use` of another library** — if library A's source does a plain
  `use <lib/b>`, the consumer's build loads B (A's `dependencies` record says so), and a
  consumer of A still needs its own `use <lib/b>` to write a name of B. A `public use
  <lib/b>` hands B's names on (§5). Loading is transitive, visibility is not (#1120); see
  `docs/libraries.md`, Library Dependencies.
- **The imports of one unit, to another unit of the same library** — a copy of a
  template resolves its body in the scope of the unit that declares the template. That
  scope holds the `dependencies` records whose `units` name the unit, and no other
  record, so a binary library has the per-unit scope of a source library (#1123).

### 5.9 Extension methods

A binary or hybrid library exports its extension methods, instance and static. Two
records carry them, both in `templates`, and every kind writes both.

- **`extensions`** — one record for each CONCRETE extension method: the target `type`,
  the signature, `self_mode` and `static` when the declaration writes them, the `unit`,
  and the `link_symbol` that the bitcode defines (`extension_symbol` over
  `extension_receiver_name`, the one rule that the backend uses to declare the method).
  The body is in the bitcode, so the record has no source. At the consumer,
  `LibraryRegistration._register_extensions` reads the signature through the one
  signature reader (`library_registry.parse_signature`), files an `ExtensionMethod` in the
  extension table, and gives the backend an `ExtendDef` that it declares and never
  defines (`library_extensions` in `codegen_llvm.py`).
- **`generic_extensions`** — one record for each extension TEMPLATE: a generic target
  (`Box@(T)`), a concrete-instance target (`Box@(i32)`), an array target (`T[]`) and a
  method type parameter (`pick@(U)`). The record is the same, plus `type_params` and the
  `source` of the declaration. The template joins the export closure (§5.5), so its body
  can call a private function of the library through `bindings`. At the consumer,
  `_register_generic_extensions` re-parses the source and files it through the collect
  pass's own `FunctionCollector`. The analyzer cuts each copy, and the copy goes to the
  entry unit, because the template's unit is not a unit of the build (§4.6). The copy
  carries `is_library_template`, so its body may call the library's privates, and
  `scope_unit`, so its names resolve in the scope of the library unit (#1120). A
  generic perk implementation of a library is the same: its methods carry both marks.

One method name on one type from two libraries is CE0101 for every kind, with a note that
names each library (`_reject_library_extension_clash`). The two bodies are two
definitions of one symbol, and before #1120 the second record was skipped with no
diagnostic, so which body ran depended on the build mode.

The methods of an implementation of a perk that does NOT ship (a private perk that no
export names) travel as `extensions` records. The perk hides its contract, and its
methods stay callable (`docs/design/visibility.md`). An implementation of a predefined
perk never travels this way.

An extension on a private type that nothing ships is left out: no consumer can name the
type, and no shipped body names it.

A consumer extension on a binary library's CONCRETE type is filed again after the
library's types register (`_refile_consumer_extensions`), because the collect pass ran
before they were in the tables. A method name that the consumer and the library both
declare on one type is CE0101, the answer that a source library gives.

## 6. Versions and compatibility

Two version fields, with two different jobs.

### 6.1 `library_version`

The library's own version, `major.minor.patch`.

Source of the value, in order:

1. `[package] version` from the `nori.toml` in the current working directory, when one
   exists. The compiler does not look in a parent directory
2. otherwise the explicit `--lib-version X.Y.Z` flag

Neither present is **CE3505** at build time. A `nori.toml` that exists must be valid: a
file that nori's manifest reader refuses stops the build with **CE3517**, and a file
that cannot be opened or read stops it with **CE3518**. This keeps the packager as the source of
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

A mismatch is a **hard error, CE3503**, raised in the library load loop in
`compiler/pipeline.py` beside the platform check. The text names the library, its
`requires_compiler`, and the running compiler.

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

## 7. Conflict and safety rules — summary

Rules marked **binary** apply only when `kind != "source"`.

| Situation | Rule | Why |
|---|---|---|
| Consumer FUNCTION name == public library function | local wins, and **CW3002** says so, for every library kind | the two are separate symbols, because each carries the unit that declared it: the consumer's call binds to its own and the library's body to its own. Legal, and rarely intended, so it warns. Both public is legal too and warns the same way. A binary or hybrid library warns from its manifest, in the `libraries` step, for a public function and a public template alike (#1103). `docs/design/visibility.md` §9.1 |
| Consumer TYPE name == public library type (**source**) | **CE0004** / **CE2046**, hard error | type identity is nominal, so one name is one shape; the consumer cannot have its own. The library's units are compilation units here, so the collect pass answers at the declaration and a note points at the library's |
| Consumer TYPE name == public library type (**binary**) | **CE3011**, hard error | the same rule, and the same answer a source library's PRIVATE type gets, for the same reason: the consumer cannot SEE the declaration that holds the name, so the head line names the library instead of pointing a note at a file the consumer does not have. The `libraries` step refuses it, where the manifest meets the collected tables. The consumer keeps its own type in its tables |
| Consumer TYPE name == library-PRIVATE type or private generic template (**source** and **binary**) | **CE3011**, hard error, and the analysis stops after it | type identity is nominal, so one name is one shape even where the consumer cannot see the library's declaration. Renaming is the only move, and `docs/design/type-identity.md` phase 2 is what would lift it |
| Consumer FUNCTION name == library-PRIVATE function (**source**) | legal, and silent | each declaration carries the unit that declared it, so the two coexist and each body calls its own (`docs/design/unit-namespaces.md` sections 6 and 9). A GENERIC function is the same shape, and a CONSTANT takes the same answer; a library's PUBLIC constant stays **CE0105**, because the consumer can see and read that name |
| Consumer name == **export-closure private** symbol (**binary**) | **CE5007**, hard error | the library's own monomorphized bodies call that private symbol by name; silently shadowing it would change what the library's shipped code does. The source path needs no twin for a function, because a source library's units are compiled and each keeps its own scope. The closure ships ONE RECORD PER UNIT -- two library units may each ship a private `helper` -- and each source-shipped template carries a `bindings` map from every free name its body resolved to the producer's link symbol, so a template can never bind to another unit's body |
| Consumer perk-impl `(type, perk)` == library-shipped perk-impl (**binary**) | local wins, silent, both semantically and at link time (§5.4, §5.7) | a consumer providing its own impl is expected, not an error |
| Consumer extension method name == library perk-impl method name (**binary**) | library impl skipped entirely (no error) | avoids recreating CE4007 as a breaking change on every library update |
| Consumer extension method == library extension method on one target (**binary**) | **CE0101**, hard error, with a note that names the library (§5.9) | the two bodies are one symbol, and a source library gives the same answer |
| Exported generic references an `unsafe external` namespace | **CE5006** | FFI bindings cannot be re-declared at a consumer that never saw the block. Fires on EVERY kind: `_extract_templates` runs the export-closure walk before the kind branch in `compiler/pipeline.py`. Arguably wrong on the source path, where the `unsafe external` block ships inside its own unit — see §9 |
| Exported generic (transitively) references a `ptr`-exposing private signature | **CE5006** | same rationale as CE5002 — `ptr` is unit-confined. Every kind, as above |
| Public function/private-closure helper exposes `ptr` in its own signature | **CE5002** | `ptr` cannot appear in any public library signature |
| Public `fn` (non-library, ordinary unit boundary) exposes `ptr` | **CE5008** | the general unit-boundary form of the same rule; CE5002 is its library-specific sibling |
| A template snippet fails to re-parse (**binary**) | perk-impl: **CW3506** (skip); generic fn/type/constant: **CE3512**, and the build stops | never let a malformed shipped snippet crash or pollute the consumer's own build, and never report it at the consumer's `use` line |
| A **source** library unit fails to compile | the real diagnostic, plus a `note` naming the library and the `use` that pulled it in (§4.4) | the consumer must never see a bare error about code they did not write |
| Public native `...T` variadic function | **CE0116** | the variadic flag is not serialized into the library format. Fires on EVERY kind: the check lives in `_extract_public_functions`, which builds the index for every kind — see §9 |
| Platform mismatch (**binary**) | **CE3504** | bitcode is platform-specific; caught early with a clear message instead of an incomprehensible late `cc`/LLVM failure. Never raised for a source library |
| An `unsafe external "C"` link-name == a symbol this build defines | **CE5013** | one module, so a declaration and a definition of one name unify: the declaration would enter the program's own body with its own signature, unchecked, and could reach a library-PRIVATE body from consumer code. Every kind: the rule reads the func table, the constant table and the library registry, so it runs after the `libraries` step. A GENERATED stdlib symbol is in none of those, so the stdlib build writes a manifest of what it defines beside its bitcode, and a reserved set in `semantics/externs_manifest.py` covers the ones the backend emits inline |
| Container version is not 5 | **CE3509** | no backward-compat shim; there are no users in the wild |
| `requires_compiler` not satisfied | **CE3503** | see §6.2; the escape is `--ignore-compiler-version` |
| No `library_version` available at build time | **CE3505** | see §6.1 |
| Call site's `nom` marker disagrees with the shipped signature | **CE2427** | the same rule as within a unit: a consume is visible at both ends or at neither (§5.6) |

## 8. Reading the code: file → responsibility

| File | Responsibility |
|---|---|
| `sushi_lang/backend/library_format.py` | The `.slib` byte container: magic/version/flags/kind/length framing, msgpack (de)serialization. No LLVM, no linking, no path resolution. |
| `sushi_lang/backend/library_manifest.py` | `LibraryManifestGenerator` — the **producer**. Builds every manifest section; `_extract_templates` / `_compute_export_closure` are the binary path's export-closure walk. |
| `sushi_lang/backend/library_errors.py` | `LibraryError(SushiError)` — the one exception type every `.slib` read/resolve/link failure raises, rendered through the normal reporter. |
| `sushi_lang/backend/library_paths.py` | `LibraryResolver` — filesystem discovery only (`SUSHI_LIB_PATH`, project deps, Nori bento, cwd) and manifest caching (`loaded_libraries`). Deliberately *not* a linker. |
| `semver` (under `sushi_lang/internals/`) | `Version` + constraint matching. Shared by the library-load path and the Nori resolver (§6.3). |
| `sushi_lang/compiler/pipeline.py` | Library resolution and the gates: `_check_library_platform` (binary only), `_check_library_compiler_version`, and the shared source-unit injector that both the bundled source stdlib and source libraries use. Also chooses monolithic vs incremental and drives per-unit caching. |
| `sushi_lang/semantics/library_registry.py` | `LibraryRegistry` — **binary path only**. Pre-parses a raw manifest dict into typed `FuncSig`/`StructType`/`EnumType` objects once, shared by the semantic analyzer and codegen. |
| `sushi_lang/semantics/library_templates.py` | **Binary path only.** The re-parse-based codec: `serialize_generic_function/struct/enum`, `serialize_perk`, `serialize_/deserialize_perk_impl`, `serialize_extension`, `serialize_generic_/deserialize_extension` (the consumer re-parses every other record through `LibraryRegistration._collect_snippet`), `slice_decl_source` (the line-span slicing algorithm), `impl_method_symbol` (perk-impl symbol mangling, kept in lockstep with `backend/functions/helpers.py`). |
| `sushi_lang/semantics/library_registration.py` | **Binary path only.** Consumer-side registration, one `LibraryRegistration` per analysis: `seed_perks` (ahead of the collect loop), `register` (`_register_types` for structs and enums, `_register_functions`, `_register_private_functions`, `_register_not_exported`, `_register_constants`, `_register_private_types`, `_register_perk_impls`, `_register_generic_perk_impls`, `_reject_foreign_drops`, `_register_extensions`, `_register_generic_extensions`, `_register_generic_functions`, `_register_generic_types`), and the two readers `signatures` and `kept_constant_names`. Every re-parsed record goes through the one `_collect_snippet`. This is where CE5007 fires and where local-wins is implemented for every category except perk impls. `semantics/semantic_analyzer.py` only decides WHEN the step runs. |
| `sushi_lang/backend/driver.py` | `LLVMDriver.compile_to_bitcode` (producer: sets `weak_odr` on perk impls, promotes export-closure private fns to `external`), `compile_multi_unit` (drives `TwoPhaseLinker` when libraries are present), `compile_library_to_object` (incremental path: one `.o` per library). |
| `sushi_lang/backend/codegen_llvm.py` | `_declare_library_functions`, `_declare_library_perk_impl_methods`, `library_extensions` (consumer: declares, never defines, library symbols). |
| `sushi_lang/backend/module_linker.py` | `TwoPhaseLinker` — the monolithic-path in-memory IR merge (reachability + priority-ordered symbol resolution). Not library-specific — the main module and stdlib bitcode go through it too. |
| `sushi_lang/backend/symbol_resolver.py` | `SymbolResolver._choose_definition` — the `MAIN > LIBRARY > STDLIB > RUNTIME` priority table used only by `TwoPhaseLinker`. |
| `sushi_lang/compiler/fingerprint.py` | `compute_lib_fingerprint` — content hash of a `.slib`, used to cache its compiled object and to invalidate consumers. |
| `sushi_lang/internals/errors/library.py` | CE35xx: the container/link/version family. |
| `sushi_lang/internals/errors/ffi.py` | CE50xx: CE5002/CE5006/CE5007/CE5008 — the export-safety family, in the "ffi" module because `ptr` confinement is one reason a symbol cannot ship. |

## 9. Producer checks on every kind

The manifest producer runs for every kind, so some of its checks reach a
`--lib-kind source` build too.

**`templates` is written for every kind.** `generate()` always builds the section, and
`_extract_templates` always runs. On the source path it is redundant, because the same
generics are in the source section as whole units. It is also the reason `--lib-info` can
list a source library's generic functions at all, so it earns its place; §3 says so.

**CE5006 and CE0116 fire on the source path.** Both checks live in the manifest producer --
CE5006 in the export-closure walk `_extract_templates` drives, CE0116 in
`_extract_public_functions` -- and the producer builds the index for every kind. So a source
library that exports a generic touching an `unsafe external` namespace is rejected, and so is
one that exports a public `...T` variadic.

For CE0116 that is right for a reason that has nothing to do with kind: the index cannot
describe a native variadic signature, and the index ships in every kind.

For CE5006 it is questionable. The rule exists because a binary library ships a *slice* of
its source and the consumer never sees the `unsafe external` block. A source library ships
the whole unit, block included, so the consumer can compile the call. Whether to make the
CE5006 walk conditional on `kind != "source"` is an open question, not a decided one: it
would let an FFI-touching generic export, and §4.7 is the reason to be careful -- the library
would compile at a consumer on the author's platform and fail on any other, which is exactly
the failure conditional compilation is supposed to describe. Leaving CE5006 as it is keeps
the honest answer ("this cannot be portable yet") until there is a way to say so.

**A rejected library build writes nothing.** The three rejection sites in
`sushi_lang/backend/library_manifest.py` emit their diagnostic into the reporter and
return, and control flow belongs to `pipeline.py`, which gates on `reporter.has_errors`
the way it does before codegen. There are two gates, and the placement is the contract:
the first sits after the export closure and BEFORE the bitcode compilation, so a CE5006
rejection costs nothing; the second sits after `generate()`, which extracts the public API
first and returns without writing once the reporter holds an error. A rejected build
therefore leaves no `.slib` behind, prints no success line and prints one diagnostic per
fault. `_reject` inside the export-closure walk returns, and each caller returns
explicitly after it.

And the producer's **CE5002 is unreachable from a CLI build**: the typecheck pass's public-fn
`ptr` fence (`passes/types/signatures.py`, CE5008) tests the identical condition and exits
earlier. The site is kept as the backstop for a direct producer call, so a missing CE5002
is not a regression.

## Rejected alternatives

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

**Binary libraries restricted to concrete functions only.** Would have deleted
`library_templates.py`, the export closure, CE5007 and the `weak_odr` perk-impl path.
Rejected: binary libraries would lose generics entirely, which is too high a price for
an internal simplification.
