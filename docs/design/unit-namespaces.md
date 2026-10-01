# Unit namespaces

**Status: IMPLEMENTED (phase 1).** Phase 2 -- two units may each declare one TYPE -- is not
implemented and belongs to `docs/design/type-identity.md`.

Each unit has its own scope. A `use` brings the public names of one unit into that scope,
and `as` puts them behind a dot instead.

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
puts a boundary around. `visibility.md` decides the marker; this document decides the
scope.

## 1. The model

- A unit sees its own declarations, plus what its own `use` statements bring. The scope is
  not transitive (section 6).
- `use "unit"` puts the unit's names into the flat scope. `use "unit" as u` puts them behind
  `u.` and puts nothing into the flat scope (section 2).
- A qualifier folds into the name that follows it, in every written-name position except an
  array size (section 5).
- An unqualified name resolves to a local, then to a declaration of this unit, then to a
  name a flat import brings. Two flat candidates are `CE3012` (section 8).
- `public use X` re-exports the public names of X (section 8.1).
- A namespace is a resolution path, not a type identity: a type is still one per program
  (section 7).

## 2. Ruling 1: `as` is the gate

`use` takes an optional `as NAME` clause. The clause decides where the imported names
land, and nothing else.

| Form | What it binds |
|---|---|
| `use "math"` | every name `math` brings enters this unit's flat scope |
| `use "math" as my_math` | every name `math` brings is reachable as `my_math.<name>`, and **nothing enters the flat scope** |

<!-- docs-sweep: skip (two units; the sweep compiles one block) -->
```sushi
use "math" as my_math
use <math> as std_math

fn run() i32 | StdError:
    let f64 a = my_math.sin(0.0)??      # the unit next door
    let f64 b = std_math.sin(0.0)       # the standard library
    return Result.Ok(0)

fn main() i32:
    match run():
        Result.Ok(code) -> return code
        Result.Err(_) -> return 1
```

The two forms compose, because each `use` statement contributes what it says and no more:

<!-- docs-sweep: skip (two units; the sweep compiles one block) -->
```sushi
use "math"                              # flat
use <math> as std_math                  # behind a dot

fn run() i32 | StdError:
    let f64 a = sin(0.0)??              # the unit next door -- unambiguous
    let f64 b = std_math.sin(0.0)       # the standard library
    return Result.Ok(0)

fn main() i32:
    match run():
        Result.Ok(code) -> return code
        Result.Err(_) -> return 1
```

The alias is what makes this program expressible: two flat imports that both bring `sin`
are `CE3012` at the call (section 8).

**The `as` clause is optional.** The flat form is not deprecated and gets no warning. It is the right form for a
program's own units, and the qualified form is the right form when two units disagree
about a name or when the reader needs to see where a name came from.

The alias is a single `NAME`. It is not a path, it is not dotted, and it may not be
`_`. The grammar clause is one token wide:

```
use_stmt: USE (stdlib_import | lib_import | user_import) [AS NAME] _NEWLINE
```

`AS` is a token, used after an import path (`external_block`, `grammar.lark:29`)
and in `cast`. Nothing else follows a `use` path, so the clause is unambiguous in LALR.

### 2.1 Where a `use` may stand, and what it reaches

**Every `use` precedes every other declaration**, after the unit's own doc block if it has
one. A `use` below a declaration is `CE3014`.

**A namespace is bound for the whole unit**, not from its `use` statement downwards. The
two halves answer one question that the grammar leaves open in both directions:
`use_stmt` is a `toplevel`, so a `use` may sit anywhere in a file, and a declaration is
already order-independent — a call above its own `fn` compiles. Without the first half an
implementer resolving top-to-bottom would land on statement-scoped bindings by accident;
without the second, a namespace would be the one name in Sushi that has to be declared
before it is used.

Go and Java both make the placement mandatory (imports directly after the package clause,
before any type). Rust allows a `use` anywhere and leaves the position to convention. Sushi
follows Go and Java: a reader should see a unit's dependencies in one block without
searching for them.

`CE3014` is the one rule in this document that refuses a placement the grammar accepts. The
fix is always one line: move the `use` up.

## 3. Ruling 2: one namespace mechanism, two producers

A namespace is a binding from a name to a set of declarations. Two things produce one:

| Producer | Alias | Members | Declared or imported |
|---|---|---|---|
| `unsafe external "C" as libc:` | **mandatory** | foreign functions only | declared in this unit |
| `use "math" as my_math` | optional | see Ruling 3 | imported into this unit |

The alias is mandatory where the namespace is the fence and optional where it is
convenience. An `unsafe external` block has no unqualified form because `libc.printf` is
part of what makes the `ptr` quarantine readable. A unit import has one because the flat
form is the right form for a program's own units (section 2).

Everything else is shared, and is one seam:

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
    StdlibNamespace(module_path, func_table)   # a registry module, keyed by (module, name)
    GenericNamespace(name)                     # an activated built-in, e.g. HashMap
```

`ExternalTable.by_namespace` is one provider behind the table rather than a thing a
pass reads directly, and `resolve_namespaced` answers for every kind. `lookup` returns a
kind-tagged `Binding`: an FFI namespace holds one kind, a unit namespace holds five, and
the resolution rule cannot tell them apart.

**Two readers ask the seam, not one.** The `typecheck` pass is the obvious one. The `scope`
pass asks it too: `_is_namespace` (`passes/scope.py`) is true when a name names a namespace
here and no local shadows it, which is section 8's local-wins rule. Both passes call the
one seam, so the shadowing rule is written once.

### 3.1 A binding holds a provider, and never a path

**The `use` statement's written path reaches the `NamespaceTable` nowhere.** An import is
resolved first; the binding is built from the result.

That is a ruling and not an implementation note, because the obvious alternative is wrong
in a way that passes every test a first implementer would write:

| What the binding could hold | Verdict |
|---|---|
| the **written path** — `h -> "helpers"` | Wrong, and not for the reason it looks. A `use` path is resolved against the MAIN file's directory (`UnitManager(root_path=src_path.parent)`, `compiler/pipeline.py:241`, and `resolve_unit_path`, `semantics/units.py:126-129`), so one written path names one file from every importing unit — `tests/basic/multifile/helpers/bar_module.sushi` writes `use "helpers/math_utils"` from inside `helpers/` and proves it. It fails on packaging instead, which the paragraph below measures |
| the **resolved unit name** — `h -> "lib/foo/helpers"` | Correct, and only if the resolved name is the one stored. A `str` field cannot say which string it wants |
| the **`Unit` object** | Correct, and more than the table needs: it couples a namespace to a unit's identity when all it uses is that unit's symbols |
| the **provider** — `h -> UnitNamespace(...)` | **The ruling.** There is no string to get wrong |

The failure the first two rows invite is real and it is library-shaped.
`_inject_library_source` renames a library's units to `lib/<library>/<unit>` and rewrites
`unit.dependencies` to match (`compiler/pipeline.py:227`) — but it leaves
`UseStatement.path` alone, because nothing resolves through the path. So a binding
built from the written path works for every user unit and breaks the moment a library unit
imports its sibling. **An alias survives packaging because it was never bound to a name that
packaging could change.**

A provider cannot be constructed before the unit it reads has been collected. That is
the collect order of section 6.2, dependencies before dependents. The
two rulings lean on each other by design.

`origin` is carried beside the provider rather than derived from it, and it is for
diagnostics alone: `geo.nope()` has to say which unit `geo` names, and `CE3013` has to point
at what bound the alias first. Nothing resolves through it.

**Two seams, in order.** `namespaces.py` answers *where* a name may be written.
`visibility.py` answers *whether* it may be named at all. A namespace therefore holds a
unit's declarations whatever their visibility, and the private ones are refused at the use
site with `CE3005`, which points at the declaration and says it has no `public`. Filtering
them out of the namespace instead would turn "not yours" into "no such name", which is the
worse diagnostic and the one `CE3005` exists to avoid.

### 3.2 The table is built by its own pass, and it goes after `libraries`

A namespace binding needs two things that different steps produce, so the position is
forced rather than chosen. The pass is named **`namespaces`**, and the order is:

```
collect -> docs -> externs -> libraries -> namespaces -> ffi-clash -> entrypoint -> ...
```

Four constraints fix it there, and each names the step that supplies something:

| A provider needs | Supplied by | Where |
|---|---|---|
| a unit's own declarations (`UnitNamespace`) | `collect` | the collect loop, `semantic_analyzer.py`. Its tables carry a unit key |
| an FFI block's foreign functions (`ExternalNamespace`) | `collect` as well — NOT the `externs` pass | `external_collector.collect(root)`, `passes/collect/__init__.py:216`. The `externs` pass validates the signatures it finds; it does not fill the table |
| a BINARY library's declarations (`UnitNamespace` over a `.slib`) | `libraries` | `LibraryRegistration.register`, `semantics/library_registration.py` |
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
pass goes immediately before it.

**The per-unit scope.** `FunctionTable`, `ConstantTable` and `VisibilityTable` carry a unit
key inside the one shared collector, and the scope of a unit is built from the `use`
statements of that unit.

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
| A static call on a type NOBODY imports — `List.new()`, `f64.from_bits(b)` | `List`, `f64` and `f32` are in scope with no import, so no namespace can ever hold them. `HashMap` is different: the import gates the name, so its static obeys the alias like the type does — `hm.HashMap.new()`, and the bare form behind an aliased import is refused exactly as the bare type is (the fold is `fold_namespaced_static`, section 5) |

### 4.3 The standard library has five shapes, and the rule reads all five

| Shape | Modules | Aliasable |
|---|---|---|
| Registry free functions, already keyed by `(module, name)` | `<time>`, `<math>`, `<sys/env>`, `<sys/process>`, `<random>`, `<io/files>` | **yes** |
| Sushi-source modules, injected as ordinary units | every module in `SOURCE_STDLIB_MODULES` (`semantics/stdlib_registry.py`): `<collections/iter>`, `<compression/zlib>`, `<encoding/msgpack>`, `<io/buf>`, `<io/contracts>`, `<io/error>`, `<io/fs>`, `<io/path>`, the six `<net/*>` modules and `<toolchain/slib>` | **yes** — a user unit in every respect |
| A built-in generic that the import activates | `<collections/hashmap>` (`GenericNamespace`, `semantics/namespaces.py`) | **yes** — `hm.HashMap@(i32, string)`. The import brings the name, so the namespace holds it — see 4.3.1 |
| A method interface: the import enables methods on a type and brings **no name** | `<collections/strings>` | pointless, and said so — see below |
| A predefined enum the import brings | `FileMode` → `<io/fs>`; `IoError`, `FileError` → `<io/error>`; `SeekFrom` → `<io/contracts>`; `NetError` → `<net/error>`; `ProcessError` → `<sys/process>`; `EnvError` → `<sys/env>`; `MathError` → `<math>` | **yes** — `fs.FileMode.Read()`. No unit declares one, so the synthesis stamps each with its HOME (`EnumType.home_module`, the table is `passes/collect/enums.py:PREDEFINED_ENUM_HOMES`); the `namespaces` pass reads the stamp to list it as a member of the home's provider, and the type-position gate (`reject_out_of_scope_type`) reads it to refuse the bare name where the home is not imported, the `HashMap` rule. **The home is reached through the modules that re-export it** (section 8.1): `<io/contracts>` says `public use <io/error>`, `<io/fs>` and `<io/buf>` say `public use <io/contracts>`, so `use <io/fs>` alone brings `IoError`, `FileError` and `SeekFrom` beside `FileMode`, and `fs.IoError` holds behind the alias. `StdError` is the general error enum and stays global. `SeekFrom` is `<io/contracts>`'s because `Seek.seek` takes it |

`stdin`, `stdout` and `stderr` are ordinary names: each is a `public var File` that
`<io/fs>` declares (`sushi_stdlib/src_sushi/io/fs.sushi`). `use <io/fs>` brings them into
the flat scope, `use <io/fs> as fs` puts them behind the dot, and with no
import `stdin` is CE1001. A METHOD INTERFACE is the shape that brings no name: an alias
on `use <collections/strings>` binds an empty namespace, and every `st.<name>` after it
fails one at a time with the cause several lines away.

#### 4.3.1 A built-in generic is activated per unit by its import

`HashMap` is always in the generic-struct table, and the SCOPE decides who may name it.
`HashMap` is a member of the `<collections/hashmap>` namespace (`GenericNamespace`, over
`GENERIC_UNIT_TYPES` in `semantics/namespaces.py`). A flat `use` puts it in the scope of the
importing unit, and an aliased one puts it behind the dot. The generic-struct table stays
flat, exactly as Ruling 6 says a `struct` does: only the right to write the name is per
unit. No process-global flag gates the table.

### 4.4 An empty namespace is a warning, and never an error

**`as` on an import that brings no nameable declaration is `CW3004`**, at the `use`
statement. A warning, not an error, because a namespace can be empty for three different
reasons and only one of them is a mistake:

| Empty because | Example |
|---|---|
| **structural** — a method interface can never bring a name | `use <collections/strings> as st` |
| **by design** — the unit exports methods, not names | a unit that is nothing but `extend` blocks |
| **incidental** — the public surface happens to be empty today | one `public fn` away from changing |

The middle row is the one that decides it, and it is not hypothetical. An extension carries
no marker: it is as visible as its target type (`visibility.md` Ruling 2). So a unit may
consist entirely of extensions, export **nothing nameable**, and still be the reason a
program works:

<!-- docs-sweep: skip (two units; the sweep compiles one block) -->
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

That is one rule per written-name position, and the positions are enumerated,
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
| A static call on a gated type | `DotCall(receiver=MemberAccess)` | `hm.HashMap.new()` — the same three-segment shape as the enum row, folded by `fold_namespaced_static` when the member names the TYPE the namespace holds |

**The AST carries one optional field.** Every node above has `namespace: Optional[str]`,
and the resolver maps `(namespace, name)` to a table key. In phase 1 that key is the bare
name; in phase 2 it would be a qualified one (section 7). The same AST serves both, and the
resolver is the only thing that would change between them.

The enum row is the one that reads as a three-deep chain, and it is not: `my_math.Sign.Plus`
parses as `DotCall(receiver=MemberAccess(Name("my_math"), "Sign"), method="Plus")`, the
alias folds into `Sign`, and what is left is the ordinary `EnumConstructor` path. The `DotCall` ladder
(`resolve_dotcall`, `passes/types/calls/dotcall.py`) has no extra rung for it: the namespace
check is the one namespace rung.

One phase runs BEFORE the fold: propagation, which stamps a GENERIC enum's constructor
with the instantiation its position declares. It reads the enum's name through the alias
for itself (`_enum_receiver_name`, `passes/types/propagation.py`), because a
`my_math.Slot.Filled(x)` reaches it with the `MemberAccess` receiver still in place. The
bare `my_math.Slot.Empty` takes the same reading.

### 5.1 The grammar

Two rules carry a qualifier, both unambiguous because a `.` cannot mean anything else in
either position:

```
atom_type: ... | NAME "." NAME AT "(" type_list ")"  -> qualified_generic_type_t
         | ... | NAME "." NAME                       -> qualified_name_t
perk_constraint_list: perk_constraint ("+" perk_constraint)*
perk_constraint: NAME ["." NAME]
```

The perk rule is a split as well as a qualifier: `perk_constraint` is the rule that holds
the second segment.

**No expression position needs grammar.** `my_math.sin(0.0)` parses as a `DotCall`,
`my_math.MAX_DEPTH` as a `MemberAccess`, and `my_math.Sign.Plus` as a `DotCall` over a
`MemberAccess`. All three reach the namespace resolver.

A qualified call carrying explicit type arguments parses as well, and that is worth
spelling out because it looks like the counter-example. A type argument is REQUIRED where
the type parameter appears only in the return type, because there is no argument to infer
it from:

<!-- docs-sweep: skip (illustrative: `iter` exports no `empty_list`) -->
```sushi
use <collections/iter> as it

fn main() i32:
    let List@(i32) a = it.empty_list@(i32)()
    return 0
```

LALR reduces `.empty_list` to `member_access` and not to `method_call`, because the token
after it is `AT` and not `(`. The chain therefore arrives as
`maybe_call: atom(it) member_access(.empty_list) call(@(i32) ())`. Omitting the argument is
CE2060, so if the builder refused a `type_list` on a call whose callee is not a bare `Name`,
**aliasing a unit would make its return-type-only generics uncallable.**

So `DotCall` carries `type_args` (`semantics/ast.py`), and `CE6102` is raised in the
typecheck pass (`_reject_call_site_type_args`, `passes/types/calls/dotcall.py`) when the
receiver is a VALUE. It cannot be a `SyntaxDiagnostic`: the builder cannot know whether a
receiver `Name` is a bound alias, and only a pass that has the `NamespaceTable` can.

The rule behind CE6102 holds. Section 5's folding turns `it.empty_list@(i32)()` into
`empty_list@(i32)()` resolved against one unit, which is a direct call to a named free
function — exactly what CE6102 permits.

### 5.2 A pattern is a written-name position, and its grammar takes the qualifier

A `match` arm is not an expression, and it does not reach the constructor path. It has its
own production. Without a third segment in it, an aliased unit's enums would be
**write-only**: you could construct one and never take it apart.

<!-- docs-sweep: skip (two units; the sweep compiles one block) -->
```sushi
use "geometry" as geo

fn main() i32:
    let geo.Sign s = geo.Sign.Plus      # constructing: Ruling 4 covers it
    match s:
        geo.Sign.Plus -> println("+")   # matching: the qualified pattern
        geo.Sign.Minus -> println("-")
    return 0
```

This is not a corner. A `match` is how Sushi consumes an enum — exhaustiveness-checked,
with a payload binding required — so an enum you cannot match is an enum you cannot use.
`Result` and `Maybe` do not show the need: both are built-ins, both are always in scope,
and neither is ever written qualified. The need is on a user enum from an aliased unit.

The grammar takes the qualifier the same way every other position does:

```
pattern: [NAME "."] NAME "." NAME ["(" [pattern_list] ")"]
```

Nesting needs nothing further: `pattern_item` admits a `pattern`, so
`Shape.Wrap(geo.Sign.Plus)` works through the production above. The resolution rule is the
same — section 5's folding strips the leading segment and the arm resolves against one
unit, so exhaustiveness, payload binding and the literal-arm rules (CE2074 / CE2075 /
CE2076) read what they read for an unqualified arm.

A qualifier that names nothing is refused as **a name that does not exist**, and the code
depends on the position. In an expression a leading `NAME .` may be a value, so an unbound
qualifier falls through to the ordinary rules and answers `CE2008` or `CE1001`. In a type
position it cannot be anything else, and the answer is `CE2001`, with a help line that
names the import which would bring the name. `CE3012` is the AMBIGUITY code and answers a
different question -- too many candidates, not none.

### 5.3 One position cannot be qualified

An array size may not be qualified. `i32[my_math.SIZE]` is refused. A fixed array's size is
read while the unit's own AST is built (that is also why a constant next door is a
value and not a size), and an alias is bound long after that. The
diagnostic is `CE2099`.

### 5.4 A constant declaration is two written-name positions

A `const` (and a `var`) writes a type and an initializer, and both take the dot:

<!-- docs-sweep: skip (two units) -->
```sushi
use "shapes" as sh

const sh.Shape SMALL = sh.UNIT               # a type, and a constant
const i32 DOUBLE = sh.SIZE * 2               # a constant in an expression
const sh.Point ORIGIN = sh.Point(y: 0, x: 0) # a struct constructor, named or positional
const sh.Shape TINY = sh.Shape.Circle(2)     # a variant, with or without a payload
```

A flat `use "shapes"` brings the same names bare. Three mechanisms make this work, and each
is the rule the body path uses:

| Position | How it resolves |
|---|---|
| a flat foreign constant | the record carries its declaration (`ConstSig.decl`), so the evaluator finds the declaration in the one program-wide `ConstantTable` |
| a qualified name | the evaluator asks the namespace seam, folds `sh.Shape.Circle(2)` to `Shape.Circle(2)` as the body does, reads a struct through the stand-in `Call` the body uses, and stamps the node for the back end |
| a qualified type | the `resolve` pass resolves the RECORD's type; the declaration keeps the written type until `validate_constant` rules on it, as a `let` does |

**A foreign initializer is read in the scope of the unit that wrote it.** `shapes` may
declare `public const i32 BIG = WIDTH * 2` with `WIDTH` from an import of its own; a
consumer folding `BIG` must see `shapes`' `WIDTH` and never its own. The evaluator
switches unit for the fold (`ConstantEvaluator._in_unit`), which is why it takes every
unit's namespace table (`SymbolTables.namespaces`) and not one scope. The cycle check
keys by declaration for the same reason: this unit's `SIZE = sh.SIZE * 2` is two
constants, not a cycle.

One more fold rides on this: `sh.Point(y: 0, x: 0)` parses as a METHOD CALL on `sh`. The
builder carries the field names on that path and writes the reordered arguments back onto
the node, in a body as in a constant.

## 6. Ruling 5: scope is per unit, and it is not transitive

A unit sees its own declarations, plus what its own `use` statements bring. Nothing else.
The scope of a unit is built from the `use` statements of that unit.

Three consequences, all deliberate:

**Two units may export one name.** Two units exporting `sine` is not an error. It is an
error only where an unqualified `sine` is written in a unit that imported both flat, and
then the diagnostic is at that use, names both candidates, and says that `as` resolves it
(`CE3012`).

**An import is not re-exported, unless it is a `public use`.** `my_math.<name>` reaches what
`math` *declares*. It never reaches what `math` imported with a plain `use`. The qualified
form and the flat form agree on this, which is what makes the rule one rule -- and they
agree on the exception too: what `math` says `public use` on is `math`'s to hand on, flat
and behind the dot alike (section 8.1).

**A name does not arrive through the import of another unit.** If `top` imports `mid` and
`mid` imports `deep`, `top` must write `use "deep"` to call a function of `deep`.

`CW3001` ("duplicate use statement") is a repeat with the same alias, or with none.
`use "math"` followed by `use "math" as m` is not a duplicate — the two statements do
different things.

### 6.1 You must import what you name

Non-transitivity has one consequence that is easy to miss, so it is ruled here rather than
discovered: **a public signature may name a type its caller cannot name.**

<!-- docs-sweep: skip (three units, and the body is elided) -->
```sushi
# geometry.sushi          # shapes.sushi              # main.sushi
public struct Vec:        use "geometry"              use "shapes"
    f64 x                 public fn origin() Vec:     fn main() i32:
                              return Vec(...)             let Vec v = origin()
```

`main` may call `origin()`, because `origin` is in scope. It may not write `Vec`, because
`geometry` is not imported. And it cannot avoid writing it: `let` requires a written type
(`error [CE2007]: missing type annotation`), so there is no binding it can make.

**The rule: to name a type, import the unit that declares it.** `main` adds
`use "geometry"`. That is Java's rule, and it is the answer for phase 1.

The five languages worth comparing each have an escape from this rule. Sushi's escape is
`public use` (section 8.1):

| Language | Names it without the import? | The escape |
|---|---|---|
| Go | no | `v := shapes.Origin()` infers; `type Vec = geometry.Vec` re-exports |
| Rust | no | `let v = shapes::origin();` infers; `pub use` re-exports; a value of an unnameable type is still usable |
| Swift | no | `let v = origin()` infers; `@_exported import` re-exports |
| Java | **yes** | a package name is global, so `java.util.List` needs no import; `var` since Java 10 |
| C++ | **yes** | namespaces are global and includes are transitive; `auto` since C++11 |
| **Sushi** | **no** | `public use` re-exports (section 8.1) |

Every one of the five infers a local binding's type. Sushi does not, and the two that also
allow a bare qualified name get there through a global, canonical package name — which a
Sushi unit path, being relative to the importing file, cannot be.

So the cost of this ruling is real: a consumer inherits the type-declaring dependencies of
everything it imports, on every `let`, unless a unit re-exports them. Two things lift it:
re-export, which is decided and built (`public use`, section 8.1), and `let` inference,
which `CE2007` marks the exact site of and which does not exist. Phase 1 accepts the cost
rather than a third mechanism to avoid it.

### 6.2 Collection order: dependencies before dependents

The collect pass runs the dependencies of a unit before the unit, because the scope of a
unit cannot be built before the units it imports are collected. The dependency graph also
holds the edge that an import of an injected source-library unit makes
(`UnitManager.build_dependency_graph`, `semantics/units.py`), so a library unit is
collected before every unit that imports it, and a perk declared in another unit is in the
table when the implementing unit is reached. `Unit.is_entry` names the entry unit; no
position reads it from the order.

A `struct`, an `enum` and a `perk` are one per program (section 7), so a duplicate of
one names the declaration that was collected first.

## 7. Ruling 6: a namespace is a resolution path, not a type identity

This is the line between the two phases, and it is the one place where the model gives less
than the syntax suggests.

`my_math.Vec` **names** the type `Vec`. It does not create a type `math::Vec`. Gating makes
a name unwritable unqualified. It does not make two declarations of one name coexist.

| Kind | An alias makes it writable | Two units may declare it |
|---|---|---|
| `fn`, `const` | phase 1 | phase 1 |
| `struct`, `enum`, `perk` | phase 1 | **phase 2** |

Coexistence for a function or a constant is a table-keying rule: `FunctionTable` and
`ConstantTable` are keyed by unit, and the flat scope is a resolution built on top. Two
units may each declare a private `fn helper` or a private `const SCRATCH`, and section 8's
ladder answers each unit's call with its own. The front end gets that free; the BACK end
pays for it with
section 9's mangling, because the monolithic build path puts every unit into one module
where two `internal` symbols collide as readily as two `external` ones.

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
scope, exactly as a local shadows an FFI namespace
(`passes/types/__init__.py:152`). One rule, one seam, both producers.

**An alias may not collide.** An alias binds a name in the unit that wrote it, so it is
`CE3013` if that unit already binds the name: another alias, an FFI namespace, or one of
its own declarations. Two aliases for one unit are legal and both work; the same alias
twice is not.

**A unit's own declaration wins over a flat import.** That gives one ladder for an
unqualified name, and it is short:

| Order | Candidate |
|---|---|
| 1 | a local variable or a parameter — the ordinary scope rules |
| 2 | a declaration of this unit |
| 3 | a name a flat `use` of this unit brought in — one candidate resolves, two or more is `CE3012` |

Row 3 holds a registry stdlib module's CONSTANT as it holds its functions. One scope-aware
lookup answers `PI`, `E` and `TAU` (`stdlib_registry.lookup_stdlib_constant`), at row 3: a
unit that did not import `<math>` has no `PI`, a unit's own `E` wins, and a `const`
initializer folds `PI / 2.0` like any other constant.
`tests/unit/test_stdlib_constants_take_the_ladder.py` keeps the module's hooks readable
by the registry alone.

#### The KIND ladder

The table above says WHICH declaration a name resolves to. Underneath it there is a
second ladder, over the KINDS a bare name can reach, and it has to be as explicit,
because a pass that walks an expression acts on the kind and not on the unit:

| Rung | Kind | What a value position does with it |
|---|---|---|
| 1 | a local or a parameter | reads it; it wins over every rung below |
| 2 | a `const` or a unit `var` | reads it |
| 3 | a registry module's constant | reads it |
| 4 | a function, generic or plain | takes its value |
| 5 | a `use ... as` alias, or an FFI namespace | **not a value** |
| 6 | a type name — struct, enum, either one's generic form, a primitive | **not a value**: `CE2105` |
| — | nothing at all | `CE1001`, with the import that would bring it |

**A type name in a value position is `CE2105`**, because the fault is the POSITION and not
the name: `let i32 x = Color` names a declared enum where a value must stand. The receiver
position follows the same rule with `CE2102`.

**The order lives in one module** (`semantics/name_ladder.py`) and the LOOKUPS stay each
pass's own, because they are not the same lookups: the typecheck pass resolves a
constant and a function through the asking unit's view (section 9.1), and the scope
pass has no unit view of the function table at all. A consumer answers one question per
rung and `classify` walks them, so a rung cannot be dropped or reordered in one pass
alone. `tests/unit/test_bare_name_ladder_is_one.py` is the gate: every rung is
reachable, a higher rung wins over the one below, and every value position -- an
initializer, a hole, a `return`, an argument, an operand, an index, a borrow -- reads
`CE2105` for all four kinds of type name.

A type name stays legal in every WRITTEN-name position, and none of them reaches this
ladder: an annotation, a constraint, and its own dot, where the name is a member of the
type and not a value (section 5, and `method-resolution.md`).

#### One name, one declaration, in one unit

Row 2 holds ONE declaration for a name. In one unit, one name has one declaration, whatever
its kind: `fn`, `const`, `var`, `struct`, `enum` and `perk` share one set of names. The
SECOND declaration in source order is `CE1005`, and its note points at the first. The first
keeps the name, and the refused declaration enters no table, so the uses of the first give
no more errors. Two declarations of ONE kind keep that kind's code (`CE0004`, `CE2046`,
`CE4001`, `CE0101`, `CE0105`), and a struct beside an enum is `CE0006`. The
collectors run kind by kind, so the collection order is not the source order: one walk over
the unit's declarations, sorted by position, decides the holder of each name before any
collector runs (`semantics/passes/collect/unit_names.py`).

Across units a name may be used again, and the ladder above decides: the unit's own
declaration wins over a flat import, and two imported names are reached with `use ... as`.
A CALL follows the same order. `box()` calls the unit's own `fn box` when a flat import
brings a struct `box`, and the struct is `sh.box(...)` behind the alias
(`name_ladder.call_constructs_struct`, asked by the typecheck pass and by the back end).
Two TYPES of one name in two units stay refused, because a type is one per program
(`type-identity.md`).

Row 2 beating row 3 is also the rule the linker follows: a private function has internal linkage, so the consumer's call binds to the
consumer's definition (`visibility.md` decision 10). It warns — `CW3002` — because
shadowing an export is rarely intended and the reader of the call site cannot see which
declaration answers. `as` is what makes the choice explicit, and `CW3002`'s `doc` string
says so.

The table is keyed by unit, so no declaration displaces another: row 2 is a lookup in the
asking unit, and row 3 is a lookup in what it imported.

A built-in's precedence is not in this ladder. `docs/design/method-resolution.md` owns that
rule and this document does not restate it.

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
and the combinators of `<collections/iter>` (`.map`, `.filter`, `.fold` on `List@(T)` and
`T[]`) have exactly this shape. **A duplicate is a hard
error**: two units extending `i32` with `tag()`
gives `CE0101: duplicate function 'extension method 'tag' for 'i32''`.

Refusing here rather than at the call is deliberate, and it is the one place this document
prefers the eager answer. A method has no written qualifier, so a lazy diagnostic would name
an ambiguity the user has no syntax to resolve. Go forbids the shape outright (a method may
only be declared in the package that declares the type); Rust forbids it with the orphan
rule; Swift allows it and diagnoses at the call; Java has no extension methods. Sushi allows
it like Swift and refuses like Go.

**The diagnostic blames neither unit.** When the two extensions come from two libraries,
neither author is at fault and the consumer can edit neither. So when the two declarations
come from two units, `CE0101` is relational: a note per unit, `unit '<name>' declares it
here`, and no side blamed (`passes/collect/functions.py`, both the concrete and the
generic-target site). One unit writing both gets the plain duplicate shape, because there
the second one IS the duplicate.

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

**A perk implementation never warns, whatever its target.** The hazard CW3003
names is a claim with no escape: a consumer holding two colliding plain extensions can edit
neither. A perk implementation's claim has the escape built in — the consumer's OWN
implementation is the sanctioned override and wins over a shipped one
(`tests/libs/shipped_perks/test_lib_perk_impl_local_override.sushi` is the measured proof). So
`extend i32 with Doubler` in a library stays quiet, and the two library fixtures of that
shape keep building clean. The predicate lives in `semantics/foreign_extensions.py`, one
function for both consumers: the CW3003 emitter in the pipeline and the manifest extractor.

The source stdlib holds many extensions and perk implementations: the combinators in
`src_sushi/collections/iter.sushi` are on builtin generic targets (`List@(T)`, `T[]`), and
the `io/*` and `net/*` modules extend the types that each module declares. A bundled stdlib
module is not a `--lib` build, so CW3003 does not apply to it. CW3003 fires nowhere in real
library code, which is what a warning aimed at a future hazard should do.

**The consumer's half.** `--lib-info` lists the foreign types a library claims methods on,
so the hazard is readable before it is hit: the manifest carries them as
`foreign_extensions` (absent when the library extends only what it declares), and both
report renderers print the section as `extend <type> <method>` lines
(`docs/library-format.md`).

**What the author does about it is a prefix**, and that is worth saying plainly. C has no
answer here, which is why `png_read_info` and `SDL_Init` carry a prefix, and a method name
is the one place Sushi stays in that row. It is deliberate. A method is found on the
receiver's type (Ruling 3), so there is no namespace to put it behind, and the alternative —
making method resolution depend on the calling unit — is the cost `visibility.md` Ruling 2
measured and refused. Sushi buys namespaced NAMES and keeps prefixed METHODS, and extending
a type you do not own stays a feature with a price attached.

**An alias is local.** It is written in one unit and is not visible in any other, not even
one that imports the aliasing unit. Nothing about it is exported, so neither `.slib` production nor
the library manifest carries it. A library unit's own alias is a different question and
section 3.1 answers it: the binding holds a provider, so the unit rename that packaging
performs cannot reach it.

**The namespace is the unit, never the library.** `use <lib/foo/bar> as f` binds unit
`bar` of library `foo`, because that is what the import names. A library whose public API
spans several units and wants a single namespace ships one façade unit that says
`public use` on each of the others (section 8.1). That is a library design choice, and
`docs/design/libraries.md` is where it belongs.

### 8.1 Ruling 7: `public use` re-exports

Section 6.1 measures the cost of a non-transitive scope -- a consumer imports every unit
that declares a type it binds, on every `let`. The predefined enums make the cost concrete:
`IoError` has its home at `<io/error>`, so without a re-export a program that writes
`use <io/fs>`, calls `open()` and matches `IoError.NotFound` needs a second import for a
name it never chose. Go, Rust and Python all answer the same way: the module whose API
answers a type re-exports it (`os.FileInfo = fs.FileInfo`, `pub use`, a package
`__init__`). Sushi names types more
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
2. Only a `public use` re-exports. A plain `use` in U brings nothing to U's importers. Re-exports compose along `public use` chains -- if X says `public use Y`, U's
   importers get Y -- and never along a plain `use`. A cycle of `public use` is legal and
   terminates on a visited set.
3. Every kind of `.slib` carries a re-export. A source `.slib` needs nothing -- the consumer re-parses the
   statement. A binary or hybrid one ships a `reexports` list in the manifest, one record
   per statement: the target, the unit that wrote it, and which of the three producers the
   target is, so the consumer reaches the same provider builder the written statement
   would have. `docs/design/libraries.md` section 5 carries the two consequences: the
   `units` index is what finds a façade unit that declares nothing of its own, and a
   re-exported stdlib module reaches the consumer's build through the record because no
   `use` line of its own does.
4. The predefined enums stay synthesized and homed (Ruling 3). A module makes a home
   reachable by re-exporting it -- `public use <io/error>` in `<io/contracts>` -- and the
   stamp machinery (`homed_enums`, `UnitScope.holds_home`, `reject_out_of_scope_type`)
   is read through the re-export. A registry (Python) module has no `use`
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
`Result<string, IoError>` interns once: a program that names `Vec` bare through two hops and
behind two aliases, and `IoError` bare and behind two aliases, gets no CE0126. The fixtures
under `tests/namespaces/reexport/` hold it.

**Mechanics.** The grammar takes `PUBLIC? USE`; `UseStatement.is_public` and
`public_span` carry it. A provider composes what it re-exports: `Provider.reexports` is the
tuple the unit's `public use` statements name, `Provider.reaches` walks the chain once
with a visited set, and `lookup`/`members` read the walk -- own first, then each
re-export in written order, then theirs. A binding a re-export answers carries the
re-export's provider, so a call through `sh.origin` routes to the unit that declares
`origin`. The flat scope reads the SAME walk: `_scope_of` puts every reached unit, module
and generic into `UnitScope`, so the dot and the bare name cannot disagree. The
`namespaces` pass stays where section 3.2 puts it -- a provider needs only what `collect` and
`libraries` produce, and a unit's own AST, which it has. A COMPILED library's unit has no
AST, so `_binary_reexports` reads the manifest record in its place and composes the same
way, with the same chain and the same visited set.

**Tests.** `tests/namespaces/reexport/`: the flat and the aliased import of a re-exporting
unit; a two-hop chain; a plain `use` in the middle that re-exports nothing (CE2001 with the
import in the help); `public use ... as` (CE3016); a `public use` below a declaration
(CE3014); two re-exports offering one name (CE3012); an own declaration beside a re-export;
one declaration reached twice; a `public use` of a unit with nothing public (CW3005). The
stdlib half: `use <io/fs>` alone writes `| IoError`, `IoError.NotFound` and
`SeekFrom.Start`; `use <io/fs> as fs` gives `fs.IoError`; `use <net/tcp>` alone matches
`IoError` from a read. The fixtures under `tests/namespaces/reexport/` hold rule 3.

**A library re-exports a library** (#1106). `public use <lib/b>` in a library A is the
same rule as `public use <io/error>` in `<io/contracts>`. The consumer's build finds B on
`SUSHI_LIB_PATH` as if the consumer wrote `use <lib/b>`: a source A by its re-parsed
statement, a compiled A by its `kind: "library"` record (`_resolve_library_imports`,
`compiler/pipeline.py`, follows both; the monolithic link reads the record through
`reexported_libraries`). Only a `public use` loads B; a plain `use <lib/b>` in A stays
local. A B that the consumer also imports for itself is one candidate, because the
candidates count by declaring unit. A B that is not on the path is CE3502 at the
consumer, with a note that names A's `public use`.

**What this does not decide.** Whether a `public use` may re-export a single name
(`public use "geometry".Vec`). It is open until asked for.

## 9. What the back end needs

Nothing, for the syntax. Rulings 1 to 4 are front-end resolution, and the back end is
handed a resolved callee.

One thing, for coexistence. Every symbol carries its unit, because two units may declare
one name. Two units each declaring `sine` need mangling by unit — and for **private** declarations as
well as public ones, because the monolithic build path puts every unit into one
`ir.Module` (`LLVMDriver.compile_multi_unit`, `backend/driver.py`, over
`build_module_multi_unit`), where an `internal` symbol collides just as an `external` one
does. Only the incremental path emits a module per unit (`backend/codegen_llvm.py`, the
`ir.Module` named `unit_<name>`).

The scheme: `<unit>$<name>`, with every `/` in the unit name replaced by `$`, so
`collections/iter`'s `next` becomes `collections$iter$next`. One function writes it,
`mangle_unit_symbol` (`sushi_lang/semantics/unit_symbols.py`), read by the back end when
it declares and by the `.slib` producer when it records. `$` is legal in an LLVM
identifier and lies outside the alphabet of every existing symbol component, so the
generic mangler's structural invariant (D) holds
(`semantics/generics/name_mangling.py:11`).

Four things are exempt, and each for its own reason:

- **`main`.** The linker needs the name, and the `entrypoint` pass guarantees one
  program declares one.
- **An FFI `link_name`.** It names a C symbol that somebody else compiled.
- **A lifted lambda.** Its name already carries the per-unit lifter's counter.
  A monomorphized INSTANCE is not exempt: it goes home to the unit that declared
  its generic and takes that unit's prefix, so two units' instances of one
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

A bare name alone does not identify a declaration, so the tables that hold declarations carry
two views: the unit that made each declaration, and the flat name. `UnitKeyedSymbols`
(`sushi_lang/semantics/unit_symbols.py`) is the one implementation, and the rule that
reads it is `FunctionTable.lookup`'s — the asking unit's own declaration answers first,
and the flat view answers everything else. One rule, so the back end cannot disagree with
the collect pass about what a name means inside a unit.

Every reader asks with a unit: the typecheck pass (`TypeValidator.func_sig`,
`TypeValidator.const_sig`), the borrow pass (`view_for`) and the back end
(`codegen.emitting_unit`).

### 9.2 A binary `.slib` names the symbol, and every record names its unit

Manifest protocol **2.3** (`sushi_lib_version`), two keys, and they answer different questions.

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

This document owns these codes:

| Code | Raised at | Says |
|---|---|---|
| **CE3012** | an unqualified use | the name is offered by more than one flat import; names every candidate, and says `as` resolves it |
| **CE3013** | the `use` statement | the alias is already bound in this unit; the note points at what bound it |
| **CE3014** | the `use` statement | a `use` below a declaration; every import comes first (section 2.1) |
| **CW3003** | an `extend` of a foreign type, at `--lib` build time ONLY | this library claims a method on a type it did not declare (section 8). Not gated on either phase: it needs the target's declaring unit and the `--lib` flag, and nothing else this document adds |
| **CW3004** | the `use` statement | `as` bound an empty namespace (section 4.4). A warning because a namespace is empty for three reasons and only one is a mistake |
| **CE1005** | the second declaration | one unit declares one name twice, in two kinds (section 8); the note points at the first declaration |

Reused rather than duplicated:

| Code | Also answers |
|---|---|
| `CE3005` | `my_math.helper` where `helper` is private to `math`. The two-seam rule (section 3) routes it here |
| `CE2008` | `my_math.nope()` where `math` declares no `nope` at all. `NamespaceTable.members` supplies the "did you mean" help line |
| `CE2099` | a qualified array size (section 5.3) |
| `CW3002` | a unit's own declaration wins over a flat import and warns (section 8) |
| `CE2007` | load-bearing. It is why section 6.1 needs an escape: a `let` cannot infer, so a type that cannot be named cannot be bound |
| `CE6102` | "Explicit type arguments only on a direct call to a named free function" reads the RECEIVER, not the parse shape, so a qualified call may carry them (section 5.1) |

Four more codes, and the scope each one has in this design:

| Code | Scope |
|---|---|
| `CE3011` | a TYPE name a consumer redeclares against a library, imported flat or behind an alias. An alias does not help a type -- identity is nominal, so one name is one shape however the name is written. A function or a constant of that name coexists: the tables are keyed by unit, so each declaration takes its own global |
| `CW3001` | a repeat with the same alias, or with none (section 6) |
| `CE0101` | a duplicate extension on a foreign type, relational, naming both units and blaming neither (section 8); and a duplicate callable in ONE unit. A private function in each of two units coexists (section 7), a library's private function and a GENERIC function included: its table carries the two views, and its instance takes its declaring unit's symbol prefix |
| `CE0105` | a duplicate constant in one unit, or against a library's PUBLIC constant, which the consumer can see and read. A private constant in each of two units coexists (section 7) |

## 11. What this does not decide

**Selective import.** `use "math" { sin, cos }` — a name-level form. Ruling 1 gives the
unit level only. `visibility.md` section 4 records that Rust's trait rule is expressible
only on top of a name-level import, so this question also decides whether a sealed perk
becomes possible. It is a separate feature and it composes with everything here.

**Re-export of one name.** Section 8.1 decides `public use X`. It leaves open a name-level
form (`public use "geometry".Vec`) and re-exporting a binary library.

**A wildcard, and a nested namespace.** `my_math.*` and `a.b.c` are both out. A namespace
is one flat set of names bound to one alias.

**Coherence.** Section 8 rules that an orphan extension is legal and a duplicate is a hard
error. It does not rule on OWNERSHIP — whether a unit should be allowed to extend a type it
did not declare at all, which is Rust's orphan rule and Go's package rule. That question
is wider than this document (`extend i32 squared()` from any unit works) and it sharpens
under phase 2, where two units may extend two different types that share a name. It belongs
with `docs/design/method-resolution.md`.

**`let` inference.** Section 6.1 measures its absence and rules without it. Adding it is a
language change with a much wider blast radius than a namespace, and `CE2007` is where it
would land.

**Per-alias visibility.** An alias is local to its unit (section 8) and carries no marker.
Whether an alias could ever be exported is the same question as re-export.
