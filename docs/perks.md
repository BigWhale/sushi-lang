# Perks

Perks are Sushi's trait/interface system that enables generic constraints and polymorphic behavior through static 
dispatch. They allow you to define behavior that multiple types can implement, enabling compile-time generic 
programming with zero runtime overhead.

## Table of Contents

- [Overview](#overview)
- [Defining Perks](#defining-perks)
- [Implementing Perks](#implementing-perks)
- [Generic Constraints](#generic-constraints)
- [Generic Functions with Perks](#generic-functions-with-perks)
- [The Predefined Perks](#the-predefined-perks)
- [Multiple Constraints](#multiple-constraints)
- [Common Patterns](#common-patterns)
- [Perk Visibility](#perk-visibility)
- [Error Codes](#error-codes)
- [Known Limitations](#known-limitations)

## Overview

Perks provide a way to:
- Define shared behavior across different types
- Constrain generic types to ensure required functionality
- Enable polymorphic functions through static dispatch
- Achieve zero-cost abstractions through monomorphization

**Key Design Principles:**
- A perk method returns a bare value (not a `Result@(T, E)`) unless it declares an error
  channel `| E`. A bare perk method body has no channel, so `??` is refused there
  ([CE0131](error-catalog.md#ce0131)). Handle a Result in the body with `match` or `.realise(default)`, or declare
  `| E` on the contract and on every implementation (see
  [Error Channels on Perk Methods](#error-channels-on-perk-methods)). A `??` inside a
  lambda in the body is legal when the lambda's type writes `| E`, because the lambda then
  has its own Result channel
- The rule is the one for every callable, a free function included: a channel exists only
  when the signature writes `| E`. A bare function is the exception, and a contract method
  that can fail, or can gain a failure later, declares `| E`. See
  [The error channel is opt-in](design/error-channel.md)
- Static dispatch only (no dynamic dispatch/vtables)
- Explicit implementations required (no structural typing). The one exception is
  the predefined `Hashable`, `Eq`, `Ord`, `Display` and `Clone`, which every type with a
  derived method satisfies (`Eq`, `Ord` and `Display` by the top-level rule; `Clone` by
  holding no resource)
- Full type checking at compile time. A generic body is checked one time, where it is
  written, and a type parameter has the methods of its constraints and nothing more (see
  [Checked generics](design/checked-generics.md))

## Defining Perks

A perk defines a set of method signatures that implementing types must provide:

```sushi
perk Describe:
    fn describe() string
    fn debug() string

perk Ranked:
    fn rank(Point other) i32
```

Five perks ship with the compiler and cannot be declared: `Hashable` (`fn hash() u64`),
`Drop` (`fn drop(poke self) ~`), `Eq` (`fn eq(Self other) bool`), `Ord`
(`fn compare(Self other) i32`) and `Display` (`fn to_str() string`). A unit that declares
any of them is **[CE4001](error-catalog.md#ce4001)**. See [The Predefined Perks](#the-predefined-perks).

There is no `Self` type in a perk you write, so a perk method that takes a value of the
implementing type names that type explicitly (`fn compare(Point other) i32`). A parameter is a borrow by
default, as in every function. The parameter modes `nom`, `peek` and `poke` are part of the
signature, so the implementation must write the same modes as the contract.

The receiver `self` is a borrow by default too. A method that changes its receiver declares
`poke self` on the contract and on every implementation. The io contracts use this form
(see [io contracts](stdlib/io/contracts.md)).

**Rules:**
- A perk method returns a bare value, unless it declares `| E`
- Methods can access `self` implicitly
- Methods can take parameters in any of the four parameter modes
- Multiple methods can be defined in a single perk
- A perk cannot hold a static method ([CE4014](error-catalog.md#ce4014)), because it has no `Self` to construct

## Implementing Perks

Use `extend TypeName with PerkName:` to implement a perk for a type:

```sushi
struct Point:
    i32 x
    i32 y

# Point already has a derived hash; this implementation REPLACES it.
extend Point with Hashable:
    fn hash() u64:
        let u64 hx = self.x as u64
        let u64 hy = self.y as u64
        return hx + hy

extend Point with Describe:
    fn describe() string:
        return "Point({self.x}, {self.y})"

    fn debug() string:
        return "Point(x: {self.x}, y: {self.y})"
```

A `{` in a double-quoted string always opens an interpolation hole, and a hole cannot hold a
string literal. For a literal brace, use a single-quoted string (`'{'`), which does not
interpolate.

**Implementation Rules:**
- All methods defined in the perk must be implemented ([CE4005](error-catalog.md#ce4005))
- Method signatures must match exactly: parameters, modes, return type ([CE4004](error-catalog.md#ce4004)) and error
  channel ([CE0133](error-catalog.md#ce0133))
- Can implement multiple perks for the same type
- Can access struct fields via `self`
- A name has one home: a perk method and an ordinary extension method of the same name on
  one type is [CE4007](error-catalog.md#ce4007). A name is a contract method or a convenience method, never both
- A function type cannot be the target ([CE2110](error-catalog.md#ce2110))

### Error Channels on Perk Methods

A perk method can declare an error channel `| E`. The contract and every implementation
must declare the same channel ([CE0133](error-catalog.md#ce0133)). The body then spells both constructors, as a
function body does: `return Result.Ok(value)` and `return Result.Err(e)`. A bare
`return value` in a channel body is [CE2030](error-catalog.md#ce2030). `??` is legal in the body, and the call answers a
`Result@(T, E)`:

```sushi
error ReadError:
    Empty

perk Source:
    fn next_value(poke self) i32 | ReadError

struct Counter:
    i32 left

extend Counter with Source:
    fn next_value(poke self) i32 | ReadError:
        if (self.left == 0):
            return Result.Err(ReadError.Empty)
        self.left := self.left - 1
        return Result.Ok(self.left)

fn drain@(S: Source)(poke S src) i32 | ReadError:
    let i32 a = src.next_value()??
    let i32 b = src.next_value()??
    return Result.Ok(a + b)

fn main() i32:
    let Counter c = Counter(left: 3)
    println(drain(poke c).realise(-1))    # 2 + 1 = 3
    println(drain(poke c).realise(-1))    # the counter is empty: -1
    return 0
```

### A generic type may implement a perk

The target may name a type parameter, and then every instantiation the program uses gets
its own copy of the implementation:

```sushi
perk Show:
    fn show() string

struct Box@(T):
    T item

extend Box@(T: Display) with Show:
    fn show() string:
        return "boxed {self.item}"

fn render@(S: Show)(S thing) string:
    return thing.show()

fn main() i32:
    let Box@(i32) n = Box(7)
    println(render(n))
    return 0
```

A concrete type argument is a **constraint** rather than a parameter name, the same rule
an extension target follows: `extend Box@(i32) with Show` applies to `Box@(i32)` and to
nothing else, and a partially concrete target such as `extend Pair@(i32, U) with Show` is
**[CE2098](error-catalog.md#ce2098)** -- there is no partial specialization. The compiler makes no copy for an
instantiation that the program does not name.

The body puts `self.item` in a hole, so the target adds the bound `T: Display`. A
`Box@(T)` whose `T` has no `Display` then does not implement `Show`, and `render` refuses it
with **[CE4006](error-catalog.md#ce4006)**. An implementation on `Box@(T)` also inherits every bound that `Box`
declares (`struct Box@(T: Hashable)` gives the body `T: Hashable`). A bound in a target is
legal only at its top level; anywhere else it is **[CE6110](error-catalog.md#ce6110)**.

The compiler checks a template implementation one time, on the written template, and not
for each instance: the header and the body. In the body, `T` is opaque and has what its
bounds promise. So `fn f(T x) i32` against a contract `fn f(i32 x) i32` is
**[CE4004](error-catalog.md#ce4004)**, also when every instance uses `T = i32`. To implement the contract for one
instance, write the concrete target: `extend Box@(i32) with Pk`. The compiler also checks a
template that has no instance, and a concrete implementation that the program does not
use.

`Drop` is no exception: a generic target may implement it, and each instantiation's copy
carries it. A `Drop` implementation adds no bound: `extend Guard@(T: Clone) with Drop` is
**[CE4019](error-catalog.md#ce4019)**, because an instance that the bound excludes would release nothing. The orphan rule still applies and reads the target's BASE name, so only the
unit that declares `Box` may write `extend Box@(T) with Drop` (**[CE4012](error-catalog.md#ce4012)**). A wrapper
whose fields already own needs no `Drop` of its own -- destroying its fields destroys the
handle -- so declare one when the wrapper has something of its OWN to say, such as
flushing a buffer before the handle closes.

There is no specialization. A template and a concrete target of one perk on one base are
two implementations for that instance, and the second one is **[CE4002](error-catalog.md#ce4002)**, in either order,
with a note at the first. Implement the perk on the template, or on each concrete target.

### Every array may implement a perk

`extend T[] with P` is a template over the element type. It covers every dynamic array:
`i32[]`, `string[]`, a nested `i32[][]` (with `T = i32[]`) and an array of a struct. The
compiler makes the copy for an array type when the program uses that array type with the
perk:

```sushi
perk Named:
    fn name() string

extend T[] with Named:
    fn name() string:
        return "{self.len()} items"

fn show@(N: Named)(N thing) string:
    return thing.name()

fn main() i32:
    let i32[] xs = from([1, 2])
    let string[] names = from(["arthur", "ford", "trillian"])
    println(xs.name())          # 2 items
    println(show(names))        # 3 items
    return 0
```

The body may name `T`: `let T head = self[0]` holds an element. The compiler checks the
body one time, with `T` opaque, so the body can do with an element only what a bound
promises. The array form of a bound is `extend (T: Display)[] with P`: an array whose
element has no `Display` then does not implement `P`.

The predefined perks are legal on an array template. `Display` gives `println(xs)` and a
hole their string form. `Eq`, `Ord` and `Hashable` are read by a direct call and by an array
that a struct holds, a struct that is a `HashMap` key included. An array is not an operand
of `==` or `<` at the top level, and it is not a `HashMap` key, with an implementation or
not. A `Hashable` implementation also answers a direct `xs.hash()`.

The rules of an array extension apply: `extend i32[] with P` is ONE array type, a nested
target `extend T[][] with P` is **[CE2101](error-catalog.md#ce2101)**, and a library ships an array template in each
library kind. `extend T[] with Drop` is **[CE4016](error-catalog.md#ce4016)**: no unit declares an array type.

## Generic Constraints

Perks enable type constraints on generic types:

### Struct Constraints

```sushi
# Generic struct requiring Hashable
struct Container@(T: Hashable):
    T value

struct Point:
    i32 x
    i32 y

fn main() i32:
    # Valid: Point has a derived hash, so it satisfies the predefined Hashable
    let Container@(Point) c = Container(Point(10, 20))
    println(c.value.x)

    # Invalid: a struct holding a HashMap has no derived hash and no implementation,
    # so `Container@(Index)` is CE4006

    return 0
```

### Enum Constraints

```sushi
perk Describe:
    fn describe() string

enum Tagged@(T: Describe):
    One(T)
    Nothing()

enum Status:
    Active(i32)
    Inactive()

extend Status with Describe:
    fn describe() string:
        match self:
            Status.Active(n) -> return "Active: {n}"
            Status.Inactive() -> return "Inactive"

fn main() i32:
    let Tagged@(Status) t = Tagged.One(Status.Active(3))
    match t:
        Tagged.One(s) -> println(s.describe())
        Tagged.Nothing() -> println("nothing")
    return 0
```

The compiler checks a constraint at each written type. `Tagged@(i32)` is **[CE4006](error-catalog.md#ce4006)**, because
`i32` does not implement `Describe`.

## Generic Functions with Perks

Perks enable generic functions with constrained type parameters:

```sushi
# Generic function with perk constraint
fn compute_hash@(T: Hashable)(T value) u64:
    return value.hash()

struct Point:
    i32 x
    i32 y

# The override: without it, compute_hash(p) answers Point's derived hash.
extend Point with Hashable:
    fn hash() u64:
        return (self.x as u64) + (self.y as u64)

fn main() i32:
    let Point p = Point(10, 20)

    # Type inference: T inferred as Point
    let u64 h = compute_hash(p)
    println(h)  # Prints 30

    return 0
```

**Features:**
- Automatic type inference from call sites
- Compile-time constraint validation
- Zero runtime overhead through monomorphization
- Works with structs, enums, and primitives

## The Predefined Perks

Six perks ship with the compiler. None needs an import, none can be declared
(**[CE4001](error-catalog.md#ce4001)**), and no alias holds them: `sh.Hashable` is **[CE2001](error-catalog.md#ce2001)**, as `sh.Drop` is.

| Perk | Contract |
|------|----------|
| `Drop` | `fn drop(poke self) ~` |
| `Hashable` | `fn hash() u64` |
| `Eq` | `fn eq(Self other) bool` |
| `Ord` | `fn compare(Self other) i32` (negative, zero or positive) |
| `Display` | `fn to_str() string` |
| `Clone` | `fn clone() Self` |

**`Self` exists in these contracts only.** A perk you write still cannot name its receiver.
An implementation of `Eq`, `Ord` or `Display` writes its own type where the contract says
`Self`. A generic target writes its own instantiation. A mismatch is **[CE4004](error-catalog.md#ce4004)**, and its help
prints the contract with the target filled in.

```sushi
struct Point:
    i32 x
    i32 y

struct Box@(T):
    T value
    i32 tag

extend Point with Eq:
    fn eq(Point other) bool:
        return self.x == other.x

extend Box@(T) with Eq:
    fn eq(Box@(T) other) bool:
        return self.tag == other.tag

fn main() i32:
    let Box@(i32) a = Box(1, 7)
    let Box@(i32) b = Box(2, 7)
    println(Point(1, 2) == Point(1, 9))        # true: the override decides
    println(a.eq(b))                           # true
    return 0
```

**`Drop`** (`fn drop(poke self) ~`) declares a resource: a type that implements it
owns something RAII must release, whatever its fields say. It is documented with
[memory management](memory-management.md).

**`Eq`, `Ord` and `Display`** are derived for every struct and enum from what it holds.
The design is in [Derived contracts](design/derived-contracts.md).

- `==` and `!=` read `Eq`. `<`, `<=`, `>` and `>=` read `Ord`. An interpolation hole,
  `print` and `println` read `Display`.
- **The implementation is the override.** It wins in every position: the operator, a field
  of another derived method, `contains` and `index_of`, a `HashMap` key and `print`.
- **Not derived:** a type that holds a function value, a `ptr`, a closure, an iterator or a
  `HashMap@(K, V)`. An implementation gives such a type the contract. A constraint on it
  is **[CE4006](error-catalog.md#ce4006)**.
- **Constraints follow the top-level rule.** `bool` satisfies `Eq` and `Display`. It does not
  satisfy `Ord`, because a bare bool has no order. A bool FIELD orders `false` before `true`.
- **Methods:** `a.eq(b)` and `a.compare(b)` exist on every struct, enum and primitive. `x.to_str()`
  exists on every struct and enum. A wrong argument type is **[CE2006](error-catalog.md#ce2006)**.
- **One home per name.** Two perks that give one type a method of the same name are
  **[CE4015](error-catalog.md#ce4015)**. A derived method is not a home: a type can implement a user perk that provides
  `compare`. An explicit `a.compare(b)` then reads that implementation, and `<` still reads
  `Ord`.

The rules of the order, the float equality and the text format are in the
[Language Reference](language-reference.md#comparison).

**`Hashable`** (`fn hash() u64`) is the contract of the derived `hash()`. The rule
is NOMINAL and it reads one perk: a type satisfies `Hashable` when the compiler
derives a `hash()` for it, or when the type implements the perk itself.

- **Satisfied by derivation:** the twelve primitives, `string`, and every struct,
  enum and array whose parts the compiler can hash -- a `List@(T)` and an `Own@(T)`
  hash what they hold. No `extend ... with Hashable` is written, and none is needed.
- **Not satisfied:** a type the derive pass refuses -- a struct holding a
  `HashMap@(K, V)`, a `ptr`, or a function value. A constraint on it is **[CE4006](error-catalog.md#ce4006)**,
  exactly as for any other perk.
- **Overridable:** `extend T with Hashable: fn hash() u64:` REPLACES the derived hash
  everywhere (see [method resolution](design/method-resolution.md)). It also satisfies
  the constraint for a type the derive pass refuses. It gives a hash only: it does not
  make the type comparable, so a type with no equality (for example, a struct with
  a function-typed field) is still not a `HashMap` key (**[CE2055](error-catalog.md#ce2055)**), unless it also
  implements `Eq` (see
  [Key Requirements](stdlib/collections/hashmap.md#key-requirements)).

**`Clone`** (`fn clone() Self`) is the promise that `.clone()` is legal. A type satisfies
it when it holds no resource: it is not a `Drop` type, and no field, element or payload
holds one. So every primitive, `string`, array, `List@(T)` and plain struct satisfies it,
and a `File` or a struct that holds a `File` does not. The built-in `.clone()` is the
contract, so there is nothing to override: `extend X with Clone` is **[CE4017](error-catalog.md#ce4017)**. A generic
body that clones a `T` writes `@(T: Clone)`; without it, the clone is **[CE4018](error-catalog.md#ce4018)**. A handle
gets a second owner with `.share()`, not with `.clone()`.

```sushi
struct Point:
    i32 x
    i32 y

fn pair_of@(T: Clone)(T value) (T, T):
    return (value.clone(), value.clone())

fn main() i32:
    let (Point, Point) p = pair_of(Point(1, 2))
    let (string, string) s = pair_of("towel")
    println("{p.0.x} {s.1}")      # 1 towel
    return 0
```

A perk of your own follows the ordinary rule: only an explicit implementation
satisfies it. `perk Hashy: fn hash() u64` is not satisfied by `i32`, because the
compiler matches the perk's NAME and never its shape.

```sushi
struct Point:
    i32 x
    i32 y

fn compute_hash@(T: Hashable)(T value) u64:
    return value.hash()

fn main() i32:
    # All satisfy Hashable by derivation - no implementation is written
    let u64 h1 = compute_hash(42)               # i32
    let u64 h2 = compute_hash("test")           # string
    let u64 h3 = compute_hash(true)             # bool
    let u64 h4 = compute_hash(Point(1, 2))      # a plain struct
    println("{h1} {h2} {h3} {h4}")

    return 0
```

## Multiple Constraints

Types can require multiple perk implementations using the `+` operator:

```sushi
perk Describe:
    fn describe() string

# Multiple constraints on struct
struct Processor@(T: Hashable + Describe):
    T item

# Multiple constraints on function
fn process@(T: Hashable + Describe)(T item) ~:
    let u64 h = item.hash()
    let string s = item.describe()
    println("Hash: {h}")
    println("Description: {s}")

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return (self.x as u64) + (self.y as u64)

extend Point with Describe:
    fn describe() string:
        return "Point({self.x}, {self.y})"

fn main() i32:
    let Point p = Point(10, 20)
    process(p)
    return 0
```

## Common Patterns

### Hashable Pattern

A key type has a derived hash already; implement the predefined `Hashable` to
replace it (a `HashMap` uses the implementation for its keys):

```sushi
struct CustomKey:
    i32 id
    string name

extend CustomKey with Hashable:
    fn hash() u64:
        let u64 id_hash = self.id as u64
        let u64 name_hash = self.name.hash()
        return id_hash * 31 + name_hash
```

### Display Pattern

A struct already prints with a derived `Display`. Implement the predefined `Display` to
replace the text. The replacement applies to a hole, `print` and `println`, and to a field
of another type:

```sushi
struct User:
    string name
    i32 age

extend User with Display:
    fn to_str() string:
        return "{self.name} (age {self.age})"

fn show@(T: Display)(T item) ~:
    println(item)

fn main() i32:
    show(User("Arthur", 42))             # Arthur (age 42)
    return 0
```

### Ordering Pattern

A struct already orders by its fields in declaration order. Implement the predefined `Ord`
to change the rule, and `Eq` to change equality. A comparison of two values reads it:

```sushi
struct Score:
    i32 value

extend Score with Ord:
    fn compare(Score other) i32:
        if (self.value < other.value):
            return -1
        if (self.value > other.value):
            return 1
        return 0

fn find_max@(T: Ord)(nom T a, nom T b) T:
    if (a >= b):
        return a
    return b

fn main() i32:
    println(find_max(nom Score(3), nom Score(9)).value)    # 9
    return 0
```

### Multiple Perks Pattern

Implementing multiple perks for rich functionality:

```sushi
perk Describe:
    fn describe() string

perk Ranked:
    fn rank(Point other) i32

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return (self.x as u64) + (self.y as u64)

extend Point with Describe:
    fn describe() string:
        return "({self.x}, {self.y})"

extend Point with Ranked:
    fn rank(Point other) i32:
        let i32 self_sum = self.x + self.y
        let i32 other_sum = other.x + other.y
        if (self_sum < other_sum):
            return -1
        if (self_sum > other_sum):
            return 1
        return 0

fn main() i32:
    let Point p1 = Point(10, 20)
    let Point p2 = Point(15, 10)

    println(p1.describe())
    let u64 h = p1.hash()
    let i32 cmp = p1.rank(p2)
    println("{h} {cmp}")

    return 0
```

## Perk Visibility

A perk is private by default, as every declaration is. Write `public perk` to export it.
What a private perk hides is the CONTRACT:

- Another unit cannot implement a private perk or constrain a type parameter with it
  ([CE4011](error-catalog.md#ce4011)). A method that the perk provides stays callable, because a unit that can name the
  type can call what the type implements.
- A public declaration cannot constrain a type parameter with a private perk of its own unit
  ([CE3010](error-catalog.md#ce3010)), because the caller would have to name a perk that it cannot see.

An implementation (`extend T with P`) carries no `public` marker. It is global: one for each
pair of type and perk, and its methods are callable wherever there is a value of the type
(R8 of [Extension visibility](design/extension-visibility.md)).

## Error Codes

Perk-related compiler errors:

| Code | Description | Example |
|------|-------------|---------|
| [CE4001](error-catalog.md#ce4001) | Duplicate perk definition | Declaring `Describe` twice, or declaring `Hashable`, `Drop`, `Eq`, `Ord`, `Display` or `Clone`, which the compiler predefines |
| [CE4002](error-catalog.md#ce4002) | Type already implements perk | Two `extend Point with Hashable:` blocks, or `extend T[] with P` and `extend i32[] with P` |
| [CE4003](error-catalog.md#ce4003) | Unknown perk, or a perk out of the unit's scope | `extend Point with UnknownPerk:`, or `@(T: Named)` where only another unit imports `Named` |
| [CE4004](error-catalog.md#ce4004) | Method signature mismatch | Wrong parameter types, modes or return type; also a template header that does not match for every `T` |
| [CE4005](error-catalog.md#ce4005) | Missing required method | Perk defines `hash()` but implementation lacks it |
| [CE4006](error-catalog.md#ce4006) | Type doesn't implement required perk | `Container@(T: Hashable)` used with a type that is not `Hashable`. Reported one time, at the type that names the instantiation, with a note at the constraint |
| [CE4017](error-catalog.md#ce4017) | `Clone` implemented by hand | `extend Point with Clone:`; the compiler decides `Clone` |
| [CE4018](error-catalog.md#ce4018) | Clone of a type parameter with no `Clone` | `x.clone()` in `fn f@(T)(T x)` |
| [CE4019](error-catalog.md#ce4019) | `Drop` with a bound in its target | `extend Guard@(T: Clone) with Drop:` |
| [CE2124](error-catalog.md#ce2124) | Bound on a name that is a type | `extend Box@(Point: Clone) m()` |
| [CE6110](error-catalog.md#ce6110) | Bound outside the top level of a target | `fn f@(T)(Box@(T: Clone) b)` |
| [CE4007](error-catalog.md#ce4007) | Method name conflict | A perk method and an extension method of the same name on one type |
| [CE4010](error-catalog.md#ce4010) | Perk cannot have type parameters | `perk Conv@(T):`, or `fn show@(U)(U x)` in an implementation |
| [CE4011](error-catalog.md#ce4011) | Private perk used from another unit | `extend Box with other.PrivatePerk:`, or `@(T: other.PrivatePerk)` |
| [CE4012](error-catalog.md#ce4012) | `Drop` implemented outside the declaring unit | `extend lib.Handle with Drop:` in a consumer |
| [CE4016](error-catalog.md#ce4016) | `Drop` on a type that no unit declares | `extend i32[] with Drop:`, `extend T[] with Drop:`, `extend string with Drop:` |
| [CE4015](error-catalog.md#ce4015) | Method name with two homes | `extend Score with Ord:` and `extend Score with Ranked:` that both provide `compare`; also `@(T: A + B)` where `A` and `B` both declare one method name |
| [CE4014](error-catalog.md#ce4014) | Static method in a perk | `static fn get() i32` in an implementation |
| [CE0133](error-catalog.md#ce0133) | Error channel mismatch | The contract declares `| E` and the implementation does not, or the two channels differ |
| [CE2110](error-catalog.md#ce2110) | Function type as the target | `extend fn(i32) -> i32 with Show:` |
| [CE3010](error-catalog.md#ce3010) | Private perk in a public constraint | `public fn f@(T: MyPrivatePerk)(T x) ~` |

## Known Limitations

### 1. No Generic Perks

Perks cannot have type parameters. Declaring one is a compile error:

```sushi
# NOT supported
perk Iterator@(Item):
    fn next() Maybe@(Item)
# CE4010: perk Iterator cannot have type parameters
```

The rule applies to an implementation method too: it cannot declare type parameters of its
own (`fn show@(U)(U x) i32:` inside `extend Box with Shown:`), because the contract has no
slot to match them against. That is the same **[CE4010](error-catalog.md#ce4010)**. In the perk declaration itself,
`fn make@(U)(U x) i32` is a parse error (**[CE6001](error-catalog.md#ce6001)**). A generic method is a plain
extension method: `extend Box pick@(U)(U x) i32:`.

Iteration needs no perk. `foreach` walks any type that has a `next()` method that answers
`Maybe@(T)`. The compiler finds that method by name, not through a contract. See
[Iteration (design)](design/iteration.md), ruling 1.

### 2. No Perk Inheritance

A perk cannot require another perk. Write both constraints at the use site instead:
`@(T: Hashable + Describe)`.

### 3. No Default Implementations

Each implementation must write every method of the perk. A perk method has no body in the
perk declaration.

### 4. No `Self` Type

A perk method that you write cannot name "the implementing type". A method that takes a value
of that type names a concrete type in its signature, so the perk fits that type only. The
predefined `Eq` and `Ord` are the exception: their contracts hold a `Self` placeholder that
only the compiler can write, and an implementation writes its own type there. For the same reason,
a perk cannot hold a static method or a constructor ([CE4014](error-catalog.md#ce4014)).

## Best Practices

1. **Keep perks focused**: Each perk should represent a single cohesive concept
2. **Use descriptive names**: `Hashable`, `Describe`, `Ranked` clearly indicate purpose
3. **Minimize method count**: Fewer methods = easier to implement
4. **Document constraints**: Make it clear what perks are required for generic types
5. **Lean on the predefined perks**: a type the compiler can hash, compare or print needs no implementation of `Hashable`, `Eq`, `Ord` or `Display`
6. **Test thoroughly**: Verify implementations work with generic functions

## See Also

- [Generics](generics.md) - Generic types and monomorphization
- [Language Reference](language-reference.md) - Complete syntax reference
- [Examples](examples/README.md) - Working code examples
