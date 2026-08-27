# Visibility

**Status: DECIDED, not implemented.** Today `public` reaches one declaration out of six.
This document rules on the other five.

The question is narrow to state and wide in effect: which declarations carry visibility,
what the default is, and how a method attached to a type gets its answer.

This document is normative for four things:

1. Which declarations carry a `public` marker, and what the default is.
2. How an extension and a perk implementation get their visibility.
3. The rule that stops a private type escaping through a public signature.
4. What this does *not* decide, so a later reader does not think it did.

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

### Three seams already exist, and each was built for `fn` alone

| Seam | Where | What it does |
|---|---|---|
| The use-site fence | `passes/types/visibility.py:12` — `reject_private_cross_unit_call(name, loc, visible=, unit_name=)` | Already generic in shape. Only the CE3005 text says "function" |
| The leak fence | `passes/types/signatures.py:16` — `_check_public_fn_ptr_fence`, with `type_predicates.py:110` — `contains_foreign_ptr` | CE5008 already answers "a `public fn` exposes a type it must not" |
| The type funnel | `passes/types/utils.py:16` — `validate_type_name` | Every named type flows through it, and it recurses through arrays and type arguments |

The funnel is reached from every declaration position that names a type: a function return
(`signatures.py:46`), its error type (`:49`), an extension target (`:93`), an extension
return (`:114`), a perk implementation target (`:151`), a perk method return (`:166`), a
constant's type (`constants.py:32`), a `let` (`statements.py:25`) and a `foreach` item
(`statements.py:281`).

### Only functions carry a unit of origin

`collect_functions` takes `unit_name` (`passes/collect/functions.py:349`). The struct,
enum and constant collectors do not (`structs.py:62`, `enums.py:70`, `constants.py:44`),
and `collect_extensions` (`functions.py:357`) takes none either. Perk implementations
carry one, but only to detect a library being shadowed (`perks.py:292`).

### The back end does not care

`backend/functions/declarations.py:78` picks `internal` or `external` linkage from
`fn.is_public`. Nothing else needs it. A struct type has no LLVM linkage, a constant is
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

Unit `dupb` is told it may not call the function it declared itself. That cascade is a
defect today, before any of this lands.

**`public` in Sushi controls callability, not namespacing.** There is one flat global
namespace, and privacy does not give a unit its own. This is the deciding fact for every
ruling below: privacy can only ever mean "you may not name mine", never "mine and yours
coexist".

## 2. Ruling 1: four declarations carry visibility, and private is the default

`fn`, `const`, `struct` and `enum` are private to their unit unless they say `public`.

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
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

`public const` is a parse error today, which is issue #466. That issue asks for the
grammar to accept the keyword. This ruling answers the question underneath it: the keyword
must also *mean* something, so the default flips.

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

**This rule needs no enforcement code.** It is self-enforcing:

- `Box` is public, so another unit holds a `Box` and calls `.doubled()`. Nothing to check.
- `Cursor` is private, so no other unit can name it, construct one, or receive one through
  a public signature — section 5 stops the last route. `.step()` is already unreachable.
  Nothing to check.

Method resolution therefore stays exactly as it is: keyed on the receiver's type, blind to
the caller. Nothing is threaded into `collect_extensions`, and the method registry does not
change. `extend i32 squared()` is public because `i32` is public, which is what happens
today.

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

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
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

One predicate, `contains_private_type(ty, struct_table, enum_table)`, is the twin of
`contains_foreign_ptr` and walks the same way. Four guards call it, and each reads the
visibility of the thing being declared:

| Position | Guard | Where |
|---|---|---|
| `public fn` signature | `if not func.is_public: return` | `signatures.py:30` — the shape exists as CE5008 |
| Extension signature | `if not target.is_public: return` | `signatures.py:114` |
| Perk method signature | `if not perk.is_public: return` | `signatures.py:166` |
| Public generic constraint | `if not func.is_public: return` | `signatures.py:30` |

The extension guard reading its *target's* flag mirrors the function guard reading its
*own*.

Three new codes are needed, and each family owns its own range. `unit.py` is registered up
to CE3008 and `perk.py` up to CE4010, so the next free numbers are:

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

`check_missing_docs` (`passes/docs.py:232`) walks `declarations(program)` (`:67`) with no
visibility gate. CW7002-CW7006 already cover every declaration whatever its visibility, so
nothing needs reconciling in either direction.

## 7. Migration

Only four Sushi sources cross a unit boundary with a type:

| Source | Needs `public` | Correctly becomes private |
|---|---|---|
| `encoding/msgpack.sushi` | `MsgValue`, `MpError` | `MpCursor` |
| `toolchain/slib.sushi` | `SlibError`, `SlibSizes` | — both are in public signatures |
| `compression/zlib.sushi` | `ZError` | 4 structs, 8 lookup tables |
| `collections/iter.sushi` | — | declares no types |

`.slib` production needs an `is_public` gate on `_extract_structs`
(`library_manifest.py:263`) and `_extract_enums` (`:293`), matching
`_extract_public_functions` (`:164`). The `not_exported` list (`:215`) grows a `struct` and
an `enum` kind, so a consumer naming a library-private type hears CE3005 rather than an
"undefined" diagnostic. That machinery already exists and was built for this shape.

A single-unit file never notices the flip.

## 8. What this does not decide

**Struct field privacy.** A struct is public or private as a whole. A private field would
make a struct unconstructible from outside, because Sushi has positional and named
construction and no user-defined constructor. Rust survives this only because you write
`Point::new()`. If an opaque type is wanted later, the cheaper route is a marker that hides
*all* fields — the C header idiom — and not a marker per field.

**Per-unit namespacing.** Nominal identity forbids it (section 1). Two units still cannot
each declare a private `Node`. If that is ever wanted, it is a change to type identity, not
to visibility, and it should be argued there.

**The CE0101 → CE3005 cascade.** Telling a unit it may not call its own function is wrong
today, independent of every ruling here. It should be fixed on its own.

**Sealed calls.** Section 4 gives up "nobody outside may call this". Recovering it needs a
name-level import, which Sushi does not have. That is a language feature, not a visibility
rule.
