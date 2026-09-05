# Unit namespaces

**Status: PHASE 1 LANDED.** The draft that this replaces measured a problem and surveyed
the answers. This document rules on them, and phase 1 of section 11 is implemented: it
landed in seven steps on `feat/unit-namespaces`, from `54ab5c30` to the tip of that branch,
under issue #490. **Phase 2 -- two units may each declare one TYPE -- is not implemented**
and belongs to `docs/design/type-identity.md`.

Section 1 is kept as the record of what the epic replaced. Every ruling below is measured
against it, and those measurements are what justify the rulings; a program in that section
is not a program that compiles now.

Sushi has one flat global namespace. `use` puts a whole unit into it and gives you no way
to say which unit you meant. This document says what replaces that.

It is normative for nine things:

1. What `as` does, and what an import without `as` keeps doing.
2. Where a `use` may stand, and how far the namespace it binds reaches.
3. That one namespace mechanism serves the FFI block and the `use` statement together, that
   what it binds is a resolved provider rather than a written path, and where in the pass
   order the table that holds it is filled.
4. Which declarations a namespace holds, and which it cannot hold.
5. How a qualified name resolves, in every position where a name can be written.
6. That a unit's scope is built from its own `use` statements, is not transitive, and
   therefore that a type must be imported to be named.
7. That a namespace is a resolution path and not a type identity — the line between the
   two phases.
8. That an orphan extension is legal and a duplicate one is refused, because no namespace
   can choose between two methods.
9. That `public use X` re-exports what X brings, that only a `public use` does, and that a
   re-exported name is one candidate exactly as a flat import's is (section 8.1).

Read `docs/design/visibility.md` first: it decides which declarations are even candidates
for a namespace. Read `docs/design/type-identity.md` for the constraint that section 7
puts a boundary around. Section 13 carries what issue #487 left for this epic, so the epic
is not done until it is gone.

This document **supersedes** parts of `visibility.md` as well as building on it: several of
that document's rulings are stated against one flat namespace, and this epic is what removes
it. **Section 14 is the obligation that follows** — each phase ends by editing
`visibility.md` to record its own drift, and it lists exactly which passages change.

## 1. What the compiler does today, measured

### 1.1 The four refusals

| Case | Today |
|---|---|
| `use "math" as my_math` | `CE6001: unexpected token 'as'`. The rule is `use_stmt: USE (stdlib_import \| lib_import \| user_import)` (`sushi_lang/grammar.lark:7`) — no alias clause exists |
| Two units, each with `public fn sine` | `CE3003: duplicate public symbol 'sine' found in units: liba, libb`, with a note at each declaration that claims the name (`semantics/units.py:242`). **The program cannot be compiled**, and no escape exists |
| Two units, each with a private `fn helper` | `CE0101: duplicate function 'helper'`, with a note at the other declaration — a name neither unit exports, refused anyway. Section 7 retires this row in phase 1 |
| Two units, each with a private `const SCRATCH` | `CE0105: duplicate constant 'SCRATCH'`, the same shape. Section 7 retires this row with the one above |
| Two units, each with `struct Node` | `CE0004: duplicate struct 'Node'` |

The first two rows are aliasing problems and the last three are not. Rows three and four
are one table holding one declaration per name, which section 7 rekeys. The last row is
narrower than either: a `StructType` compares and hashes on its name alone
(`semantics/typesys.py:75`), so one name **is** one type for the whole program. There is
nothing to alias there — it is the identity that would have to change.

Every duplicate in the table renders relationally today — `CE3003`, `CE0101`, `CE0105` and
`CE0004` each carry a note at the declaration they contest. That is the visibility epic's
work (`visibility.md` section 9), and it is the shape the replacements in section 10 have
to keep.

Privacy does not help. A private declaration still occupies the one namespace — that is
`visibility.md` section 1's deciding fact — so making a declaration private frees no name.
`CE3011` was the interim rule that followed from it: a consumer may not declare a name
that a library or a bundled stdlib module already declares, because in one namespace the
library's own bodies would then call the consumer's function. Phase 1 narrowed it to a
TYPE, and its `doc` string now names `docs/design/type-identity.md` as the design that
would lift the rest.

### 1.2 The namespace is not only flat, it is transitive

Three units. `top` imports `mid`, `mid` imports `deep`, and `top` never mentions `deep`:

<!-- docs-sweep: skip (records what the epic replaced; the program no longer compiles) -->
```sushi
# deep.sushi                      # mid.sushi              # top.sushi
public fn deep_value() i32:       use "deep"               use "mid"
    return Result.Ok(7)                                    fn main() i32:
                                                               let i32 a = deep_value()??
```

It compiles, links and prints `7`, and the reason is worth getting right because phase 1
replaces the thing that causes it. The flat scope is made twice over. `CollectorPass` is
**one instance for the whole program** (`semantics/semantic_analyzer.py:147`): its tables
are instance fields, so `_collect` returns a `SymbolTables` wrapping the same cumulative
table objects on every call (`passes/collect/__init__.py:222-236`).
`SymbolTableMerger.merge_all` (`semantics/symbol_merger.py:17`) then folds that one table
into a single `global_tables` once per unit, which is what
`validator.func_table.by_name` reads at the call site
(`passes/types/calls/user_defined.py:171`). A `use` statement today does not decide what
this unit can see. It decides what gets compiled.

It is NOT `build_global_symbol_table` (`semantics/units.py:219`), which is easy to blame
and does nothing of the sort. It fills `UnitManager.global_symbols`, and that dict is
**write-only**: its one reader, `find_symbol` (`:268`), has no callers anywhere in the tree
or the tests. The function's whole live effect is to raise `CE3003`. Section 10 retires
that code, so the function, the dict and `find_symbol` retire with it — a deletion, not a
replacement.

### 1.3 The standard library wins over your own function, and then crashes

`check_stdlib_function` (`passes/types/calls/user_defined.py:306`) searches a hard-coded
list of module paths and returns the first hit. It runs at line 166. The user function
table is not consulted until line 171.

<!-- docs-sweep: skip (records what the epic replaced: it crashed the compiler) -->
```sushi
use <math>

fn sin(f64 x) f64:
    return Result.Ok(999.0)
```

```
error [CE0000]: internal compiler error: TypeError: Can't index at [0] in double.
```

The stdlib signature answers the call and the user's body is emitted for it. The one flat
namespace has a fixed-priority search order, and that order is spelled as a Python list
literal. Nothing tells the user their function was passed over.

### 1.4 A namespace already exists, for one kind of symbol

The FFI boundary solved this years ago:

<!-- docs-sweep: skip (an extern block with no body to link) -->
```sushi
unsafe external "C" as libc because "the platform's own printf":
    fn printf(string fmt) i32 = "printf"
```

`libc.printf(...)` resolves through a real namespace, and the machinery is already the
shape a unit namespace needs:

| Piece | Where |
|---|---|
| `ExternalTable.by_namespace: Dict[str, Dict[str, ExternalSig]]`, with `is_namespace(ns)` and `lookup(ns, name)` | `passes/collect/externals.py:33`, `:36`, `:40` |
| `_resolve_external_call` — a `DotCall` whose receiver is a `Name` that is a registered namespace **and not a local variable** | `passes/types/__init__.py:145` |
| The grammar's `AS NAME` clause | `grammar.lark:29` |

Two properties carry over unchanged. A local variable shadows a namespace, so `libc` as a
variable name is not stolen by the FFI block. And the namespace is bound by the
*declaration*, not derived from a file name, so the author chooses the word.

The standard library is namespace-shaped too, in a second place:
`FunctionTable._stdlib_functions` is keyed by `(module_path, name)`
(`passes/collect/functions.py:206`). `stdlib_by_name()` is the function that throws the
module away.

## 2. Ruling 1: `as` is the gate

`use` gains an optional `as NAME` clause. The clause decides where the imported names
land, and nothing else.

| Form | What it binds |
|---|---|
| `use "math"` | every name `math` brings enters this unit's flat scope — unchanged |
| `use "math" as my_math` | every name `math` brings is reachable as `my_math.<name>`, and **nothing enters the flat scope** |

<!-- docs-sweep: skip (two units; the sweep compiles one block) -->
```sushi
use "math" as my_math
use <math> as std_math

fn main() i32:
    let f64 a = my_math.sin(0.0)??      # the unit next door
    let f64 b = std_math.sin(0.0)??     # the standard library
    return Result.Ok(0)
```

The two forms compose, because each `use` statement contributes what it says and no more:

<!-- docs-sweep: skip (two units; the sweep compiles one block) -->
```sushi
use "math"                              # flat
use <math> as std_math                  # behind a dot

fn main() i32:
    let f64 a = sin(0.0)??              # the unit next door -- unambiguous now
    let f64 b = std_math.sin(0.0)??     # the standard library
    return Result.Ok(0)
```

That is the case section 1.3 crashes on today. The alias is what makes it expressible.

**Every program that compiles today still compiles**, because no program today carries an
`as`. The flat form is not deprecated and gets no warning. It is the right form for a
program's own units, and the qualified form is the right form when two units disagree
about a name or when the reader needs to see where a name came from.

The alias is a single `NAME`. It is not a path, it is not dotted, and it may not be
`_`. The grammar clause is one token wide:

```
use_stmt: USE (stdlib_import | lib_import | user_import) [AS NAME] _NEWLINE
```

`AS` is already a token, already used after an import path (`external_block`, `grammar.lark:29`)
and in `cast`. Nothing follows a `use` path today, so the clause is unambiguous in LALR.

### 2.1 Where a `use` may stand, and what it reaches

**Every `use` precedes every other declaration**, after the unit's own doc block if it has
one. A `use` below a declaration is `CE3014`.

**A namespace is bound for the whole unit**, not from its `use` statement downwards. The
two halves answer one question that today's grammar leaves open in both directions:
`use_stmt` is a `toplevel`, so a `use` may sit anywhere in a file, and a declaration is
already order-independent — a call above its own `fn` compiles. Without the first half an
implementer resolving top-to-bottom would land on statement-scoped bindings by accident;
without the second, a namespace would be the one name in Sushi that has to be declared
before it is used.

Go and Java both make the placement mandatory (imports directly after the package clause,
before any type). Rust allows a `use` anywhere and leaves the position to convention. Sushi
follows Go and Java: a reader should see a unit's dependencies in one block without
searching for them.

`CE3014` is the one rule in this document that refuses source which compiles today, so it
was measured: **one file** in the tree puts a `use` below a declaration
(`tests/memory/test_consume_hashmapinsert_owned_array.sushi:25`, a
`use <collections/hashmap>` under a `struct`), and moving the line up is the whole
migration. Ruling 1's promise is about the `as` clause, which no program carries; this
clause is the exception to it, and it costs one line.

## 3. Ruling 2: one namespace mechanism, two producers

A namespace is a binding from a name to a set of declarations. Two things produce one:

| Producer | Alias | Members | Declared or imported |
|---|---|---|---|
| `unsafe external "C" as libc:` | **mandatory** | foreign functions only | declared in this unit |
| `use "math" as my_math` | optional | see Ruling 3 | imported into this unit |

The alias is mandatory where the namespace is the fence and optional where it is
convenience. An `unsafe external` block has no unqualified form because `libc.printf` is
part of what makes the `ptr` quarantine readable. A unit import has one because the flat
form is what Sushi has always had.

Everything else is shared, and becomes one seam:

```
semantics/namespaces.py

    Binding(kind, name, provider)      # kind is a `declarations()` word, or "extern"

    NamespaceTable
        bind(alias, provider, origin, loc)
        is_namespace(alias) -> bool
        lookup(alias, name) -> Optional[Binding]
        members(alias)      -> Iterable[str]   # the "did you mean" help line
        origin(alias)       -> str             # the unit a diagnostic names

    ExternalNamespace(external_table, ns)      # an unsafe external block
    UnitNamespace(symbol_tables)               # a user unit, a source library unit,
                                               # a source stdlib module
    StdlibNamespace(module_path, func_table)   # a registry module -- already (module, name)
    GenericNamespace(name)                     # an activated built-in, e.g. HashMap
```

`ExternalTable.by_namespace` becomes one provider behind the table rather than a thing a
pass reads directly, and `_resolve_external_call` becomes `resolve_namespaced`, which
answers for every kind. The refactor's whole content is that `lookup` returns a
kind-tagged `Binding` instead of an `ExternalSig`: an FFI namespace holds one kind, a unit
namespace holds five, and the resolution rule cannot tell them apart.

**Two readers move, not one.** The `typecheck` pass is the obvious one. The `scope` pass
has its own copy — `_is_external_namespace` (`passes/scope.py:119`) asks
`external_table.is_namespace(name) and not self._is_bound_local(name)`, which is section
8's local-wins rule written a second time. Both become calls to the one seam, and the
duplicated shadowing rule is the reason the seam is worth building rather than widening
`ExternalTable` in place.

### 3.1 A binding holds a provider, and never a path

**The `use` statement's written path reaches the `NamespaceTable` nowhere.** An import is
resolved first; the binding is built from the result.

That is a ruling and not an implementation note, because the obvious alternative is wrong
in a way that passes every test a first implementer would write:

| What the binding could hold | Verdict |
|---|---|
| the **written path** — `h -> "helpers"` | Wrong, and not for the reason it looks. A `use` path is resolved against the MAIN file's directory (`UnitManager(root_path=src_path.parent)`, `compiler/pipeline.py:241`, and `resolve_unit_path`, `semantics/units.py:126-129`), so one written path names one file from every importing unit — `tests/basic/helpers/bar_module.sushi` writes `use "helpers/math_utils"` from inside `helpers/` and proves it. It fails on packaging instead, which the paragraph below measures |
| the **resolved unit name** — `h -> "lib/foo/helpers"` | Correct, and only if the resolved name is the one stored. A `str` field cannot say which string it wants |
| the **`Unit` object** | Correct, and more than the table needs: it couples a namespace to a unit's identity when all it uses is that unit's symbols |
| the **provider** — `h -> UnitNamespace(...)` | **The ruling.** There is no string to get wrong |

The failure the first two rows invite is real and it is library-shaped.
`_inject_library_source` renames a library's units to `lib/<library>/<unit>` and rewrites
`unit.dependencies` to match (`compiler/pipeline.py:227`) — but it leaves
`UseStatement.path` alone, because nothing resolves through the path today. So a binding
built from the written path works for every user unit and breaks the moment a library unit
imports its sibling. **An alias survives packaging because it was never bound to a name that
packaging could change.**

A provider cannot be constructed before the unit it reads has been collected. That is not a
new constraint: it is section 13.2's collect order, dependencies before dependents, which
phase 1 makes anyway. The two rulings lean on each other by design.

`origin` is carried beside the provider rather than derived from it, and it is for
diagnostics alone: `geo.nope()` has to say which unit `geo` names, and `CE3013` has to point
at what bound the alias first. Nothing resolves through it.

**Two seams, in order.** `namespaces.py` answers *where* a name may be written.
`visibility.py` answers *whether* it may be named at all. A namespace therefore holds a
unit's declarations whatever their visibility, and the private ones are refused at the use
site with `CE3005`, which points at the declaration and says it has no `public`. Filtering
them out of the namespace instead would turn "not yours" into "no such name", which is the
worse diagnostic and the one `CE3005` exists to avoid.

### 3.2 The table is built by a new pass, and it goes after `libraries`

A namespace binding needs two things that different steps produce, so the position is
forced rather than chosen. The pass is named **`namespaces`**, and the order becomes:

```
collect -> docs -> externs -> libraries -> namespaces -> ffi-clash -> entrypoint -> ...
```

Four constraints fix it there, and each names the step that supplies something:

| A provider needs | Supplied by | Where |
|---|---|---|
| a unit's own declarations (`UnitNamespace`) | `collect` | the collect loop, `semantic_analyzer.py:174-181`. Its tables are cumulative today; phase 1 is what gives them a unit key |
| an FFI block's foreign functions (`ExternalNamespace`) | `collect` as well — NOT the `externs` pass | `external_collector.collect(root)`, `passes/collect/__init__.py:216`. The `externs` pass validates the signatures it finds; it does not fill the table |
| a BINARY library's declarations (`UnitNamespace` over a `.slib`) | `libraries` | the twelve `_register_library_*` methods, `semantic_analyzer.py:242-262` |
| a registry module's functions (`StdlibNamespace`) | `collect` | `register_stdlib_functions`, `passes/collect/__init__.py:215` |

The third row is the one that pushes the pass past `libraries` and is easy to miss: a
SOURCE library's units are ordinary compilation units and `collect` sees them, but a binary
`.slib` has no AST at all — its declarations arrive from a manifest, and only the
`libraries` step puts them anywhere. `use <lib/foo/bar> as f` must work for both kinds, so
the table cannot be complete before that step has run.

Nothing between `collect` and `libraries` resolves a name that could be qualified. `docs`
walks declarations and matches doc blocks to them; `externs` validates C-ABI types, which
are a closed set with no user type in it. `ffi-clash` is the first step that asks whether a
name is already taken, and it is the first that has to ask it of ONE unit, which is why the
new pass goes immediately before it.

**The per-unit scope is a separate deletion, and section 1.2 says why.** Phase 1 does not
"replace `build_global_symbol_table`" — that function is write-only and retires with
`CE3003`. What phase 1 replaces is `merge_all`, and the replacement **builds** the per-unit
table rather than keeping one, because there is none to keep (section 13.1):
`FunctionTable`, `ConstantTable` and `VisibilityTable` gain a unit key inside the one
shared collector. That is the same change section 13.1 needs and the same one that retires
`_replace_shadowed_functions`. One deletion, one rekey, and they are not the same edit.

## 4. Ruling 3: a namespace holds exactly what its `use` brings into scope

That is the whole rule. `as` does not change what an import brings; it changes where it
lands.

### 4.1 What is in

Five declaration kinds, and only their `public` members are reachable from another unit:

| Kind | Written as |
|---|---|
| `fn` | `my_math.sin(0.0)` |
| `const` | `my_math.MAX_DEPTH` |
| `struct` | `let my_math.Vec v = my_math.Vec(1, 2)` |
| `enum` | `let my_math.Sign s = my_math.Sign.Plus` |
| `perk` | `fn f@(T: my_math.Loud)(peek T x) ~:` |

### 4.2 What is out, and why

| Not a member | Reason |
|---|---|
| An extension method, a perk implementation | A method is found on the receiver's **type**, not through a scoped bound (`docs/design/method-resolution.md`). `v.length()` needs no namespace, and making it need one would make method resolution depend on the calling unit. This is `visibility.md` Ruling 2, restated |
| An enum variant | A variant follows its enum (`visibility.md` section 2), so it is reached *through* the enum: `my_math.Sign.Plus`, never `my_math.Plus` |
| `print`, `println` | These are grammar forms (`print_stmt`, `println_stmt`, `grammar.lark:69`), not symbols. Syntax is never namespaced |
| `string`, `List`, `Result`, `Maybe`, `Own`, `StdError` | In scope with no import. Nothing brought them, so no namespace holds them |
| A private declaration of another unit | Not a visibility carve-out — see Ruling 2's second seam. It is a member, and naming it is `CE3005` |
| A static call on a type NOBODY imports — `List.new()`, `f64.from_bits(b)` | `List`, `f64` and `f32` are in scope with no import, so no namespace can ever hold them. `HashMap` is different: the import gates the name, so its static obeys the alias like the type does — `hm.HashMap.new()`, and the bare form behind an aliased import is refused exactly as the bare type is (#506, decision A-strict; the fold is `fold_namespaced_static`, section 5) |

### 4.3 The standard library has four shapes, and the rule reads all four

| Shape | Modules | Aliasable |
|---|---|---|
| Registry free functions, already keyed by `(module, name)` | `<time>`, `<math>`, `<sys/env>`, `<sys/process>`, `<random>`, `<io/files>` | **yes** — this is the cheap half, and it fixes section 1.3 |
| Sushi-source modules, injected as ordinary units | `<collections/iter>`, `<compression/zlib>`, `<encoding/msgpack>`, `<toolchain/slib>` | **yes** — a user unit in every respect |
| A built-in generic that the import activates | `<collections/hashmap>` (`generics/active_generics.py:3`) | **yes** — `hm.HashMap@(i32, string)`. The import brings the name, so the namespace holds it. `active_generics` retires whole — see 4.3.1 |
| A method interface: the import enables methods on a type and brings **no name** | `<collections/strings>` | pointless, and said so — see below |
| A predefined enum the import brings (#574, Ruling 3) | `FileMode` → `<io/fs>`; `IoError`, `FileError` → `<io/error>`; `SeekFrom` → `<io/contracts>`; `NetError` → `<net/error>`; `ProcessError` → `<sys/process>`; `EnvError` → `<sys/env>`; `MathError` → `<math>` | **yes** — `fs.FileMode.Read()`. No unit declares one, so the synthesis stamps each with its HOME (`EnumType.home_module`, the table is `passes/collect/enums.py:PREDEFINED_ENUM_HOMES`); the `namespaces` pass reads the stamp to list it as a member of the home's provider, and the type-position gate (`reject_out_of_scope_type`) reads it to refuse the bare name where the home is not imported, the `HashMap` rule. **The home is reached through the modules that re-export it** (section 8.1, #586): `<io/contracts>` says `public use <io/error>`, `<io/fs>` and `<io/buf>` say `public use <io/contracts>`, so `use <io/fs>` alone brings `IoError`, `FileError` and `SeekFrom` beside `FileMode`, and `fs.IoError` holds behind the alias. `StdError` is the implicit Result arm and stays global. `SeekFrom` is `<io/contracts>`'s because `Seek.seek` takes it |

`use <io/fs>` does not bring `stdin` into scope: `stdin` is always a name
(`passes/types/visitor.py:703`), and what the import enables is `read_line()` on it. An
alias on such an import binds an empty namespace, and every `io.<name>` after it fails one
at a time with the cause several lines away.

#### 4.3.1 `active_generics` retires, and it is one reader

The third row looks like the expensive one and is the cheapest thing in this document.
`is_generic_active` has **exactly one reader** in the whole tree:
`_register_predefined_generics` (`passes/collect/__init__.py:296`), which puts `HashMap`
into the program-wide `generic_structs` table only when the flag is set. Every other
mention is a writer — `activate_generic_unit` at `compiler/pipeline.py:283` and
`backend/stdlib_linker.py:48` — or a test calling `reset_active_generics`.

That gate exists because there is no per-unit scope to ask. Once there is one, the table
holds `HashMap` unconditionally and the SCOPE decides who may name it: `HashMap` is a
member of the `<collections/hashmap>` namespace, a flat `use` puts it in the importing
unit's scope, and an aliased one puts it behind the dot. The generic-struct table stays
flat under phase 1, exactly as Ruling 6 says a `struct` does — what becomes per-unit is
only the right to write the name.

So `active_generics.py` is **deleted**, not converted: `GENERIC_UNIT_TYPES` becomes the
membership of one `GenericNamespace`, the one reader loses its `if`, and the two writers
and the process-global set go with it. **Five test files stop calling
`reset_active_generics`** (`tests/unit/conftest.py`, `test_lambda_names_unique_across_units.py`,
`test_struct_string_raii.py`, `test_layering_gate.py`, `test_ffi.py`), and that is the
argument in miniature: a process-global that five tests must reset between compilations is
scope kept in the wrong place.

### 4.4 An empty namespace is a warning, and never an error

**`as` on an import that brings no nameable declaration is `CW3004`**, at the `use`
statement. A warning, not an error, because a namespace can be empty for three different
reasons and only one of them is a mistake:

| Empty because | Example |
|---|---|
| **structural** — a method interface can never bring a name | `use <io/fs> as io` |
| **by design** — the unit exports methods, not names | a unit that is nothing but `extend` blocks |
| **incidental** — the public surface happens to be empty today | one `public fn` away from changing |

The middle row is the one that decides it, and it is not hypothetical. An extension carries
no marker: it is as visible as its target type (`visibility.md` Ruling 2). So a unit may
consist entirely of extensions, export **nothing nameable**, and still be the reason a
program works:

<!-- docs-sweep: skip (two units; behaviour this epic did not change) -->
```sushi
# extonly.sushi -- zero public declarations     # main.sushi
extend i32 squared() i32:                       use "extonly"
    return self * self                          # 7.squared() is 49
```

Built on this tree, it prints `49`. Delete the `use` and it is
`CE2008: undefined function 'i32.squared'` — **the import is load-bearing even though the
unit exports no name.** Refusing an `as` there would refuse a good import for a redundant
clause, and refusing the third row would make an error appear and disappear as a library's
public surface changed.

One rule covers all three, needs no hard-coded list of method-interface modules, and says
the true thing: the `as` bound nothing, and the import still did its work. The clause is
not needed, it does no harm, and there is no reason to write it.

## 5. Ruling 4: the qualifier folds into the name that follows it

`my_math.sin(0.0)` is not a new kind of expression. It is `sin(0.0)` with a namespace
written in front of it. One rule covers every position:

> A leading `NAME .` whose `NAME` is a bound alias, and is not a local variable, is
> stripped and attached to the name that follows it. Resolution then proceeds exactly as
> it does for an unqualified name, except that it consults one unit rather than the
> unit's flat scope.

That is one change per written-name position, and the positions are already enumerated,
because each one is a place where source text becomes a table key:

| Position | Node that carries the written name | Qualified form |
|---|---|---|
| A named type | `UnknownType(name)` / `GenericTypeRef(base_name)` | `my_math.Vec`, `my_math.Box@(i32)` |
| A called function | `Call(callee=Name)` | `my_math.sin(0.0)` |
| A struct constructor | `Call(callee=Name)` matched against the struct table | `my_math.Vec(1, 2)` |
| An enum constructor | `DotCall(receiver=Name)` / `EnumConstructor(enum_name)` | `my_math.Sign.Plus` |
| An enum **pattern** | `pattern`, its own grammar production | `my_math.Sign.Plus ->` (section 5.2) |
| A named value | `Name(id)` | `my_math.MAX_DEPTH` |
| A perk in a constraint | `perk_constraint_list` | `@(T: my_math.Loud)` |
| A static call on a gated type | `DotCall(receiver=MemberAccess)` | `hm.HashMap.new()` — the same three-segment shape as the enum row, folded by `fold_namespaced_static` when the member names the TYPE the namespace holds (#506, decision A-strict; landed after the epic) |

**The AST change is one optional field.** Every node above grows `namespace: Optional[str]`,
and the resolver maps `(namespace, name)` to a table key. In phase 1 that key is the bare
name; in phase 2 it is a qualified one (section 7). The same AST serves both, and the
resolver is the only thing that changes between them.

The enum row is the one that reads as a three-deep chain, and it is not: `my_math.Sign.Plus`
parses as `DotCall(receiver=MemberAccess(Name("my_math"), "Sign"), method="Plus")`, the
alias folds into `Sign`, and what is left is the `EnumConstructor` path the compiler
already takes. The `DotCall` ladder in `visit_dotcall` (`passes/types/visitor.py:410`)
gains no rung: the namespace check is the rung `_resolve_external_call` already occupies.

One phase runs BEFORE the fold: propagation, which stamps a GENERIC enum's constructor
with the instantiation its position declares. It reads the enum's name through the alias
for itself (`_enum_receiver_name`, `passes/types/propagation.py`), because a
`my_math.Slot.Filled(x)` reaches it with the `MemberAccess` receiver still in place; read
as a bare name only, the qualified spelling of a generic enum carried no stamp and the
backend reported the program as a compiler bug (CE0113). The bare `my_math.Slot.Empty`
takes the same reading (#545).

### 5.1 The grammar

Two rules gain a qualifier, both unambiguous because a `.` cannot mean anything else in
either position:

```
atom_type: ... | NAME "." NAME AT "(" type_list ")"  -> qualified_generic_type_t
         | ... | NAME "." NAME                       -> qualified_name_t
perk_constraint_list: perk_constraint ("+" perk_constraint)*
perk_constraint: NAME ["." NAME]
```

The perk rule is a split as well as a qualifier: today the list is
`perk_constraint_list: NAME ("+" NAME)*` (`grammar.lark:21`) and there is no
`perk_constraint` rule to hang the second segment on.

**No expression position needs grammar.** `my_math.sin(0.0)` already parses as a `DotCall`,
`my_math.MAX_DEPTH` as a `MemberAccess`, and `my_math.Sign.Plus` as a `DotCall` over a
`MemberAccess`. All three reach a resolver that returns nothing today.

A qualified call carrying explicit type arguments parses as well, and that is worth
spelling out because it looks like the counter-example. A type argument is REQUIRED where
the type parameter appears only in the return type, because there is no argument to infer
it from:

<!-- docs-sweep: skip (illustrative: `iter` exports no `empty_list`) -->
```sushi
use <collections/iter> as it

fn main() i32:
    let List@(i32) a = it.empty_list@(i32)()??
    return Result.Ok(0)
```

LALR reduces `.empty_list` to `member_access` and not to `method_call`, because the token
after it is `AT` and not `(`. The chain therefore arrives as
`maybe_call: atom(it) member_access(.empty_list) call(@(i32) ())`, and what refuses it is
`CE6102`, raised in the AST builder (`ast_builder/expressions/chains.py:84-89`) whenever a
`type_list` rides a call whose accumulated callee is not a bare `Name`. Omitting the
argument instead is CE2060, so without a change here **aliasing a unit would make its
return-type-only generics uncallable.** Seven call sites in the tree carry explicit type
arguments, and one of them is the negative test that asserts CE6102.

So the change is an AST field and a moved decision. `DotCall` gains `type_args`, which it
has none of today (`semantics/ast.py:541-551`), and `CE6102` **moves out of the AST builder
into a semantic pass**, narrowing from "it is a method call" to "the receiver is a VALUE".
It cannot stay a `SyntaxDiagnostic`: the builder cannot know whether a receiver `Name` is a
bound alias, and only a pass that has the `NamespaceTable` can.

The rule behind CE6102 is unharmed. Section 5's folding turns `it.empty_list@(i32)()` into
`empty_list@(i32)()` resolved against one unit, which is a direct call to a named free
function — exactly what CE6102 permits. It was the builder in the way, not the rule.

### 5.2 A pattern is a written-name position, and its grammar is one segment short

A `match` arm is not an expression, and it does not reach the constructor path. It has its
own production, and that production counts to exactly two:

```
pattern: NAME "." NAME ["(" [pattern_list] ")"]
pattern_item: pattern | NAME | wildcard_pattern | own_pattern
            | BORROW_MODE NAME -> ref_binding
```

`Sign.Plus` fits. `my_math.Sign.Plus` does not, and `pattern_item` offers no way in for a
nested one either. Without a third segment an aliased unit's enums are **write-only**: you
can construct one and never take it apart.

<!-- docs-sweep: skip (two units; the sweep compiles one block) -->
```sushi
use "geometry" as geo

fn main() i32:
    let geo.Sign s = geo.Sign.Plus      # constructing: Ruling 4 covers it
    match s:
        geo.Sign.Plus -> println("+")   # matching: no grammar exists for this
        geo.Sign.Minus -> println("-")
    return Result.Ok(0)
```

This is not a corner. A `match` is how Sushi consumes an enum — exhaustiveness-checked,
with a payload binding required — so an enum you cannot match is an enum you cannot use.
`Result` and `Maybe` hide the hole rather than filling it: both are built-ins, both are
always in scope, and neither is ever written qualified, so the common `match` compiles and
the gap only appears on a user enum from an aliased unit.

The grammar takes the qualifier the same way every other position does:

```
pattern: [NAME "."] NAME "." NAME ["(" [pattern_list] ")"]
```

Nesting needs nothing further: `pattern_item` already admits a `pattern`, so
`Shape.Wrap(geo.Sign.Plus)` works once the production above does. The resolution rule does
not move — section 5's folding strips the leading segment and the arm resolves against one
unit, so exhaustiveness, payload binding and the literal-arm rules (CE2074 / CE2075 /
CE2076) all read what they read today.

A qualifier that names nothing is refused as **a name that does not exist**, and the code
depends on the position. In an expression a leading `NAME .` may be a value, so an unbound
qualifier falls through to the ordinary rules and answers `CE2008` or `CE1001`. In a type
position it cannot be anything else, and the answer is `CE2001`, with a help line that
names the import which would bring the name. `CE3012` is the AMBIGUITY code and answers a
different question -- too many candidates, not none. No new code is needed either way.

### 5.3 One position cannot be qualified

An array size may not be qualified. `i32[my_math.SIZE]` is refused. A fixed array's size is
read while the unit's own AST is built (Known Limitation 14, and it is already why a
constant next door is a value and not a size), and an alias is bound long after that. The
diagnostic is the existing `CE2099`.

### 5.4 A constant declaration is two written-name positions (#561)

A `const` (and a `var`) writes a type and an initializer, and both take the dot:

<!-- docs-sweep: skip (two units) -->
```sushi
use "shapes" as sh

const sh.Shape SMALL = sh.UNIT               # a type, and a constant
const i32 DOUBLE = sh.SIZE * 2               # a constant in an expression
const sh.Point ORIGIN = sh.Point(y: 0, x: 0) # a struct constructor, named or positional
const sh.Shape TINY = sh.Shape.Circle(2)     # a variant, with or without a payload
```

A flat `use "shapes"` brings the same names bare. Three things stood in the way, and each
was a second copy of a rule the body path already had:

| Face | What stood | What stands |
|---|---|---|
| a flat foreign constant was `CE1002` | the typecheck pass rebuilt a table of `ConstDef`s PER UNIT, beside the program-wide `ConstantTable`; the evaluator found the signature and not the declaration | the record carries its declaration (`ConstSig.decl`); the second table is gone |
| a qualified name was `CE0108` | the evaluator had no arm for a `MemberAccess` or `DotCall` whose receiver names a namespace | the evaluator asks the namespace seam, folds `sh.Shape.Circle(2)` to `Shape.Circle(2)` as the body does, reads a struct through the stand-in `Call` the body uses, and stamps the node for the back end |
| a qualified type was `CE2001` with the import help | the `resolve` pass rewrote the declaration's `UnknownType("Shape", namespace="sh")` into the bare `EnumType` before the typecheck pass read the qualifier | the pass resolves the RECORD's type; the declaration keeps the written type until `validate_constant` rules on it, as a `let` does |

**A foreign initializer is read in the scope of the unit that wrote it.** `shapes` may
declare `public const i32 BIG = WIDTH * 2` with `WIDTH` from an import of its own; a
consumer folding `BIG` must see `shapes`' `WIDTH` and never its own. The evaluator
switches unit for the fold (`ConstantEvaluator._in_unit`), which is why it takes every
unit's namespace table (`SymbolTables.namespaces`) and not one scope. The cycle check
keys by declaration for the same reason: this unit's `SIZE = sh.SIZE * 2` is two
constants, not a cycle.

One more fold rides on this: `sh.Point(y: 0, x: 0)` parses as a METHOD CALL on `sh`, and
the builder dropped the field names on that path, so a named construction behind an
alias landed positionally, in a body as in a constant. The names are carried now and the
reordered arguments are written back onto the node.

## 6. Ruling 5: scope is per unit, and it is not transitive

A unit sees its own declarations, plus what its own `use` statements bring. Nothing else.
The program-wide `build_global_symbol_table` is replaced by a per-unit scope built from
that unit's `uses`.

Three consequences, all deliberate:

**`CE3003` is retired.** Two units exporting `sine` is not an error. It is an error only
where an unqualified `sine` is written in a unit that imported both flat, and then the
diagnostic is at that use, names both candidates, and says that `as` resolves it
(`CE3012`). Refusing the whole program with no location was the only answer available in a
flat namespace; it is not the answer now.

**An import is not re-exported, unless it is a `public use`.** `my_math.<name>` reaches what
`math` *declares*. It never reaches what `math` imported with a plain `use`. The qualified
form and the flat form agree on this, which is what makes the rule one rule -- and they
agree on the exception too: what `math` says `public use` on is `math`'s to hand on, flat
and behind the dot alike (section 8.1).

**Section 1.2's program stops compiling.** `top.sushi` must add `use "deep"`. This is the
one migration this document forces, and it is measured: of 2096 tracked `.sushi` files,
28 import a user unit, and 2 import more than one. The count grew with the visibility
epic's own test batches (`tests/visibility/**` and `tests/perks/cross_unit/**` are 10 of
the 28), so it tracks the test suite rather than the language.

`CW3001` ("duplicate use statement") narrows with the same change. `use "math"` followed by
`use "math" as m` is not a duplicate — the two statements do different things. The warning
survives for a repeat with the same alias, or none.

### 6.1 You must import what you name

Non-transitivity has one consequence that is easy to miss, so it is ruled here rather than
discovered: **a public signature may name a type its caller cannot name.**

<!-- docs-sweep: skip (three units, and the body is elided) -->
```sushi
# geometry.sushi          # shapes.sushi              # main.sushi
public struct Vec:        use "geometry"              use "shapes"
    f64 x                 public fn origin() Vec:     fn main() i32:
                              return Result.Ok(...)       let Vec v = origin()??
```

`main` may call `origin()`, because `origin` is in scope. It may not write `Vec`, because
`geometry` is not imported. And it cannot avoid writing it: `let` requires a written type
(`error [CE2007]: missing type annotation`), so there is no binding it can make.

**The rule: to name a type, import the unit that declares it.** `main` adds
`use "geometry"`. That is Java's rule, and it is the answer for phase 1.

It is also, stated plainly, the weakest position of the five languages worth comparing,
because Sushi is the only one with no escape from it:

| Language | Names it without the import? | The escape |
|---|---|---|
| Go | no | `v := shapes.Origin()` infers; `type Vec = geometry.Vec` re-exports |
| Rust | no | `let v = shapes::origin();` infers; `pub use` re-exports; a value of an unnameable type is still usable |
| Swift | no | `let v = origin()` infers; `@_exported import` re-exports |
| Java | **yes** | a package name is global, so `java.util.List` needs no import; `var` since Java 10 |
| C++ | **yes** | namespaces are global and includes are transitive; `auto` since C++11 |
| **Sushi** | **no** | **none** |

Every one of the five infers a local binding's type. Sushi does not, and the two that also
allow a bare qualified name get there through a global, canonical package name — which a
Sushi unit path, being relative to the importing file, cannot be.

So the cost of this ruling is real: a consumer inherits the type-declaring dependencies of
everything it imports, on every `let`. Two things would lift it, and neither is in this
document — `let` inference, which `CE2007` already marks the exact site of, and re-export
(section 12). The ruling is that phase 1 pays the cost rather than growing a third
mechanism to avoid it.

## 7. Ruling 6: a namespace is a resolution path, not a type identity

This is the line between the two phases, and it is the one place where the model gives less
than the syntax suggests.

`my_math.Vec` **names** the type `Vec`. It does not create a type `math::Vec`. Gating makes
a name unwritable unqualified. It does not make two declarations of one name coexist.

| Kind | An alias makes it writable | Two units may declare it |
|---|---|---|
| `fn`, `const` | phase 1 | phase 1 |
| `struct`, `enum`, `perk` | phase 1 | **phase 2** |

Coexistence for a function or a constant is a table-keying change: `FunctionTable.by_name`
and `ConstantTable.by_name` become keyed by unit, and the flat scope becomes a resolution
built on top. Section 1.1's third and fourth rows go with it: two units may each declare a
private `fn helper` or a private `const SCRATCH`, and section 8's ladder answers each
unit's call with its own. The front end gets that free; the BACK end pays for it with
section 9's mangling, because the monolithic build path puts every unit into one module
where two `internal` symbols collide as readily as two `external` ones.

**Tests move, and the visibility epic is why.** Its batches assert the cross-unit shapes
this ruling makes legal, so three of them are rewritten rather than deleted — each keeps
its single-unit case and loses its cross-unit one:

| Test | Asserts today | Under phase 1 |
|---|---|---|
| `tests/unit/test_duplicate_declaration_cascades.py` | cross-unit `CE0101` for a private `fn scale` | legal; each unit's call answers itself |
| `tests/unit/test_constant_visibility.py` | cross-unit `CE0105` for two private constants, `CE3003` for two public ones | `CE0105` goes. The both-public case becomes **nothing at all**, because that fixture's consumer declares the name itself and a unit's own declaration always wins; `CE3012` needs a THIRD unit that declares neither, which the file gained as a second case |
| `tests/unit/test_collect_attribution.py` | cross-unit `CE0101` for one `libc.strlen` declared in two units | legal; an FFI namespace is bound by the unit that declares it (section 3) |

Of the thirteen files that assert `CE0101`, those two are the cross-unit ones; the other
eleven are single-unit and do not move. Two files assert `CE3003` and both are cross-unit.

Coexistence for a type is a change to nominal identity — the interned name becomes
qualified — which reaches `StructType.__hash__`/`__eq__` and the enum twins, the struct and
enum tables, monomorphized instance names built by string concatenation, `display_type`,
and every match site that reads an interned name as text.

The two are not the same size, and the tree says so:

| Table | `.by_name` read sites | Files |
|---|---|---|
| `FunctionTable` | 20 | 8, of which 1 is the back end |
| `ConstantTable` | 16 | 10, of which 2 are the back end |
| `StructTable` | 200 | — |
| `EnumTable` | 209 | — |

36 against 409. (Counted as `(func_table|function_table)\.by_name` and its three siblings
over `sushi_lang/**.py`.) That is why the phase line falls where it does, and phase 2
belongs in `docs/design/type-identity.md`, argued there.

## 8. Shadowing and collisions

**A local variable wins.** `my_math` as a variable shadows the alias for the rest of its
scope, exactly as a local shadows an FFI namespace today
(`passes/types/__init__.py:152`). One rule, one seam, both producers.

**An alias may not collide.** An alias binds a name in the unit that wrote it, so it is
`CE3013` if that unit already binds the name: another alias, an FFI namespace, or one of
its own declarations. Two aliases for one unit are legal and both work; the same alias
twice is not.

**A unit's own declaration wins over a flat import.** That gives one ladder for an
unqualified name, and it is short:

| Order | Candidate |
|---|---|
| 1 | a local variable or a parameter — the existing scope rules, unchanged |
| 2 | a declaration of this unit |
| 3 | a name a flat `use` of this unit brought in — one candidate resolves, two or more is `CE3012` |

Row 3 holds a registry stdlib module's CONSTANT as it holds its functions. `PI`, `E` and
`TAU` used to sit ABOVE the ladder -- the scope pass, the inference visitor and the back
end each asked the math module before any table -- so `let i32 PI = 3` printed 3.14159
and a unit's own `const f64 E` read as 2.71828, with no `use <math>` in sight (#560). One
scope-aware lookup answers them now (`stdlib_registry.lookup_stdlib_constant`), at row 3:
a unit that did not import `<math>` has no `PI`, a unit's own `E` wins, and a `const`
initializer folds `PI / 2.0` like any other constant.
`tests/unit/test_stdlib_constants_take_the_ladder.py` keeps the module's hooks readable
by the registry alone.

Row 2 beating row 3 is the rule the compiler already follows and the linker already agrees
with: a private function has internal linkage, so the consumer's call binds to the
consumer's definition (`visibility.md` decision 10). It keeps warning — `CW3002` survives
this document unchanged, because shadowing an export is still rarely intended and the
reader of the call site still cannot see which declaration answers. `as` is what makes the
choice explicit, and `CW3002`'s `doc` string already says so.

What does NOT survive is the machinery underneath the rule. `_replace_shadowed_functions`
(`semantics/symbol_merger.py`) exists only because library units merge first into one flat
table, so a consumer's declaration had to be put back on top of one that had already won.
With the table keyed by unit there is no winner to displace: row 2 is a lookup in the
asking unit, and row 3 is a lookup in what it imported.

A built-in's precedence is not in this ladder. `docs/design/method-resolution.md` owns that
rule and this document does not move it.

**An extension on a foreign type collides globally, and a namespace cannot fix it.** An
extension is not a namespace member (Ruling 3), because a method is found on the receiver's
type. So two units may extend a third unit's type with one method name, and no alias can
choose between them:

<!-- docs-sweep: skip (proposed syntax across four units) -->
```sushi
# render.sushi                      # physics.sushi
use "geometry" as geo               use "geometry" as geo
extend geo.Vec length() f64:        extend geo.Vec length() f64:
    return self.x                       return self.y
```

**Orphan extensions stay legal** — `extend i32 squared()` from any unit is idiomatic Sushi,
and Known Limitation 9 pushes combinators toward exactly this shape. **A duplicate is a hard
error**, which is what the compiler already does: two units extending `i32` with `tag()`
gives `CE0101: duplicate function 'extension method 'tag' for 'i32''`.

Refusing here rather than at the call is deliberate, and it is the one place this document
prefers the eager answer. A method has no written qualifier, so a lazy diagnostic would name
an ambiguity the user has no syntax to resolve. Go forbids the shape outright (a method may
only be declared in the package that declares the type); Rust forbids it with the orphan
rule; Swift allows it and diagnoses at the call; Java has no extension methods. Sushi allows
it like Swift and refuses like Go.

What must change is the TEXT, and it has (PX). `CE0101` named one declaration and called
the other a duplicate, which is right for one author and wrong for two: when the two
extensions come from two libraries, neither author is at fault and the consumer can edit
neither. When the two declarations come from two units, the diagnostic is relational: a
note per unit, `unit '<name>' declares it here`, and no side blamed
(`passes/collect/functions.py`, both the concrete and the generic-target site). One unit
writing both keeps the old shape, because there the second one IS the duplicate.

#### The warning belongs to `--lib`, and to nothing else

The person who can fix a foreign-extension collision is never the person who sees it. A
consumer combining two libraries reads `CE0101` about code they did not write and cannot
change; only a diagnostic at the declaration reaches somebody who can act. So there is a
warning, and its scope is the whole of its design:

> **CW3003**, at `--lib` build time only: this library extends a type it did not declare,
> so the method name is claimed for every consumer, and a second library claiming it makes
> the two unusable together.

**`--lib` is the scope because shipping is when the hazard becomes other people's
problem**, and because it is the moment the author is present. Warning on the extension
itself was measured and rejected. Of 325 extensions in the tree, 199 target the unit's own
type, **125 target a builtin** — `i32` alone is 87 — and **none targets a foreign unit's
type**. A warning on "a target this unit did not declare" fires 125 times,
`extend i32 squared()` among them, which is the form CLAUDE.md's own quick reference
teaches. A warning that common is a warning people switch off.

Exempting builtins to quiet it is backwards: `i32` is the MOST collidable target, because
every unit in every program can reach it. That predicate fires zero times in the whole
tree — a warning nothing triggers teaches nobody. Neither predicate on the extension works,
and the scope is what makes the rule right.

**The predicate reads the TARGET TYPE, never the perk.** `extend Crate with Heavy` where
`Heavy` comes from next door is not a foreign extension: the method is claimed on `Crate`,
which this unit declares, so no consumer can be surprised by it. Every one of the twelve
cross-unit perk implementations in the tree — `tests/libs/`, `tests/perks/cross_unit/`,
`tests/visibility/perk/` — is that shape, eight of them on a builtin and four on a type the
implementing unit declares itself. That is why the foreign-target count is zero.

**A perk implementation never warns, whatever its target** (PX ruling). The hazard CW3003
names is a claim with no escape: a consumer holding two colliding plain extensions can edit
neither. A perk implementation's claim has the escape built in — the consumer's OWN
implementation is the sanctioned override and wins over a shipped one
(`tests/libs/test_lib_perk_impl_local_override.sushi` is the measured proof). So
`extend i32 with Doubler` in a library stays quiet, and the two library fixtures of that
shape keep building clean. The predicate lives in `semantics/foreign_extensions.py`, one
function for both consumers: the CW3003 emitter in the pipeline and the manifest extractor.

Cost when measured: `sushi_stdlib/src_sushi` and `toolchain/src` held **zero** extensions
between them, and `tests/libs` holds 11 — eight on a builtin, three on a type the consumer
declares. The UFCS epic then put six method combinators in
`src_sushi/collections/iter.sushi`, all on builtin generic targets (`List@(T)`, `T[]`) —
the shape the measurement already covered, and a bundled stdlib module is not a `--lib`
build, so CW3003's scope is untouched. It still fires nowhere in real library code, which
is what a warning aimed at a future hazard should do.

**The consumer's half.** `--lib-info` lists the foreign types a library claims methods on,
so the hazard is readable before it is hit: the manifest carries them as
`foreign_extensions` (absent when the library extends only what it declares), and both
report renderers print the section as `extend <type> <method>` lines
(`docs/library-format.md`).

**What the author does about it is a prefix**, and that is worth saying plainly. Section 1.3
puts C in the row with no answer, where `png_read_info` and `SDL_Init` live, and a method
name is the one place Sushi stays in that row. It is deliberate. A method is found on the
receiver's type (Ruling 3), so there is no namespace to put it behind, and the alternative —
making method resolution depend on the calling unit — is the cost `visibility.md` Ruling 2
measured and refused. Sushi buys namespaced NAMES and keeps prefixed METHODS, and extending
a type you do not own stays a feature with a price attached.

**An alias is local.** It is written in one unit and is not visible in any other, not even
one that imports the aliasing unit. Nothing about it is exported, so `.slib` production and
the library manifest do not change. A library unit's own alias is a different question and
section 3.1 answers it: the binding holds a provider, so the unit rename that packaging
performs cannot reach it.

**The namespace is the unit, never the library.** `use <lib/foo/bar> as f` binds unit
`bar` of library `foo`, because that is what the import names. A library whose public API
spans several units and wants a single namespace ships one façade unit that says
`public use` on each of the others (section 8.1). That is a library design choice, and
`docs/design/libraries.md` is where it belongs.

### 8.1 Ruling 7: `public use` re-exports

**Status: LANDED** (#586, 2026-09-05). Section 6.1 measured the cost of a non-transitive
scope -- a consumer imports every unit that declares a type it binds, on every `let` --
and section 12 left re-export as the question to answer it against. #574 made the cost
concrete: once `IoError` had a home at `<io/contracts>`, a program that wrote `use <io/fs>`,
called `open()` and matched `IoError.NotFound` needed `use <io/contracts>` for a name it
never chose, and 90 tests and 44 doc fences gained that line in one PR. Go, Rust and
Python all answer the same way: the module whose API answers a type re-exports it
(`os.FileInfo = fs.FileInfo`, `pub use`, a package `__init__`). Sushi names types more
often than any of them -- a `let` declares its type and a channel spells `| IoError` in
every signature -- so it needs the re-export more. This is Sushi's `pub use`.

**The mental model.** `public use X` in U means: take X's public names, make them U's own,
and re-export them as public. Every importer of U gets the effect of `use X` in the same
place U's own names land -- flat behind a flat `use "U"`, behind the dot of `use "U" as u`.

**Four rules.**

1. `public use X` re-exports what X brings. It is also an ordinary `use` for U itself. It
   takes no `as` (CE3016): a re-export is of names, not of a namespace, and an alias is
   local to the unit that wrote it (section 8). The alias still binds, so the one fault
   gets one diagnostic.
2. Only a `public use` re-exports. A plain `use` in U brings nothing to U's importers, as
   before. Re-exports compose along `public use` chains -- if X says `public use Y`, U's
   importers get Y -- and never along a plain `use`. A cycle of `public use` is legal and
   terminates on a visited set.
3. A binary `.slib` carries no re-export today. A `public use` in a unit built with
   `--lib-kind binary` or `hybrid` is CE3514 at the line, before anything is compiled: the
   manifest has no record for it, and a consumer would read a narrower API than the author
   wrote, silently. A source `.slib` needs nothing -- the consumer re-parses the statement.
   #585 is the manifest record; CLAUDE.md Known Limitation 6 and `docs/design/libraries.md`
   section 5 carry the limit.
4. The predefined enums stay synthesized and homed (Ruling 3, #574). A module makes a home
   reachable by re-exporting it -- `public use <io/error>` in `<io/contracts>` -- and the
   stamp machinery (`homed_enums`, `UnitScope.holds_home`, `reject_out_of_scope_type`)
   is read through the re-export unchanged. A registry (Python) module has no `use`
   statement to write, so it declares its re-exports in a `REEXPORTS` tuple beside its
   functions (`StdlibModule.reexports`); `<io/files>` hands on `<io/error>` that way. A
   `public use` that hands on nothing public warns (CW3005), as an empty alias does (CW3004).

**Shadows and duplicates.** A re-exported name is a candidate exactly as a flat import's
is. U's own declaration wins over its re-exports, as a unit's own wins over its imports
(section 8's ladder, row 2 over row 3). Two re-exports that offer DIFFERENT declarations
of one name are CE3012 at the use, like two flat imports. The same declaration reached
twice -- `use <io/fs>` and `use <io/error>` in one unit, or two units that both re-export
`geometry` -- is ONE candidate: `VisibilityTable.candidates` counts by declaring unit and
not by path, and must keep doing so. A predefined enum has no declaring unit and is never
a candidate for CE3012. Only X's PUBLIC declarations travel: U cannot give away what it may
not name, and CE3005 stays the consumer's answer for a private one it writes anyway. The
provider still HOLDS the private, as every namespace does, so `u.hidden` is "not yours"
and not "no such name"; what CW3005 counts is the public subset.

**One type, whatever the path.** Guaranteed by the type model and pinned by a gate.
Identity is nominal and program-wide (`docs/design/type-identity.md`; Ruling 6: a
namespace is a resolution path, not a type identity). A qualifier folds into the bare
name before the table lookup, so `IoError`, `fs.IoError`, `io.IoError` and a name reached
through a two-hop chain resolve to the ONE synthesized `EnumType`, and
`Result<string, IoError>` interns once. `tests/unit/test_public_use_reexport.py` is the
gate: one program names `Vec` bare through two hops and behind two aliases, `IoError` bare
and behind two aliases, and every `let` resolves to the same table object; no CE0126.

**Mechanics.** The grammar takes `PUBLIC? USE`; `UseStatement.is_public` and
`public_span` carry it. A provider composes what it re-exports: `Provider.reexports` is the
tuple the unit's `public use` statements name, `Provider.reaches` walks the chain once
with a visited set, and `lookup`/`members` read the walk -- own first, then each
re-export in written order, then theirs. A binding a re-export answers carries the
re-export's provider, so a call through `sh.origin` routes to the unit that declares
`origin`. The flat scope reads the SAME walk: `_scope_of` puts every reached unit, module
and generic into `UnitScope`, so the dot and the bare name cannot disagree. The
`namespaces` pass does not move -- a provider still needs only what `collect` and
`libraries` produce, and a unit's own AST, which it has.

**Tests.** `tests/namespaces/reexport/`: the flat and the aliased import of a re-exporting
unit; a two-hop chain; a plain `use` in the middle that re-exports nothing (CE2001 with the
import in the help); `public use ... as` (CE3016); a `public use` below a declaration
(CE3014); two re-exports offering one name (CE3012); an own declaration beside a re-export;
one declaration reached twice; a `public use` of a unit with nothing public (CW3005). The
stdlib half: `use <io/fs>` alone writes `| IoError`, `IoError.NotFound` and
`SeekFrom.Start`; `use <io/fs> as fs` gives `fs.IoError`; `use <net/tcp>` alone matches
`IoError` from a read. `tests/unit/test_public_use_reexport.py` holds the identity gate
and rule 3.

**What this does not decide.** Whether a `public use` may re-export a single name
(`public use "geometry".Vec`), and whether a `.slib` consumer may re-export a library
(rule 3 says not from a binary one; a source one works by construction). Both are open
until asked for.

## 9. What the back end needs

Nothing, for the syntax. Rulings 1 to 4 are front-end resolution, and the back end is
handed a resolved callee exactly as it is today.

One thing, for coexistence. A symbol used to be its bare Sushi name. Two units each
declaring `sine` therefore need mangling by unit — and for **private** declarations as
well as public ones, because the monolithic build path puts every unit into one
`ir.Module` (`backend/codegen_llvm.py:327`), where an `internal` symbol collides just as
an `external` one does. Only the incremental path emits a module per unit
(`backend/codegen_llvm.py:549`).

The scheme: `<unit>$<name>`, with every `/` in the unit name replaced by `$`, so
`collections/iter`'s `next` becomes `collections$iter$next`. One function writes it,
`mangle_unit_symbol` (`sushi_lang/semantics/unit_symbols.py`), read by the back end when
it declares and by the `.slib` producer when it records. `$` is legal in an LLVM
identifier and lies outside the alphabet of every existing symbol component, so the
generic mangler's structural invariant (D) is untouched
(`semantics/generics/name_mangling.py:11`).

Four things are exempt, and each for its own reason:

- **`main`.** The linker needs the name, and the `entrypoint` pass already guarantees one
  program declares one.
- **An FFI `link_name`.** It names a C symbol that somebody else compiled.
- **A lifted lambda.** Its name already carries the per-unit lifter's counter (#402).
  A monomorphized INSTANCE is no longer exempt: it goes home to the unit that declared
  its generic and takes that unit's prefix (#495), so two units' instances of one
  mangled base name are two symbols. The one instance that stays bare is a binary
  library's template instance, whose home names no unit in the consumer's build.
- **An extension or perk-impl method.** Its symbol is derived from the receiver's TYPE,
  which is nominal and program-wide. This is not an oversight but Ruling 2 of
  `visibility.md`: a method is found on the receiver's type, so there is no unit to put it
  behind. Section 8 records the cost.

**A constant is a symbol too**, and the same rule reaches it: its global is
`<unit>$<name>`, and the value follows the name — the constant evaluator takes the asking
unit, so `const i32 DOUBLE = SCRATCH * 2` in two units folds each unit's own `SCRATCH`.

### 9.1 Reading a symbol back

A bare name is no longer an answer on its own, so the tables that hold declarations carry
two views: the unit that made each declaration, and the flat name. `UnitKeyedSymbols`
(`sushi_lang/semantics/unit_symbols.py`) is the one implementation, and the rule that
reads it is `FunctionTable.lookup`'s — the asking unit's own declaration answers first,
and the flat view answers everything else. One rule, so the back end cannot disagree with
the collect pass about what a name means inside a unit.

Every reader that had no asking unit gained one: the typecheck pass
(`TypeValidator.func_sig`, `TypeValidator.const_sig`), the borrow pass (`view_for`, since
phase 1) and the back end (`codegen.emitting_unit`). `_replace_shadowed_functions` existed
only to make a flat first-wins table behave, and it retires with them.

### 9.2 A binary `.slib` names the symbol, and every record names its unit

Manifest protocol **2.2**, two keys, and they answer different questions.

**`link_symbol`** names the symbol a record has in the SHIPPED BITCODE. Written by every
build, and read by the **binary** path alone. A source library recompiles at the consumer,
where its units are renamed to `lib/<library>/<unit>`, so the consumer derives
`lib$<library>$<unit>$name` and a producer-written symbol would be wrong. A binary library
links: its private closure helpers ship as signatures with `"source": null`, and the
consumer compiles a re-monomorphized template body that CALLS a name it cannot derive from
anything it can see. Only a record with a symbol carries the key — a public constant ships
its source and is re-evaluated, a template is monomorphized at the consumer, and a
perk-impl method already carries the derived `symbol`.

**`unit`** names the unit that DECLARED the record, on every record. It is what Ruling 1
binds an alias to: `use <lib/foo/bar> as f` binds `f` to the unit `bar`, and for a binary
library the manifest is the only place that can say.

**There is no scheme identifier.** A manifest records what is, not the recipe, and
`compiler_version` already says which compiler wrote it, with `CE3503` refusing a `.slib`
the running compiler may not consume.

## 10. Diagnostics

`unit.py` owns `CE3xxx` and is registered up to `CE3011`, and `visibility.md` reserves
`CE3009` and `CE3010`. `warnings.py` is registered to `CW3002` in the same family. This
document takes the next three errors and the next two warnings:

| Code | Raised at | Says |
|---|---|---|
| **CE3012** | an unqualified use | the name is offered by more than one flat import; names every candidate, and says `as` resolves it. Replaces `CE3003` |
| **CE3013** | the `use` statement | the alias is already bound in this unit; the note points at what bound it |
| **CE3014** | the `use` statement | a `use` below a declaration; every import comes first (section 2.1) |
| **CW3003** | an `extend` of a foreign type, at `--lib` build time ONLY | this library claims a method on a type it did not declare (section 8). Not gated on either phase: it needs the target's declaring unit and the `--lib` flag, and nothing else this document adds |
| **CW3004** | the `use` statement | `as` bound an empty namespace (section 4.4). A warning because a namespace is empty for three reasons and only one is a mistake |

Reused rather than duplicated:

| Existing code | Now also answers |
|---|---|
| `CE3005` | `my_math.helper` where `helper` is private to `math`. The two-seam rule (section 3) routes it here |
| `CE2008` | `my_math.nope()` where `math` declares no `nope` at all. `NamespaceTable.members` supplies the "did you mean" help line |
| `CE2099` | a qualified array size (section 5.3) |
| `CW3002` | unchanged. A unit's own declaration still wins over a flat import and still warns (section 8); only the machinery under the rule retires |
| `CE2007` | unchanged, and load-bearing. It is why section 6.1 has no escape: a `let` cannot infer, so a type that cannot be named cannot be bound |
| `CE6102` | narrowed. "Explicit type arguments only on a direct call to a named free function" now reads the RECEIVER, not the parse shape, so a qualified call may carry them (section 5.1) |

Retired or narrowed:

| Code | Change |
|---|---|
| `CE3003` | retired. It refused a whole program for a collision that may never be written |
| `CE3011` | narrowed to what phase 2 has to lift: a TYPE name a consumer redeclares against a library, imported flat or behind an alias. An alias does not help a type -- identity is nominal, so one name is one shape however the name is written -- and that was measured under both import forms. The FUNCTION arm retired in phase 1, and a library's private CONSTANT went with it (#507): the constant table is keyed by unit too, so each declaration takes its own global |
| `CW3001` | narrowed to a repeat with the same alias, or none (section 6) |
| `CE0101` | kept for a duplicate extension on a foreign type, with new TEXT: relational, naming both units, blaming neither (section 8). Retired for a cross-unit private function, which phase 1 makes legal (section 7), a library's private function included. A GENERIC function coexists the same way (#495): its table carries the two views, and its instance takes its declaring unit's symbol prefix, so CE0101 is a one-unit duplicate for every callable kind |
| `CE0105` | retired cross-unit with `CE0101`, and for the same reason: two units may each declare a private constant once the table is keyed by unit (section 7). Kept whole within one unit, and against a library's PUBLIC constant, which the consumer can see and read (#507) |

## 11. Delivery

**Phase 1 — the alias, the scope, and the tractable half.** Rulings 1 to 5, and Ruling 6's
phase-1 row: a `struct`, an `enum` and a `perk` are reachable through a namespace, and are
still one per program. Adds `semantics/namespaces.py` and the `namespaces` pass that fills
it (section 3.2), the `as` clause, the qualified forms of two grammar rules, the optional
`namespace` field on the nodes of section 5's table, a per-unit scope in place of
`merge_all`'s fold, unit keys on `FunctionTable` and `ConstantTable`, the collect-order
reversal of section 13.2, and the mangling of section 9.

Five things are deletions rather than rewrites, and they are the phase's cheapest half:
`build_global_symbol_table` with its write-only `global_symbols` and `find_symbol`
(section 1.2), `_replace_shadowed_functions` (section 8), `_library_units_first` and the
`collect_perk_definitions` pre-sweep (section 13.2, one each), and `active_generics.py`
whole (section 4.3.1).

What it buys: the section 1.3 crash becomes two working calls; two libraries may both
export `sine`; a unit's scope is what its `use` statements say it is; and a name's origin is
readable at the call. It closes section 13.1, the last open item of #487.

What it moves: the three cross-unit test batches of section 7, and the `visibility.md` edit
of section 14, which is phase 1's last commit and not a follow-up.

**Phase 2 — types coexist.** Qualified interned names, so two units may each declare
`struct Node`. Owned by `docs/design/type-identity.md`. Phase 1's AST and resolver are
already shaped for it: only the key the resolver produces changes. It ends with its own
`visibility.md` edit (section 14.2).

**Not gated on either phase.** `CW3003` (section 8) needs the target type's declaring unit
and the `--lib` flag, neither of which this document introduces. It can ship on its own, and
it is the cheapest thing here: a predicate, a code, and a test batch.

## 12. What this does not decide

**Selective import.** `use "math" { sin, cos }` — a name-level form. Ruling 1 gives the
unit level only. `visibility.md` section 4 records that Rust's trait rule is expressible
only on top of a name-level import, so this question also decides whether a sealed perk
becomes possible. It is a separate feature and it composes with everything here.

**Re-export.** DECIDED: section 8.1 (Ruling 7, #586). `public use X` is the deliberate act
with its own marker that this paragraph left room for; section 6.1's demand is what
forced it, once #574 gave `IoError` a home. What section 8.1 leaves open is a name-level
form (`public use "geometry".Vec`) and re-exporting a binary library.

**A wildcard, and a nested namespace.** `my_math.*` and `a.b.c` are both out. A namespace
is one flat set of names bound to one alias.

**Coherence.** Section 8 rules that an orphan extension is legal and a duplicate is a hard
error. It does not rule on OWNERSHIP — whether a unit should be allowed to extend a type it
did not declare at all, which is Rust's orphan rule and Go's package rule. That question
predates this document (`extend i32 squared()` from any unit works today) and it sharpens
under phase 2, where two units may extend two different types that share a name. It belongs
with `docs/design/method-resolution.md`.

**`let` inference.** Section 6.1 measures its absence and rules without it. Adding it is a
language change with a much wider blast radius than a namespace, and `CE2007` is where it
would land.

**Per-alias visibility.** An alias is local to its unit (section 8) and carries no marker.
Whether an alias could ever be exported is the same question as re-export.

## 13. Carried over

The visibility flip left three limits behind (issue **#487**; the `LEFT.md` working doc that
enumerated them has been consumed, so the option letters below are kept for the issue's
history). PR **#488** closed two of them and recorded the third as waiting for this epic.
What remains here is therefore one open defect and one prerequisite:

| | Was | Now |
|---|---|---|
| **13.1** a shadowed call reads the winner's parameter modes | waiting | **CLOSED.** Both readers of a callee's modes ask with a unit |
| **13.2** collection order: dependencies before dependents | waiting | **LANDED.** The order is reversed and both hand-patches are gone |

Each is recorded below with the ruling that resolves it, so the epic's definition of done
includes them.

### 13.1 A shadowed call reads the winner's parameter modes

`LEFT.md` item 1, and the decision on it is that item's **option C**: wait.

A source library exports `fn eat(string s)` — a borrow — and calls it from its own
`drive()`. The consumer declares its own `fn eat(nom string s)`, which is legal and warns
(`CW3002`). The compiler then says:

```
main.sushi:3:4:      warning [CW3002]: 'eat' shadows the function 'lib/shadowlib/shadowlib' exports.
shadowlib.sushi:8:9: error [CE2427]: argument mode does not match the declared mode of parameter 's'.
shadowlib.sushi:9:30: error [CE2405]: cannot borrow moved variable 'msg'.
```

The library's body is correct, and it is the file the errors point at. The `borrow` pass
measured the library's own call against the consumer's declaration.

**Why this document owns it.** There is one hole with two readers, and both ask "what does
the name `eat` mean" without saying which unit is asking:

| Reader | Site | What it reads |
|---|---|---|
| the `borrow` pass | `_build_callee_modes`, `passes/borrow/__init__.py:24` | one name-keyed dict merged from every signature in the program |
| the back end | `backend/expressions/calls/dispatcher.py:61` | `codegen.func_table.by_name[callee]`, for the modes and for variadic-ness |

That is the question Ruling 6's phase-1 row answers: once `FunctionTable` and
`ConstantTable` are keyed by unit, a lookup has an asking unit, and a library body reads
the library's own signature. The item does not need a fix of its own — it disappears with
the key.

**The per-unit answer is never built, and that is the defect.**
`collector.run(unit.ast, unit_name=…, unit_file=…)` reads as though it returns one unit's
symbols, and it does not. `CollectorPass` is one instance for the whole program
(`semantic_analyzer.py:147`), its tables are instance fields, and `_collect` returns a
`SymbolTables` wrapping **the same table objects on every call**
(`passes/collect/__init__.py:222-236`). So the `unit_tables` that `merge_all` folds into
`global_tables` (`semantic_analyzer.py:174-181`) is the cumulative program table, replayed
once per unit — first-wins is the only reason that replay is near-idempotent, and
`VisibilityTable.record`'s own comment says exactly this
(`semantics/visibility.py:139-142`). `_replace_shadowed_functions`
(`semantics/symbol_merger.py`) then lets the consumer's declaration replace the library's
and books the library as a loser of the name. The type pass knows it is looking at a loser
and validates the arguments alone, because the loser's signature is gone.

Phase 1 gives the three name-keyed tables a unit key inside that shared collector. It is a
key change and not a new mechanism, but it BUILDS the per-unit table rather than stopping a
discard — and the cumulative table is load-bearing while it lasts, because it is how a
collector detects a cross-unit duplicate at all (`if name in self.funcs.by_name`,
`passes/collect/functions.py:542`). The unit key and the duplicate diagnostics therefore
move together.

**What waiting costs, and the bound on it.** The wrong-file diagnostic stays until the epic
lands. Two facts bound it: only a SOURCE library is exposed, plus a binary library's
generic templates, because a binary library's concrete bodies were settled when the library
was built; and the library body has to call the shadowed name itself. A shadow of a name
the library never calls is correct today. `CW3002`'s own `doc` string already names this
document, so the pointer is in the catalogue as well as here.

**The fallback, if the epic slips.** `LEFT.md` option A — stamp `callee_param_modes` and
`callee_param_types` on the `Call` node from the asking unit's signature, the way
`passes/types/calls/methods.py` already does for a method call — is a complete answer on
its own and stays available. It is a second answer to "which declaration does this call
mean", and this document's whole content is the first, so it is a fallback and not the
plan.

**What landed.** `FunctionTable` gained `by_unit` beside `by_name`. The flat view keeps
one declaration per name and stays the winner of a shadowed name, so every reader with no
asking unit answers as it did; `by_unit` keeps every declaration under the unit that wrote
it, and `view_for`/`lookup` read it. Both readers named above now ask with a unit: the
borrow pass builds its resolver from the unit it is about to walk, and the back end
resolves a named callee through the unit whose bodies it is emitting. The DIAGNOSTIC is
what this closed first; the mangling of section 9 then landed for concrete functions,
for constants, and for monomorphized generic instances (#495), so two units may now
declare one name of any callable kind and the two bodies are two symbols.

### 13.2 Collection order: dependencies before dependents

`LEFT.md` item 3, **option B**, and it has LANDED. Option A — one sweep for perk
definitions before the collect loop — landed first, in **#488**, as
`CollectorPass.collect_perk_definitions`. Option B is the class fix, and it belongs here
because **a per-unit scope cannot be built before its dependencies are collected**. It
retired option A with it.

The symptom, as it read before #488: two ordinary units could not implement each other's
perks.

<!-- docs-sweep: skip (records the symptom before #488; two units) -->
```sushi
# helpers/traits.sushi           # main.sushi
public perk Heavy:               use "helpers/traits"
    fn weigh() i32               struct Pallet:
                                     i32 crates
                                 extend Pallet with Heavy:
                                     fn weigh() i32:
                                         return self.crates
```

```
error [CE4003]: unknown perk: Heavy.
error [CE2008]: undefined function 'Pallet.weigh'.
```

That program compiles today. What it cost is a second hand-patch: the pre-sweep answers
the two rules that read the perk table (CE4003, and CE4011 for the marker) ahead of the
loop, and it answers nothing else. Every other order-sensitive table still sees a dependent
before its dependency.

**The order is measured, not incidental.** `topological_sort` (`semantics/units.py:157`)
counts in-degree as "how many units depend on me" and starts its queue at the units nothing
depends on. It therefore yields **dependents before dependencies**, and `main` is collected
first. `_library_units_first` (`semantic_analyzer.py:48`) is a hand-patch that pulls library
units to the front for exactly this reason, and its own docstring says the order "is wrong
for collection".

Ruling 5 replaces `build_global_symbol_table` with a scope built from each unit's own `use`
statements. That scope cannot be built for `main` until `helpers/traits` has been collected,
so the reversal is a phase-1 prerequisite and not a separate improvement.

**What the reversal costs, split by kind.** `LEFT.md` prices option B as "every first-wins
table changes its winner". Under phase 1 that price is only partly paid:

| Kind | Under phase 1 |
|---|---|
| `fn`, `const` | free — they stop being first-wins, because the table is keyed by unit. `_replace_shadowed_functions` retires with them (section 8), and so does 13.1 |
| `struct`, `enum`, `perk` | still flat, so a duplicate names the other declaration as the first one. `CE0004`, `CE0101` and `CE4001` report at the same pair of sites with the roles swapped, and their test batches move with the order |

Phase 2 pays the rest by making the three kinds coexist, at which point nothing is
first-wins and the order stops being observable at all.

**Two hand-patches retired with the reversal**, and they are the measure of what it was
worth. `_library_units_first` went because a library unit is a dependency of everything the
consumer wrote, so a dependencies-first order puts it in front without being told to — once
the graph carries the edge. It did not: `Unit.dependencies` holds user-unit imports alone,
and a source library's units arrive by injection, so `build_dependency_graph` now adds the
edge that an import of an injected unit creates. The `collect_perk_definitions` pre-sweep
went for the same reason as the first patch: a perk declared next door is in the table when
the implementing unit is reached, without a sweep that knows about perks in particular. Two
order-shaped patches for two declaration kinds was the argument that the ORDER was what was
wrong.

**Two positions stopped meaning "the entry unit" by accident.** A synthesized instance goes
to `units[0]` and a lifted body to the first unit with an AST, and both were the entry unit
only while the order put a dependent first. `Unit.is_entry` names it instead. A third,
in the back end, is the same shape: a unit's own module declares that unit's functions
FIRST, so a name it shadows keeps internal linkage and its call binds to its own
definition. One symbol name holds one declaration in a module, which is why the order could
reach that far at all, and section 9's mangling is what ends it.

## 14. What this epic owes `visibility.md`

`visibility.md` is implemented and this document builds on it. It also **supersedes** parts
of it: several of its rulings are stated against one flat namespace, and this epic is what
removes that. A superseded ruling that still reads as current is worse than no document, so
the epic is not done until `visibility.md` says what the compiler does.

**The rule.** `docs/design/visibility.md` is edited as the last commit of each phase, not
before it and not in a follow-up issue. The edit records drift; it never re-argues a
ruling. Where a ruling survives with a narrower reach, the reach changes and the reasoning
stays. Where a ruling is retired, it says which phase retired it and points here.

**`visibility.md` stays normative for the marker.** Which declarations carry `public`, what
the default is, how a method inherits its target's visibility, and the leak fence are its
rulings and this document does not move any of them. What changes is only ever the sentence
that says a name is unique program-wide.

### 14.1 Phase 1's edit

| `visibility.md` | Today | After phase 1 |
|---|---|---|
| §1, "**`public` in Sushi controls callability, not namespacing**" — the deciding fact | privacy can only mean "you may not name mine" | narrows to TYPES. A `fn` and a `const` coexist; a `struct`, an `enum` and a `perk` still do not |
| §1, "The constraint that does not move" — the `CE0004` example | stands for every kind | stands for types only; the `CE0101` twin below it goes, and `CE0105` with it |
| §1, the `CE0101` → `CE3005` cascade example | a fixed defect, recorded | the shape stops being an error at all |
| §8, "**Per-unit namespacing**" — "two units still cannot each declare a private `Node`" | true, and cited as a thing visibility does not decide | true for `Node`, false for a function and a constant. Point at section 7's phase table here |
| §9.1, the four-combination table | four rows, one of them `CE3003` | rebuilt: `CE3003` retires (section 10), `CE3011` narrows to types, and the private/private row becomes legal |
| §9.1, "the merge kept the library's signature", `_replace_shadowed_functions`, "booked as a loser of the name" | the machinery that makes shadowing work | retired (section 8). The per-unit key leaves no winner to displace |
| §9.1, `CW3002` | warns, with no way to make the choice explicit | warns, and `as` is now the answer its `doc` string promised |
| §7, "The manifest protocol is **2.1**" | 2.1 | bumped: section 9 adds the link symbol beside the Sushi name for a binary `.slib` |
| §3 Ruling 2, "an extension inherits its target type's marker" | unchanged | unchanged, plus a pointer to `CW3003` (section 8): the marker is inherited, and claiming a method on a type you did not declare is still a hazard at `--lib` time |
| §4, "Sushi has no name-level import: `use "unit"` brings a whole unit" | true | still true — this epic is unit-level. Point at section 12, which is where a selective import would be decided |

Two of its rulings are load-bearing here and must NOT be softened. §1's "only functions
carried a unit of origin" is the history that section 3.1's provider ruling rests on, and
Ruling 2 — a method is found on the receiver's type — is the reason section 8 cannot put an
extension behind a namespace.

**The edit is made.** §1 narrows its deciding fact to types and records that the
`CE0101` → `CE3005` shape is legal now; §3 Ruling 2 points at section 8's `CW3003`; §4
points at section 12; §7's manifest protocol went to **2.2** with the link symbol; §8's
"Per-unit namespacing" row was rewritten when the per-unit scope landed; and §9.1 carries
the rebuilt four-combination table, where every row is legal and each was measured with a
source library.

### 14.2 Phase 2's edit

Phase 2 finishes the sentence. §1's deciding fact, §1's "constraint that does not move" and
§8's "Per-unit namespacing" all retire together, because nominal identity is what they
appealed to and `docs/design/type-identity.md` is where it changes. `visibility.md` keeps
one line in place of the three: privacy is a marker, coexistence is a namespace, and the
two are decided in different documents.

### 14.3 The other documents

The same last-commit rule applies to whatever else the phase falsifies, and the checklist is
short because the reference map in `CLAUDE.md` names the owner of each:

- `CLAUDE.md` — Known Limitation 15 is written entirely against the flat namespace and is
  what phase 1 and phase 2 close between them; the visibility paragraph gains the alias.
- `docs/language-reference.md`, `docs/language-guide.md` — `use` gains a clause.
- `docs/design/type-identity.md` — phase 2 is its ruling to make, not this document's.
- `docs/design/method-resolution.md` — section 12 leaves it coherence and ownership.
- `docs/libraries.md`, `docs/library-format.md` — the manifest field of section 9.
- `internals/errors/unit.py`, `warnings.py` — the `doc` strings of `CE3011` and `CW3002`
  are the only two in the catalogue that name this document as the design that lifts them,
  so both are edited by the commit that lifts them. `CW3002`'s also says "the one
  combination that would break the link -- both declarations public -- is CE3003 already",
  which stopped being true when section 10 retired that code.

Every item above is done. `CE3011`'s text names `type-identity.md` now, `CW3002`'s names
the alias, `CLAUDE.md` carries the alias and the per-unit scope, and the two language
documents gained the `as` clause when the scope landed.
