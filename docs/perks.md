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
  (CE0131). Handle a Result in the body with `match` or `.realise(default)`, or declare
  `| E` on the contract and on every implementation (see
  [Error Channels on Perk Methods](#error-channels-on-perk-methods)). A `??` inside a
  lambda in the body is legal, because the lambda has its own Result channel
- Static dispatch only (no dynamic dispatch/vtables)
- Explicit implementations required (no structural typing). The one exception is
  the predefined `Hashable`, which every type with a derived `hash()` satisfies
- Full type checking at compile time

## Defining Perks

A perk defines a set of method signatures that implementing types must provide:

```sushi
perk Displayable:
    fn display() string
    fn debug() string

perk Comparable:
    fn compare(Point other) i32
```

Two perks ship with the compiler and cannot be declared: `Hashable` (`fn hash() u64`)
and `Drop` (`fn drop(poke self) ~`). A unit that declares either is **CE4001**. See
[The Predefined Perks](#the-predefined-perks).

There is no `Self` type, so a perk method that takes a value of the implementing type
names that type explicitly (`fn compare(Point other) i32`). A parameter is a borrow by
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
- A perk cannot hold a static method (CE4014), because it has no `Self` to construct

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

extend Point with Displayable:
    fn display() string:
        return "Point({self.x}, {self.y})"

    fn debug() string:
        return "Point(x: {self.x}, y: {self.y})"
```

A `{` in a double-quoted string always opens an interpolation hole, and a hole cannot hold a
string literal. For a literal brace, use a single-quoted string (`'{'`), which does not
interpolate.

**Implementation Rules:**
- All methods defined in the perk must be implemented (CE4005)
- Method signatures must match exactly: parameters, modes, return type (CE4004) and error
  channel (CE0133)
- Can implement multiple perks for the same type
- Can access struct fields via `self`
- A name has one home: a perk method and an ordinary extension method of the same name on
  one type is CE4007. A name is a contract method or a convenience method, never both
- A function type cannot be the target (CE2110)

### Error Channels on Perk Methods

A perk method can declare an error channel `| E`. The contract and every implementation
must declare the same channel (CE0133). The body then spells both constructors, as a
function body does: `return Result.Ok(value)` and `return Result.Err(e)`. A bare
`return value` in a channel body is CE2030. `??` is legal in the body, and the call answers a
`Result@(T, E)`:

```sushi
enum ReadError:
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
    return Result.Ok(0)
```

### A generic type may implement a perk

The target may name a type parameter, and then every instantiation the program uses gets
its own copy of the implementation:

```sushi
perk Show:
    fn show() string

struct Box@(T):
    T item

extend Box@(T) with Show:
    fn show() string:
        return "boxed {self.item}"

fn render@(S: Show)(S thing) string:
    return Result.Ok(thing.show())

fn main() i32:
    let Box@(i32) n = Box(7)
    println(render(n).realise("failed"))
    return Result.Ok(0)
```

A concrete type argument is a **constraint** rather than a parameter name, the same rule
an extension target follows: `extend Box@(i32) with Show` applies to `Box@(i32)` and to
nothing else, and a partially concrete target such as `extend Pair@(i32, U) with Show` is
**CE2098** -- there is no partial specialization. The compiler makes no copy for an
instantiation that the program does not name.

The compiler checks the header of a template implementation one time, on the written
template, and not for each instance. So `fn f(T x) i32` against a contract
`fn f(i32 x) i32` is **CE4004**, also when every instance uses `T = i32`. To implement the
contract for one instance, write the concrete target: `extend Box@(i32) with Pk`. The
compiler also checks a template that has no instance, and a concrete implementation that the
program does not use.

`Drop` is no exception: a generic target may implement it, and each instantiation's copy
carries it. The orphan rule still applies and reads the target's BASE name, so only the
unit that declares `Box` may write `extend Box@(T) with Drop` (**CE4012**). A wrapper
whose fields already own needs no `Drop` of its own -- destroying its fields destroys the
handle -- so declare one when the wrapper has something of its OWN to say, such as
flushing a buffer before the handle closes.

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

    return Result.Ok(0)
```

### Enum Constraints

```sushi
perk Displayable:
    fn display() string

enum Tagged@(T: Displayable):
    One(T)
    Nothing()

enum Status:
    Active(i32)
    Inactive()

extend Status with Displayable:
    fn display() string:
        match self:
            Status.Active(n) -> return "Active: {n}"
            Status.Inactive() -> return "Inactive"

fn main() i32:
    let Tagged@(Status) t = Tagged.One(Status.Active(3))
    match t:
        Tagged.One(s) -> println(s.display())
        Tagged.Nothing() -> println("nothing")
    return Result.Ok(0)
```

The compiler checks a constraint at each written type. `Tagged@(i32)` is **CE4006**, because
`i32` does not implement `Displayable`.

## Generic Functions with Perks

Perks enable generic functions with constrained type parameters:

```sushi
# Generic function with perk constraint
fn compute_hash@(T: Hashable)(T value) u64:
    return Result.Ok(value.hash())

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
    let u64 h = compute_hash(p)??
    println(h)  # Prints 30

    return Result.Ok(0)
```

**Features:**
- Automatic type inference from call sites
- Compile-time constraint validation
- Zero runtime overhead through monomorphization
- Works with structs, enums, and primitives

## The Predefined Perks

Two perks ship with the compiler. Neither needs an import, neither can be declared
(**CE4001**), and no alias holds them: `sh.Hashable` is **CE2001**, as `sh.Drop` is.

**`Drop`** (`fn drop(poke self) ~`) declares a resource: a type that implements it
owns something RAII must release, whatever its fields say. It is documented with
[memory management](memory-management.md).

**`Hashable`** (`fn hash() u64`) is the contract of the derived `hash()`. The rule
is NOMINAL and it reads one perk: a type satisfies `Hashable` when the compiler
derives a `hash()` for it, or when the type implements the perk itself.

- **Satisfied by derivation:** the twelve primitives, `string`, and every struct,
  enum and array whose parts the compiler can hash -- a `List@(T)` and an `Own@(T)`
  hash what they hold. No `extend ... with Hashable` is written, and none is needed.
- **Not satisfied:** a type the derive pass refuses -- a struct holding a
  `HashMap@(K, V)`, a `ptr`, or a function value. A constraint on it is **CE4006**,
  exactly as for any other perk.
- **Overridable:** `extend T with Hashable: fn hash() u64:` REPLACES the derived hash
  everywhere (see [method resolution](design/method-resolution.md)). It also satisfies
  the constraint for a type the derive pass refuses. It gives a hash only: it does not
  make the type comparable, so a type with no equality test (for example, a struct with
  a function-typed field) is still not a `HashMap` key (**CE2055**, see
  [Key Requirements](stdlib/collections/hashmap.md#key-requirements)).

A perk of your own follows the ordinary rule: only an explicit implementation
satisfies it. `perk Hashy: fn hash() u64` is not satisfied by `i32`, because the
compiler matches the perk's NAME and never its shape.

```sushi
struct Point:
    i32 x
    i32 y

fn compute_hash@(T: Hashable)(T value) u64:
    return Result.Ok(value.hash())

fn main() i32:
    # All satisfy Hashable by derivation - no implementation is written
    let u64 h1 = compute_hash(42)??               # i32
    let u64 h2 = compute_hash("test")??           # string
    let u64 h3 = compute_hash(true)??             # bool
    let u64 h4 = compute_hash(Point(1, 2))??      # a plain struct
    println("{h1} {h2} {h3} {h4}")

    return Result.Ok(0)
```

## Multiple Constraints

Types can require multiple perk implementations using the `+` operator:

```sushi
perk Displayable:
    fn display() string

# Multiple constraints on struct
struct Processor@(T: Hashable + Displayable):
    T item

# Multiple constraints on function
fn process@(T: Hashable + Displayable)(T item) ~:
    let u64 h = item.hash()
    let string s = item.display()
    println("Hash: {h}")
    println("Display: {s}")
    return Result.Ok(~)

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return (self.x as u64) + (self.y as u64)

extend Point with Displayable:
    fn display() string:
        return "Point({self.x}, {self.y})"

fn main() i32:
    let Point p = Point(10, 20)
    process(p)??
    return Result.Ok(0)
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

### Displayable Pattern

Used for types that can be converted to strings:

```sushi
perk Displayable:
    fn display() string

struct User:
    string name
    i32 age

extend User with Displayable:
    fn display() string:
        return "{self.name} (age {self.age})"

fn print_item@(T: Displayable)(T item) ~:
    println(item.display())
    return Result.Ok(~)
```

### Comparable Pattern

Used for types that can be compared:

```sushi
perk Comparable:
    fn compare(Score other) i32

struct Score:
    i32 value

extend Score with Comparable:
    fn compare(Score other) i32:
        if (self.value < other.value):
            return -1
        if (self.value > other.value):
            return 1
        return 0

fn find_max@(T: Comparable)(T a, T b) T:
    let i32 cmp = a.compare(b)
    if (cmp >= 0):
        return Result.Ok(a)
    return Result.Ok(b)
```

### Multiple Perks Pattern

Implementing multiple perks for rich functionality:

```sushi
perk Displayable:
    fn display() string

perk Comparable:
    fn compare(Point other) i32

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return (self.x as u64) + (self.y as u64)

extend Point with Displayable:
    fn display() string:
        return "({self.x}, {self.y})"

extend Point with Comparable:
    fn compare(Point other) i32:
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

    println(p1.display())
    let u64 h = p1.hash()
    let i32 cmp = p1.compare(p2)
    println("{h} {cmp}")

    return Result.Ok(0)
```

## Perk Visibility

A perk is private by default, as every declaration is. Write `public perk` to export it.
What a private perk hides is the CONTRACT:

- Another unit cannot implement a private perk or constrain a type parameter with it
  (CE4011). A method that the perk provides stays callable, because a unit that can name the
  type can call what the type implements.
- A public declaration cannot constrain a type parameter with a private perk of its own unit
  (CE3010), because the caller would have to name a perk that it cannot see.

An implementation (`extend T with P`) carries no `public` marker. It is as visible as its
target type.

## Error Codes

Perk-related compiler errors:

| Code | Description | Example |
|------|-------------|---------|
| CE4001 | Duplicate perk definition | Declaring `Displayable` twice, or declaring `Hashable` or `Drop`, which the compiler predefines |
| CE4002 | Type already implements perk | Two `extend Point with Hashable:` blocks |
| CE4003 | Unknown perk | `extend Point with UnknownPerk:` |
| CE4004 | Method signature mismatch | Wrong parameter types, modes or return type; also a template header that does not match for every `T` |
| CE4005 | Missing required method | Perk defines `hash()` but implementation lacks it |
| CE4006 | Type doesn't implement required perk | `Container@(T: Hashable)` used with a type that is not `Hashable`. Reported one time, at the type that names the instantiation, with a note at the constraint |
| CE4007 | Method name conflict | A perk method and an extension method of the same name on one type |
| CE4010 | Perk cannot have type parameters | `perk Conv@(T):`, or `fn show@(U)(U x)` in an implementation |
| CE4011 | Private perk used from another unit | `extend Box with other.PrivatePerk:`, or `@(T: other.PrivatePerk)` |
| CE4012 | `Drop` implemented outside the declaring unit | `extend lib.Handle with Drop:` in a consumer |
| CE4014 | Static method in a perk | `static fn get() i32` in an implementation |
| CE0133 | Error channel mismatch | The contract declares `| E` and the implementation does not, or the two channels differ |
| CE2110 | Function type as the target | `extend fn(i32) -> i32 with Show:` |
| CE3010 | Private perk in a public constraint | `public fn f@(T: MyPrivatePerk)(T x) ~` |

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
slot to match them against. That is the same **CE4010**. In the perk declaration itself,
`fn make@(U)(U x) i32` is a parse error (**CE6001**). A generic method is a plain
extension method: `extend Box pick@(U)(U x) i32:`.

Iteration needs no perk. `foreach` walks any type that has a `next()` method that answers
`Maybe@(T)`. The compiler finds that method by name, not through a contract. See
[Iteration (design)](design/iteration.md), ruling 1.

### 2. No Perk Inheritance

A perk cannot require another perk. Write both constraints at the use site instead:
`@(T: Hashable + Displayable)`.

### 3. No Default Implementations

Each implementation must write every method of the perk. A perk method has no body in the
perk declaration.

### 4. No `Self` Type

A perk method cannot name "the implementing type". A method that takes a value of that type
names a concrete type in its signature, so the perk fits that type only. For the same reason,
a perk cannot hold a static method or a constructor (CE4014).

## Best Practices

1. **Keep perks focused**: Each perk should represent a single cohesive concept
2. **Use descriptive names**: `Hashable`, `Displayable`, `Comparable` clearly indicate purpose
3. **Minimize method count**: Fewer methods = easier to implement
4. **Document constraints**: Make it clear what perks are required for generic types
5. **Lean on the predefined `Hashable`**: a type the compiler can hash needs no implementation
6. **Test thoroughly**: Verify implementations work with generic functions

## See Also

- [Generics](generics.md) - Generic types and monomorphization
- [Language Reference](language-reference.md) - Complete syntax reference
- [Examples](examples/README.md) - Working code examples
