# Visibility

**Status: IMPLEMENTED.** `public` reached one declaration out of six when this was
written. It reaches all five that carry a marker now, private is the default for every
one of them, and the leak fence is in place. Section 1 records what the compiler did
before, because every ruling below is measured against it.

The question is narrow to state and wide in effect: which declarations carry visibility,
what the default is, and how a method attached to a type gets its answer.

This document is normative for four things:

1. Which declarations carry a `public` marker, and what the default is.
2. How an extension and a perk implementation get their visibility.
3. The rule that stops a private type escaping through a public signature.
4. What this does *not* decide, so a later reader does not think it did.

Two later rulings live in section 9, because both are about the one flat namespace rather
than about a marker: what happens when a consumer declares a name a library already
declares, and who owns the record of a perk implementation.

Read `docs/libraries.md` for what a `.slib` exports today, and
`docs/design/method-resolution.md` for the order a method is found in.

## 1. What the compiler does today

### One keyword, one declaration

`grammar.lark:42` is the only rule that mentions `PUBLIC`:

```
function_def: PUBLIC? FN NAME [type_params] "(" [parameters] ")" type? ["|" type] ":" block
```

`const_def` (`:6`), `struct_def` (`:12`), `enum_def` (`:15`), `perk_def` (`:25`) and
`extend_stmt` (`:35`) carry no such token. So:

| Declaration | Default today | Marker |
|---|---|---|
| `fn` | private to the unit | `public fn` |
| `const` | **public, always** | none — `public const` is a parse error |
| `struct` | **public, always** | none |
| `enum` | **public, always** | none |
| `perk` | **public, always** | none |
| `extend` | **public, always** | none |

`ast_builder/declarations/constants.py:44` hard-codes `is_public=True` on every constant,
with the comment "Constants are always global". A struct or an enum needs no flag at all:
the collect pass builds one table for the whole program, and any unit may name any entry.

### Three seams existed, each built for `fn` alone, and each with its own hole

| Seam | Then | Now |
|---|---|---|
| The use-site fence | `reject_private_cross_unit_call` — generic in shape, but the CE3005 text said "function" | `semantics/visibility.py:195` — one record, one predicate, one CE3005 with a `{kind}` word and a note at the declaration |
| The leak fence | `_check_public_fn_ptr_fence` read `ret` and `params` of a `public fn` and nothing else | `passes/types/public_signatures.py:165` — one runner over one walk, and each rule brings its own pair of sets |
| The type funnel | `passes/types/utils.py:22` — `validate_type_name`, which fell through a borrow and a function type | the same funnel, with every arm, over `semantics/type_walk.py:55` — `walk_named_types` |

The funnel is reached from every declaration position that names a type: a function's
return and error type, an extension target and return, a perk-implementation target, a
perk method's return, a constant's type, a `let` and a `foreach` item. What it did NOT
reach was a struct field, an enum payload or an extern signature, and
`semantics/ast_walk.py:156` — `signature_types` — is the walk that does.

Two total walks and their gates hold the whole thing up:
`semantics/ast_walk.py:74` — `declarations` — yields every declaration of a unit, and
`semantics/type_walk.py:55` yields every type inside a type. The visibility seam is filled
from the first (`semantics/visibility.py:359` — `record_declarations`) and every predicate
over types is one line over the second.

### Only functions carried a unit of origin

`collect_functions` took `unit_name`; the struct, enum and constant collectors did not,
and `collect_extensions` took none either. Perk implementations carried one, in a
collector-private dict that answered one question and could answer no other.

All six collectors are given `current_unit_name` beside `current_unit_file` in one loop
now (`passes/collect/__init__.py`), every collected record says which unit and which file
it came from, and the perk-implementation owner sits on `PerkImplementationTable` beside
the implementations themselves.

### The back end does not care

`backend/functions/declarations.py:78` picks `internal` or `external` linkage from
`fn.is_public`. Nothing else needs it, and that one line is load-bearing for section 9. A struct type has no LLVM linkage, a constant is
inlined by `const_eval`, and an extension is a mangled function that already follows the
function rule. **Visibility for a type is a pure front-end rule and costs the back end
nothing.**

### The constraint that does not move

Type identity is nominal (`docs/design/type-identity.md`): a `StructType` compares and
hashes on its name alone. One name, one type, for the whole program. So two units cannot
each hold a private declaration of the same name:

<!-- docs-sweep: skip (records today's behaviour; two units, one name each) -->
```sushi
# unit_a.sushi          # unit_b.sushi
struct Node:            struct Node:
    i32 v                   i32 w
```

```
error [CE0004]: duplicate struct 'Node'.
```

The same holds for a function and for a perk, and the function case emits a second
diagnostic that is wrong:

```
error [CE0101]: duplicate function 'helper'.
error [CE3005]: cannot call private function 'helper' from unit 'dupb'
                (function is defined in 'dupa').
```

Unit `dupb` was told it may not call the function it declared itself. That cascade was a
defect before any of this landed, and it is fixed: the loser of a contested name is
recorded, and no rule measures a loser's own code against the winner's declaration.

**`public` in Sushi controls callability, not namespacing.** There is one flat global
namespace, and privacy does not give a unit its own. This is the deciding fact for every
ruling below: privacy can only ever mean "you may not name mine", never "mine and yours
coexist".

## 2. Ruling 1: four declarations carry visibility, and private is the default

`fn`, `const`, `struct` and `enum` are private to their unit unless they say `public`.

<!-- docs-sweep: skip (declarations only; the sweep compiles a block with a main) -->
```sushi
public const i32 MAX_DEPTH = 32     # another unit may name it
const i32 SCRATCH_SIZE = 4096       # this unit only

public struct Point:                # another unit may name the type
    i32 x
    i32 y

enum Cursor:                        # this unit only
    Start
    Mid(i32)
```

The default is private for two reasons. It matches `fn`, which is the only declaration
that carries visibility today. And a default of public makes the keyword decoration: a
marker that grants what the reader already has says nothing.

`public const` was a parse error, which is issue #466. That issue asked for the grammar to
accept the keyword. This ruling answered the question underneath it: the keyword must also
*mean* something, so the default flipped. Both halves have landed --
`grammar.lark` carries `PUBLIC?` on `const_def`, `struct_def`, `enum_def` and `perk_def`,
and `PUBLIC` gained the word guard its keyword neighbours use, so `publication` is a name.

One reader answers for all five rules: `read_public` in
`ast_builder/utils/tree_navigation.py` hands back the marker AND its span. The span is
what tells a written marker from an absent one, which is what CE6103 points at and what
the flip needed while it was in progress.

### Enum variants follow the enum

A variant carries no marker of its own. Rust and Swift both make a variant as visible as
its enum, and Sushi has a harder reason to agree: a private variant would make a total
`match` unwritable across a unit boundary, so exhaustiveness checking would break.

## 3. Ruling 2: a method is as visible as its type

An extension and a perk implementation carry no marker. Each is as visible as the type it
is attached to.

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
public struct Box:
    i32 n

extend Box doubled() i32:           # public, because Box is public
    return self.n * 2

struct Cursor:
    i32 at

extend Cursor step() i32:           # private, because Cursor is private
    return self.at + 1
```

**This rule needs almost no enforcement code.** It is self-enforcing:

- `Box` is public, so another unit holds a `Box` and calls `.doubled()`. Nothing to check.
- `Cursor` is private, so no other unit can name it, construct one, or receive one through
  a public signature — section 5 stops the last route. `.step()` is already unreachable.
  Nothing to check.

Method resolution therefore stays exactly as it is: keyed on the receiver's type, blind to
the caller. Nothing is threaded into `collect_extensions`, and the method registry does not
change. `extend i32 squared()` is public because `i32` is public, which is what happens
today.

One thing the ruling did need: **the marker has to be refused where it can be written.** A
perk-implementation body is built out of the `function_def` rule, so `public fn shout()`
inside `extend Box with Loud:` parsed, was stored on the method, and was read by nobody.
CE6103 refuses it, with a caret on the marker.

And one thing the leak fence has to know: an extension inherits its target type's marker,
and a builtin target carries none. An extension on `i32` is therefore not fenced by
section 5 -- it has no marker to promise with -- which is what keeps section 6's
undertaking that a single-unit file never notices the flip.

The alternative was to give an extension its own marker. That makes a method's availability
depend on the calling unit, which makes method resolution unit-dependent — and method
resolution is a global, hot, one-seam path. The cost is real and the gain is small (see
section 6).

## 4. Ruling 3: a perk declaration is a name, its implementation is a method

A perk is two things, and they take different answers.

| | Marker | Visibility decided by |
|---|---|---|
| `perk Loud:` — the declaration | `public perk` | its own |
| `extend Box with Loud:` — the implementation | none | the target type |

The declaration is not attached to anything. It is a standalone name that occupies the one
global namespace, and it collides like a struct does:

```
error [CE4001]: duplicate perk definition: Loud.
```

So it takes a marker for the same reason a struct does. The implementation is a method, so
Ruling 2 answers it.

### The consequence, stated deliberately

A public struct, a private perk, and an implementation joining them:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
# unit.sushi
perk Loud:                          # private -- the default
    fn shout() i32

public struct Box:
    i32 n

extend Box with Loud:               # public, because Box is public
    fn shout() i32:
        return 0
```

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
# main.sushi
use "unit"

struct MyStruct:
    i32 v

extend MyStruct with Loud:          # ERROR: Loud is private to 'unit'
    fn shout() i32:
        return 1

fn main() i32:
    let Box my_box = Box(7)
    my_box.shout()                  # WORKS: Box is public, so its methods are
    return Result.Ok(0)
```

`.shout()` is callable from `main.sushi`, and `Loud` is not nameable there. **The perk is
the contract; the method belongs to the type.** Privacy on a perk decides who may write new
implementations of the contract. It does not retract a method from a type you chose to
publish.

### The same pair, with a public perk

Mark the perk `public` and the contract itself becomes part of the API:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
# unit.sushi
public perk Loud:                   # public -- the contract is API now
    fn shout() i32

public struct Box:
    i32 n

extend Box with Loud:               # public, because Box is public
    fn shout() i32:
        return 0
```

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
# main.sushi
use "unit"

struct MyStruct:
    i32 v

extend MyStruct with Loud:          # WORKS: Loud is public, so another unit may implement it
    fn shout() i32:
        return 1

fn loudest@(T: Loud)(peek T a, peek T b) i32:   # WORKS: Loud may be named in a constraint
    if (a.shout() > b.shout()):
        return Result.Ok(a.shout())
    return Result.Ok(b.shout())

fn main() i32:
    let Box my_box = Box(7)
    my_box.shout()                  # WORKS -- unchanged by the perk's visibility
    return Result.Ok(0)
```

Two things changed, and one did not:

| | private `perk Loud` | `public perk Loud` |
|---|---|---|
| `extend MyStruct with Loud` in another unit | CE4011 | allowed |
| `@(T: Loud)` in another unit | CE4011 | allowed |
| `my_box.shout()` in another unit | **allowed** | **allowed** |

The call is the row that does not move. A perk's visibility governs the **contract** — who
may implement it, and who may demand it in a constraint. It never governs a method on a type
that is already public. That is the whole content of Ruling 3, and the table is the shortest
way to say it.

The practical reading: keep a perk private while it is an implementation detail you may still
want to change, because nothing outside can then depend on it. Publish it when you want other
units to implement it, and accept that its method set is API from that point on.

### Java draws the same line

An interface with no modifier is package-private. A public class may still implement it,
and the method stays callable:

```java
// package unit
interface Loud { int shout(); }              // package-private
public class Box implements Loud {
    public int shout() { return 0; }
}
```

```java
// package app
Box b = new Box();
System.out.println(b.shout());               // prints 0
```

The other half fails, exactly as in Sushi:

```java
class MyStruct implements unit.Loud { ... }
```
```
error: Loud is not public in unit; cannot be accessed from outside package
```

Java can do this because a method's visibility is declared on the class, independently of
the interface's. `Box.shout()` is public because `Box` says so.

### Rust decides it the other way, and Sushi cannot copy it

```rust
mod unit {
    trait Loud { fn shout(&self) -> i32; }   // private trait
    pub struct Box { }
    impl Loud for Box { fn shout(&self) -> i32 { 0 } }
}
let b = unit::Box::new();
b.shout();
```
```
error[E0599]: no method named `shout` found for struct `unit::Box` in the current scope
   = help: items from traits can only be used if the trait is implemented and in scope
   = help: trait `crate::unit::Loud` which provides `shout` is implemented but not reachable
```

Rust blocks the call. But Rust splits what Sushi does not:

| Rust form | Sushi form | Callable outside? |
|---|---|---|
| `impl Box { pub fn shout(&self) }` — inherent | `extend Box shout()` | yes |
| `impl Loud for Box` — trait | `extend Box with Loud` | only with `Loud` in scope |

So Rust agrees with Ruling 2 for a plain extension, and differs only on the perk form. The
mechanism it uses is `use crate::unit::Loud` — selecting one name into scope. Sushi has no
name-level import: `use "unit"` brings a whole unit. Rust's rule is only expressible on top
of scope-import, and adopting it would make method availability depend on the call site,
which is the cost Ruling 2 exists to avoid.

Sushi's method model is Java-shaped — a method is found on the receiver's type, not through
a scoped bound. It therefore lands on Java's answer, and not as a compromise.

**What this gives up** is the second half of a sealed perk: you cannot say "nobody outside
may call this". The first half survives — a private perk means nobody outside may implement
it, so a method may be added to the perk later without breaking a consumer. That is what
sealing is normally used for.

## 5. The leak fence: a public thing may not name a private type

Privacy on a type is worth nothing if a public signature hands the type out anyway. Rust
answers this with E0446 (private type in public interface) and E0445 (private trait in
public interface). Sushi needs both.

<!-- docs-sweep: skip (declarations only; the sweep compiles a block with a main) -->
```sushi
struct Point:                       # private
    i32 x

perk Loud:                          # private
    fn shout() i32

public struct Box:                  # public
    Point at                        # ERROR: a public struct with a private field type

public fn origin() Point:           # ERROR: leaks Point
    return Result.Ok(Point(0))

public fn many() List@(Point):      # ERROR: leaks Point through a type argument
    return Result.Ok(List.new())

extend Box where() Point:           # ERROR: Box is public, so this is; Point is not
    return self.at

public fn loudest@(T: Loud)(T x) ~:  # ERROR: names a private perk in a constraint
    return Result.Ok(~)
```

One predicate, `first_private_name` (`semantics/type_predicates.py:126`), is the twin of
`contains_foreign_ptr` and walks the same way. It names the offender rather than answering
yes or no, because a leak diagnostic has to say which type it is.

One runner applies it, `check_public_signatures`
(`passes/types/public_signatures.py:165`), over one walk of every position a signature
names. Each position carries two facts -- what declares it, and which slot it is -- and a
rule reads a set of each, so a rule with a different answer brings its own sets instead of
widening somebody else's:

| Position | Fenced | Visibility read from |
|---|---|---|
| `fn` return, error arm, parameter | yes | the function's own marker |
| Constant's type | yes | the constant's own marker |
| **Public struct field** | yes | the struct's own marker |
| **Public enum variant payload** | yes | the enum's own marker |
| Extension return and parameter | yes | the TARGET type's marker |
| Perk method return and parameter | yes | the perk's, or the target type's |
| Public generic constraint | yes (CE3010) | the declaring function's, struct's or enum's marker |
| Extension or perk-implementation RECEIVER | no | it IS the gate; asking would answer itself |
| A target with no declaration (`extend i32`) | no | there is no marker to inherit |

The extension guard reading its *target's* flag mirrors the function guard reading its
*own*. The two bold rows are what decision 2 added: the guard table this replaced listed
four positions while the example above already marked a field an error.

The predicate stops at a named declaration. A signature hands out the names it SPELLS, and
what one of those names holds belongs to its own declaration -- so a public struct with a
private field hears CE3009 once, at the field, and not again at every signature that
mentions the struct.

The runner does not run over a LIBRARY unit at all. Its signatures were fenced when the
library was built, and at the consumer its templates carry whatever the consumer's call
substituted into them -- a private type of the consumer's included.

Three new codes were needed, and each family owns its own range. All three are registered
and emitted:

| Code | Family | Answers |
|---|---|---|
| **CE3009** | `unit.py` | A public signature names a private type — Rust's E0446 |
| **CE3010** | `unit.py` | A public signature's constraint names a private perk — Rust's E0445 |
| **CE4011** | `perk.py` | Another unit implements or names a private perk — the perk twin of CE3005 |

CE3010 and CE4011 both involve a private perk and are not the same error. CE4011 is a
**use-site** rule: the perk is not nameable in that unit at all. CE3010 is a **leak** rule:
the perk is nameable right there, in its own unit, and the signature would hand it to a unit
where it is not.

The implicit `Result` wrap needs no special case. `fn origin() Point` becomes
`Result@(Point, StdError)`, but the fence runs on `func.ret` before the wrap.

CE6103 joined them, in the syntax family, for the marker Ruling 2 refuses: a perk
implementation method cannot say `public`.

A consumer cannot instantiate a public generic at a private type, because it cannot name
the type. No fence is needed there.

## 6. What this costs, measured

### A private helper method on a public type becomes impossible

That is the only capability Ruling 2 removes. The current cost is zero:

| Multi-unit Sushi source | `extend` count |
|---|---|
| `src_sushi/compression/zlib.sushi` | 0 |
| `src_sushi/encoding/msgpack.sushi` | 0 |
| `src_sushi/toolchain/slib.sushi` | 0 |
| `src_sushi/collections/iter.sushi` | 0 |
| `toolchain/src/slib_info.sushi` | 0 |

No multi-unit Sushi source in the tree uses an extension. Of the 18 files that import a
user unit (out of 2095 `.sushi` files), one has an extension, and it is the syntax
showcase. Internal helpers are already written as private free functions, and Known
Limitation 9 pushes combinators the same way.

Encapsulation is not lost, it moves: make the **type** private and its extensions become
unreachable for free.

### What the change buys

`src_sushi/compression/zlib.sushi` is the case the whole ruling is for:

| Kind | Declared | Belongs in the API | Controllable today |
|---|---|---|---|
| `fn` | 38 | 6 | **yes** |
| `struct` / `enum` | 5 | 1 (`ZError`) | no |
| `const` | 8 | 0 | no |

The module gets function privacy exactly right: 6 public out of 38. It has no control over
the other 13. `ZBits`, `ZHuff`, `ZOut` and `ZCode` are decoder state. `ZLIB_LEN_BASE`,
`ZLIB_DIST_SMALL` and six more are DEFLATE lookup tables. Every one is in every consumer's
namespace, and — because `library_manifest.py:263` and `:293` ship structs and enums with
no `is_public` gate — every one is frozen API that cannot change without breaking a
consumer.

zlib goes from 13 exported names to 1.

### The doc lints do not move

`check_missing_docs` (`passes/docs.py:174`) walks `declarations(program)`
(`semantics/ast_walk.py:74`) with no visibility gate. CW7002-CW7006 already cover every declaration whatever its visibility, so
nothing needs reconciling in either direction.

## 7. Migration — done

Only four Sushi sources cross a unit boundary with a type, and all four are marked:

| Source | Marked `public` | Correctly private |
|---|---|---|
| `encoding/msgpack.sushi` | `MsgValue`, `MpError` | `MpCursor` |
| `toolchain/slib.sushi` | `SlibError`, `SlibSizes` | — both are in public signatures |
| `compression/zlib.sushi` | `ZError` | 4 structs, 8 lookup tables |
| `collections/iter.sushi` | — | declares no types |

`.slib` production carries the gate. `_extract_structs`
(`backend/library_manifest.py:286`), `_extract_enums` (`:322`) and
`_extract_public_constants` (`:262`) all read the marker now, matching
`_extract_public_functions`; the constant extractor also stopped iterating every unit,
which had been putting a bundled stdlib module's constants in the manifest. The
`not_exported` list (`:215`) grew a `struct`, an `enum` and a `constant` kind, so a
consumer naming a library-private type hears CE3005 rather than "unknown type".

One thing the plan did not foresee. Gating the public index left a private type that a
public generic's template body NAMES with nowhere to travel, so the transplanted body
arrived at the consumer as CE2001 about the library's own struct. The export closure ships
it as source now, beside the closure's private constants, and the consumer registers it
with a PRIVATE record -- so only the transplanted body may name it. Each private is still
named in exactly one place: the closure, or the kept list.

The manifest protocol is **2.2** -- 2.1 for the public gate above, and 2.2 for the two
keys the unit-namespaces epic added: `unit` on every record, and `link_symbol` on every
record with a symbol in the shipped bitcode (`docs/library-format.md`,
`docs/design/unit-namespaces.md` section 9). An older `.slib` is refused through the
existing compiler-version gate (CE3503); there is no grandfather branch.

**A single-unit file never notices the flip.** That is an undertaking, and it is what the
leak fence's two gates protect: an extension on a builtin inherits no marker, so it is not
fenced, and a library unit's own bodies are not fenced at the consumer at all.

## 8. What this does not decide

**Struct field privacy.** A struct is public or private as a whole. A private field would
make a struct unconstructible from outside, because Sushi has positional and named
construction and no user-defined constructor. Rust survives this only because you write
`Point::new()`. If an opaque type is wanted later, the cheaper route is a marker that hides
*all* fields — the C header idiom — and not a marker per field.

**Per-unit namespacing.** Nominal identity forbids it (section 1). Two units still cannot
each declare a private `Node`. If that is ever wanted, it is a change to type identity, not
to visibility, and it should be argued there. `docs/design/unit-namespaces.md` carries the
design; CE3011 and CW3002 both cite it as the rule that would lift them.

**The CE0101 → CE3005 cascade.** Telling a unit it may not call its own function was wrong
independent of every ruling here, and it rode this work rather than waiting: four more
declaration kinds would otherwise have inherited it. Section 9 records what came out of
that.

**Sealed calls.** Section 4 gives up "nobody outside may call this". Recovering it needs a
name-level import, which Sushi does not have. That is a language feature, not a visibility
rule.

## 9. The flat namespace, ruled later

Both rulings here came out of implementing sections 2 to 7. Neither is about a marker: each
is about what one flat namespace means when a consumer and a library reach for the same
name.

### 9.1 A consumer may shadow a library's export, and is told that it does

Four combinations, and only one of them was ever a link-level clash:

| consumer writes | library writes | answer | linkage |
|---|---|---|---|
| `fn f` (private) | `public fn f` | allowed, **CW3002** | the consumer's is internal, the library's external |
| `public fn f` | `public fn f` | CE3003 — a real clash | both external |
| `fn f` (private) | `fn f` (private) | CE3011 | both internal |
| `public fn f` | `fn f` (private) | CE3011 | no clash at all |

Row 1 stays legal, and the measurement is why. A private function is emitted with INTERNAL
linkage (`backend/functions/declarations.py:78`), so the consumer's `f` is invisible
outside its own object file: the consumer's call binds to the consumer's definition, and
the library's own body keeps calling its own. A library with `public fn use_value()`
returning `get_value() * 2` still returns 200 when the consumer's `get_value` returns 7.
The generic case measures the same, and it is the one that should break if any does: a
library's `public fn through@(T)` is transplanted into the consumer's compile and
monomorphized there.

Two things were owed and are now paid. The consumer's own call had to RESOLVE to the
consumer's declaration -- every symbol table merges first-wins and library units merge
first, so the library's signature answered the consumer's call and a replacement with a
different signature was refused with a spurious CE2009. And shadowing an export is legal
but rarely intended, so **CW3002** says so.

The displaced declaration's unit is booked as a loser of the name, because one table holds
one declaration and the library's own body must not then be measured against the
consumer's.

A library's PRIVATE name cannot be shadowed at all (**CE3011**): the consumer collides
with a name it cannot see, so renaming is its only move. For a TYPE, even a public library
name stays the plain duplicate (CE0004 / CE2046) -- type identity is nominal, so one name
is one shape and the consumer cannot have its own.

### 9.2 The perk-implementation override stays, and its record moved onto the table

A consumer's `extend X with P` wins over a library's, the library's goes to
`shadowed_impls`, and the analyzer drops it from the AST so the method symbol is not
defined twice. This is not the same question as 9.1: a perk implementation is keyed by
`(type, perk)` and not by a name in the flat namespace, and it is the sanctioned override
that `docs/design/method-resolution.md` already names.

What changed is the bookkeeping. "Which unit declared this implementation" lived in a
collector-private dict, so the only reader it could ever have was the collector. It sits on
`PerkImplementationTable` beside `implementations`, `by_type` and `by_perk` now, the
takeover is a method on the table, and the owner survives the merge.

A PRIVATE perk needs nothing here: CE4011 refuses `extend X with P` in another unit before
the override question arises.
