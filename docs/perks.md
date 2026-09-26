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
- [Error Codes](#error-codes)
- [Known Limitations](#known-limitations)

## Overview

Perks provide a way to:
- Define shared behavior across different types
- Constrain generic types to ensure required functionality
- Enable polymorphic functions through static dispatch
- Achieve zero-cost abstractions through monomorphization

**Key Design Principles:**
- Perks return bare types (not `Result@(T)`), so a perk method body has no error
  channel: `??` is rejected there (CE0131). Handle a Result in the body with `match`
  or `.realise(default)`. A `??` inside a lambda in the body is legal -- the lambda
  has its own Result channel
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
    fn compare(peek Point other) i32
```

Two perks ship with the compiler and cannot be declared: `Hashable` (`fn hash() u64`)
and `Drop` (`fn drop(poke self) ~`). A unit that declares either is **CE4001**. See
[The Predefined Perks](#the-predefined-perks).

A perk method that takes the implementing type by reference names that type
explicitly (there is no `Self` keyword) and must use an explicit `peek` / `poke`
borrow. The implementation and call site must use the same borrow kind.

**Rules:**
- Perk methods do not return `Result@(T)` (unlike regular functions)
- Methods can access `self` implicitly
- Methods can take parameters including references
- Multiple methods can be defined in a single perk

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
        return "Point { x: {self.x}, y: {self.y} }"
```

**Implementation Rules:**
- All methods defined in the perk must be implemented
- Method signatures must match exactly (parameters, return types)
- Can implement multiple perks for the same type
- Can access struct fields via `self`

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
**CE2098** -- there is no partial specialization. An instantiation the program never
names costs nothing: no copy is made.

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
enum Result@(T: Displayable, E):
    Ok(T)
    Err(E)

enum Status:
    Active(i32)
    Inactive()

extend Status with Displayable:
    fn display() string:
        match self:
            Status.Active(n) -> return "Active: {n}"
            Status.Inactive() -> return "Inactive"
```

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
        return id_hash * 31 as u64 + name_hash
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
    fn compare(peek Score other) i32

struct Score:
    i32 value

extend Score with Comparable:
    fn compare(peek Score other) i32:
        if (self.value < other.value):
            return -1
        if (self.value > other.value):
            return 1
        return 0

fn find_max@(T: Comparable)(T a, T b) T:
    let i32 cmp = a.compare(peek b)
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
    fn compare(peek Point other) i32

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
    fn compare(peek Point other) i32:
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
    let i32 cmp = p1.compare(peek p2)

    return Result.Ok(0)
```

## Error Codes

Perk-related compiler errors:

| Code | Description | Example |
|------|-------------|---------|
| CE4001 | Duplicate perk definition | Declaring `Displayable` twice, or declaring `Hashable` or `Drop`, which the compiler predefines |
| CE4002 | Type already implements perk | Two `extend Point with Hashable:` blocks |
| CE4003 | Unknown perk | `extend Point with UnknownPerk:` |
| CE4004 | Method signature mismatch | Wrong parameter types or return type |
| CE4005 | Missing required method | Perk defines `hash()` but implementation lacks it |
| CE4006 | Type doesn't implement required perk | `Container@(T: Hashable)` used with type lacking Hashable. Reported once, at the type that names the instantiation, with a note at the constraint; the analysis stops there and no copy of the template is cut for the refused instantiation (#579) |
| CE4007 | Method name conflict | Perk method name conflicts with existing method |

## Known Limitations

### 1. Generic Function Type Inference

Cannot extract type parameters from complex generic types in function parameters:

```sushi
# Does NOT work - type inference limitation
fn hash_container@(T: Hashable)(Container@(T) c) u64:
    return Result.Ok(c.value.hash())

# Works - simple type parameter
fn compute_hash@(T: Hashable)(T value) u64:
    return Result.Ok(value.hash())
```

**Workaround:** Use simple type parameters only.

### 2. Nested Generic Function Calls

Generic functions calling other generic functions may fail to monomorphize:

```sushi
# May not work correctly
fn wrapper@(T: Hashable)(T value) u64:
    return compute_hash(value)  # Nested generic call

fn compute_hash@(T: Hashable)(T value) u64:
    return Result.Ok(value.hash())
```

**Workaround:** Avoid chained generic function calls or inline the logic.

### 3. No Generic Perks

Perks cannot have type parameters. Declaring one is a compile error:

```sushi
# NOT supported
perk Iterator@(Item):
    fn next() Maybe@(Item)
# CE4010: perk Iterator cannot have type parameters
```

**Status:** Planned for future release. The compiler rejects the declaration outright
(**CE4010**) — it used to be silently accepted and ignored.

The rule reaches the other end too: an implementation method may not declare type
parameters of its own (`fn show@(U)(U x) i32:` inside `extend Box with Shown:`), because
the contract has no slot to match them against. That is the same **CE4010**, at the list
itself. A generic method is a plain extension method — `extend Box pick@(U)(U x) i32:`.

**This example is not a missing feature, though.** Iteration needs no perk: `foreach`
walks any type carrying `next()` that answers `Maybe@(T)`, resolved as a method rather
than through a contract. That is a PROTOCOL, and it exists precisely because a perk
cannot name what it yields — see
[Iteration (design)](design/iteration.md), ruling 1.

### 4. No Perk Inheritance

Perks cannot require other perks:

```sushi
# NOT supported
perk Ord: Eq:
    fn compare(&Self other) i32
```

**Status:** Deferred to v0.6.

### 5. No Default Implementations

All perk methods must be fully implemented:

```sushi
# NOT supported
perk Eq:
    fn equals(&Self other) bool

    # Cannot provide default implementation
    fn not_equals(&Self other) bool:
        return not self.equals(other)
```

**Status:** Deferred to v0.6.

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
