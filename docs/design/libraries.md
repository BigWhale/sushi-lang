# Design: `.slib` Libraries — source-first distribution

**Status: BUILT.** Every phase has landed; this document describes code that runs today.

| Phase | Content | State |
|---|---|---|
| 1 | This document | BUILT |
| 2 | Container VERSION 4, manifest fields, a `semver` module under `sushi_lang/internals/`, CE3503/CE3505/CE3506 | BUILT |
| 3 | `--lib-kind source` writes the source section; the platform gate becomes kind-conditional | BUILT |
| 4 | The consumer compiles library source as ordinary units; `--lib-kind` defaults to `source` | BUILT |
| 5 | `slib-info` and the `sushic` fallback report the v4 fields | BUILT |
| 6 | User-facing docs | BUILT |

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

Sushi had already crossed half of this line before the decision was made. A generic
cannot be pre-compiled, because monomorphization needs the consumer's concrete type
arguments, so generics have always travelled as **re-parsable Sushi source text**.
The compiler also already compiles whole `.sushi` files that arrive as text:
`_inject_source_stdlib_units` (`compiler/pipeline.py`) parses `collections/iter`,
`encoding/msgpack` and `toolchain/slib` and puts them in the unit table as ordinary
units. Source libraries generalize a mechanism that already works.

**Binary distribution stays available as an opt-in** (`--lib-kind binary`), and keeps
today's per-declaration behaviour exactly: concrete bodies as bitcode, generics as
source slices. §5 specifies it. A binary `.slib` is platform-bound and is rejected on
a foreign platform (CE3504); a source `.slib` is not.

Note that a binary `.slib` does **not** hide a library's generics. It never has. Their
source text is in the manifest, because that is the only way a generic can cross a
boundary at all.

### Why the bitcode was never portable

LLVM bitcode looks target-neutral and is not. It carries a target triple and a data
layout, and the C ABI is already lowered into it: `sret`, `byval` and struct-by-value
rules differ between x86-64 SysV, AArch64 and Windows x64. Apple removed App Store
bitcode submission in Xcode 14 for the same reason. For our purposes bitcode is as
platform-bound as an object file.

## 2. The container (VERSION 4)

`sushi_lang/backend/library_format.py` defines `LibraryFormat`, a flat binary
container — no LLVM in it, no linking logic, just framing. Version 4 claims two of the
reserved fields, so the fixed 52-byte header does not change size:

```
MAGIC (16B) 🍣SUSHILIB🍣
VERSION       (u32 LE) = 4
FLAGS         (u32 LE)   was SPARE_1; bit 0 = source section compressed
KIND          (u32 LE)   was SPARE_2; 1 = source, 2 = binary, 3 = hybrid
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
- **CE3509** — version mismatch. The reader accepts `VERSION == 4` only. There is no
  backward-compat shim and none is needed: Sushi has no users in the wild, so an
  older `.slib` is rejected, not upgraded.
- **CE3510** / **CE3506** / **CE3511** — the metadata, source or bitcode section is
  truncated (`f.read(n)` returned fewer bytes than the length prefix promised). One
  code per section rather than one shared code: the text names which section is short,
  which is what tells a reader where the file was cut.
- **CE3512** — a blob is present but is not valid MessagePack
- **CE3513** — total file size exceeds the 1 GiB sanity limit

### Compression

`FLAGS` bit 0 is reserved for source-section compression and is **always written as
zero** today. **Compression is now planned** (ruled 2026-08-25), and one of the two
reasons this section gave for waiting has expired: the self-hosted reader
(`sushi_stdlib/src_sushi/toolchain/slib.sushi` plus `encoding/msgpack`) needed an
inflate written in Sushi, and `compression/zlib.sushi` is in the stdlib now. The other
still holds for the source section — Nori archives are already `tar.gz`
(`packager/archive.py`), so distribution is compressed regardless.

What moved the decision is the **metadata blob**, not the source section. An earlier
draft of this section said the blob is never compressed, because it is the index and
every reader must be able to take it cheaply. That reasoning is about the cost of
reading it, and it argued for keeping the index SMALL — which turned into an argument
for carrying less in it. The index is where doc text lives
(`docs/design/documentation.md` section 8, R8), and a library must not carry thinner
documentation to keep a number down.

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
| `sushi_lib_version` | `"2.0"`. A protocol string, unrelated to `VERSION` or to `templates.version` |
| `library_name` | from the output filename |
| `library_version` | **new** — see §6 |
| `requires_compiler` | **new** — see §6 |
| `kind` | **new** — `"source"` / `"binary"` / `"hybrid"`, matching the `KIND` header field |
| `units` | **new** — ordered unit names present in the source section |
| `compiler_version` | informational: exactly which compiler built the file |
| `platform` | meaningful only when `kind != "source"` |
| `compiled_at` | ISO-8601 UTC |
| `public_functions`, `public_constants`, `structs`, `enums` | the index |
| `templates` | written for EVERY kind. It is redundant on the source path -- the generics are in the source section as well -- but it is what lets `--lib-info` list a source library's generic functions without parsing anything (§5) |
| `not_exported` | **new** — what the library declares and keeps: a name and its kind, and nothing else. The complement of `templates.closure_summary`, and absent when a library keeps nothing (§5.5) |
| `dependencies` | see `TODO.md` 6b |

`structs` / `enums` / `public_functions` carry **only concrete, non-generic**
declarations. `_extract_public_functions`, `_extract_structs` and `_extract_enums` all
explicitly `continue` past anything with `type_params`: a generic function is not a
concrete callable, so listing it here would hand the consumer a bogus `FuncSig` with
unresolved type parameters. There is no `public` keyword for struct/enum *types* —
every concrete struct/enum in a library ships.

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

`_inject_source_stdlib_units` (`compiler/pipeline.py`) is generalized into one shared
unit injector, used by both the bundled source stdlib and source libraries. It already
does the work: read text, `parse_to_ast`, build a `Unit`, and loop until no new `use`
appears.

Three rules make this sound:

- **Namespacing.** A library unit enters `unit_manager.units` as
  `lib/<library_name>/<unit>`, so it can never collide with a consumer unit name.
- **Privacy is the existing unit mechanism.** `semantics/units.py` already gates
  exports on `func.is_public`. Library privates stay private with no new machinery.
- **The registry is skipped.** For `kind = "source"`, none of the
  `_register_library_*` helpers in `semantics/semantic_analyzer.py` runs. A library
  unit is an ordinary unit, so the ordinary passes handle it.
- **Library units are COLLECTED first, and the order says why.** A consumer's
  `extend i32 with Display` is checked against the perks visible when its own unit is
  collected, so a perk the library declares has to be in the table already. The
  compilation order yields every unit after the units it depends on
  (`docs/design/unit-namespaces.md` section 13.2), and `build_dependency_graph` records
  the edge a `use <lib/...>` creates, so a source library's units come first without a
  hand-patch. Seeding them ahead of the loop the way `_seed_library_perks` does for a
  binary library would register the same perk twice (CE4001), because this one also
  arrives in a real unit.
- **A consumer definition SHADOWS a library one, silently.** Same rule the binary path
  documents in §7, now enforced for functions, generics and perk impls by
  `passes/collect/`. Without it, `--lib-kind` would change program semantics instead of
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
  so. The rule is applied to source-library units only -- see §4.6.

Three consequences follow, and they are the point of the design:

1. **No export closure.** A private helper is reachable because it is compiled, not
   because the producer predicted that an exported generic would need it.
2. **CE5007 cannot fire.** That code exists because export-closure private symbols
   share the consumer's namespace, so shadowing one would change what the library's
   own shipped bodies call. Namespaced units remove the shared namespace.
3. **CE3504 cannot fire.** Nothing in a source library is platform-bound.

`_check_library_platform` becomes conditional on `kind != "source"`. That single
condition is the whole cross-platform fix.

### 4.3 Caching

Library units are ordinary units, so `__sushi_cache__` caches one `.o` for each of
them, keyed as any other unit is. The first build pays; later builds do not.

`compute_lib_fingerprint` (`compiler/fingerprint.py`) already invalidates every
consumer unit when a `.slib` changes. That is correct and over-rebuilds; see
`TODO.md` B4.

### 4.4 Diagnostics from source the consumer did not write

This is the one real cost of the design, and it needs deliberate handling.

Today `_register_library_generic_functions` builds a **throwaway `Reporter`** so a
malformed template snippet can never leak a diagnostic into the consumer's own
compile — a parse failure is silently skipped. That trick cannot survive. When the
whole library is source, an error inside it must be shown, and it must be
attributable.

A failure in a library unit renders as a **tier-3 relational diagnostic**: the error at
its real location inside the library unit, plus a `note` naming the library, its
version, and the consumer `use` statement that pulled it in. No new error code — the
existing ladder in `internals/report.py` covers it.

The user-visible contract is that a consumer must never see a bare error with no
explanation of why code they did not write is being compiled.

**The binary path owes the same contract** and did not pay it until #471; §5.2 has the
mechanism. A source library gets this for free because it arrives as a `Unit` with a
`provenance`, and every per-unit pass runs against `_unit_reporter(unit)`.

### 4.5 Where CE5007 went

The binary path needs CE5007 because an export-closure private shares the consumer's
flat namespace, so a consumer symbol of the same name would silently change what the
library's own shipped bodies call. Namespaced units remove the shared namespace, and
the instance-goes-home rule above removes the need to ship privates at all, so the
clash cannot occur. `tests/libs/helpers/private_closure_lib.sushi` is therefore built
as a BINARY library by the test runner (`BINARY_ONLY_HELPERS`): the code it guards is
binary-path machinery, and `test_err_lib_private_clash` asserts a rule that only exists
there.

### 4.6 A deliberately narrow rule

"A monomorphized instance belongs to the unit that declared the generic" is true of
ordinary multi-unit programs and of the bundled source stdlib as well, but it is
applied only to source-library units. Moving every instance breaks lookup of a
monomorphized `<collections/iter>` combinator
(`KeyError: unknown function: map__i32_i32`), and finding out why is its own change.
Everything outside a source library keeps landing in the first unit.

### 4.7 What the source path does not solve

A source library is portable as **text**. It is not automatically portable in
**behaviour**.

FFI is the case that matters. `stat` differs between macOS and Linux, and the `open`
flags differ. FFI stays a private unit detail (CE5002, CE5008 keep `ptr` out of every
public signature), so the FFI travels inside the library and must compile on the
consumer's platform. Sushi has no conditional compilation today: no `cfg`, no build
tags, no per-platform source files.

So an FFI-heavy library is still platform-specific, and cannot yet say so. That gap is
a language feature, tracked in `ROADMAP.md`, and it is the next thing that blocks
genuinely portable FFI-heavy libraries. It is out of scope here.

## 5. The binary path (opt-in)

Everything in this section is **what the compiler does today**, and it is unchanged by
the source-first decision. It applies when `kind` is `binary` or `hybrid`.

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

The `templates` section carries its own `"version": 4`, which has revved independently
of the container three times as the cross-library-generics feature grew:

1. generic function templates (source slices)
2. + generic struct/enum templates
3. + concrete perk-impl shipping (C4a)
4. + the export closure: private-symbol shipping (C4b/C5) and `closure_summary`

### 5.1 Concrete functions

A non-generic `public fn` becomes a `public_functions` record: `name`, `params`
(`name`+`type` strings), `return_type` (also a string — types are serialized via
`str(ty)` and re-parsed with `parse_type_string` at the consumer, not pickled). Two
checks gate it, both raising and aborting the `.slib` write (no partial artifact):

- **CE0116** — a v1 native `...T` variadic function cannot appear here. The registry
  gives the reason as a serialization gap: the variadic flag is not written into the
  library format. (A v2 type-pack `...Ts` function is unaffected — it carries
  `type_params` and is filtered into `generic_functions` before this check is reached;
  the discriminator is `is_pack` vs `is_variadic`, not the shared `...` spelling.)
  This check does not apply on the source path, where the declaration ships whole.
- **CE5002** — a public function whose signature (param or return, recursively
  including inside `Result`/`Maybe`) exposes a foreign `ptr` cannot appear in a public
  API at all. FFI is a private unit detail (`_contains_foreign_ptr`, shared with the
  in-program unit-boundary check that raises CE5008).

At the consumer, `LibraryRegistry._parse_functions` turns each record back into a
`FuncSig` (`sushi_lang/semantics/library_registry.py`), and codegen
(`_declare_library_functions_from_registry` in `codegen_llvm.py`) emits an `external`
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

At the consumer, `SemanticAnalyzer._register_library_generic_functions` /
`_register_library_generic_types` re-parse each record's `source` through
`parse_to_ast`, run a **throwaway** `CollectorPass` against a throwaway `Reporter` (so
a malformed template snippet can never leak a diagnostic into the consumer's own
compile — a parse failure is silently skipped, not fatal), pull the resulting
`GenericFuncDef`/generic type out, and register it into the consumer's own generic
table under its original name — indistinguishable, from that point on, from a generic
the consumer wrote itself. **Local definitions win silently**: a template is
registered only if its name is not already present.

**A diagnostic raised in a transplanted template body belongs to the library** (#471).
The throwaway reporter above covers the collect pass only. What the consumer's per-unit
passes check is the MONOMORPHIZED INSTANCE, which is a `FuncDef` in one of the
consumer's own unit ASTs (`register_synthesized_function`'s `units[0]` fallback, since
`home_unit` is honoured only for a `from_library` unit -- §4.2). Its spans came from
parsing the SLICE, where the declaration is on line 1, so rendering them against the
consumer's file named a line the consumer never wrote: an error landed on a blank line
and a caret could mark unrelated consumer text.

`report.Origin` is the answer, and it carries exactly three things -- what to call the
body, what text the caret marks, and why it is being compiled here:

| field | value |
|---|---|
| `filename` | `<template:<library>:<name>>`, the same shape the throwaway reporter already used |
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
`tests/libs/test_lib_pack.sushi` exercise this: the library ships `perk Display` and
`show_all@(...Ts: Display)`; the consumer supplies `Display` impls for `i32`/`string`
and calls with zero and two arguments.

### 5.4 Concrete perk implementations (C4a)

A library's own `extend <ConcreteType> with <Perk>:` block, for a perk one of its
exported generics constrains on, ships as a `perk_impls` record: `type`, `perk`,
`source` (the whole `extend` block, for signatures), `methods: [{name, symbol}]`. The
method symbol name is computed by `impl_method_symbol()` — mirrors
`backend/functions/helpers.py:get_extension_method_name`'s mangling (`<` → `__`, `>`
dropped, `", "` → `_`) — deliberately duplicated into the manifest rather than
re-derived at the consumer, so producer and consumer never drift.

The **bodies are not re-emitted**. In the library's own bitcode, every perk-impl
method gets `weak_odr` linkage (`_set_weak_odr_on_perk_impls` in `codegen_llvm.py`,
called from `compile_to_bitcode` right after building the module) — `weak_odr`, not
`linkonce_odr`, specifically because it must **survive** LLVM's optimizer even though
nothing inside the library module itself calls it (an unreferenced `linkonce_odr`
definition can be dropped as dead code; `weak_odr` cannot). At the consumer,
`_register_library_perk_impls` rebuilds an `ExtendWithDef` via `deserialize_perk_impl`
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

A perk-impl record that fails to re-parse is not fatal to the consumer's build: it is
skipped with **CW3506** (a warning, not an error) — "methods it provides will be
unavailable unless the consumer supplies its own."

Only perk *implementations* ship this way; perk *definitions* (the method-signature
contract) ship separately and unconditionally for every perk named in an exported
generic's constraints, via `_seed_library_perks`, which runs **before** the consumer's
own units are collected (perk-impl collection validates against CE4003, so a consumer
implementing a library-shipped perk needs the contract present at collection time, not
after).

### 5.5 The export closure of private dependencies (C4b/C5)

Before C5, an exported generic whose body called a library-private helper simply
failed to build the library (CE5006 on every private reference — the old, wide reading
of that code). C5 makes ordinary private-helper references shippable instead:
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
  compile-time *value*, not a link-time symbol. `_register_library_constants` re-parses
  it, appends the reconstructed `ConstDef` onto the first consumer unit's AST (constant
  globals get internal linkage per module, so appending a duplicate-content const to a
  different module never collides).
- **concrete struct/enum types** referenced are *not* separately shipped by the
  closure — they already ship unconditionally in `structs`/`enums` (§3); the closure
  walk only recurses through them for further transitive references.

Only two reference shapes still abort the export with **CE5006**, attributed to the
exported generic at the root of the dependency chain (not the private helper itself —
the generic is what the consumer sees fail): a reference into an `unsafe external`
namespace (foreign bindings cannot be re-declared at a consumer that never saw the
`unsafe external` block), and a private helper whose signature exposes a foreign `ptr`
(same rationale as CE5002).

`closure_summary` (`{private_functions: [name...], private_generic_functions:
[name...], constants: [name...]}`, sorted) is not consumed by any code path — it exists
purely for observability (inspectable via `--lib-info` / direct manifest reads) so a
library author can see what their public API is quietly dragging along.

**The closure is for the library's own bodies, and the CALL SITE is what says so**
(#468). A shipped private has to be resolvable at the consumer, because a monomorphized
template body lands there and still calls what it called at home; consumer code that
names the same symbol is `CE3005`. The two are told apart by whose body the call is in,
never by the symbol: `FuncDef.is_library_template` marks an instance of a `.slib`
template (stamped in `register_synthesized_function`, and carried onto a lambda lifted
out of such a body), the typecheck pass reads it as `in_library_body`, and the gate in
`passes/types/visibility.py` exempts that and nothing else. An instance of the
*consumer's* generic is synthesized the same way and is not exempt — it is the user's
code. A constant is the one kind still readable, because a private constant cannot be
written yet (#466).

**A private the closure does not ship is named, not hidden** (#469). The walk starts at
the public *generics*, so a private that only a concrete public function calls -- or one
nothing public calls -- ships nowhere and reaches the consumer's tables not at all. That
made it `CE2008: undefined function` on the binary path, for a function the library defines
and deliberately kept, while the source path called the same call `CE3005`. The manifest's
`not_exported` key closes that: `_extract_not_exported` lists the name and the kind of every
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
structural difference between the two. The wording no longer differs, though: a source
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
- **`peek` / `poke` could be, and that is exactly how they were lost.** The manifest
  always serialized `"peek string"` correctly, because `str(ReferenceType)` produces it
  — but `parse_type_string` had no reference arm, so the consumer read back
  `UnknownType("peek string")`: a type that names nothing, with the mode silently gone.
  A library could therefore *write* a borrow and never have one *read*. The parser
  learned the two words alongside the field.

`tests/unit/test_lib_param_modes.py` is the gate: it builds a library declaring one
function per mode, asserts what the manifest records, asserts what the consumer reads
back, and asserts that an unsupported container version is rejected with CE3509 rather
than guessed at.

On the source path this question does not arise at the manifest level: the declaration
ships whole, so the mode is in the text and the ordinary passes read it.

**Not yet at the boundary:** a `nom` receiver (`nom self` does not exist), and a
consuming variadic — a public v1 `...T` cannot ship in a binary library (CE0116, §5.1).

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
logic involved at all. This is exactly why `weak_odr` had to be used instead of
`linkonce_odr` for perk-impl bodies — `linkonce_odr` symbols never even survive as
*declarations* other object files can override; the point of `weak_odr` is to be a
retained-but-overridable definition that a plain `cc` link resolves correctly.

A `.slib`'s fingerprint also feeds `compute_unit_fingerprint` for every *consumer* unit
(`library_fingerprints` in `_compile_incremental`), so a rebuilt library invalidates
the cache of any consumer unit that depends on it, even though the unit's own source
did not change. This applies to both paths.

**A source library uses neither mechanism.** Its units are compiled and linked exactly
as the consumer's own units are.

### 5.8 What does NOT cross a binary boundary

- **Generic-target perk impls** (`extend List@(T) with SomePerk:`) — unsupported
  **in-program** already (Sushi has no mechanism for a perk impl generic over its target
  type), so naturally nothing ships across a binary `.slib` either. `_extract_templates`
  filters these out explicitly (`isinstance(impl.target_type, GenericTypeRef)` skip).
- **v1 native `...T` variadics** as public functions — CE0116, §5.1.
- **Transitive library dependencies** — if library A's source itself does `use <lib/b>`,
  a consumer of A still needs its own `use <lib/b>` statement; nothing auto-propagates.
  See `docs/libraries.md` Limitation #1 and `TODO.md` 6b/6d.

## 6. Versions and compatibility

Two version fields, with two different jobs.

### 6.1 `library_version`

The library's own version, `major.minor.patch`. A `.slib` has never recorded one:
`library_name` comes from the output filename and nothing states a version at all.

Source of the value, in order:

1. `[package] version` from a `nori.toml` beside the sources, when one exists
2. otherwise the explicit `--lib-version X.Y.Z` flag

Neither present is **CE3505** at build time. This keeps the packager as the source of
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

A new `semver` module under `sushi_lang/internals/`: `Version` (parse, compare, order) and a constraint
matcher covering exact, `~X.Y`, caret and comparator ranges.

It lives in `internals` rather than `packager` because the compiler needs it on the
library-load path, and `internals` is the shared bottom layer. `TODO.md` 6a originally
specified it under `sushi_lang/packager/`; the Nori resolver reuses this module rather than
growing a second implementation. Both packages are in the blocking mypy set
(`MYPY_PKGS` in `.githooks/pre-push`), so the module must be mypy-clean.

## 7. Conflict and safety rules — summary

Rules marked **binary** apply only when `kind != "source"`.

| Situation | Rule | Why |
|---|---|---|
| Consumer FUNCTION name == public library function | local wins, and **CW3002** says so | the two are separate symbols, because each carries the unit that declared it: the consumer's call binds to its own and the library's body to its own. Legal, and rarely intended, so it warns. Both public is legal too and warns the same way; `CE3003` refused that program once and retired with unit namespaces. `docs/design/visibility.md` §9.1 |
| Consumer TYPE name == public library type | **CE0004** / **CE2046**, hard error | type identity is nominal, so one name is one shape; the consumer cannot have its own |
| Consumer TYPE name == library-PRIVATE type (**source**) | **CE3011**, hard error | type identity is nominal, so one name is one shape even where the consumer cannot see the library's declaration. Renaming is the only move, and `docs/design/type-identity.md` phase 2 is what would lift it |
| Consumer FUNCTION name == library-PRIVATE function (**source**) | legal, and silent | each declaration carries the unit that declared it, so the two coexist and each body calls its own (`docs/design/unit-namespaces.md` sections 6 and 9). A CONSTANT is the same shape and takes the same answer (#507); a library's PUBLIC constant stays **CE0105**, because the consumer can see and read that name |
| Consumer name == **export-closure private** symbol (**binary**) | **CE5007**, hard error | the library's own monomorphized bodies call that private symbol by name; silently shadowing it would change what the library's shipped code does. The source path needs no twin for a function, because a source library's units are compiled and each keeps its own scope |
| Consumer perk-impl `(type, perk)` == library-shipped perk-impl (**binary**) | local wins, silent, both semantically and at link time (§5.4, §5.7) | a consumer providing its own impl is expected, not an error |
| Consumer extension method name == library perk-impl method name (**binary**) | library impl skipped entirely (no error) | avoids recreating CE4007 as a breaking change on every library update |
| Exported generic references an `unsafe external` namespace | **CE5006** | FFI bindings cannot be re-declared at a consumer that never saw the block. Fires on EVERY kind: `_extract_templates` runs the export-closure walk before the kind branch in `compiler/pipeline.py`. Arguably wrong on the source path, where the `unsafe external` block ships inside its own unit — see §9 |
| Exported generic (transitively) references a `ptr`-exposing private signature | **CE5006** | same rationale as CE5002 — `ptr` is unit-confined. Every kind, as above |
| Public function/private-closure helper exposes `ptr` in its own signature | **CE5002** | `ptr` cannot appear in any public library signature |
| Public `fn` (non-library, ordinary unit boundary) exposes `ptr` | **CE5008** | the general unit-boundary form of the same rule; CE5002 is its library-specific sibling |
| A template snippet fails to re-parse (**binary**) | perk-impl: **CW3506** (skip); generic fn/type/constant: silently skipped, no diagnostic | never let a malformed shipped snippet crash or pollute the consumer's own build |
| A **source** library unit fails to compile | the real diagnostic, plus a `note` naming the library and the `use` that pulled it in (§4.4) | the consumer must never see a bare error about code they did not write |
| Public v1 native `...T` variadic function | **CE0116** | the variadic flag is not serialized into the library format. Fires on EVERY kind: the check lives in `_extract_public_functions`, which builds the index for every kind — see §9 |
| Platform mismatch (**binary**) | **CE3504** | bitcode is platform-specific; caught early with a clear message instead of an incomprehensible late `cc`/LLVM failure. Never raised for a source library |
| An `unsafe external "C"` link-name == a symbol this build defines | **CE5013** | one module, so a declaration and a definition of one name unify: the declaration entered the program's own body with its own signature, unchecked. It reached a library-PRIVATE body from consumer code, and where the compiler already held a declaration of the name the same program was a `CE0000` instead (#470). Every kind: the rule reads the func table, the constant table and the library registry, so it runs after the `libraries` step. A GENERATED stdlib symbol is in none of those, and used to build clean and die with a bus error: the stdlib build now writes a manifest of what it defines beside its bitcode, and a reserved set in `semantics/externs_manifest.py` covers the ones the backend emits inline (#472) |
| Container version is not 4 | **CE3509** | no backward-compat shim; there are no users in the wild |
| `requires_compiler` not satisfied | **CE3503** | see §6.2; the escape is `--ignore-compiler-version` |
| No `library_version` available at build time | **CE3505** | see §6.1 |
| Call site's `nom` marker disagrees with the shipped signature | **CE2427** | the same rule as within a unit: a consume is visible at both ends or at neither (§5.6) |

## 8. Reading the code: file → responsibility

| File | Responsibility |
|---|---|
| `sushi_lang/backend/library_format.py` | The `.slib` byte container: magic/version/flags/kind/length framing, msgpack (de)serialization. No LLVM, no linking, no path resolution. |
| `sushi_lang/backend/library_manifest.py` | `LibraryManifestGenerator` — the **producer**. Builds every manifest section; `_extract_templates` / `_compute_export_closure` are the binary path's export-closure walk. |
| `sushi_lang/backend/library_errors.py` | `LibraryError(SushiError)` — the one exception type every `.slib` read/resolve/link failure raises, rendered through the normal reporter. |
| `sushi_lang/backend/library_paths.py` | `LibraryResolver` — filesystem discovery only (`SUSHI_LIB_PATH`, project deps, Nori bento, cwd) and manifest caching (`loaded_libraries`). Deliberately *not* a linker despite the historical name (`LibraryLinker`) it was renamed away from. |
| `semver` (new, under `sushi_lang/internals/`) | `Version` + constraint matching. Shared by the library-load path and the Nori resolver (§6.3). |
| `sushi_lang/compiler/pipeline.py` | Library resolution and the gates: `_check_library_platform` (binary only), `_check_library_compiler_version`, and the shared source-unit injector that both the bundled source stdlib and source libraries use. Also chooses monolithic vs incremental and drives per-unit caching. |
| `sushi_lang/semantics/library_registry.py` | `LibraryRegistry` — **binary path only**. Pre-parses a raw manifest dict into typed `FuncSig`/`StructType`/`EnumType` objects once, shared by the semantic analyzer and codegen. |
| `sushi_lang/semantics/library_templates.py` | **Binary path only.** The re-parse-based codec: `serialize_/deserialize_generic_function/struct/enum`, `serialize_/deserialize_perk`, `serialize_/deserialize_perk_impl`, `slice_decl_source` (the line-span slicing algorithm), `impl_method_symbol` (perk-impl symbol mangling, kept in lockstep with `backend/functions/helpers.py`). |
| `sushi_lang/semantics/semantic_analyzer.py` | **Binary path only.** Consumer-side registration: `_build_library_registry`, `_register_library_{functions,private_functions,constants,perk_impls,generic_functions,generic_structs,generic_enums,structs,enums}`, `_seed_library_perks`. This is where CE5007 fires and where local-wins is implemented for every category except perk impls. |
| `sushi_lang/backend/codegen_llvm.py` | `compile_to_bitcode` (producer: sets `weak_odr` on perk impls, promotes export-closure private fns to `external`), `_declare_library_functions[_from_registry]`, `_declare_library_perk_impl_methods` (consumer: declares, never defines, library symbols), `compile_multi_unit` (drives `TwoPhaseLinker` when libraries are present), `compile_library_to_object` (incremental path: one `.o` per library). |
| `sushi_lang/backend/module_linker.py` | `TwoPhaseLinker` — the monolithic-path in-memory IR merge (reachability + priority-ordered symbol resolution). Not library-specific — the main module and stdlib bitcode go through it too. |
| `sushi_lang/backend/symbol_resolver.py` | `SymbolResolver._choose_definition` — the `MAIN > LIBRARY > STDLIB > RUNTIME` priority table used only by `TwoPhaseLinker`. |
| `sushi_lang/compiler/fingerprint.py` | `compute_lib_fingerprint` — content hash of a `.slib`, used to cache its compiled object and to invalidate consumers. |
| `sushi_lang/internals/errors/library.py` | CE35xx: the container/link/version family. |
| `sushi_lang/internals/errors/ffi.py` | CE50xx: CE5002/CE5006/CE5007/CE5008 — the export-safety family, despite living in the "ffi" module (historical: `ptr`-confinement was the first reason a symbol could be un-shippable). |

## 9. Measured during the phase-6 doc pass

Three things the code does that this document said it did not. All measured at 0.11.1
against a `--lib-kind source` build.

**`templates` is written for every kind.** `generate()` always builds the section, and
`_extract_templates` always runs. On the source path it is redundant, because the same
generics are in the source section as whole units. It is also the reason `--lib-info` can
list a source library's generic functions at all, so it earns its place; §3 now says so.

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

**A rejected library build reported a spurious CE0000 after the real diagnostic.**
FIXED in #436. The three rejection sites in `sushi_lang/backend/library_manifest.py` emitted
their diagnostic into the reporter and then raised `ValueError` to stop the build. Nothing
caught it, so the top-level guard in `sushi_lang/compiler/cli.py` rendered it as an internal
compiler error on top of the correct message, telling the user to report a compiler bug that
was not one:

```
varlib.sushi:1:11: error [CE0116]: public function 'total' is variadic and cannot appear ...
varlib.sushi: error [CE0000]: internal compiler error: ValueError: CE0116: public function ...
  = note: this is a bug in the Sushi compiler, not in your program
```

Each site now emits and returns, and control flow belongs to `pipeline.py`, which gates on
`reporter.has_errors` the way it already does before codegen. There are two gates, and the
placement is the contract: the first sits after the export closure and BEFORE the bitcode
compilation, so a CE5006 rejection still costs nothing; the second sits after
`generate()`, which itself extracts the public API first and returns without writing once
the reporter holds an error. A rejected build therefore leaves no `.slib` behind and prints
no success line.

Two things surfaced while fixing it. `_reject` inside the export-closure walk had been
non-returning, so the code after each call site was unreachable; each caller now returns
explicitly, which keeps the one-diagnostic-per-build behaviour the raise used to give.
And the producer's **CE5002 is unreachable from a CLI build**: the typecheck pass's public-fn
`ptr` fence (`passes/types/signatures.py`, CE5008) tests the identical condition and exits
earlier. The site is kept as the backstop for a direct producer call, and
`tests/unit/test_lib_rejection_diagnostics.py` pins the shadowing so a missing CE5002 is
never read as a regression.

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

## Disagreements found while writing this document

- **The recursive-generic-enum limitation** (`Own@(Tree@(T))`, "whether it crosses a
  `.slib` boundary is untested") was CLAUDE.md Known Limitation #12 when this document
  was written. It is false as written — verified to work today, both in-program and
  across a library boundary: a library exporting
  `enum Tree@(T): Leaf(T) / Node(Own@(Tree@(T)))` compiles as a `.slib`, and a consumer
  that does `use <lib/treelib>` and constructs `Tree.Leaf(5)` compiles and runs. The
  claim has since left CLAUDE.md on its own, and #12 there is now an unrelated (and
  true) note about i32 range bounds. Nothing to narrow; the verification is recorded
  here so it is not re-discovered.
- **`tests/libs/helpers/generic_types_lib.sushi`**, comment claiming a recursive generic
  enum "infinite-loops the monomorphizer's type substitutor... even for a purely
  in-program definition": stale — the same definition compiles and runs correctly today.
  The comment predates the tie-the-knot fix and was not updated when it landed.
- **`docs/library-format.md`** disagrees with itself about the container version, and
  this entry described the disagreement the wrong way round. Measured at 0.11.1: the
  layout diagram says `2`, the "Version" subsection and the "Writing" steps say `3`, and
  `LibraryFormat.VERSION` in code says `4`. All three places in the document need to say
  `4`. Fixed in the phase-6 doc pass.
- The manifest schema shown in `docs/library-format.md` lists `is_generic`/`type_params`
  fields on `public_functions`/`structs`/`enums` entries as if they vary; in the actual
  producer generic declarations are filtered out of these lists entirely (they route to
  `templates.*`), so on every record that does appear here `is_generic` is always
  `False` and `type_params` is always `[]`. Fixed in the phase-6 doc pass.
- **`CLAUDE.md` Known Limitation #6** says a `.slib` "is not portable across platforms".
  True for a binary library, false for a source library. Narrowed in the phase-6 doc
  pass.
