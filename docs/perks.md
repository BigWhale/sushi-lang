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
- [Synthetic Implementations](#synthetic-implementations)
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
- Perks return bare types (not `Result<T>`)
- Static dispatch only (no dynamic dispatch/vtables)
- Explicit implementations required (no structural typing)
- Full type checking at compile time

## Defining Perks

A perk defines a set of method signatures that implementing types must provide:

```sushi
perk Hashable:
    fn hash() u64

perk Displayable:
    fn display() string
    fn debug() string

perk Comparable:
    fn compare(&Self other) i32
```

**Rules:**
- Perk methods do not return `Result<T>` (unlike regular functions)
- Methods can access `self` implicitly
- Methods can take parameters including references
- Multiple methods can be defined in a single perk

## Implementing Perks

Use `extend TypeName with PerkName:` to implement a perk for a type:

```sushi
struct Point:
    i32 x
    i32 y

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

## Generic Constraints

Perks enable type constraints on generic types:

### Struct Constraints

```sushi
perk Hashable:
    fn hash() u64

# Generic struct requiring Hashable implementation
struct Container<T: Hashable>:
    T value

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return self.x as u64 + self.y as u64

fn main() i32:
    # Valid: Point implements Hashable
    let Container<Point> c = Container(Point(10, 20))

    # Invalid: Would fail with CE4006 if NoHash doesn't implement Hashable
    # let Container<NoHash> bad = Container(NoHash(42))

    return Result.Ok(0)
```

### Enum Constraints

```sushi
enum Result<T: Displayable, E>:
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
perk Hashable:
    fn hash() u64

# Generic function with perk constraint
fn compute_hash<T: Hashable>(T value) u64:
    return Result.Ok(value.hash())

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return self.x as u64 + self.y as u64

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

## Synthetic Implementations

Primitives automatically satisfy perks when they have matching auto-derived methods. This allows generic functions to work with primitives without explicit `extend...with` declarations:

```sushi
perk Hashable:
    fn hash() u64

fn compute_hash<T: Hashable>(T value) u64:
    return Result.Ok(value.hash())

fn main() i32:
    # All work automatically - no explicit implementations needed
    let u64 h1 = compute_hash(42)??           # i32
    let u64 h2 = compute_hash("test")??       # string
    let u64 h3 = compute_hash(true)??         # bool
    let u64 h4 = compute_hash(3.14)??         # f64

    return Result.Ok(0)
```

**Primitives with synthetic Hashable:**
- Integer types: `i8`, `i16`, `i32`, `i64`, `u8`, `u16`, `u32`, `u64`
- Floating-point: `f32`, `f64`
- Boolean: `bool`
- String: `string`

## Multiple Constraints

Types can require multiple perk implementations using the `+` operator:

```sushi
perk Hashable:
    fn hash() u64

perk Displayable:
    fn display() string

# Multiple constraints on struct
struct Processor<T: Hashable + Displayable>:
    T item

# Multiple constraints on function
fn process<T: Hashable + Displayable>(T item) ~:
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
        return self.x as u64 + self.y as u64

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

Used for types that can be hashed (e.g., HashMap keys):

```sushi
perk Hashable:
    fn hash() u64

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

fn print_item<T: Displayable>(T item) ~:
    println(item.display())
    return Result.Ok(~)
```

### Comparable Pattern

Used for types that can be compared:

```sushi
perk Comparable:
    fn compare(&Self other) i32

struct Score:
    i32 value

extend Score with Comparable:
    fn compare(&Score other) i32:
        if (self.value < other.value):
            return -1
        if (self.value > other.value):
            return 1
        return 0

fn find_max<T: Comparable>(T a, T b) T:
    let i32 cmp = a.compare(&b)
    if (cmp >= 0):
        return Result.Ok(a)
    return Result.Ok(b)
```

### Multiple Perks Pattern

Implementing multiple perks for rich functionality:

```sushi
perk Hashable:
    fn hash() u64

perk Displayable:
    fn display() string

perk Comparable:
    fn compare(&Self other) i32

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return self.x as u64 + self.y as u64

extend Point with Displayable:
    fn display() string:
        return "({self.x}, {self.y})"

extend Point with Comparable:
    fn compare(&Point other) i32:
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
    let i32 cmp = p1.compare(&p2)

    return Result.Ok(0)
```

## Error Codes

Perk-related compiler errors:

| Code | Description | Example |
|------|-------------|---------|
| CE4001 | Duplicate perk definition | Defining `Hashable` twice |
| CE4002 | Type already implements perk | Two `extend Point with Hashable:` blocks |
| CE4003 | Unknown perk | `extend Point with UnknownPerk:` |
| CE4004 | Method signature mismatch | Wrong parameter types or return type |
| CE4005 | Missing required method | Perk defines `hash()` but implementation lacks it |
| CE4006 | Type doesn't implement required perk | `Container<T: Hashable>` used with type lacking Hashable |
| CE4007 | Method name conflict | Perk method name conflicts with existing method |

## Known Limitations

### 1. Generic Function Type Inference

Cannot extract type parameters from complex generic types in function parameters:

```sushi
# Does NOT work - type inference limitation
fn hash_container<T: Hashable>(Container<T> c) u64:
    return Result.Ok(c.value.hash())

# Works - simple type parameter
fn compute_hash<T: Hashable>(T value) u64:
    return Result.Ok(value.hash())
```

**Workaround:** Use simple type parameters only.

### 2. Nested Generic Function Calls

Generic functions calling other generic functions may fail to monomorphize:

```sushi
# May not work correctly
fn wrapper<T: Hashable>(T value) u64:
    return compute_hash(value)  # Nested generic call

fn compute_hash<T: Hashable>(T value) u64:
    return Result.Ok(value.hash())
```

**Workaround:** Avoid chained generic function calls or inline the logic.

### 3. No Generic Perks

Perks cannot have type parameters:

```sushi
# NOT supported yet
perk Iterator<Item>:
    fn next() Maybe<Item>
```

**Status:** Planned for future release (Phase 6).

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
5. **Leverage synthetic implementations**: Use primitive types when possible
6. **Test thoroughly**: Verify implementations work with generic functions

## See Also

- [Generics](generics.md) - Generic types and monomorphization
- [Language Reference](language-reference.md) - Complete syntax reference
- [Examples](examples/) - Working code examples
