# Semantic Analysis Passes

[← Back to Documentation](../index.md) | [Architecture](architecture.md)

Detailed documentation of Sushi's multi-pass semantic analysis pipeline.

## Pass Overview

The passes have NAMES, not numbers. A number goes out of order the moment a pass is
inserted between two others, which is what the old numbered scheme did to itself.
`SemanticAnalyzer.check()` (`semantics/semantic_analyzer.py`) is the code authority on the
order; this list mirrors it.

| Pass | What it does | Where |
|---|---|---|
| `collect` | constants, function headers, generic types, externals | `semantics/passes/collect/` |
| `docs` | check each doc block against its declaration (CE7001-CE7008, CW7001), and its completeness under `--warn-missing-docs` (CW7002-CW7006) | `semantics/passes/docs.py` |
| `externs` | extern signatures (CE5003), `CW5001`, the `ptr` unit gate (CE5009) | `semantics/passes/types/externals.py` |
| `libraries` | register every symbol a `.slib` exports | `semantics/semantic_analyzer.py` |
| `namespaces` | bind what each unit may write behind a dot, and what its flat scope holds (CE3013, CE3014, CE3016, CW3004, CW3005) | `semantics/passes/namespaces.py` |
| `ffi-clash` | reject an `unsafe external` that names a symbol this build defines (CE5013) | `semantics/passes/types/externals.py` |
| `entrypoint` | `main()`'s signature and its `string[] args` | `semantics/semantic_analyzer.py` |
| `instantiate` | collect every generic instantiation the program asks for | `semantics/generics/instantiate/` |
| `monomorphize` | generic definitions become concrete instances | `semantics/generics/monomorphize/` |
| `resolve` | struct field and enum variant types become concrete | `semantics/passes/resolve.py` |
| `finite-types` | reject a type that contains itself by value (CE2095) | `semantics/passes/finite_types.py` |
| `derive` | auto-derive `hash()` and `clone()` | `semantics/passes/derive.py` |
| `shadowing` | reject an extension method that collides with a built-in (CE2097) | `semantics/semantic_analyzer.py` |
| `effects` | which functions destroy a `poke` parameter, transitively | `semantics/passes/borrow/destroy_effects.py` |
| `scope` | scope and variable analysis | `semantics/passes/scope.py` |
| `typecheck` | type validation and inference | `semantics/passes/types/` |
| `lift` | each lambda becomes a top-level function plus an environment | `semantics/passes/lift.py` |
| `borrow` | borrow checking | `semantics/passes/borrow/` |

The last four run per unit, in one loop, so the whole-program passes above them see every
unit before any function body is walked.

`semantics/passes/const_eval.py` is **not** a pass. The `typecheck` pass and the backend
both call it as a helper.

### The word "phase"

"Phase" names the three sub-steps of the `typecheck` pass per statement — resolution →
propagation → validation. It never names a pass. Where you meet `#300 phase 2` or
"Phase 9" in the tree, those are work phases of an issue or of a past project, not passes.

## The `collect` pass: headers and constants

**Files:** `semantics/passes/collect/*.py`

### Purpose

Collect global definitions before analyzing function bodies.

### Responsibilities

1. **Constants**: Parse and register constant definitions, and unit variables (`var`) in
   the same table with `is_var` set (`docs/design/unit-storage.md`)
2. **Function Signatures**: Collect return types and parameters
3. **Generic Types**: Register struct and enum definitions
4. **Symbol Table**: Build initial global scope
5. **Visibility**: Record who declared what, and whether it says `public`
6. **Extension methods**, instance and STATIC alike, with `is_static` carried on the
   collected signature (`docs/design/method-resolution.md`). The two refusals a static
   brings are structural and so are decided here: a receiver named in the signature or
   the body is `CE0134`, and a static spelling a variant of the enum it extends is
   `CE2103`. A `static` inside a perk implementation is `CE4014`, in the perk collector.

### A unit is collected after the units it depends on

The compilation order (`UnitManager.topological_sort`) yields every unit AFTER the units
it depends on. The walk itself counts in-degree as "how many units depend on me" and so
produces the opposite; the result is reversed once, and the direction is a ruling
(`docs/design/unit-namespaces.md` section 13.2): a unit's scope is built from what its own
imports declare, so the declaring unit has to be collected already.

A source library's units and a bundled Sushi-source stdlib module are injected as ordinary
compilation units, and `build_dependency_graph` records the edge that an import of one
creates. That is why a library unit comes first without being told to. A binary `.slib`
matches no unit and adds no edge, because it has no unit to compile.

Two hand-patches retired with the order. Library units were pulled to the front of the
collect loop, and every unit's perk DEFINITIONS were swept up ahead of the loop so that an
implementation could meet the two rules that read the perk table -- the perk exists
(`CE4003`), and its marker lets this unit implement it (`CE4011`). A perk declared next
door is in the table when the implementing unit is reached, so neither patch is needed.

### Example

```sushi
const i32 MAX = 100  # Register constant

struct Pair@(T, U):   # Register generic struct
    T first
    U second

fn add(i32 a, i32 b) i32:  # Register signature
    return Result.Ok(a + b)
```

**Output:**
- `constants = {'MAX': 100}`
- `functions = {'add': FunctionSignature(...)}`
- `generic_types = {'Pair': GenericStruct(...)}`

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
never be shown its own code measured against somebody else's declaration -- which is what
"cannot call private function 'helper'" said to the unit that wrote `helper` itself.

The rules that read it live where the use is: `passes/types/visibility.py` for a call and a
bare constant read, the type funnel for a named type, the collect pass itself for a
TYPE declaration that collides with a library's (CE3011) or a declaration that promises
something about a private perk (CE4011), and `passes/types/public_signatures.py` for the leak fence (CE3009,
CE3010). `docs/design/visibility.md` is normative.

### One reporter, many files

This pass is the only whole-program pass that walks every unit's AST while sharing ONE
reporter -- the per-unit passes each build their own through `_unit_reporter(unit)`. A span
is meaningless without the file it came from, so `CollectorPass.run` names the unit it is
reading (`Reporter.origin`), and `Reporter._record` stamps it onto every diagnostic the pass
raises. Without it, a declaration in a non-entry unit was reported against the ENTRY file:
the head line named a line the user did not write, and the caret landed on whatever text sat
at that column (#473).

A `first defined here` note needs one thing more. It points at a table entry, and the entry
may have been made while a DIFFERENT unit was being collected, so each record remembers its
own file: `files` beside `spans` on the struct and enum tables, `PerkTable.files`, and a
`filename` field on `FuncSig`, `ConstSig` and `ExternalSig`. `note_first_declaration` is the
one place that reads them.

### Limitations

Constants can only be literal values (no expressions).

### FFI External Collection

`semantics/passes/collect/externals.py` builds an `ExternalTable` from each
`unsafe external "C"` block: a namespace-keyed map of `ExternalSig` (Sushi name,
link name, param/return types). It rejects duplicate names within a namespace and
emits `CE5001` when a link-name clashes with a `RESERVED_EXTERNS` built-in of a
different signature. The table is exposed as `collector.externals` and threaded
into the scope pass, the type validator, and the backend.

The C-ABI allowlist check (`CE5003`) and the `CW5001` four-guarantee warning live
in `semantics/passes/types/externals.py::validate_external_signatures`, run right
after collection.

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
    return Result.Ok(a + b)
```

Eight errors and one warning, all of them always on. `check_docs` is the entry point:

| Condition | Code |
|---|---|
| a `- Parameter` tag names no parameter of this callable | CE7001 |
| two `- Parameter` tags for one name | CE7002 |
| a second `- Returns:` or `- Errors:` | CE7003 |
| an unrecognised tag keyword | CE7004 |
| a block in a body that is not the first item | CE7005 |
| a declaration with a block above it and a block first in its body | CE7006 |
| an `- Example:` tag that introduces no fenced block | CE7007 |
| a fence inside a block that is never closed | CE7008 |
| a block that documents nothing | CW7001 |

Every check finds a claim that CONTRADICTS the declaration, which is why none of them is
behind a flag.

### Behind `--warn-missing-docs`

Completeness is the other half, and it is policy rather than contradiction, so the CALLER
decides. `check_missing_docs` is a second entry point, and `_check_multi_file` runs it in
the same loop only when the flag is set. The pass holds no policy flag of its own: that is
what keeps the always-on side unable to drift behind a flag.

| Condition | Code |
|---|---|
| a declaration with no doc block | CW7002 |
| a documented callable with a parameter that no `- Parameter` tag names | CW7003 |
| a documented callable that returns a value, with no `- Returns:` | CW7004 |
| a documented function that declares `\| E`, with no `- Errors:` | CW7005 |
| a unit with no doc block | CW7006 |

Three rules shape the table. Every declaration is asked, public and private, because an
internal API is documented surface too. `fn main()` and the `unsafe external` seam are the
two exemptions, named in one predicate. And CW7003, CW7004 and CW7005 presuppose a block:
a declaration with none collects CW7002 and stops, so one omission is one diagnostic.

### One walk

`declarations()` yields every declaration of a unit with the word for its kind, block or
none. `documented()` filters it, and `check_missing_docs` asks each yield whether it
carries a block. `tests/docs_sweep.py` reads the same walk, and its order is fixed: the
sweep numbers its generated `doc_example_<n>` helpers from it.
`tests/unit/test_declaration_walk_is_total.py` is the gate.

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

## The `externs` pass: FFI signature validation

**File:** `semantics/passes/types/externals.py`

Runs over every unit right after `collect`, so no later pass ever meets an extern the
C ABI cannot carry.

- `validate_external_signatures()` — the C-ABI allowlist (`CE5003`) and the `CW5001`
  four-guarantee warning. A variadic libc extern must be declared `var_arg`; a fixed
  declaration reads garbage on Apple arm64.
- `validate_ptr_unit_gate()` — `ptr` is opaque and quarantined to a unit that declares
  the extern block it came from (`CE5009`).

See `docs/ffi.md`.

## The `libraries` pass: library symbol registration

**File:** `semantics/semantic_analyzer.py` (`_register_library_*`)

Every symbol a linked `.slib` exports enters the same tables the consumer's own `collect`
filled: structs, enums, functions, published constants, export-closure private helpers and
constants, perk implementations, and generic templates.

A constant is registered from SOURCE, whichever list it came from: it has no body to link,
so the manifest carries the declaration's text and the consumer re-parses it. A clash with
the consumer's own name is `CE0105` for a published constant -- the answer a source library
gives for the same program -- and `CE5007` for a closure one, which may not be renamed
because the library's own bodies call it.

Placement is load-bearing at both ends. Perk DEFINITIONS are seeded BEFORE the `collect`
loop, because perk-impl collection validates each impl against the visible definitions
(`CE4003`). Perk IMPLEMENTATIONS register here, after the consumer's own (local wins) and
before `instantiate`, so the constraint validator sees them. Generic structs register
before generic enums, because an enum payload may name a struct.

A clash between a library's export-closure helper and a local name is `CE5007`:
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

A stdlib provider also lists the PREDEFINED enums homed at its module (#574, Ruling 3).
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

- `CE3014` -- a `use` below a declaration. The span comes from the AST builder, because
  the `libraries` step above appends a library's constants and private types to a host
  unit's lists and each carries a span from its own file.
- `CE3013` -- the alias is already bound in this unit: another alias, an FFI namespace,
  or one of its own declarations. `_` is refused too, as the discard name.
- `CW3004` -- the `as` reached no name. A warning, because a namespace is empty for
  three reasons and only one is a mistake (`unit-namespaces.md` section 4.4).
- `CE3016` -- `public use ... as`. A re-export is of names and not of a namespace; the
  alias still binds, so the one fault gets one diagnostic.
- `CW3005` -- a `public use` whose import brings no PUBLIC name. The provider holds the
  privates too (so `u.hidden` is CE3005 and not "no such name"), and the count here is
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
and a private one is refused at the use site with `CE3005` -- filtering privates out
would turn "not yours" into "no such name".

The typecheck pass reads the table through `TypeValidator.resolve_namespaced`, and the
`scope` pass through `_is_namespace`. Both used to carry their own copy of the
local-wins rule.

## The `entrypoint` pass: `main()`'s signature

**File:** `semantics/semantic_analyzer.py` (`_check_main_function_args_multi_file`)

`main` takes no parameters or exactly one `string[] args`. The `args` array is a BORROWED
view of argv, so moving it is `CE2410`.

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
passed straight on. The generic-target extension and perk-implementation copies are cut from
the set this pass collects, so the pass records the SUBSTITUTED signature of every generic
call it resolves -- the return, the `Result` the declaration wraps it in, and the parameters
-- through the same type walk a concrete declaration gets (#549, #555).

The typecheck pass's inferrer types a generic call through its monomorphized copy, which does
not exist yet, so it answers nothing for one here. A `match` over a generic call therefore
types its arm bindings from that substituted signature, and a generic called with such a
binding is collected like any other (#549).

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
a `Box@(string)` only once `outer@(string)` is substituted. A copy binds its `let` locals
while it walks its body for nested generic calls, so a generic called with one is collected
like one called with a parameter, and it interns every type its `let` annotations name,
exactly as it interns its signature's.

A substituted type that is itself an instance -- the `Box<string>` a `Box@(B)` field
becomes under `B := string`, a `Maybe<string>` payload, a `Pair<i32, string>` return --
is published to its table when it is BUILT (`TypeMonomorphizer._publish`, #577). The
collector sees what the program spells; the substitutor is the one place every producer
passes, so publishing there is the worklist, and the analyzer reads the reached
instances back as instantiations for the copies below. An abstract instance, a
method-level `U` still unbound while a generic-target template is cut per receiver, is
not published.

### A refused instantiation

An instantiation that violates a perk constraint is CE4006 ONCE, at the first site that
named it -- the collector records `(span, file)` per instantiation for this -- with a note
at the constraint, which may stand in another file (a stdlib template's). It is built
nowhere: not cached, not published, so no template copy is ever cut for it, and the
whole-program analysis STOPS after the monomorphize step, the CE2095 precedent (#579,
Ruling 4). The per-unit passes would only have read the same fault back as a CE2008 from
inside a copy's body.

The generic-target extension and perk-implementation copies are first cut from the
collector's set, before the functions are monomorphized. Every instantiation interned after
that -- the tables are the authority on what exists -- gets its copies afterwards, and a
copy's body can instantiate more functions, so this runs to a fixpoint (#555). A perk
constraint on such a type reads the templates as well as the registered copies, so its
answer does not depend on the order the copies were cut in.

### Example

**Generic definition:**
```sushi
struct Pair@(T, U):
    T first
    U second

extend Pair@(T, U) swap@(T, U)() Pair@(U, T):
    return Result.Ok(Pair(first: self.second, second: self.first))
```

**After monomorphization for `Pair@(i32, string)`:**
```sushi
struct Pair__i32__string:
    i32 first
    string second

extend Pair__i32__string swap() Pair__string__i32:
    return Result.Ok(Pair__string__i32(first: self.second, second: self.first))
```

### Name Mangling

- `Pair@(i32, string)` → `Pair__i32__string`
- `List@(T)` → `List__i32`, `List__string`
- Nested: `Maybe@(Maybe@(i32))` → `Maybe__Maybe__i32`

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
unequal, which poisons the enum table (CE0126). This pass is what makes the table entry
the single authority, so every later pass reads a resolved type and never rebuilds one.

## The `finite-types` pass: reject a by-value containment cycle

**File:** `semantics/passes/finite_types.py`

A type that contains itself by value has no finite size, and is rejected with `CE2095`. The
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
resolved field types, and BEFORE `derive`, whose topological sort would report the same
cycle as an internal error (`CE0128`). It is also the one pass that STOPS the analysis on
failure: every later pass assumes a finitely-sized type.

## The `derive` pass: hash and clone auto-derivation

**File:** `semantics/passes/derive.py`

### Purpose

Auto-generate `.hash() -> u64` and `.clone()` for all types.

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

### Limitations

Nested arrays cannot be hashed (type system constraint).

## The `shadowing` pass: an extension may not shadow a built-in

**File:** `semantics/semantic_analyzer.py` (`_check_extension_shadows_builtin`)

All three resolution layers pick a built-in method before an extension method, so an
extension whose name collides with one could never be called. That is `CE2097` rather than
silent dead code (#239).

Placement is load-bearing at BOTH ends: after `derive`, which registers the struct and enum
`hash`/`clone`, and after the generic-extension table merge, which is where a monomorphized
`extend Box@(i32) hash()` enters the extension table.

A perk implementation is unaffected by construction — an `ExtendWithDef` never enters the
extension table. It is the sanctioned way to replace a built-in. See
`docs/design/method-resolution.md`.

## The `effects` pass: the destroy-effect summary

**File:** `semantics/passes/borrow/destroy_effects.py`

Which functions destroy a `poke` parameter, transitively (#168). The `borrow` pass reads
the summary to decide whether a call invalidates the caller's value.

Computed ONCE over EVERY unit, because `borrow` runs per unit: a per-unit summary would
make a cross-unit callee invisible.

## The `scope` pass: scope and variable analysis

**File:** `semantics/passes/scope.py`

### Purpose

Track variable lifetimes, scopes, and ownership.

### Responsibilities

1. **Variable Declarations**: Register all `let` declarations
2. **Scope Analysis**: Track block-level scopes
3. **Move Semantics**: Mark variables as moved
4. **Usage Tracking**: Detect undefined variables

### Variable States

- **Declared**: Variable exists in scope
- **Moved**: Ownership transferred, cannot use
- **Destroyed**: Explicitly destroyed via `.destroy()`
- **Borrowed**: Temporarily passed by reference

### Examples

**Valid:**
```sushi
let i32 x = 42
let i32 y = x  # OK: primitives copy
```

**Invalid:**
```sushi
let i32[] arr = from([1, 2, 3])
let i32[] moved = arr
println(arr.len())  # ERROR CE2405: Use of moved variable 'arr'
```

### Scope Tracking

```sushi
fn example() i32:
    let i32 x = 1  # Scope 0 (function)

    if (true):
        let i32 y = 2  # Scope 1 (if block)
        x := 3         # OK: x from outer scope

    # println(y)  # ERROR CE1003: Undefined variable 'y'

    return Result.Ok(0)
```

## The `typecheck` pass: type validation

**Files:** `semantics/passes/types/*.py`

### Purpose

Ensure all expressions and statements are type-correct.

### Modular Type Checking

**types/utils.py** - Type utilities
- `is_numeric()`, `is_integer()`, `is_float()`
- Type comparison and normalization

**types/inference.py** - Type inference
- Infer types from literals
- Propagate types through expressions

**FFI call-site resolution** - `type_visitor.py::visit_dotcall` (both the
`ExpressionValidator` and `TypeInferenceVisitor`) has a new first branch: when the
receiver is a `Name` that is a registered external namespace **and not a bound
local** (locals shadow namespaces), it resolves the `ExternalSig`, validates
argument count/types, sets the inferred return type to the raw C type (no Result
wrapping), and annotates the node with `external_ref = (ns, name)` for the
backend. `??` on a raw foreign value therefore falls out as the existing
`CE2507`.

**types/compatibility.py** - Type compatibility
- Check if type A can be assigned to type B
- Handle Result@(T) unwrapping

**types/expressions.py** - Expression type checking
- Binary operators (+, -, *, /, %, ==, !=, <, >, and, or)
- Unary operators (-, not)
- Function calls
- Array access
- Struct field access

**types/matching.py** - Pattern match validation
- Exhaustiveness checking
- Variant data extraction
- Nested pattern support

**types/calls.py** - Function call validation
- Argument count matching
- Parameter type compatibility
- Return type inference

**types/statements.py** - Statement validation
- Variable declarations
- Rebinding
- Control flow (if, while, foreach)
- Return statements

### Type Checking Examples

**Valid:**
```sushi
let i32 x = 42
let i32 y = x + 10  # OK: i32 + i32 → i32
```

**Invalid:**
```sushi
let i32 x = 42
let i32 y = x + "hello"  # ERROR CE2xxx: Cannot add i32 and string
```

**Result Handling:**
```sushi
fn get_value() i32:
    return Result.Ok(42)

# ERROR CE2505: Cannot assign Result@(i32) to i32
let i32 x = get_value()

# OK: Use .realise()
let i32 y = get_value().realise(0)
```

## The `lift` pass: lambda lifting

**File:** `semantics/passes/lift.py`

Each lambda literal becomes a top-level function plus a captured environment. It runs
BETWEEN `typecheck` and `borrow`, per unit: the lifted body needs the types `typecheck`
stamped, and the lifted function must be borrow-checked like any other.

The environment parameter is a `poke` borrow, never a `peek` one. See
`docs/design/closures.md`.

## The `borrow` pass: borrow checking

**File:** `semantics/passes/borrow/` (`__init__.py` holds `BorrowChecker`)

### Purpose

Enforce memory safety rules for references.

### Rules

1. **A reference-typed `let` is a checked borrow binding (#409; CE2413 retired)**

```sushi
let i32 x = 42
let poke i32 r = x       # a pointer into x's slot, block-scoped
r := r + 1               # x is 43
```

`bind_let_reference` (`passes/borrow/bindings.py`) registers the binding with its full
`ReferenceType`, freezes the owner (CE2412 on a later mutation), and refuses a second
`poke` of the same owner (CE2403) or a `peek`/`poke` mix (CE2407). Before #409 the form
was rejected as CE2413 rather than compiled as an unchecked
alias.

2. **Cannot move/rebind while borrowed**

```sushi
fn borrow(peek i32 x) i32:
    return Result.Ok(x)

fn main() i32:
    let i32 num = 42
    let i32 borrowed = borrow(peek num).realise(0)
    # num := 50  # ERROR CE1007: Cannot rebind while borrowed
    return Result.Ok(0)
```

3. **Cannot borrow temporaries**

```sushi
# ERROR: Cannot borrow temporary expression
# let i32 x = func(peek (5 + 3))

# OK: Use variable
let i32 temp = 5 + 3
let i32 x = func(peek temp)
```

4. **Use-after-destroy detection**

```sushi
let i32[] arr = from([1, 2, 3])
arr.destroy()
# println(arr.len())  # ERROR CE2406: Use of destroyed variable 'arr'
```

5. **A `let` reading through an owner BORROWS, and consuming or invalidating that borrow is an
   error (CE2411, CE2412)**

A `let` does not always take ownership of what it binds. Its OWNERSHIP is derived from the
*provenance* of its source expression -- one of three: `OWNED` (a bare local or a by-value
parameter), `BORROWED` (a `match`/`foreach` binding, a `peek`/`poke` parameter, or any read
through a still-live owner -- a field, an index, a container get-out), or `FRESH` (a constructor, a
call result, `.clone()`, a literal). See `docs/design/ownership-conventions.md` for the full
classification table.

```sushi
struct Wrapper:
    i32[] items

fn take(i32[] xs) ~:
    println("{xs.len()}")
    return Result.Ok(~)

fn main() i32:
    let Wrapper w = Wrapper(items: from([1, 2, 3]))
    let i32[] borrowed = w.items  # borrowed BORROWS from w; no allocation happens

    # ERROR CE2411: cannot consume 'borrowed': another owner keeps this value
    # take(borrowed)

    take(borrowed.clone())  # OK: an independent copy
    return Result.Ok(0)
```

The borrow lasts to the end of the block that declared it. Mutating, freeing, or rebinding `w`
while `borrowed` is still live is **CE2412**; handing `borrowed` itself to a by-value sink is
**CE2411**. A value binding and a reference binding (rule 1) are tracked the same way; the
reference binding adds the WRITE path -- a store through it reaches the owner.

### Borrow Tracking

**Data structures:**
```python
active_borrows: Dict[str, BorrowId] = {}
destroyed_variables: Set[str] = set()
```

**On borrow:**
```python
if var in active_borrows:
    raise BorrowError("Already borrowed")
active_borrows[var] = borrow_id
```

**On borrow end (function return):**
```python
del active_borrows[var]
```

**On destroy:**
```python
destroyed_variables.add(var)
```

**On usage:**
```python
if var in destroyed_variables:
    raise UseAfterDestroyError("CE2406")
if var in moved_variables:
    raise UseAfterMoveError("CE2405")
```

## Pass Interdependencies

```
whole program, once:

  collect → docs → externs → libraries → namespaces → ffi-clash → entrypoint
     → instantiate → monomorphize → resolve → finite-types → derive → shadowing
     → effects

then per unit, in one loop:

  scope → typecheck → lift → borrow
```

**Dependencies:**
- `docs` needs `collect` (the merged unit table), and must run BEFORE `instantiate` and
  `monomorphize`, or one mistake in a generic's block is reported once per instantiation,
  and `--warn-missing-docs` demands a block on every monomorphized clone
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
- `derive` needs `finite-types` (a cycle would otherwise reach its topological sort as
  `CE0128`)
- `shadowing` needs `derive` (the auto-derived pair must be registered before a collision
  can be seen)
- `scope` needs `collect` (function signatures), and runs AFTER every whole-program pass,
  because the body walks need concrete, monomorphized types
- `typecheck` needs `resolve` and `scope`
- `lift` needs `typecheck` (the lifted body reads its stamps)
- `borrow` needs `typecheck` (the stamps) and `effects` (the cross-unit summary)

## Error Examples by Pass

**`scope`:**
- CE1003: Undefined variable
- CE2405: Use of moved variable

**`typecheck`:**
- CE2xxx: Type mismatch
- CE2502: `.realise()` wrong argument count
- CE2505: Assigning Result@(T) without handling

**`borrow`:**
- CE1007: Cannot rebind while borrowed
- CE2406: Use of destroyed variable

---

**See also:**
- [Architecture](architecture.md) - Overall compiler design
- [Backend](backend.md) - Code generation details
