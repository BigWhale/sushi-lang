# Semantic Analysis Passes

[← Back to Documentation](../index.md) | [Architecture](architecture.md)

Detailed documentation of Sushi's multi-pass semantic analysis pipeline.

## Pass Overview

There are 19 passes. The passes have NAMES, not numbers, because a number goes out of
order when a pass is inserted between two others.
`SemanticAnalyzer.check()` (`semantics/semantic_analyzer.py`) is the code authority on the
order; this list mirrors it. `_check_multi_file` runs that order, one call per stage,
each named for the stage it runs.

| Pass | What it does | Where |
|---|---|---|
| `collect` | constants, function headers, generic types, externals | `semantics/passes/collect/` |
| `docs` | check each doc block against its declaration ([CE7001](../error-catalog.md#ce7001)-[CE7008](../error-catalog.md#ce7008), [CW7001](../error-catalog.md#cw7001)), and its completeness under `--warn-missing-docs` ([CW7002](../error-catalog.md#cw7002)-[CW7006](../error-catalog.md#cw7006)) | `semantics/passes/docs.py` |
| `unused` | under `--warn-unused`: a private declaration nothing in its unit reaches ([CW1004](../error-catalog.md#cw1004)), an import whose unit names nothing it brings ([CW3006](../error-catalog.md#cw3006)) | `semantics/unused.py` |
| `externs` | extern signatures ([CE5003](../error-catalog.md#ce5003)), [`CW5001`](../error-catalog.md#cw5001), the `ptr` unit gate ([CE5009](../error-catalog.md#ce5009)) | `semantics/passes/types/externals.py` |
| `libraries` | register every symbol a `.slib` exports | `semantics/library_registration.py` |
| `namespaces` | bind what each unit may write behind a dot, and what its flat scope holds ([CE3013](../error-catalog.md#ce3013), [CE3014](../error-catalog.md#ce3014), [CE3016](../error-catalog.md#ce3016), [CW3004](../error-catalog.md#cw3004), [CW3005](../error-catalog.md#cw3005)) | `semantics/passes/namespaces.py` |
| `ffi-clash` | fold each link name written as a string constant ([CE5015](../error-catalog.md#ce5015)), then reject an `unsafe external` that names a symbol this build defines ([CE5013](../error-catalog.md#ce5013)). Its instance half runs directly after `monomorphize` (see below) | `semantics/passes/types/externals.py` |
| `entrypoint` | main's whole rule: it exists ([CE3007](../error-catalog.md#ce3007)), a library carries none ([CE3501](../error-catalog.md#ce3501)), it returns an integer ([CE0106](../error-catalog.md#ce0106)), and it takes `string[] args` or nothing ([CE0138](../error-catalog.md#ce0138)) | `semantics/semantic_analyzer.py` |
| `instantiate` | collect every generic instantiation the program asks for | `semantics/generics/instantiate/` |
| `monomorphize` | generic definitions become concrete instances | `semantics/generics/monomorphize/` |
| `resolve` | struct field, enum variant and constant types become concrete; a spelled Result return is interned | `semantics/passes/resolve.py` |
| `finite-types` | reject a type that contains itself by value ([CE2095](../error-catalog.md#ce2095)) | `semantics/passes/finite_types.py` |
| `derive` | auto-derive `hash()` and `clone()` (not `Eq`, `Ord` or `Display`: the contract walk answers those) | `semantics/passes/derive.py` |
| `shadowing` | reject an extension method that collides with a built-in or with a stdlib method on a built-in type ([CE2097](../error-catalog.md#ce2097)); warn where a unit's own extension hides an imported public one ([CW3007](../error-catalog.md#cw3007)) | `semantics/semantic_analyzer.py` |
| `effects` | which functions destroy a `poke` parameter, transitively | `semantics/passes/borrow/destroy_effects.py` |
| `scope` | scope and variable analysis | `semantics/passes/scope.py` |
| `typecheck` | type validation and inference; each generic template is checked one time, where it is written, with its type parameters opaque | `semantics/passes/types/` |
| `lift` | each lambda becomes a top-level function plus an environment | `semantics/passes/lift.py` |
| `borrow` | borrow checking | `semantics/passes/borrow/` |

The last four run per unit, in one loop, so the whole-program passes above them see every
unit before any function body is walked.

`semantics/const_eval.py` is **not** a pass. Three callers reach it as a helper: the
**AST builder**, which reads a fixed array's size while the unit is parsed (so a
size is a literal or a constant of the same unit) and keeps a constant table of its own for it; the `typecheck` pass; and
the backend. The last two share the collect pass's table, and with it the fold memo.

### The word "phase"

"Phase" names the three sub-steps of the `typecheck` pass per statement — resolution →
propagation → validation. It never names a pass. Where a comment in the tree says
"phase" about an issue or a project, it names a work step, not a pass.

## The `collect` pass: headers and constants

**Files:** `semantics/passes/collect/*.py`

### Purpose

Collect global definitions before analyzing function bodies.

### Responsibilities

1. **Constants**: Parse and register constant definitions, and unit variables (`var`) in
   the same table with `is_var` set (`docs/design/unit-storage.md`)
2. **Function Signatures**: Collect return types and parameters
3. **Generic Types**: Register struct and enum definitions. An `error` declaration is an
   enum with `is_error` set, on the `EnumType` and on a generic template, so each instance
   carries it; the seven predefined error types carry it too, and `FileMode` and
   `SeekFrom` do not (`docs/design/error-conversion.md` section 2)
4. **Symbol Table**: Build initial global scope
5. **Visibility**: Record who declared what, and whether it says `public`
6. **Extension methods**, instance and STATIC alike, with `is_static` carried on the
   collected signature (`docs/design/method-resolution.md`). Each record keeps its
   declaring unit and its `public` marker, and the visibility table files a `DeclOrigin`
   of the kind "extension method" under `<target>.<method>`
   (`docs/design/extension-visibility.md`). Two units can each declare one method name on
   one type (C5); one unit cannot (C1, [`CE0101`](../error-catalog.md#ce0101)). The two refusals a static
   brings are structural and so are decided here: a receiver named in the signature or
   the body is [`CE0134`](../error-catalog.md#ce0134), and a static spelling a variant of the enum it extends is
   [`CE2103`](../error-catalog.md#ce2103). A `static` inside a perk implementation is [`CE4014`](../error-catalog.md#ce4014), in the perk collector.
6a. **The `dont_panic` gate.** `passes/collect/dont_panic.py` reads the marker once, at the
   header of a function, a static, an extension method, a perk implementation method or a
   conversion, so there is one [`CE0152`](../error-catalog.md#ce0152) per declaration and
   not one per index. A bundled stdlib unit and a build with `--dont-panic` pass. In a
   library unit the message names the library. An inert marker is
   [`CW0004`](../error-catalog.md#cw0004), for the author's units only. The AST builder
   sets `IndexAccess.unchecked` on each index under a marked body and stops at a lambda
   (`semantics/unchecked_index.py`); a marker on a lambda, a perk contract method or an
   extern is [`CE6111`](../error-catalog.md#ce6111), in the builder. The backend reads the
   stamp at the two bounds-check sites (`backend/types/arrays/indexing.py`).
7. **The error channel of a written body.** A callable has a channel only when its
   signature writes `| E` or returns an explicit `Result@(T, E)`; there is no default
   error type (`docs/design/error-channel.md`). The collect pass emits [`CE0131`](../error-catalog.md#ce0131) for a `??`
   in EVERY bare function and method body: a free function, an extension method and a
   perk-implementation method (`passes/collect/utils.py`, `functions.py`, `perks.py`).
   It fires once per declaration and covers a template nobody instantiates. Both
   spellings at once (`Result@(T, E1) | E2`) is [`CE2085`](../error-catalog.md#ce2085), here too. A lambda takes its
   channel from its TYPE, which only the `typecheck` pass knows, so the `typecheck` pass
   emits [`CE0131`](../error-catalog.md#ce0131) for a `??` in a bare lambda (`passes/types/expressions.py`).
8. **Conversions.** `extend <Source> as <Target>:` is an `ExtendDef` named `as`, and
   `collect_conversion` (`passes/collect/conversions.py`) judges it in this order: a `??`
   in the body is [`CE0131`](../error-catalog.md#ce0131) (the body is bare); a side that is generic or not an error type
   is [`CE2520`](../error-catalog.md#ce2520); an identity is [`CE2521`](../error-catalog.md#ce2521); a declaration outside the unit of the target
   type (the home module, for a predefined target) is [`CE2519`](../error-catalog.md#ce2519); a second declaration of
   one pair is [`CE0101`](../error-catalog.md#ce0101). An accepted conversion is filed in `SymbolTables.conversions`,
   keyed by the pair of names; a refused one leaves the AST. A conversion is not filed in
   the extension table, and a side that only a binary library declares is judged again
   in the `libraries` step.

### One predicate for the channel

`has_channel` (`semantics/channel.py`) is the one question "does this callable answer a
Result". Every reader asks it: the `collect` pass ([`CE0131`](../error-catalog.md#ce0131), [`CE2085`](../error-catalog.md#ce2085)), the `typecheck`
pass (the body state, the return rule, the `??` channel, [`CE0107`](../error-catalog.md#ce0107), what a call yields),
the `lift` pass (the desugar of an expression lambda: `return e` when bare,
`return Result.Ok(e)` with a channel), and the backend. Do not test `err_type` directly:
an explicit `Result@(T, E)` return has a channel and no `err_type`.

### Source order decides the holder of a name in one unit

The collectors run kind by kind, so the collection order is not the source order. Before
they run, `claim_unit_names` (`collect/unit_names.py`) sorts the unit's top-level
declarations by position and refuses each later declaration of a name that an earlier one
of another kind holds: [`CE0006`](../error-catalog.md#ce0006) for a struct beside an enum, [`CE1005`](../error-catalog.md#ce1005) for every other
pair. A refused declaration enters no table. Two declarations of one kind
stay with that kind's collector and its own code.

### A unit is collected after the units it depends on

The compilation order (`UnitManager.topological_sort`) yields every unit AFTER the units
it depends on. The walk itself counts in-degree as "how many units depend on me" and so
produces the opposite; the result is reversed once, and the direction is a ruling
(`docs/design/unit-namespaces.md` section 6.2): a unit's scope is built from what its own
imports declare, so the declaring unit has to be collected already.

A source library's units and a bundled Sushi-source stdlib module are injected as ordinary
compilation units, and `build_dependency_graph` records the edge that an import of one
creates. That is why a library unit comes first without being told to. A binary `.slib`
matches no unit and adds no edge, because it has no unit to compile.

The order also serves the two rules that read the perk table when an implementation is
collected -- the perk exists ([`CE4003`](../error-catalog.md#ce4003)), and its marker lets this unit implement it
([`CE4011`](../error-catalog.md#ce4011)). A perk declared next door is in the table when the implementing unit is reached.
Five perks are in the table before any unit is collected: `Drop`, `Hashable`, `Eq`, `Ord`
and `Display`, the compiler's own (`register_predefined_perks`). The contracts of `Eq` and
`Ord` hold a receiver placeholder (`ReceiverType`) where a user perk cannot write `Self`;
only this function builds it. The constraint check reads `Hashable` through the derive
pass's predicate, `hashability_of`, and `Eq`, `Ord` and `Display` through the contract
walk (`operand_contract`), so no implementation table entry stands for a derived hash or
a derived contract.

### Example

```sushi
const i32 MAX = 100  # Register constant

struct Pair@(T, U):   # Register generic struct
    T first
    U second

fn add(i32 a, i32 b) i32:  # Register signature
    return a + b
```

**Output**, in `SymbolTables` (`semantics/tables.py`):
- `tables.constants`: a `ConstSig` for `MAX`
- `tables.functions`: a `FuncSig` for `add`
- `tables.generic_structs`: the template of `Pair`

### One seam for who may name what

`semantics/visibility.py` is the one answer to "may unit U name declaration D". One record
(`DeclOrigin`), one predicate, and four sets that classify every kind the declaration walk
yields: a kind carries its own marker, follows the declaration it is part of, follows the
type it is attached to, or has no visibility at all.
`tests/unit/test_visibility_seam_is_total.py` asserts the union is exactly the walk, in
both directions, so a new declaration kind cannot get half the rule.

The table is filled at the END of each unit's collection, from `declarations()` -- the same
total walk the `docs` pass uses -- so the gate on that walk protects the seam. The merger
replays it once per unit, which is why `record()` is idempotent.

Two facts it has to carry beyond the marker. **A name with no record is public**: the
compiler synthesizes types nothing declared (a monomorphized instance, a lifted closure
environment, `FileMode`), and none of them can carry a source marker. And the table
remembers the LOSER of every contested name, because a unit that declared a name must
never be shown its own code measured against somebody else's declaration.

The rules that read it live where the use is: `passes/types/visibility.py` for a call and a
bare constant read, the type funnel for a named type, the collect pass itself for a
TYPE declaration that collides with a library's ([CE3011](../error-catalog.md#ce3011)) or a declaration that promises
something about a private perk ([CE4011](../error-catalog.md#ce4011)), and `passes/types/public_signatures.py` for the leak fence ([CE3009](../error-catalog.md#ce3009),
[CE3010](../error-catalog.md#ce3010)). `docs/design/visibility.md` is normative.

### One reporter, many files

This pass is the only whole-program pass that walks every unit's AST while sharing ONE
reporter -- the per-unit passes each build their own through `_unit_reporter(unit)`. A span
is meaningless without the file it came from, so `CollectorPass.run` names the unit it is
reading (`Reporter.origin`), and `Reporter._record` stamps it onto every diagnostic the pass
raises. So a diagnostic about a declaration in a non-entry unit names the file of that unit,
not the entry file.

A `first defined here` note needs one thing more. It points at a table entry, and the entry
may have been made while a DIFFERENT unit was being collected, so each record remembers its
own file: `files` beside `spans` on the struct and enum tables, `PerkTable.files`, and a
`filename` field on `FuncSig`, `ConstSig` and `ExternalSig`. `note_first_declaration` is the
one place that reads them.

### Constants

A constant initializer is a constant expression: literals, other constants, operators,
`as`, and a struct construction or an enum variant whose arguments are all constant. The
fold is `semantics/const_eval.py`. A constant cannot call a function or a method. A unit
variable (`var`) also takes an empty container (`List.new()`, `from([])`).

### FFI External Collection

`semantics/passes/collect/externals.py` builds an `ExternalTable` from each
`unsafe external "C"` block: a namespace-keyed map of `ExternalSig` (Sushi name,
link name, param/return types). It rejects duplicate names within a namespace. The
same module holds `LinkNames` and `reject_disagreeing_link_names`, which the `ffi-clash`
step runs over every unit after it folds the link names: two declarations of one link
name with other C types are [`CE5001`](../error-catalog.md#ce5001), and each `RESERVED_EXTERNS` built-in is the first
declaration of its name (#1099). The table is exposed as `collector.externals` and threaded
into the scope pass, the type validator, and the backend.

The C-ABI allowlist check ([`CE5003`](../error-catalog.md#ce5003)) and the [`CW5001`](../error-catalog.md#cw5001) four-guarantee warning live
in `semantics/passes/types/externals.py::validate_external_signatures`, run right
after collection.

[`CE5013`](../error-catalog.md#ce5013) has two halves. The `ffi-clash` step checks every symbol that exists before
`instantiate`: a function, a constant, a library record, a generated symbol. A
monomorphized instance exists only after `monomorphize`, so
`SemanticAnalyzer._check_instance_clash` runs the second half directly after that pass
(#1113). It reads the instance signatures through the same `_declaration_emitting`. The
symbol of an instance takes its home unit: the unit that declared the generic
(`<unit>$ident__i32`, or the prefix of a source library's unit), or no unit for the
template of a binary library (`gen__i32`). `generics/synthesis.py` writes that home unit
on the signature and on the body, and the back end's `declaring_unit` reads the same
value from the body, so the two layers cannot disagree.

## The `docs` pass: a doc block against its declaration

**File:** `semantics/passes/docs.py`

A doc block is part of the declaration (`docs/design/documentation.md`), so the compiler can
check what the block claims against what the declaration says. A `- Parameter q:` that names
no parameter of this function is wrong, and the compiler knows it is wrong.

```sushi
##:
Adds two numbers.

- Parameter q: CE7001 -- there is no parameter called q.
:##
fn add(i32 a, i32 b) i32:
    return a + b
```

Eight errors and one warning, all of them always on. `check_docs` is the entry point:

| Condition | Code |
|---|---|
| a `- Parameter` tag names no parameter of this callable | [CE7001](../error-catalog.md#ce7001) |
| two `- Parameter` tags for one name | [CE7002](../error-catalog.md#ce7002) |
| a second `- Returns:` or `- Errors:` | [CE7003](../error-catalog.md#ce7003) |
| an unrecognised tag keyword | [CE7004](../error-catalog.md#ce7004) |
| a block in a body that is not the first item | [CE7005](../error-catalog.md#ce7005) |
| a declaration with a block above it and a block first in its body | [CE7006](../error-catalog.md#ce7006) |
| an `- Example:` tag that introduces no fenced block | [CE7007](../error-catalog.md#ce7007) |
| a fence inside a block that is never closed | [CE7008](../error-catalog.md#ce7008) |
| a block that documents nothing | [CW7001](../error-catalog.md#cw7001) |

Every check finds a claim that CONTRADICTS the declaration, which is why none of them is
behind a flag.

### Behind `--warn-missing-docs`

Completeness is the other half, and it is policy rather than contradiction, so the CALLER
decides. `check_missing_docs` is a second entry point, and `_check_multi_file` runs it in
the same loop only when the flag is set. The pass holds no policy flag of its own: that is
what keeps the always-on side unable to drift behind a flag.

| Condition | Code |
|---|---|
| a declaration with no doc block | [CW7002](../error-catalog.md#cw7002) |
| a documented callable with a parameter that no `- Parameter` tag names | [CW7003](../error-catalog.md#cw7003) |
| a documented callable that returns a value, with no `- Returns:` | [CW7004](../error-catalog.md#cw7004) |
| a documented function that declares `\| E`, with no `- Errors:` | [CW7005](../error-catalog.md#cw7005) |
| a unit with no doc block | [CW7006](../error-catalog.md#cw7006) |

Three rules shape the table. Every declaration is asked, public and private, because an
internal API is documented surface too. `fn main()` and the `unsafe external` seam are the
two exemptions, named in one predicate. And [CW7003](../error-catalog.md#cw7003), [CW7004](../error-catalog.md#cw7004) and [CW7005](../error-catalog.md#cw7005) presuppose a block:
a declaration with none collects [CW7002](../error-catalog.md#cw7002) and stops, so one omission is one diagnostic.

### One walk

`declarations()` yields every declaration of a unit with the word for its kind, block or
none. `documented()` filters it, and `check_missing_docs` asks each yield whether it
carries a block. `tests/docs_sweep.py` reads the same walk, and its order is fixed: the
sweep numbers its generated `doc_example_<n>` helpers from it.

### Placement

Placement is load-bearing on one side. The pass needs the merged unit table and nothing
later, and it must run before `instantiate` and `monomorphize`: a generic's block is written
once, and checking it afterwards would report one mistake once per instantiation. The
completeness lint has a second reason to stay there. `register_synthesized_function`
appends a monomorphized clone to a unit's own `ast.functions`, so a lint that ran later
would demand a doc block on every instance the program asked for.

Library units are skipped, both ways. A consumer must not be told about the library
author's doc typos, and must not be warned once per undocumented symbol in every library
it imports.

The test runner's stdlib doc-block gate sets the hidden environment variable
`SUSHI_STDLIB_DOC_GATE=1`. It has no CLI flag, in the style of `SUSHI_SPELLING_GATE`. When
it is set, the pass also checks each BUNDLED stdlib unit, the units whose name is in
`SOURCE_STDLIB_MODULES` (`semantics/stdlib_registry.py`), and `--warn-missing-docs` adds the
completeness lint on them. A unit of a source library stays skipped. The runner compiles
one program that imports every bundled module, because a stdlib module is never built
alone. When the variable is not set, the pass skips every library unit.

## The `unused` pass: dead private declarations and unused imports

The pass runs only under `--warn-unused`. A private name is visible only in its own unit,
so the pass checks each unit alone, over the WRITTEN declarations of the unit.

**[CW1004](../error-catalog.md#cw1004).** The roots of a unit are every `public` declaration, every perk implementation
and every conversion (no call names them), the `unsafe external` blocks, and `fn main()`.
The pass starts at the roots and follows each name that a reached declaration mentions.
A private constant, variable, struct, enum, perk, function or extension method that it
does not reach is dead. Only its own unit can call a private extension method (R4 of
`docs/design/extension-visibility.md`), so the unit is the whole question for it too. A
`foreach` calls `next()` with no written name, so a `foreach` mentions `next`.

**[CW3006](../error-catalog.md#cw3006).** A `use` line is used when the unit mentions a name that the import brings: a
name the imported unit declares or re-exports (`Provider.members`, the walk the
`namespaces` pass reads), the alias of an `as` import, or a method that only the import
makes callable: a public extension of a unit it reaches on a type that the unit does not
declare (R6, the predicate `needs_import` in `semantics/visibility.py`), or a method of a
perk implementation of that unit on such a type, because the import can be what loads it.
A stdlib method on a built-in type (R1) and a public method of the home unit of a type
(R5) need no import, so neither is a use. A `public use` is a root and the pass never reports it. The pass does not
report an import of a library.

The names that a declaration mentions are all the strings in its subtree, but not the doc
blocks and not the string literals. This is more than the declaration uses: a local that
has the same name as a private function keeps the function alive. Thus the lint can miss a
dead declaration, but it does not report a live one. The AST builder folds the size of a
fixed array to a number. It also keeps the name of the constant that the size names, in
`ArrayType.size_name`, and the pass reads that field. The field is not part of the type
identity.

### Placement

The pass runs immediately after `docs`, for the same reason: it must read the written
declarations. The `libraries` step adds the constants of a binary library to a host unit,
and `monomorphize` adds the instances.

Library units are skipped. The test runner's dead-code gate sets the hidden
environment variable `SUSHI_STDLIB_DEAD_GATE=1`. When it is set, the pass also checks each
bundled stdlib unit, in the same way that `SUSHI_STDLIB_DOC_GATE=1` works for the `docs`
pass. The runner compiles the program that imports every bundled module, and each program
under `toolchain/src/`, and fails the run on a [CW1004](../error-catalog.md#cw1004) or [CW3006](../error-catalog.md#cw3006) in one of those files.

## The `externs` pass: FFI signature validation

**File:** `semantics/passes/types/externals.py`

Runs over every unit right after `collect`, so no later pass ever meets an extern the
C ABI cannot carry.

- `validate_external_signatures()` — the C-ABI allowlist ([`CE5003`](../error-catalog.md#ce5003)) and the [`CW5001`](../error-catalog.md#cw5001)
  four-guarantee warning. A variadic libc extern must be declared `var_arg`; a fixed
  declaration reads garbage on Apple arm64.
- `validate_ptr_unit_gate()` — `ptr` is opaque and quarantined to a unit that declares
  the extern block it came from ([`CE5009`](../error-catalog.md#ce5009)).

See `docs/ffi.md`.

## The `libraries` pass: library symbol registration

**File:** `semantics/library_registration.py` (`LibraryRegistration`; the analyzer calls `seed_perks` and `seed_generic_types` ahead of the collect loop, and `register` after it)

Every symbol a linked `.slib` exports enters the same tables the consumer's own `collect`
filled: structs, enums, functions, published constants, export-closure private helpers and
constants, perk implementations, and generic templates.

A constant is registered from SOURCE, whichever list it came from: it has no body to link,
so the manifest carries the declaration's text and the consumer re-parses it. A clash with
the consumer's own name is [`CE0105`](../error-catalog.md#ce0105) for a published constant -- the answer a source library
gives for the same program -- and [`CE5007`](../error-catalog.md#ce5007) for a closure one, which may not be renamed
because the library's own bodies call it.

Placement is load-bearing at both ends. Perk DEFINITIONS are seeded BEFORE the `collect`
loop, because perk-impl collection validates each impl against the visible definitions
([`CE4003`](../error-catalog.md#ce4003)). Perk IMPLEMENTATIONS register here, after the consumer's own (local wins) and
before `instantiate`, so the constraint validator sees them. Generic structs register
before generic enums, because an enum payload may name a struct.

A clash between a library's export-closure helper and a local name is [`CE5007`](../error-catalog.md#ce5007):
local-wins would silently change what the library's monomorphized bodies call. See
`docs/design/libraries.md`.

## The `namespaces` pass: what a unit may write behind a dot

**File:** `semantics/passes/namespaces.py`; the seam is `semantics/namespaces.py`.

Builds one `NamespaceTable` per unit, because an alias is local to the unit that wrote
it. A namespace is a binding from a name to a set of declarations, and four providers
make one:

| Provider | Bound by |
|---|---|
| `ExternalNamespace` | an `unsafe external "C" as <ns>` block |
| `UnitNamespace` | `use "path" as N`, a library unit, a bundled source module |
| `StdlibNamespace` | `use <math> as N` and every other registry module |
| `GenericNamespace` | `use <collections/hashmap> as N` -- the built-in the import activates |

A binding holds the PROVIDER and never the written path: `_inject_library_source`
renames a library's units and leaves `UseStatement.path` alone, so an alias built from
the path would break the moment a library unit imported its sibling.

A stdlib provider also lists the PREDEFINED enums homed at its module.
No unit declares `FileMode`, so no declaration record can say who may write it; the
`collect` pass stamps each of the nine with its home (`EnumType.home_module`, the table
is `passes/collect/enums.py:PREDEFINED_ENUM_HOMES`), `homed_enums` reads the stamp for
the provider, and the typecheck pass's type-position gate (`reject_out_of_scope_type`)
reads it to refuse the bare name where the home is not imported -- the `HashMap` rule.
`StdError` carries no home and stays global.

A provider COMPOSES what its unit re-exports (`unit-namespaces.md` section 8.1, Ruling
7). `_unit_provider` reads the unit's own `public use` statements and builds a provider
for each through `_provider_for`, recursively and with a visited set, so a chain composes
and a cycle terminates; a registry module names its re-exports in `StdlibModule.reexports`
instead. `Provider.reaches` walks the chain once -- the provider, then each re-export in
written order, then theirs -- and both halves of the answer read it: `lookup`/`members`
for the dot, and `_scope_of` for the flat scope, which puts every reached unit, module and
generic into the importer's `UnitScope`. A binding a re-export answers carries the
re-export's own provider, so the back end routes a call through `sh.origin` to the unit
that declares `origin`.

Five rules:

- [`CE3014`](../error-catalog.md#ce3014) -- a `use` below a declaration. The span comes from the AST builder, because
  the `libraries` step above appends a library's constants and private types to a host
  unit's lists and each carries a span from its own file.
- [`CE3013`](../error-catalog.md#ce3013) -- the alias is already bound in this unit: another alias, an FFI namespace,
  or one of its own declarations. `_` is refused too, as the discard name.
- [`CW3004`](../error-catalog.md#cw3004) -- the `as` reached no name. A warning, because a namespace is empty for
  three reasons and only one is a mistake (`unit-namespaces.md` section 4.4). An aliased
  import still brings the public extensions of its unit on types that the unit does not
  declare (R6), so the import itself can still do its work.
- [`CE3016`](../error-catalog.md#ce3016) -- `public use ... as`. A re-export is of names and not of a namespace; the
  alias still binds, so the one fault gets one diagnostic.
- [`CW3005`](../error-catalog.md#cw3005) -- a `public use` whose import brings no PUBLIC name. The provider holds the
  privates too (so `u.hidden` is [CE3005](../error-catalog.md#ce3005) and not "no such name"), and the count here is
  of what the re-export can hand on.

### Why it stands between `libraries` and `ffi-clash`

A provider needs what `collect` and `libraries` produce and nothing later. `collect`
fills a unit's own declarations, the FFI table and the registry modules; a BINARY
library has no AST at all, so its declarations exist only once `libraries` has read the
manifest. `ffi-clash` is the first step that asks whether a name is already taken, and
the first that has to ask it of ONE unit.

### Two seams, in order

This pass answers WHERE a name may be written. `semantics/visibility.py` answers WHETHER
it may be named. So a namespace holds a unit's declarations whatever their visibility,
and a private one is refused at the use site with [`CE3005`](../error-catalog.md#ce3005) -- filtering privates out
would turn "not yours" into "no such name".

The typecheck pass reads the table through `TypeValidator.resolve_namespaced`, and the
`scope` pass through `_is_namespace`. Neither carries its own copy of the local-wins rule.

## The `entrypoint` pass: main's rule

**File:** `semantics/semantic_analyzer.py` (`_check_entrypoint`)

The ONE home of main's rule. It checks four things, in this order:

1. an executable carries a `main` -- [`CE3007`](../error-catalog.md#ce3007);
2. a library carries none -- [`CE3501`](../error-catalog.md#ce3501);
3. `main` returns a BARE integer type (i8-i64, u8-u64), the exit code -- [`CE0106`](../error-catalog.md#ce0106). A
   `| E` on `main`, or a `Result@(T, E)` return, is [`CE0106`](../error-catalog.md#ce0106) too, and a `??` in its body
   is [`CE0131`](../error-catalog.md#ce0131) from the `collect` pass;
4. `main` takes no parameters or exactly one `string[] args` -- [`CE0138`](../error-catalog.md#ce0138). The answer
   sets `main_expects_args` for the back end.

The build kind reaches the analyzer as the `is_library` keyword, the way the library
linker does. The `args` array is a BORROWED view of argv, so moving it is [`CE2410`](../error-catalog.md#ce2410).

CW3003, the foreign-extension warning of the pipeline, is retired (epic #1251): under R6 of
`docs/design/extension-visibility.md`, an extension on a type that a library does not
declare is visible only where its unit is imported.

## The `instantiate` pass: generic instantiation collection

**Files:** `semantics/generics/instantiate/*.py`

### Purpose

Detect which generic instantiations are needed.

### How It Works

1. Traverse AST looking for generic types
2. When `List@(i32)` appears, record it
3. When `.push()` is called on `List@(i32)`, record `List@(i32).push`
4. Build complete set of required instantiations

### A generic call's substituted signature

A call to `fn wrap@(T)(nom T v) Box@(T)` with a string names `Box@(string)`, and the program
may name that instantiation nowhere else: a `match` arm binds the payload, or the value is
passed straight on. The generic-target PERK-implementation copies are cut from
the set this pass collects (a generic-target extension copy is cut at a call, see "A call
cuts an extension copy"), so the pass records the SUBSTITUTED signature of every generic
call it resolves -- the return, the `Result` the declaration wraps it in, and the parameters
-- through the same type walk a concrete declaration gets.

The typecheck pass's inferrer types a generic call through its monomorphized copy, which does
not exist yet, so it answers nothing for one here. A `match` over a generic call therefore
types its arm bindings from that substituted signature, and a generic called with such a
binding is collected like any other.

### The inferrer reads the unit's scope

This pass and the `monomorphize` pass type the arguments of a generic call with
`ReadOnlyInferrer`. Each inferrer gets the namespace table of the unit that holds the call,
the same table the typecheck pass gives that unit. For a monomorphized copy, that unit is
the home unit of the template. Thus `sh.Pt(0)` and `sh.Mark.Off()` behind `use "shapes" as
sh` have the type that `Pt(0)` and `Mark.Off()` have under a flat import, and the argument
solves the type parameter in every pass (#1147). An inferrer with no unit (a copy of an
extension or of a perk implementation) reads only the FFI namespaces.

### Where a type names an instantiation

A type names an instantiation in every position that HOLDS a type, and the reader of those
positions is `type_walk.walk_named_types` -- the one walk over a type. `peek Box@(string)`,
`fn(i32) -> Box@(string)` and a struct field of that function type each name `Box@(string)`.
A hand-written recursion that sees only an array, a struct and an enum misses those
positions, so this pass does not write one.

There are two node handlers over that one walk, because the two readers see two spellings of
one instantiation. `instantiate/type_collection.py` reads a WRITTEN type -- a
`GenericTypeRef`, whose arguments the resolver resolves -- and
`monomorphize/functions.extract_type_instantiations` reads a SUBSTITUTED one, which IS the
instance and carries the base it came from. `tests/unit/test_instantiation_collection_is_total.py`
is the gate: a kind the walk enters needs an answer from both.

### Example

```sushi
let List@(i32) nums = List.new()  # Collect: List@(i32), List@(i32).new
nums.push(42)                     # Collect: List@(i32).push

let List@(string) names = List.new()  # Collect: List@(string), List@(string).new
names.push("Alice")                   # Collect: List@(string).push
```

**Collected instantiations:**
- `List@(i32)`
- `List@(i32).new()`
- `List@(i32).push()`
- `List@(string)`
- `List@(string).new()`
- `List@(string).push()`

## The `monomorphize` pass: generic to concrete

**Files:** `semantics/generics/monomorphize/*.py`

### Purpose

Generate concrete types from generic definitions.

### Process

1. For each collected instantiation (e.g., `List@(i32)`)
2. Substitute type parameters (`T` → `i32`)
3. Create specialized struct/function
4. Add to AST as concrete definition

### A late instantiation

A generic BODY names types the collector never saw: `let Box@(T) b` inside `outer@(T)` is
a `Box@(string)` only once `outer@(string)` is substituted. A copy binds its `let` locals,
its `foreach` binders and its `match` payload bindings while it walks its body for nested
generic calls, so a generic called with one is collected like one called with a parameter,
and it interns every type its `let` annotations name, exactly as it interns its
signature's. A statement that binds a name has an arm of its own in that walk; every other
statement goes through the one node walk, so no statement kind is skipped (#1157,
`tests/unit/test_monomorphize_statement_walk_is_total.py`). The `instantiate` pass binds a
`foreach` binder in the same way (`generics/local_bindings.py`, #1155).

### A late function request

The two early walks are an optimisation, not the only source of function instances. A
call that they cannot type -- an argument that is a field or a method result of a
generic instance that does not exist yet, for example -- is not collected. The `typecheck`
pass then types the call and asks the analyzer for the instance through
`tables.request_function_instance`, as it asks for an extension copy at a call site
(#1155, #1156). The analyzer cuts the copy at once, so the call has its signature, and
the copy's body waits in `Monomorphizer.late_bodies`, because the per-unit loop is
walking the ASTs. After the loop, `_check_array_extensions` puts each waiting body into
its home unit and checks it with the `scope`, `typecheck`, `lift` and `borrow` passes of
that unit (`_check_copies`, the one loop for every copy that is cut after the per-unit
loop), in the same fixpoint as the extension copies. A
function copy that an extension copy's body names waits and is checked in the same way.

A substituted type that is itself an instance -- the `Box<string>` a `Box@(B)` field
becomes under `B := string`, a `Maybe<string>` payload, a `Pair<i32, string>` return --
is published to its table when it is BUILT (`TypeMonomorphizer._publish`). The
collector sees what the program spells; the substitutor is the one place every producer
passes, so publishing there is the worklist, and the analyzer reads the reached
instances back as instantiations for the copies below. An abstract instance, a
method-level `U` still unbound while a generic-target template is cut per receiver, is
not published.

### A call cuts an extension copy

A copy of an extension method is cut where a call reaches it, for all three kinds of
template: an array target (`extend T[] m()`), a method-generic (`extend List@(T) m@(U)()`)
and a generic target (`extend Box@(T) m()`, a static, and a concrete-argument target
`extend Box@(i32) m()`). It is one mechanism. The `typecheck` pass resolves the call, cuts
ONE copy for the instance, the method and the declaring unit, and queues it. After the
per-unit loop, `_check_array_extensions` puts the copy in its home unit and checks it
(`_check_copies`), in the same fixpoint as the function copies above. `foreach` cuts the
`next()` of its iterable in the same way.

So a method that no call reaches has no copy: it is not checked as a copy and it is not
emitted. The `instantiate` pass does not walk the signatures of generic-target extensions,
and the `monomorphize` stage does not cut them. One lookup answers "which template gives
this instance the method M": `target_templates_of`, `target_methods_of` and
`templates_by_instance`, `semantics/generics/extensions.py`. CE2097, CE4007, the CE2106
note and the `share()` help of CE2411 ask it. The call rung is
`instantiate_target_extension`, `passes/types/calls/methods.py`, and it goes through
`_choose_visible` (C3, C4, C5).

An own template wins, also when its target bound fails for the instance: the call is
CE4006 (the rule of `T[]`). A queued copy whose body names an instance that E3 refuses is
dropped, so the program gets [CE2084](../error-catalog.md#ce2084) alone and no
[CE0149](../error-catalog.md#ce0149). The "required by" note of that CE2084 points at the
call that cut the copy.

Two things stay per instance. A perk-implementation copy on a generic target is cut for
each instance, because code that the compiler makes calls it. The E3 judge
(`judge_error_arguments`) runs for each instance and makes no copy.

A late instance gets its perk-implementation copies in
`_cut_templates_for_late_instantiations`, a fixpoint after the function round. Each round
sends the types that its new copies name -- the return, the channel, the parameters and the
`let` annotations -- through `collect_type_instantiations` and monomorphizes them, before
`resolve` and `derive`, and it runs the E3 judge for the new instances (#1146).

### One source, one report

The `typecheck` pass checks each template one time, where it is written (see "The template
check" under that pass). A fault of the template is reported there, and the compiler adds
the template to `SymbolTables.refused_templates`. Every copy of a refused template is
MUTED: `Reporter.enter_body(func)` sets `muted` when the body's `template_id` is in the
set, and `_record` drops each diagnostic of a muted body. A function copy, an extension
copy, a perk-implementation copy and a lifted lambda carry the `template_id`, and the
`typecheck`, `lift` and `borrow` passes all enter a body through `enter_body`, so one
mechanism covers them all. A copy's message names a concrete type (`expected i32, got
string`), so the identity collapse below cannot see that it repeats the template's fault.

Every instance carries the TEMPLATE's spans, and each copy of a CLEAN template is walked
by the per-unit passes as an ordinary function, for the facts that are only visible there:
the code of a consume, the drop set, the derived methods, the layout. The reporter keeps
from such a copy only the per-instance remainder (`docs/design/checked-generics.md` section
8.2): `enter_body` sees the template in `SymbolTables.checked_templates` and marks the body
`instance_only`, and `_record` keeps an error of E3 ([CE2084](../error-catalog.md#ce2084)) or of a lambda parameter
that owns ([CE2094](../error-catalog.md#ce2094)), turns every other error into the internal [CE0149](../error-catalog.md#ce0149), and drops a
warning that repeats a warning of the template at the same code and span. What must not
follow is the COUNT. A fault in the shared body is reported once, at one caret, and not
once per instantiation.

The copy is stamped `instance_of` with the template's name. `Reporter.enter_body(func)`
reads it -- the one seam every per-unit pass calls to say whose body it is about to read,
and the same seam that answers whose FILE the spans belong to -- and sets
`collapse_repeats`, so a diagnostic whose `diagnostic_identity` has already been recorded
is dropped. The identity is the kind, the code, the MESSAGE, the file and the span, so a
finding that genuinely differs by type argument keeps its own message and is still told.
E3 of an opaque `E` is such a finding (ruling R8): `fail@(E)(nom E e) i32 | E` called with
an `i32` and with a plain enum `Color` answers two [CE2084](../error-catalog.md#ce2084)s, one at each call, each with
its own message and a note at the template. A fault that does not depend on the type
argument (`v + 1` on a `T`) is the template's: it is reported one time, at the template
([CE2518](../error-catalog.md#ce2518)), and the copies are muted.

It is not a general de-duplicator. A repeat anywhere else is a bug to be fixed where it is
made, and stays visible.

A lambda in a generic body lifts once per instance, so `LambdaLifter` carries
`instance_of` onto what it lifts. The `borrow` pass checks a template on its check copy
(see "The template check"), and its per-unit walk skips a written template that the
`typecheck` pass checked (`BorrowChecker.run(..., skip=)` reads `checked_template_ids`). A
written template that the driver did not check (a constraint that names no perk, a
template of a consumed library unit) is walked as written, as before.

### The substitution walk is total

`TypeSubstitutor.substitute_expr` and `substitute_statement` replace a type parameter
wherever an instantiated body names one. Both walks are TOTAL over their node union, and
the fall-through is a hard [CE0135](../error-catalog.md#ce0135). A copy is not an acceptable answer: a node with no arm
keeps the type parameter, and the compiler's own bookkeeping name -- `T`, `U` -- reaches
the user.

The walk substitutes every type the SOURCE writes: a cast target, the type arguments of a
call, a lambda's parameters, return and `| E` channel, a `let` annotation and a `foreach`
item annotation. An analysis STAMP is not substituted, because the typecheck pass writes
it after this pass and writes it on the copy. `INERT_EXPRS` names the leaves: a node with
no sub-expression and no type of its own, which a shallow copy answers completely.

`tests/unit/test_substitution_dispatch_is_total.py` is the CI gate, in the shape
`test_borrow_dispatch_is_total.py` gives the borrow pass.

### A refused instantiation

An instantiation that violates a perk constraint is [CE4006](../error-catalog.md#ce4006) ONCE, at the first site that
named it -- the collector records `(span, file)` per instantiation for this -- with a note
at the constraint, which may stand in another file (a stdlib template's). It is built
nowhere: not cached, not published, so no template copy is ever cut for it, and the
whole-program analysis STOPS after the monomorphize step, as it does after
[CE2095](../error-catalog.md#ce2095). The per-unit passes would only have read the same fault back as a [CE2008](../error-catalog.md#ce2008) from
inside a copy's body. A late function request that a constraint refuses is [CE4006](../error-catalog.md#ce4006) at the
call that asked for it, and the analysis goes on; no [CE2061](../error-catalog.md#ce2061) is added to it.

The generic-target extension and perk-implementation copies are first cut from the
collector's set, before the functions are monomorphized. Every instantiation interned after
that -- the tables are the authority on what exists -- gets its copies afterwards, and a
copy's body can instantiate more functions, so this runs to a fixpoint. A perk
constraint on such a type reads the templates as well as the registered copies, so its
answer does not depend on the order the copies were cut in.

### Example

**Generic definitions:**
```sushi
struct Pair@(T, U):
    T first
    U second

extend Pair@(T: Clone, U: Clone) swapped() Pair@(U, T):
    return Pair(self.second.clone(), self.first.clone())

fn first_of@(T: Clone, U)(Pair@(T, U) p) T:
    return p.first.clone()

fn main() i32:
    let Pair@(i32, string) p = Pair(42, "Mostly Harmless")
    let Pair@(string, i32) q = p.swapped()
    println("{q.first} {q.second} {first_of(p)}")
    return 0
```

**What the program asks for:** the struct instances `Pair@(i32, string)` and
`Pair@(string, i32)`, the extension copy `swapped` for each of them (the body of one
copy names the other), and the function instance `first_of@(i32, string)`.

### Names of the instances

A TYPE instance is interned under its name with angle brackets, for example
`Pair<i32, string>`. That name is internal: `display_type()`
(`semantics/generics/type_display.py`) gives the `@(...)` spelling back for a diagnostic.
The LLVM struct of the instance carries the same name.

A FUNCTION instance gets a mangled symbol (`semantics/generics/name_mangling.py`):

| Instance | Symbol |
|---|---|
| `first_of@(i32, string)` in unit `main` | `main$first_of__i32_string` (`mangle_function_name`, with the unit prefix) |
| `swapped` on `Pair@(i32, string)`, declared in unit `main` | `main$Pair__i32_string_swapped` (`extension_symbol`, with the declaring unit) |

An extension method with its own type parameters adds `__` and those type arguments to
the extension symbol. A pack instance adds `.pack` and the pack arity, for example
`.pack2`.

## The `resolve` pass: field and variant type resolution

**File:** `semantics/passes/resolve.py`

### Purpose

Every named type a declaration mentions becomes the one interned type object for that
name. The pass runs AFTER `monomorphize`, so every struct and enum a generic produced is
already in the tables.

### What it resolves

1. **Struct fields** — `resolve_struct_field_types()` walks every entry of the struct
   table and replaces each `UnknownType("Point")` field with the `StructType` (or
   `EnumType`) the tables hold under that name.
2. **Enum variants** — `resolve_enum_variant_types()` does the same for every variant's
   associated types.
3. **Constants** — `resolve_constant_types()` resolves each constant's declared type on
   its record.
4. **Spelled Result returns** — `resolve_function_returns()` interns each
   `fn f() Result@(T, E)` return through `intern_wrapper_enum` and stamps the enum on
   `FuncDef.resolved_result`. `ret` keeps the type as written, because the
   typecheck pass rules on a qualified name in it. The backend reads the stamp through
   `declared_result_of`, its one reader of a function's Result.

```sushi
struct Point:
    i32 x
    i32 y

struct Rectangle:
    Point top_left      # collected as UnknownType("Point")
    Point bottom_right  # resolved here to the interned StructType
```

### Why it matters

Type identity is NOMINAL (`docs/design/type-identity.md`): a `StructType` compares and
hashes on its name alone. Two spellings of one name therefore hash alike and compare
unequal, which poisons the enum table ([CE0126](../error-catalog.md#ce0126)). This pass is what makes the table entry
the single authority, so every later pass reads a resolved type and never rebuilds one.

## The `finite-types` pass: reject a by-value containment cycle

**File:** `semantics/passes/finite_types.py`

A type that contains itself by value has no finite size, and is rejected with [`CE2095`](../error-catalog.md#ce2095). The
escape is indirection: `Own@(T)`, or a dynamic array.

```sushi
struct Node:
    i32 value
    Node next          # CE2095: infinite size

struct Chain:
    i32 value
    Own@(Chain) next   # legal: a pointer has a size
```

Placement is load-bearing on both sides. It runs AFTER `resolve`, because it needs the
resolved field types, and BEFORE `derive`, because a derived hash walks a type by value.
It is also the one pass that STOPS the analysis on failure: every later pass assumes a
finitely-sized type.

The pass owns EVERY inline cycle: a struct field, a fixed-size array element and an
enum payload are all stored inline. A pure enum cycle reads the same [`CE2095`](../error-catalog.md#ce2095) as the struct
twin -- once per cycle, at the first member's declaration, with the chain:

<!-- docs-sweep: error CE2095 -->
```sushi
enum A:
    X(B)               # CE2095: A refers to B refers to A

enum B:
    Y(A)
```

The walk visits every struct before any enum, so a mixed cycle is reported at its struct
whatever the declaration order. The
`derive` pass has no sort: the table it writes holds a lazy emitter per type, and no
reader depends on an order.

A late-interned instance -- a `Tree@(bool)` a call site solves from an argument and no
annotation spells -- is created after this pass ran, so the late interner
(`SemanticAnalyzer._intern_generic_type_refs`) runs the check again from the NEW names
alone. A cycle among older declarations stopped the analysis the first time, so the walk
from the new roots finds every new cycle and repeats none.

## The `derive` pass: hash and clone auto-derivation

**File:** `semantics/passes/derive.py`

### Purpose

Derive `.hash() -> u64` and `.clone()` for each type that can have them. The rules for
`hash()` are below.

**The pass does not register `Eq`, `Ord` or `Display`.** The compiler derives those three
predefined perks too, but it answers them at the call, not from a table this pass fills.
`semantics/generics/contracts.py` holds the walk. `contract_of` applies the HELD rule (what
a field or a payload may be), and `operand_contract` applies the TOP-LEVEL rule (what an
operator, a constraint or a hole may take). The typecheck pass asks them for `==`, `<`, an
interpolation hole, `print`, `println`, the methods `eq`, `compare` and `to_str`, the
constraints `@(T: Eq)`, `@(T: Ord)` and `@(T: Display)`, and the HashMap key rule. It
stamps the answer on the AST (`BinaryOp.operand_type`, `InterpolatedString.display_types`,
`Print`/`PrintLn.display_type`), so the backend never re-derives it. `contract_walk.py` holds
the walk mechanics that `hashing.py` shares. An `extend T with Eq` implementation is read
first and wins. See [Derived contracts](../design/derived-contracts.md).

### Algorithm

**Primitives:**
- Integers: FxHash
- Floats: Normalized to u64, then FxHash
- Strings: FNV-1a
- Booleans: 0 or 1

**Structs:**
```python
hash = FNV_OFFSET_BASIS
for field in fields:
    hash ^= field.hash()
    hash *= FNV_PRIME
return hash
```

**Enums:**
```python
hash = discriminant.hash()
hash ^= variant_data.hash()
return hash
```

**Arrays:**
```python
hash = FNV_OFFSET_BASIS
for element in elements:
    hash ^= element.hash()
    hash *= FNV_PRIME
return hash
```

### Where a derived method lives

The pass writes each method into `SymbolTables.derived_methods`, which belongs to ONE
compilation. It has to: the method closes over the type it was derived for, type
identity is nominal, and two programs compiled in one process that each declare a `Point`
name one key -- so a module-level table would hand the second program the first one's emitter,
closed over the first one's fields, and the first one's answer to "can this be hashed".
Any host that compiles twice in a process reaches that, the pytest layer and a future
language server included.

The table is stored on `EnumTable.derived` and read by name everywhere else
(`SymbolTables.derived_methods`, `TypeValidator.derived_methods`,
`LLVMCodegen.derived_methods`). The enum table is the carrier because the `Result` and
`Maybe` interning seams derive a hash the moment they intern an enum and hold only that
table; every other reader already holds a validator or a codegen.

A lookup that finds nothing falls through to `builtin_registry`, the process-wide table
of what the compiler defines for EVERY program -- `hash`, `to_str`, `to_bits`, `clone`
and the bit methods (`reverse_bits`, `leading_zeros`, `trailing_zeros`) on the
primitives, registered once at import time from `backend/types/primitives/`. Those emitters close over a `BuiltinType` and nothing a
program can change, so one table serves the process.

### Which types get a hash

`hashability_of` (`semantics/generics/hashing.py`) is the one reader. A struct field, an
enum payload and an array element all ask it, and its dispatch is total over the type
kinds: `UNHASHABLE_KINDS` names every kind a derived hash cannot read, `WALKED_KINDS`
names the kinds that answer through what they hold, and `HASHABLE_KINDS` names the
primitives. `tests/unit/test_hashability_dispatch_is_total.py` is the gate.

`LET_THROUGH_KINDS` is the fourth set, and it is EMPTY. A kind belongs in
`UNHASHABLE_KINDS` or in a walk, never in a hole: a kind that no set names would fall out
of the walk and read as hashable, and the backend could then not emit the hash.

A container answers from what it HOLDS, never from its backing fields.
`CONTAINER_HASH_KINDS` names the containers that hash and the backend emitter kind of
each (`container_hash_kind`): a `List@(T)` hashes its elements and then its length, and an
`Own@(T)` hashes its payload, so two allocations of one value hash alike. A
`HashMap@(K, V)` has NO derived hash, because the order of its slots is not the order of
its entries.

A `Hashable` implementation is the override. `hash_override_of` reads the
perk-implementation tables, and an override wins at every held position: a struct field,
an enum payload, an array element, a `List` or `Own` element and a map key. The backend
reads the same order in `emit_value_hash` (`backend/types/value_hash.py`): the override,
then the derived method, then the primitive built-ins.

A type that derives no `hash()` has no such method, so a `.hash()` call on it is [CE2008](../error-catalog.md#ce2008)
at the call site, with the line and the caret.

`.clone()` is refused on a type that declares a resource or holds one ([CE2431](../error-catalog.md#ce2431)); the
escape is `.share()`.

## The `shadowing` pass: an extension may not shadow a built-in

**File:** `semantics/semantic_analyzer.py` (`_check_extension_shadows_builtin`)

All three resolution layers pick a built-in method before an extension method, so an
extension whose name collides with one could never be called. That is [`CE2097`](../error-catalog.md#ce2097) rather than
silent dead code.

Placement is load-bearing at BOTH ends: after `derive`, which registers the struct and enum
`hash`/`clone`, and after the generic-extension table merge, which is where a monomorphized
`extend Box@(i32) hash()` enters the extension table.

A perk implementation is unaffected by construction — an `ExtendWithDef` never enters the
extension table. It is the sanctioned way to replace a built-in. See
`docs/design/method-resolution.md`.

A public method that a Sushi-source stdlib module declares on a built-in type is visible
in every unit (R1 of `docs/design/extension-visibility.md`), so an extension of its name
is [`CE2097`](../error-catalog.md#ce2097) too, with a note at the stdlib declaration.

The same pass then gives [`CW3007`](../error-catalog.md#cw3007) where a unit's own extension
hides a public extension that the unit imports (C3, `hides_import` in
`semantics/visibility.py`). The own extension wins in its unit. Which extension a call
can reach is the question of the `typecheck` pass: `extension_reach` answers it for the
unit of the call, a private extension of another unit is
[`CE3005`](../error-catalog.md#ce3005), a public extension of a unit that the caller does
not import is [`CE3022`](../error-catalog.md#ce3022), and two imported public ones are
[`CE3023`](../error-catalog.md#ce3023).

## The `effects` pass: the destroy-effect summary

**File:** `semantics/passes/borrow/destroy_effects.py`

Which functions destroy a `poke` parameter, transitively. The `borrow` pass reads
the summary to decide whether a call invalidates the caller's value.

Computed ONCE over EVERY unit, because `borrow` runs per unit: a per-unit summary would
make a cross-unit callee invisible.

## The `scope` pass: scope and variable analysis

**File:** `semantics/passes/scope.py`

### Purpose

Track declarations and block scopes, and find the kind of each bare name. Moves, borrows
and destroyed values are the work of the `borrow` pass.

### Responsibilities

1. **Declarations**: register each `let`, parameter and pattern binding in its scope
2. **Scopes**: track block-level scopes
3. **Names**: report a name that reaches nothing ([CE1001](../error-catalog.md#ce1001))
4. **What KIND of name is this**: the bare-name ladder, from `semantics/name_ladder.py`

### The bare-name ladder

`docs/design/unit-namespaces.md` section 8 gives an unqualified name one ordered ladder
over the KINDS it can reach: a local, a constant, a registry constant, a function, a
namespace, a type, nothing. This pass and the typecheck pass both walk it, and the ORDER
lives in `semantics/name_ladder.py` so neither can drift from the other. Each pass answers one question per rung with
its own lookups (`ScopeAnalyzer.is_local` … `is_type`, and `visitor._InferenceRungs`),
`classify` walks them, and `tests/unit/test_bare_name_ladder_is_one.py` is the gate.

This pass owns the two rungs that are not values: a type name in a value position or
under a borrow is [`CE2105`](../error-catalog.md#ce2105), and a name that reaches nothing is [`CE1001`](../error-catalog.md#ce1001).

### Scope Tracking

<!-- docs-sweep: error CE1001 -->
```sushi
fn example() i32:
    let i32 x = 1          # scope 0 (the function)

    if (true):
        let i32 y = 2      # scope 1 (the if block)
        x := y + 3         # OK: x is in an outer scope

    println(y)             # CE1001: use of undeclared identifier 'y'
    return x

fn main() i32:
    return example()
```

## The `typecheck` pass: type validation

**Files:** `semantics/passes/types/*.py`

### Purpose

Ensure all expressions and statements are type-correct.

### Three phases per statement

The pass checks each statement in three phases, in this order:

1. **resolution** (`types/resolution.py`): a written type becomes a resolved type.
2. **propagation** (`types/propagation.py`): a declared type goes down into the value,
   and the pass stamps `resolved_enum_type` and `resolved_struct_type` for the backend.
   Propagation MUST run before validation.
3. **validation** (`types/compatibility.py`, `types/expressions.py`,
   `types/result_validation.py`): the value is checked against the type.

### Modules

All paths are under `semantics/passes/`.

| Module | What it holds |
|---|---|
| `types/__init__.py` | `TypeValidator`, the entry of the pass; `ReadOnlyInferrer` for the early passes |
| `types/visit/` | the three visitors, one module each: `statements.py`, `expressions.py`, `inference.py` (what an expression yields), and `helpers.py` |
| `types/visitor.py` | a facade that re-exports the visitors for the modules outside the pass |
| `types/arguments.py` | `check_arguments`: the one argument check (the count, then each argument) |
| `types/method_registry.py` | `METHOD_TYPE_REGISTRY`: which built-in method family a call belongs to |
| `types/calls/` | call validation: `dotcall.py` (`resolve_dotcall`, what `X.Y(args)` names), `statics.py`, `namespaced.py`, `methods.py`, `user_defined.py`, `generics.py`, `structs.py`, `enums.py` |
| `types/expressions.py` | operators, and the three closed operand rules: `reject_non_bool_condition`, `reject_non_numeric_arithmetic`, `reject_uncomparable_operands` |
| `types/statements.py` | `let`, rebind, `if`, `while`, `foreach`, `return` |
| `types/control_flow.py`, `types/signatures.py` | the return paths ([CE0107](../error-catalog.md#ce0107), [CE0140](../error-catalog.md#ce0140)) and the declaration signatures |
| `types/matching.py` | patterns: each arm checked against the scrutinee, the rows for the checker, [CE2040](../error-catalog.md#ce2040) / [CE2074](../error-catalog.md#ce2074) / [CE2118](../error-catalog.md#ce2118) |
| `types/exhaustiveness.py` | the one exhaustiveness checker for every match: usefulness over a pattern matrix (missing patterns, dead arms) |
| `types/arrays.py` | the built-in array methods, and `reject_non_i32` for an index, a count or a range bound |
| `types/constants.py` | constant definitions |
| `types/public_signatures.py` | the fence over every public signature ([CE3009](../error-catalog.md#ce3009), [CE3010](../error-catalog.md#ce3010), the `ptr` fence) |
| `types/visibility.py` | the pass's view of the visibility seam |
| `types/qualified.py` | a type name behind an alias (`geo.Vec`) |
| `types/externals.py` | the FFI checks (the `externs` and `ffi-clash` passes also run from here) |
| `types/perks.py`, `types/field_matcher.py`, `types/inference.py`, `types/utils.py` | perk checks, named struct arguments, inference helpers, shared helpers |

The type predicates (`is_numeric_type`, `is_integer_type`, `is_float_type` and the others)
are in `semantics/type_predicates.py`.

**FFI call-site resolution.** `TypeValidator._resolve_external_call` asks the namespace
table (`resolve_namespaced`) for the name behind the dot. When the binding is an extern,
it stamps `external_ref = (provider origin, name)` on the node for the backend and gives
back the `ExternalSig`. The call yields the raw C type, with no Result around it, so `??`
on a foreign value is [`CE2507`](../error-catalog.md#ce2507).

### Error types, `??` and conversions

**E3.** The `E` of every `Result@(T, E)` is an error type (`is_error_type`,
`semantics/type_predicates.py`). `semantics/error_types.py` is the seam, and
`reject_non_error_type` is the one emitter of [`CE2084`](../error-catalog.md#ce2084). Three callers reach it: the
written-type walk (`validate_type_name` → `reject_non_error_channels`,
`passes/types/utils.py`), the spelled `| E` of a signature (`validate_error_channel`,
`passes/types/signatures.py`), and each generic instance whose template writes a type
parameter in an `E` position (the monomorphizer, and `resolve_method_generic_extension`
for a method-level type parameter), with a note at the template. A type with no span
came from inference and is not judged.

**What `??` takes.** `validate_try_expression` (`passes/types/expressions.py`) asks for the
enclosing channel first ([`CE0131`](../error-catalog.md#ce0131) in a bare lambda, [`CE2508`](../error-catalog.md#ce2508) outside every body; a bare
written body was refused by the `collect` pass), and then for the operand:
`_unwrapped_arms` accepts a `Result` instance by type identity (`is_instance_of`) and
nothing else, so a `Maybe` and a user enum with `Ok`/`Err` variants are [`CE2507`](../error-catalog.md#ce2507).

**Conversions.** `_error_arms_agree` compares the operand's error type with the
channel's. The same type propagates unchanged; otherwise `find_conversion`
(`semantics/conversions.py`, the one reader of the table, gate
`tests/unit/test_conversion_lookup_is_one.py`) answers the declared conversion, or the
`??` is [`CE2511`](../error-catalog.md#ce2511) with a help that names the declaration. The answer is stamped on the node
(`TryExpr.inferred_conversion`), and the backend calls the conversion before the scope
cleanup of the propagation path. `validate_cast_expression` asks the same question for
`e as T` (`CastExpr.inferred_conversion`, else [`CE2014`](../error-catalog.md#ce2014)). In the check of a template, an
opaque `E` has no conversion: `??` propagates the same `E` and no other ([`CE2511`](../error-catalog.md#ce2511) with a
note at `E`, ruling R8). Each instance asks again on its own copy of the node, where E3
also stays.

**`or_err` and `map_err`.** Both are built-in methods with a method-level type parameter
(`docs/design/error-conversion.md` section 8.3). A family's row in
`MAYBE_METHOD_SIGNATURES` / `RESULT_METHOD_SIGNATURES` is a `BuiltinSignature`
(`semantics/generics/builtin_signatures.py`): the inference hook receives the call,
`solve_builtin_signature` solves `E` or `F` from the argument through
`solve_leading_type_args`, E3 judges the solved type at the call, and the parameter modes
and the `nom self` receiver are stamped for the `borrow` pass. A read-through `or_err` (a
borrowed `Maybe` whose payload owns a resource) outside the operand of a `??` is [`CE2522`](../error-catalog.md#ce2522),
from the `borrow` pass.

### The template check

The pass checks each template one time, where it is written (#1070,
`docs/design/checked-generics.md`): a generic function, a generic-target or array
extension, an extension with a method-level type parameter, and a perk implementation on a
generic or array target. The driver is `passes/types/templates.py`, with three entry
points: `check_function_template`, `check_extension_template` and `check_perk_template`.
`TypeValidator.run()` calls them where it skipped a template before, for a unit that
`_checks_templates` (`semantic_analyzer.py`) admits: a unit of the program (a library's own
units at its `--lib` build included) and a bundled stdlib unit. A consumed library unit is
not checked again.

Each check:

1. builds the opaque form of each type parameter (`opaque_type_params`, the one builder):
   a `TypeParameter` with an owner and its constraints. For an extension or a perk
   implementation, the receiver parameters get their implied and added bounds first
   (`receiver_bounds`, `generics/extension_targets.py`);
2. opens an overlay of the tables (`semantics/template_scope.py`): a read goes through to
   the program table, a write stays in the overlay, and a scratch monomorphizer builds
   `List@(T)`, `Maybe@(T)` and the other instances over `T` there;
3. deep-copies the template and cuts the copy with the opaque substitution (the CHECK
   COPY);
4. checks the check copy with a `TemplateValidator`, whose `in_template_check` flag stops
   every writer that cuts or queues a program copy
   (`tests/unit/test_template_check_writes_no_copy.py`);
5. runs `lift` on the check copy, into a scratch program;
6. runs the `borrow` pass on the check copy and its lifted functions
   (`BorrowChecker.check_template_copy`, handed to the `TypeValidator` as
   `borrow_check_copy`), over the overlay tables: an opaque `T` always moves (R5), so a
   borrowed `T` that is returned, stored or passed on is [CE2411](../error-catalog.md#ce2411), at the template;
7. adds the template to `checked_templates`, and to `refused_templates` when the error
   count grew.

Then it discards the check copy and the overlay. The statement rules ([CE0107](../error-catalog.md#ce0107),
[CE0140](../error-catalog.md#ce0140), [CE2030](../error-catalog.md#ce2030)) run on the check copy too, with its stamps, and
`check_template_statements` runs them on the written body of a template that the driver did
not check. After the per-unit loop, `_reject_opaque_in_program_tables` is the backstop
([CE0148](../error-catalog.md#ce0148)): no instance over an opaque parameter reached a program table.

### Return paths

A body that answers a value or a Result and can reach its end with no `return` is
[`CE0107`](../error-catalog.md#ce0107). The rule is one for a function, a lambda block body and an extension or perk
method. A `~` body with a channel ends with `return Result.Ok(~)`; nothing adds the
`Ok`. A BARE `~` body (a function, a method or a lambda) answers nothing and may reach its
end.

A statement after a statement that always ends the path is [`CE0140`](../error-catalog.md#ce0140): one diagnostic for
each block, at the first dead statement, with a note at the statement that ends the path.

<!-- docs-sweep: error CE0107 -->
```sushi
fn sign(i32 x) i32:        # CE0107: the path with x == 0 has no return
    if (x > 0):
        return 1
    elif (x < 0):
        return -1

fn main() i32:
    return 0
    println("never")       # CE0140: unreachable statement
```

### A field the type does not declare

A name behind a VALUE's dot is a field of that value's type, and one the type does not
declare is [`CE2106`](../error-catalog.md#ce2106), at the read. The four backend [`CE0029`](../error-catalog.md#ce0029) sites are an internal
backstop that no program reaches.

A receiver that carries NO field reads the same rule. An array, a primitive, a
string, a closure and a `ptr` declare nothing, so every name behind their dot is a miss.
`_field_names_of` (`passes/types/expressions.py`) is the one
answer to "which fields does this receiver declare": a struct answers its own list, a
fieldless kind answers the empty list, and everything else answers None.

None means the position is not this rule's. A namespace member, a bare enum variant,
an unresolved name, a generic reference and a receiver the pass could not type all
belong elsewhere, and a false [`CE2106`](../error-catalog.md#ce2106) there would be worse than the [`CE0029`](../error-catalog.md#ce0029) backstop.

An ENUM receiver answers the empty list too, so `pts.get(0).x` over a `Maybe@(Point)` is
[`CE2106`](../error-catalog.md#ce2106). An enum carries variants, and
a variant is reached by a pattern and not by a dot, so the note says that and the help says
how to get at the value: `??`, `.realise(default)` or `match` for a `Result`,
`.realise(default)`, `match` or `.or_err(nom e)??` for a `Maybe` (`is_builtin_wrapper_enum`,
`take_the_value`), `match` for a user enum. A `Maybe@(T)` gets no implicit
unwrap: nothing else in the language has one, it reads against the rule that a condition
is a bool and nothing else, and the `None` arm has no answer.

A METHOD is not a field, and a bound-method value is not supported yet, so `v.probe` with
no parentheses is the same refusal with a note that says so. `_is_a_method` asks the
extension table for what the program declares and `builtin_method_exists` for what the
compiler declares, so `s.len` reads the same note. Otherwise the help quotes
`suggest_member` -- the one reader every position that can miss already uses -- or lists
what the type does declare.

[`CE2102`](../error-catalog.md#ce2102) is the same rule one position over: a name behind a TYPE's dot.

### Type Checking Examples

**Valid:**
```sushi
let i32 x = 42
let i32 y = x + 10  # OK: i32 + i32 → i32
```

**Invalid:**
```sushi
let i32 x = 42
let i32 y = x + "hello"  # CE2509: operator '+' cannot be used with string types
```

**Result Handling:**
```sushi
fn get_value() i32 | StdError:
    return Result.Ok(42)

# CE2505: cannot assign Result@(T, E) to non-Result variable without handling
let i32 x = get_value()

# OK: Use .realise()
let i32 y = get_value().realise(0)
```

## The `lift` pass: lambda lifting

**File:** `semantics/passes/lift.py`

Each lambda literal becomes a top-level function plus a captured environment. It runs
BETWEEN `typecheck` and `borrow`, per unit: the lifted body needs the types `typecheck`
stamped, and the lifted function must be borrow-checked like any other.

**This pass owns the lambda BODY.** A lifted lambda is a function, so its body goes
through `_validate_function` -- the `annotate` hook -- like every other function's, and
the `typecheck` pass does not descend into a lambda body at all. `visit_lambda` keeps
only what no lifted function carries: the function TYPE the enclosing expression needs,
and the capture rules ([CE2094](../error-catalog.md#ce2094)), because lift consumes the capture list into the
environment struct. So the body is checked once, and a fault in it is reported once.

The annotation of one lifted body comes BEFORE the search for a lambda nested in it. The
hook is what types a `Lambda` node, so a nested lambda lifted first would carry no parameter
types, no captures and no channel, and its own body would not be checked.

The environment parameter is a `poke` borrow, never a `peek` one. See
`docs/design/closures.md`.

## The `borrow` pass: borrow checking

**File:** `semantics/passes/borrow/` (`__init__.py` holds `BorrowChecker`)

### Purpose

Enforce memory safety rules for references.

### Rules

1. **A reference-typed `let` is a checked borrow binding**

```sushi
let i32 x = 42
let poke i32 r = x       # a pointer into x's slot, block-scoped
r := r + 1               # x is 43
```

`bind_let_reference` (`passes/borrow/bindings.py`) registers the binding with its full
`ReferenceType`, freezes the owner ([CE2412](../error-catalog.md#ce2412) on a later mutation), and refuses a second
`poke` of the same owner ([CE2403](../error-catalog.md#ce2403)) or a `peek`/`poke` mix ([CE2407](../error-catalog.md#ce2407)). A write through a
`peek` binding is [CE2408](../error-catalog.md#ce2408).

2. **A call borrow ends with the call; a `let`-borrow freezes its owner**

A `peek` or `poke` argument borrows for the call only, so the owner is free again after
the call:

```sushi
fn borrow(peek i32 x) i32:
    return x

fn main() i32:
    let i32 num = 42
    let i32 got = borrow(peek num)
    num := 50              # OK: the borrow ended with the call
    println("{num} {got}")
    return 0
```

A `let` that reads through an owner is a borrow for the rest of its block. A change to
the owner while that borrow lives is [CE2412](../error-catalog.md#ce2412):

<!-- docs-sweep: error CE2412 -->
```sushi
struct Wrapper:
    i32[] items

fn main() i32:
    let Wrapper w = Wrapper(items: from([1, 2, 3]))
    let i32[] view = w.items
    w.items.push(4)        # CE2412: cannot mutate 'w' while 'view' borrows from it
    println(view.len())
    return 0
```

3. **A borrow needs a stable address**

```sushi
fn func(peek i32 x) i32:
    return x

# CE2404: cannot borrow '(5 + 3)': expression has no stable address
# let i32 x = func(peek (5 + 3))

# OK: borrow a variable
let i32 temp = 5 + 3
let i32 x = func(peek temp)
```

4. **Use after a move, use after a destroy**

```sushi
let i32[] arr = from([1, 2, 3])
let i32[] moved = arr
println(arr.len())         # CE2405: cannot borrow moved variable 'arr'
```

```sushi
let i32[] arr = from([1, 2, 3])
arr.destroy()
println(arr.len())         # CE2406: use of destroyed variable 'arr'
```

[CE2406](../error-catalog.md#ce2406) is the one diagnostic for a use after a destroy. The `borrow` pass reads the flow,
and it covers a value of every type. (The `typecheck` pass gave CE2024 for an array at the
same position until #1137; that code is retired.)

5. **A `let` reading through an owner BORROWS, and consuming or invalidating that borrow is an
   error ([CE2411](../error-catalog.md#ce2411), [CE2412](../error-catalog.md#ce2412))**

A `let` does not always take ownership of what it binds. Its OWNERSHIP is derived from the
*provenance* of its source expression -- one of three: `OWNED` (a bare local, or a `nom`
parameter), `BORROWED` (a plain, `peek` or `poke` parameter, a `match`/`foreach` binding,
or any read through a still-live owner -- a field, an index, a container get-out), or
`FRESH` (a constructor, a call result, `.clone()`, a literal). See
`docs/design/ownership-conventions.md` for the full classification table.

<!-- docs-sweep: error CE2411 -->
```sushi
struct Wrapper:
    i32[] items

fn look(i32[] xs) ~:
    println("{xs.len()}")

fn take(nom i32[] xs) ~:
    println("{xs.len()}")

fn main() i32:
    let Wrapper w = Wrapper(items: from([1, 2, 3]))
    let i32[] borrowed = w.items  # borrowed BORROWS from w; no allocation happens

    look(borrowed)                # OK: a plain parameter is a borrow too
    take(nom borrowed.clone())    # OK: the callee takes an independent copy
    take(nom borrowed)            # CE2411: cannot consume 'borrowed': another owner keeps this value
    return 0
```

The borrow lasts to the end of the block that declared it. Mutating, freeing, or rebinding `w`
while `borrowed` is still live is **[CE2412](../error-catalog.md#ce2412)**; handing `borrowed` itself to a `nom`
parameter or another consuming position is **[CE2411](../error-catalog.md#ce2411)**. A value binding and a reference binding (rule 1) are tracked the same way; the
reference binding adds the WRITE path -- a store through it reaches the owner.

6. **A loop body is checked in rounds, and `break` / `continue` end a path**

`check_loop_body` (`passes/borrow/statements.py`) checks a `while` or `foreach` body in two
rounds and returns a `LoopFlow` (`passes/borrow/flow.py`):

- `entry`: the facts before the loop.
- `fixed_point`: `entry` joined with the facts that reach the back edge after round 1. Round 2,
  the reporting round, starts here, so a move in one round is seen by the next.
- `back_edge`: the facts on the paths that reach the next round -- the end of the body and every
  `continue`. A `foreach` reads its iterator's invalidation ([CE2412](../error-catalog.md#ce2412)) from these paths only.
- `exit`: `fixed_point` joined with every `break` path. This is the state after the loop, so a
  move before a `break` is still seen after the loop.

A `break` or a `continue` ends its path for the joins of an `if` or a `match` inside the body
(`terminates(..., leaves_round=True)`). A `foreach` over an owned temporary (`a.clone().iter()`)
freezes nothing (`walks_a_temporary`). When the iterator reports a move of its container that
came round the back edge, the owner's state records it (`move_reported_by`), and the move gives
no second [CE2405](../error-catalog.md#ce2405) in round 2.

### Borrow Tracking

`BorrowChecker` (`passes/borrow/__init__.py`) keeps the state of one callable:

- `borrow_state: Dict[str, BorrowState]`: one record for each name
  (`passes/borrow/state.py`). A record holds the `peek` and `poke` counts, `is_moved`,
  `is_destroyed`, the owner a `let`-borrow reads out of (`borrows_from`), where the name
  was moved or invalidated, and the kind of the name (a `let`-borrow, a borrow parameter,
  a unit variable, main's `args`).
- `active_borrows: Set[str]`: the names that a call borrows in the current statement. It
  is cleared for each statement.
- `_scope_binding_borrows`: one frame for each open block, so a `let`-borrow ends with its
  block.
- `callee_modes`: the parameter modes of each callee, from `semantics/param_modes.py`.

The checker does not raise. Each finding goes through the reporter as a registered code
(`self.err.emit(...)`), so one run reports every fault.

## Pass Interdependencies

```
whole program, once:

  collect → docs → unused → externs → libraries → namespaces → ffi-clash
     → entrypoint → instantiate → monomorphize → resolve → finite-types → derive
     → shadowing → effects

then per unit, in one loop:

  scope → typecheck → lift → borrow
```

Each turn of that loop reports into a reporter of its own, and `_merge_unit` drains it
into the program reporter through `in_source_order` (`internals/report.py`). The four
passes each walk the unit whole, so what they emit is in PASS order and a reader wants
the FILE: a fault the `lift` pass found in a lambda body would otherwise stand behind
every fault the `typecheck` pass found. A file keeps the place its first
diagnostic gave it -- the order the passes reached the files in is information, and
alphabetical is not -- and the sort is stable, so two findings on one caret keep pass
order.

**Dependencies:**
- `docs` needs `collect` (the merged unit table), and must run BEFORE `instantiate` and
  `monomorphize`, or one mistake in a generic's block is reported once per instantiation,
  and `--warn-missing-docs` demands a block on every monomorphized clone
- `unused` needs `collect`, and runs before `libraries` and `monomorphize`, because it reads
  the WRITTEN declarations of each unit
- `externs`, `libraries` and `entrypoint` need `collect` (the tables and the signatures)
- `namespaces` needs `collect` (a unit's declarations, the FFI table, the registry) and
  `libraries` (a binary library's declarations arrive from a manifest and nowhere else);
  `scope` and `typecheck` read the table it builds
- `instantiate` needs `libraries` (a library template must be visible to instantiate at
  the consumer)
- `monomorphize` needs `instantiate` (the set of instantiations to generate)
- `resolve` needs `monomorphize` (every struct and enum a generic produced must exist)
- `finite-types` needs `resolve` (the resolved field types), and STOPS the analysis on
  failure
- `derive` needs `finite-types` (a derived hash walks a type by value, so the type must
  have a finite size)
- `shadowing` needs `derive` (the auto-derived pair must be registered before a collision
  can be seen)
- `scope` needs `collect` (function signatures), and runs AFTER every whole-program pass,
  because the body walks need concrete, monomorphized types
- `typecheck` needs `resolve` and `scope`
- `lift` needs `typecheck` (the lifted body reads its stamps)
- `borrow` needs `typecheck` (the stamps) and `effects` (the cross-unit summary)

## Error Examples by Pass

**`scope`:**
- [CE1001](../error-catalog.md#ce1001): use of undeclared identifier
- [CE2105](../error-catalog.md#ce2105): a type name in a value position

**`typecheck`:**
- [CE2002](../error-catalog.md#ce2002) and the other CE2xxx codes: type mismatch
- [CE2009](../error-catalog.md#ce2009): wrong argument count, `.realise()` included
- [CE2505](../error-catalog.md#ce2505): cannot assign Result@(T, E) to non-Result variable without handling
- [CE0107](../error-catalog.md#ce0107): a path with no `return`; [CE0140](../error-catalog.md#ce0140): an unreachable statement

**`borrow`:**
- [CE2405](../error-catalog.md#ce2405): cannot borrow moved variable
- [CE2406](../error-catalog.md#ce2406): use of destroyed variable
- [CE2411](../error-catalog.md#ce2411): cannot consume a borrow
- [CE2412](../error-catalog.md#ce2412): cannot mutate an owner while a `let`-borrow lives
- [CE2404](../error-catalog.md#ce2404): a borrow of an expression with no stable address

---

**See also:**
- [Architecture](architecture.md) - Overall compiler design
- [Backend](backend.md) - Code generation details
