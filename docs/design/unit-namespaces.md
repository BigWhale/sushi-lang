# Unit namespaces

**Status: DECIDED, not implemented.** The draft that this replaces measured a problem and
surveyed the answers. This document rules on them.

Sushi has one flat global namespace. `use` puts a whole unit into it and gives you no way
to say which unit you meant. This document says what replaces that.

It is normative for eight things:

1. What `as` does, and what an import without `as` keeps doing.
2. Where a `use` may stand, and how far the namespace it binds reaches.
3. That one namespace mechanism serves the FFI block and the `use` statement together, and
   that what it binds is a resolved provider rather than a written path.
4. Which declarations a namespace holds, and which it cannot hold.
5. How a qualified name resolves, in every position where a name can be written.
6. That a unit's scope is built from its own `use` statements, is not transitive, and
   therefore that a type must be imported to be named.
7. That a namespace is a resolution path and not a type identity — the line between the
   two phases.
8. That an orphan extension is legal and a duplicate one is refused, because no namespace
   can choose between two methods.

Read `docs/design/visibility.md` first: it decides which declarations are even candidates
for a namespace. Read `docs/design/type-identity.md` for the constraint that section 7
puts a boundary around. Section 13 carries the two limits of issue #487 that wait for this
epic, so the epic is not done until they are gone.

## 1. What the compiler does today, measured

### 1.1 The four refusals

| Case | Today |
|---|---|
| `use "math" as my_math` | `CE6001: unexpected token 'as'`. The rule is `use_stmt: USE (stdlib_import \| lib_import \| user_import)` (`sushi_lang/grammar.lark:7`) — no alias clause exists |
| Two units, each with `public fn sine` | `CE3003: duplicate public symbol 'sine' found in units: liba, libb`, raised with no location at all (`semantics/units.py:229`). **The program cannot be compiled**, and no escape exists |
| Two units, each with a private `fn helper` | `CE0101: duplicate function 'helper'` — a name neither unit exports, refused anyway. Section 7 retires this row in phase 1 |
| Two units, each with `struct Node` | `CE0004: duplicate struct 'Node'` |

The last two are not aliasing problems. A `StructType` compares and hashes on its name
alone (`semantics/typesys.py:75`), so one name **is** one type for the whole program.
There is nothing to alias: it is the identity that would have to change.

Privacy does not help. A private declaration still occupies the one namespace — that is
`visibility.md` section 1's deciding fact — so making a declaration private frees no name.
`CE3011` is the interim rule that follows from it: a consumer may not declare a name that
a library or a bundled stdlib module already declares, because in one namespace the
library's own bodies would then call the consumer's function. That code's `doc` string
names this document as the design that lifts it.

### 1.2 The namespace is not only flat, it is transitive

Three units. `top` imports `mid`, `mid` imports `deep`, and `top` never mentions `deep`:

<!-- docs-sweep: skip (records today's behaviour; a three-unit program) -->
```sushi
# deep.sushi                      # mid.sushi              # top.sushi
public fn deep_value() i32:       use "deep"               use "mid"
    return Result.Ok(7)                                    fn main() i32:
                                                               let i32 a = deep_value()??
```

It compiles, links and prints `7`. `build_global_symbol_table` (`semantics/units.py:211`)
builds ONE table for the program, so every public name of every unit anywhere in the
dependency graph is in scope in every unit. A `use` statement today does not decide what
this unit can see. It decides what gets compiled.

### 1.3 The standard library wins over your own function, and then crashes

`check_stdlib_function` (`passes/types/calls/user_defined.py:296`) searches a hard-coded
list of module paths and returns the first hit. It runs at line 165. The user function
table is not consulted until line 170.

<!-- docs-sweep: skip (records today's behaviour: this crashes the compiler) -->
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
| `ExternalTable.by_namespace: Dict[str, Dict[str, ExternalSig]]`, with `is_namespace(ns)` and `lookup(ns, name)` | `passes/collect/externals.py:31` |
| `_resolve_external_call` — a `DotCall` whose receiver is a `Name` that is a registered namespace **and not a local variable** | `passes/types/__init__.py:136` |
| The grammar's `AS NAME` clause | `grammar.lark:29` |

Two properties carry over unchanged. A local variable shadows a namespace, so `libc` as a
variable name is not stolen by the FFI block. And the namespace is bound by the
*declaration*, not derived from a file name, so the author chooses the word.

The standard library is namespace-shaped too, in a second place:
`FunctionTable._stdlib_functions` is keyed by `(module_path, name)`
(`passes/collect/functions.py:205`). `stdlib_by_name()` is the function that throws the
module away.

## 2. Ruling 1: `as` is the gate

`use` gains an optional `as NAME` clause. The clause decides where the imported names
land, and nothing else.

| Form | What it binds |
|---|---|
| `use "math"` | every name `math` brings enters this unit's flat scope — unchanged |
| `use "math" as my_math` | every name `math` brings is reachable as `my_math.<name>`, and **nothing enters the flat scope** |

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
use "math" as my_math
use <math> as std_math

fn main() i32:
    let f64 a = my_math.sin(0.0)??      # the unit next door
    let f64 b = std_math.sin(0.0)??     # the standard library
    return Result.Ok(0)
```

The two forms compose, because each `use` statement contributes what it says and no more:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
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

`ExternalTable.by_namespace` becomes one provider behind the table rather than a thing the
type pass reads directly, and `_resolve_external_call` becomes `resolve_namespaced`, which
answers for every kind. The refactor's whole content is that `lookup` returns a
kind-tagged `Binding` instead of an `ExternalSig`: an FFI namespace holds one kind, a unit
namespace holds five, and the resolution rule cannot tell them apart.

### 3.1 A binding holds a provider, and never a path

**The `use` statement's written path reaches the `NamespaceTable` nowhere.** An import is
resolved first; the binding is built from the result.

That is a ruling and not an implementation note, because the obvious alternative is wrong
in a way that passes every test a first implementer would write:

| What the binding could hold | Verdict |
|---|---|
| the **written path** — `h -> "helpers"` | Wrong always. A path is relative to the importing file, so `use "helpers"` names different files in `a/b.sushi` and in `c/d.sushi`. It is not a global key |
| the **resolved unit name** — `h -> "lib/foo/helpers"` | Correct, and only if the resolved name is the one stored. A `str` field cannot say which string it wants |
| the **`Unit` object** | Correct, and more than the table needs: it couples a namespace to a unit's identity when all it uses is that unit's symbols |
| the **provider** — `h -> UnitNamespace(...)` | **The ruling.** There is no string to get wrong |

The failure the middle rows invite is real and it is library-shaped.
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
| `string`, `file`, `List`, `Result`, `Maybe`, `Own` | In scope with no import. Nothing brought them, so no namespace holds them |
| A private declaration of another unit | Not a visibility carve-out — see Ruling 2's second seam. It is a member, and naming it is `CE3005` |

### 4.3 The standard library has four shapes, and the rule reads all four

| Shape | Modules | Aliasable |
|---|---|---|
| Registry free functions, already keyed by `(module, name)` | `<time>`, `<math>`, `<sys/env>`, `<sys/process>`, `<random>`, `<io/files>` | **yes** — this is the cheap half, and it fixes section 1.3 |
| Sushi-source modules, injected as ordinary units | `<collections/iter>`, `<compression/zlib>`, `<encoding/msgpack>`, `<toolchain/slib>` | **yes** — a user unit in every respect |
| A built-in generic that the import activates | `<collections/hashmap>` (`generics/active_generics.py:3`) | **yes** — `hm.HashMap@(i32, string)`. The import brings the name, so the namespace holds it. `active_generics` becomes per-unit, which is one dict instead of one set |
| A method interface: the import enables methods on a type and brings **no name** | `<io/stdio>`, `<collections/strings>` | pointless, and said so — see below |

`use <io/stdio>` does not bring `stdin` into scope: `stdin` is always a name
(`passes/types/visitor.py:691`), and what the import enables is `read_line()` on it. An
alias on such an import binds an empty namespace, and every `io.<name>` after it fails one
at a time with the cause several lines away.

### 4.4 An empty namespace is a warning, and never an error

**`as` on an import that brings no nameable declaration is `CW3004`**, at the `use`
statement. A warning, not an error, because a namespace can be empty for three different
reasons and only one of them is a mistake:

| Empty because | Example |
|---|---|
| **structural** — a method interface can never bring a name | `use <io/stdio> as io` |
| **by design** — the unit exports methods, not names | a unit that is nothing but `extend` blocks |
| **incidental** — the public surface happens to be empty today | one `public fn` away from changing |

The middle row is the one that decides it, and it is not hypothetical. An extension carries
no marker: it is as visible as its target type (`visibility.md` Ruling 2). So a unit may
consist entirely of extensions, export **nothing nameable**, and still be the reason a
program works:

<!-- docs-sweep: skip (two units; records today's behaviour) -->
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

**The AST change is one optional field.** Every node above grows `namespace: Optional[str]`,
and the resolver maps `(namespace, name)` to a table key. In phase 1 that key is the bare
name; in phase 2 it is a qualified one (section 7). The same AST serves both, and the
resolver is the only thing that changes between them.

The enum row is the one that reads as a three-deep chain, and it is not: `my_math.Sign.Plus`
parses as `DotCall(receiver=MemberAccess(Name("my_math"), "Sign"), method="Plus")`, the
alias folds into `Sign`, and what is left is the `EnumConstructor` path the compiler
already takes. The `DotCall` ladder in `visit_dotcall` (`passes/types/visitor.py:408`)
gains no rung: the namespace check is the rung `_resolve_external_call` already occupies.

### 5.1 The grammar

Two rules gain a qualifier, both unambiguous because a `.` cannot mean anything else in
either position:

```
atom_type: ... | NAME "." NAME AT "(" type_list ")"  -> qualified_generic_type_t
         | ... | NAME "." NAME                       -> qualified_name_t
perk_constraint_list: perk_constraint ("+" perk_constraint)*
perk_constraint: NAME ["." NAME]
```

Expression positions need one change, and only one. `my_math.sin(0.0)` already parses as a
`DotCall` and `my_math.MAX_DEPTH` as a `MemberAccess`; both reach a resolver that returns
nothing today. What does not parse is a qualified call carrying explicit type arguments:

```
call:        [AT "(" type_list ")"] "(" [args] ")"     # foo@(i32)(5)   -- has the slot
method_call: "."  method_name      "(" [args] ")"      # x.foo(5)       -- has none
```

A type argument is REQUIRED where the type parameter appears only in the return type,
because there is no argument to infer it from:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
use <collections/iter> as it

fn main() i32:
    let List@(i32) a = it.empty_list@(i32)()??   # `it.` makes this a method_call
    return Result.Ok(0)
```

`method_call` has nowhere to put `@(i32)`, so the form is a parse error — and omitting the
argument is CE2060. **Aliasing a unit would make its return-type-only generics
uncallable.** Nine call sites in the tree use the form.

So `method_call` gains the slot, and `CE6102` narrows from "it is a method call" to "the
receiver is a VALUE":

```
method_call: "." method_name [AT "(" type_list ")"] "(" [args] ")"
```

The rule behind CE6102 is unharmed. Section 5's folding turns `it.empty_list@(i32)()` into
`empty_list@(i32)()` resolved against one unit, which is a direct call to a named free
function — exactly what CE6102 permits. It was the grammar in the way, not the rule.

### 5.2 A pattern is a written-name position, and its grammar is one segment short

A `match` arm is not an expression, and it does not reach the constructor path. It has its
own production, and that production counts to exactly two:

```
pattern: NAME "." NAME ["(" [pattern_list] ")"]
pattern_item: pattern | NAME | wildcard_pattern | own_pattern
```

`Sign.Plus` fits. `my_math.Sign.Plus` does not, and `pattern_item` offers no way in for a
nested one either. Without a third segment an aliased unit's enums are **write-only**: you
can construct one and never take it apart.

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
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
CE2076) all read what they read today. A qualifier that names nothing is `CE3012`, like any
other namespace miss; no new code is needed.

### 5.3 One position cannot be qualified

An array size may not be qualified. `i32[my_math.SIZE]` is refused. A fixed array's size is
read while the unit's own AST is built (Known Limitation 14, and it is already why a
constant next door is a value and not a size), and an alias is bound long after that. The
diagnostic is the existing `CE2099`.

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

**An import is not re-exported.** `my_math.<name>` reaches what `math` *declares*. It never
reaches what `math` imported. The qualified form and the flat form agree on this, which is
what makes the rule one rule.

**Section 1.2's program stops compiling.** `top.sushi` must add `use "deep"`. This is the
one migration this document forces, and it is measured: of 1951 `.sushi` files in the tree,
15 import a user unit, and 2 import more than one.

`CW3001` ("duplicate use statement") narrows with the same change. `use "math"` followed by
`use "math" as m` is not a duplicate — the two statements do different things. The warning
survives for a repeat with the same alias, or none.

### 6.1 You must import what you name

Non-transitivity has one consequence that is easy to miss, so it is ruled here rather than
discovered: **a public signature may name a type its caller cannot name.**

<!-- docs-sweep: skip (proposed behaviour across three units) -->
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
built on top. Section 1.1's third row goes with it: two units may each declare a private
`fn helper`, and section 8's ladder answers each unit's call with its own. The front end
gets that free; the BACK end pays for it with section 9's mangling, because the monolithic
build path puts every unit into one module where two `internal` symbols collide as readily
as two `external` ones. No test moves — of the eight files asserting `CE0101`, none is
cross-unit, and nothing asserts `CE3003` at all.

Coexistence for a type is a change to nominal identity — the interned name becomes
qualified — which reaches `StructType.__hash__`/`__eq__` and the enum twins, the struct and
enum tables, monomorphized instance names built by string concatenation, `display_type`,
and every match site that reads an interned name as text.

The two are not the same size, and the tree says so:

| Table | `.by_name` read sites | Files |
|---|---|---|
| `FunctionTable` | 49 | 11, of which 1 is the back end |
| `ConstantTable` | 16 | 10, of which 2 are the back end |
| `StructTable` | 195 | — |
| `EnumTable` | 206 | — |

65 against 401. That is why the phase line falls where it does, and phase 2 belongs in
`docs/design/type-identity.md`, argued there.

## 8. Shadowing and collisions

**A local variable wins.** `my_math` as a variable shadows the alias for the rest of its
scope, exactly as a local shadows an FFI namespace today
(`passes/types/__init__.py:143`). One rule, one seam, both producers.

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

What must change is the TEXT. `CE0101` today names one declaration and calls the other a
duplicate, which is right for one author and wrong for two: when the two extensions come
from two libraries, neither author is at fault and the consumer can edit neither. It becomes
a relational diagnostic naming both units, with no side blamed.

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
itself was measured and rejected. Of 348 extensions in the tree, 209 target the unit's own
type, **137 target a builtin** — `i32` alone is 87 — and 2 target a foreign unit's type. A
warning on "a target this unit did not declare" fires 139 times, `extend i32 squared()`
among them, which is the form CLAUDE.md's own quick reference teaches. A warning that
common is a warning people switch off.

Exempting builtins to quiet it is backwards: `i32` is the MOST collidable target, because
every unit in every program can reach it. That predicate would fire twice in the whole
tree. Neither predicate on the extension works, and the scope is what makes the rule right.

Cost today: `sushi_stdlib/src_sushi` and `toolchain/src` hold **zero** extensions between
them, and `tests/libs` holds 11 fixtures. It fires nowhere in real library code, which is
what a warning aimed at a future hazard should do.

**The consumer's half.** `--lib-info` should list the foreign types a library claims methods
on, so the hazard is readable before it is hit. That needs one new extractor: the manifest
carries functions, constants, structs, enums and perk implementations, and no extension
list at all.

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
spans several units and wants a single namespace should ship one façade unit that
re-declares it. That is a library design choice, and `docs/design/libraries.md` is where it
belongs.

## 9. What the back end needs

Nothing, for the syntax. Rulings 1 to 4 are front-end resolution, and the back end is
handed a resolved callee exactly as it is today.

One thing, for coexistence. A function's LLVM symbol is its bare Sushi name
(`backend/functions/declarations.py:40`). Two units each declaring `sine` therefore need
mangling by unit — and for **private** functions as well as public ones, because the
monolithic build path puts every unit into one `ir.Module`
(`backend/codegen_llvm.py:320`), where an `internal` symbol collides just as an `external`
one does. Only the incremental path emits a module per unit
(`backend/codegen_llvm.py:542`).

The scheme: `<unit>$<name>`, with every `/` in the unit name replaced by `$`, so
`collections/iter`'s `next` becomes `collections$iter$next`. `$` is legal in an LLVM
identifier and lies outside the alphabet of every existing symbol component, so the
generic mangler's structural invariant (D) (`generics/name_mangling.py:11`) is untouched.
`main` is exempt: the linker needs the name. An FFI `link_name` is never mangled: it names
a C symbol that somebody else compiled.

A binary `.slib` then needs the link symbol recorded next to the Sushi name in its
manifest, which is one field. `CE3503` already pins the compiler version a `.slib` may be
consumed by, so the scheme is free to change between versions.

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
| `CE3011` | narrowed to what phase 2 cannot lift: a name a consumer redeclares against a library it imported **flat**. An aliased import cannot collide |
| `CW3001` | narrowed to a repeat with the same alias, or none (section 6) |
| `CE0101` | kept for a duplicate extension on a foreign type, with new TEXT: relational, naming both units, blaming neither (section 8). Retired for a cross-unit private function, which phase 1 makes legal (section 7) |

## 11. Delivery

**Phase 1 — the alias, the scope, and the tractable half.** Rulings 1 to 5, and Ruling 6's
phase-1 row: a `struct`, an `enum` and a `perk` are reachable through a namespace, and are
still one per program. Adds `semantics/namespaces.py`, the `as` clause, the qualified forms
of two grammar rules, the optional `namespace` field on the nodes of section 5's table, the
per-unit scope in place of `build_global_symbol_table`, unit keys on `FunctionTable` and
`ConstantTable`, and the mangling of section 9.

What it buys: the section 1.3 crash becomes two working calls; two libraries may both
export `sine`; a unit's scope is what its `use` statements say it is; and a name's origin is
readable at the call. It also closes both items section 13 carries.

**Phase 2 — types coexist.** Qualified interned names, so two units may each declare
`struct Node`. Owned by `docs/design/type-identity.md`. Phase 1's AST and resolver are
already shaped for it: only the key the resolver produces changes.

**Not gated on either phase.** `CW3003` (section 8) needs the target type's declaring unit
and the `--lib` flag, neither of which this document introduces. It can ship on its own, and
it is the cheapest thing here: a predicate, a code, and a test batch.

## 12. What this does not decide

**Selective import.** `use "math" { sin, cos }` — a name-level form. Ruling 1 gives the
unit level only. `visibility.md` section 4 records that Rust's trait rule is expressible
only on top of a name-level import, so this question also decides whether a sealed perk
becomes possible. It is a separate feature and it composes with everything here.

**Re-export.** Section 6 rules that an import is not re-exported, and a future `public use`
is not ruled out — it would be a deliberate act with its own marker rather than the accident
section 1.2 measures. Section 6.1 is what creates the demand for it: without re-export or
`let` inference, a consumer must import every unit that declares a type it binds. Rust and
Swift both answer that question with re-export, and this is the question to answer it
against.

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

Two of the three limits that the visibility flip left behind (`LEFT.md`, issue **#487**)
wait for this epic rather than being fixed on their own. Each is recorded here with the
ruling that resolves it, so the epic's definition of done includes them.

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

**The correct answer already exists and is thrown away.** `collector.run(unit.ast, unit_name=…, unit_file=…)`
builds one `SymbolTables` per unit, and `merge_all` folds it into the global tables and
drops it (`semantic_analyzer.py:168-175`). `_replace_shadowed_functions`
(`semantics/symbol_merger.py`) then lets the consumer's declaration replace the library's
and books the library as a loser of the name. The type pass knows it is looking at a loser
and validates the arguments alone, because the loser's signature is gone. Phase 1 keeps the
per-unit table instead of building it and discarding it, which is why this is a key change
and not a new mechanism.

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

### 13.2 Collection order: dependencies before dependents

`LEFT.md` item 3, **option B**. Option A — one sweep for perk definitions before the
collect loop — fixes that item's symptom now and is not this epic's work. Option B is the
class fix, and it belongs here because **a per-unit scope cannot be built before its
dependencies are collected**.

The symptom, still reproducing on this tree: two ordinary units cannot implement each
other's perks.

<!-- docs-sweep: skip (records today's behaviour; two units) -->
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

`_library_units_first` retires with the reversal: a library unit is a dependency of
everything the consumer wrote, so a dependencies-first order puts it in front without being
told to.
