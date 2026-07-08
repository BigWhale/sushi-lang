# Generics

[← Back to Documentation](index.md)

Complete guide to generic programming in Sushi: generic types, functions, and compile-time monomorphization.

## Table of Contents

- [Overview](#overview)
- [Generic Structs](#generic-structs)
- [Generic Enums](#generic-enums)
- [Generic Functions](#generic-functions)
- [Extension Methods](#extension-methods)
- [Nested Generics](#nested-generics)
- [Monomorphization](#monomorphization)

## Overview

Sushi provides zero-cost generics through compile-time monomorphization:

- **Generic structs** - `Pair<T, U>`, `Box<T>`
- **Generic enums** - `Result<T>`, `Maybe<T>`
- **Generic functions** - Type parameters inferred from usage
- **Extension methods** - Add methods to a type
- **Zero runtime overhead** - All generic code specialized at compile time

## Generic Structs

### Single Type Parameter

```sushi
struct Box<T>:
    T value

fn main() i32:
    let Box<i32> int_box = Box(value: 42)
    let Box<string> str_box = Box(value: "Mostly Harmless")

    println("Int: {int_box.value}")
    println("String: {str_box.value}")

    return Result.Ok(0)
```

### Multiple Type Parameters

```sushi
struct Pair<T, U>:
    T first
    U second

fn main() i32:
    let Pair<i32, string> p1 = Pair(first: 42, second: "answer")
    let Pair<bool, f64> p2 = Pair(first: true, second: 3.14)

    println("First: {p1.first}, Second: {p1.second}")
    println("Flag: {p2.first}")

    return Result.Ok(0)
```

### Generic Struct with Arrays

```sushi
struct Container<T>:
    T[] items
    i32 capacity

fn main() i32:
    let Container<string> names = Container(
        items: from(["Arthur", "Ford"]),
        capacity: 10
    )

    names.items.push("Trillian")
    println("Count: {names.items.len()}")

    return Result.Ok(0)
```

## Generic Enums

### Defining Generic Enums

A variant's payload is written as a **bare type** — `Some(T)`, not `Some(T value)` — and is
constructed positionally:

```sushi
enum Option<T>:
    Some(T)
    None()

fn main() i32:
    let Option<i32> num = Option.Some(42)
    let Option<string> text = Option.None()

    match num:
        Option.Some(v) -> println("Value: {v}")
        Option.None() -> println("No value")

    match text:
        Option.Some(v) -> println("Text: {v}")
        Option.None() -> println("No text")

    return Result.Ok(0)
```

### Built-in Generic Enums

Sushi provides two essential generic enums:

**Result<T>:**
```sushi
# Implicit return type for all functions
fn divide(i32 a, i32 b) i32:  # Returns Result<i32>
    if (b == 0):
        return Result.Err()
    return Result.Ok(a / b)
```

**Maybe<T>:**
```sushi
fn find_first_even(i32[] numbers) Maybe<i32>:
    foreach(n in numbers.iter()):
        if (n % 2 == 0):
            return Result.Ok(Maybe.Some(n))
    return Result.Ok(Maybe.None())
```

## Generic Functions

Sushi supports generic functions with automatic type inference from call sites. Type parameters are inferred from argument types at compile time.

### Type Parameter Syntax

```sushi
fn identity<T>(T value) T:
    return Result.Ok(value)

fn main() i32:
    let i32 x = identity(42).realise(0)          # T inferred as i32
    let string s = identity("Ford").realise("")  # T inferred as string

    println("x={x}, s={s}")

    return Result.Ok(0)
```

!!! note
    These examples use `.realise(default)` to unwrap the returned `Result` because the `??`
    operator is discouraged inside `main()` (it produces warning CW2511). Inside other
    functions, `let i32 x = identity(42)??` is the idiomatic form.

### Multiple Type Parameters

```sushi
struct Pair<T, U>:
    T first
    U second

fn make_pair<T, U>(T first, U second) Pair<T, U>:
    return Result.Ok(Pair(first: first, second: second))

fn main() i32:
    # T=i32, U=string inferred from arguments
    let Pair<i32, string> p = make_pair(42, "answer").realise(Pair(first: 0, second: ""))
    println("Pair: {p.first}, {p.second}")

    return Result.Ok(0)
```

### Type Inference

Type parameters are inferred from the function's arguments. The result type is known to the
caller, so the variable still needs an explicit type annotation:

```sushi
struct Box<T>:
    T value

fn wrap<T>(T value) Box<T>:
    return Result.Ok(Box(value: value))

fn main() i32:
    let Box<i32> b1 = wrap(42).realise(Box(value: 0))
    let Box<string> b2 = wrap("hello").realise(Box(value: ""))

    println("Wrapped int: {b1.value}")
    println("Wrapped string: {b2.value}")

    return Result.Ok(0)
```

### Perk Constraints

Generic functions can require type parameters to satisfy perk constraints. Note that perk
methods (like `hash()` below) return a **bare** value, while the surrounding ordinary
function still wraps its result in `Result.Ok`:

```sushi
perk Hashable:
    fn hash() u64

fn compute_hash<T: Hashable>(T value) u64:
    return Result.Ok(value.hash())

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return (self.x as u64) + (self.y as u64)

fn main() i32:
    let Point p = Point(x: 10, y: 20)
    let u64 h = compute_hash(p).realise(0 as u64)  # T=Point inferred, Hashable verified

    println("Hash: {h}")
    return Result.Ok(0)
```

### Multiple Constraints

Functions can require multiple perk constraints with `+`:

```sushi
perk Hashable:
    fn hash() u64

perk Displayable:
    fn display() string

struct Tag:
    i32 id

extend Tag with Hashable:
    fn hash() u64:
        return self.id as u64

extend Tag with Displayable:
    fn display() string:
        return "Tag#{self.id}"

fn process<T: Hashable + Displayable>(T item) ~:
    let u64 h = item.hash()
    let string s = item.display()
    println("Hash: {h}, Display: {s}")
    return Result.Ok(~)

fn main() i32:
    let Tag t = Tag(id: 7)
    process(t)
    return Result.Ok(0)
```

### Referencing a Generic Function as a Value

A generic function can be used as a [first-class function value](first-class-functions.md) when an
**explicit expected function type** is present. The annotation fixes which instantiation you mean:

```sushi
fn identity<T>(T x) T:
    return Result.Ok(x)

fn main() i32:
    let fn(i32) -> i32 g = identity   # picks identity<i32>
    let i32 n = g(41).realise(-1)     # 41
    println(n)
    return Result.Ok(0)
```

The same typed binding lets you hand a generic function to a higher-order function such as `map`:

```sushi
let fn(i32) -> i32 id = identity
let List<i32> same = map(xs, id).realise(List.new())
```

The requirement is the **expected type**: referencing a generic function with no expected function
type — for example passing `identity` directly as a call argument without a typed binding — is
still **CE2093**. Bind it to a typed local first.

### Known Limitations

1. **Type parameters must be inferrable from function parameters**
   - Cannot use generic functions with no parameters
   - Type arguments must appear in parameter types

2. **A few inference positions are still unsupported**
   - Named generics (`Pair<T, U>`, `List<T>`, `Maybe<T>`), array elements (`T[]`, `T[N]`), and
     function-typed parameters (`fn(T) -> U`) all infer their type parameters
   - A **bare-parameter** lambda argument (`|x| ...`) to a generic cannot be inferred (its type
     would come from the type parameter being inferred — circular); use a typed lambda
     (`|i32 x| ...`) or a function reference instead
   - A nested generic of an enclosing type parameter (e.g. `first(singleton(x))` where
     `singleton(x): List<T>` inside a `<T>` function) still fails inference

3. **No explicit type arguments**
   - Cannot write `identity<i32>(42)`
   - Must rely on inference from arguments

## Extension Methods

Add methods to a type using `extend`. An extension method returns its value **directly** —
there is no `Result.Ok(...)` wrapper, and you call it without `??` or `.realise()`:

### Basic Extension

```sushi
extend i32 squared() i32:
    return self * self

extend i32 is_even() bool:
    return self % 2 == 0

fn main() i32:
    let i32 x = 7

    println("Squared: {x.squared()}")

    if (x.is_even()):
        println("Even")
    else:
        println("Odd")

    return Result.Ok(0)
```

### String Extensions

Strings do not support the `+` operator (use interpolation instead). Build new strings with
`"{...}"`. String methods and interpolation-based concatenation require the strings unit:

```sushi
use <collections/strings>

extend string shout() string:
    return "{self}!!!"

extend string repeat(i32 times) string:
    let string result = ""
    let i32 i = 0
    while (i < times):
        result := "{result}{self}"
        i := i + 1
    return result

fn main() i32:
    println("Don't Panic".shout())     # Don't Panic!!!
    println("Ha".repeat(3))            # HaHaHa

    return Result.Ok(0)
```

### Generic Extension Methods

You can extend a user-defined generic struct. The method may return one of the struct's
type parameters or a concrete type:

```sushi
struct Box<T>:
    T value

extend Box<T> unwrap() T:
    return self.value

extend Box<T> describe() string:
    return "Box holding {self.value}"

fn main() i32:
    let Box<i32> b = Box(value: 42)

    println("Unwrapped: {b.unwrap()}")
    println(b.describe())

    return Result.Ok(0)
```

!!! warning "Limitations of generic extension methods"
    Generic extension methods are restricted. The following are **not** currently supported
    and will fail to compile: extending the built-in collections (`extend List<T> ...`),
    extending array types (`extend T[] ...`), methods that return the blank type (`~`),
    methods that take a generic parameter, and methods that permute multiple type parameters
    (for example `extend Pair<T, U> swap() Pair<U, T>`). Prefer ordinary generic functions
    for those cases.

## Nested Generics

Sushi supports nested generic types.

### Two Levels

A function returning `Maybe<i32>` is implicitly wrapped to `Result<Maybe<i32>>`, so you match
the outer `Result` and then the inner `Maybe`:

```sushi
fn parse_optional(string s) Maybe<i32>:
    if (s == "42"):
        return Result.Ok(Maybe.Some(42))
    return Result.Ok(Maybe.None())

fn main() i32:
    match parse_optional("42"):
        Result.Ok(maybe) ->
            match maybe:
                Maybe.Some(value) -> println("Value: {value}")
                Maybe.None() -> println("No value")
        Result.Err(_) -> println("Parse error")

    return Result.Ok(0)
```

### Three Levels

```sushi
fn main() i32:
    let Maybe<Maybe<Maybe<i32>>> deeply_nested = Maybe.Some(Maybe.Some(Maybe.Some(42)))

    match deeply_nested:
        Maybe.Some(level2) ->
            match level2:
                Maybe.Some(level3) ->
                    match level3:
                        Maybe.Some(value) -> println("Value: {value}")
                        Maybe.None() -> println("Level 3 None")
                Maybe.None() -> println("Level 2 None")
        Maybe.None() -> println("Level 1 None")

    return Result.Ok(0)
```

### Collections of Generics

```sushi
use <collections/hashmap>

fn main() i32:
    # List<Maybe<i32>>
    let List<Maybe<i32>> optionals = List.new()
    optionals.push(Maybe.Some(1))
    optionals.push(Maybe.None())
    optionals.push(Maybe.Some(3))

    foreach(opt in optionals.iter()):
        match opt:
            Maybe.Some(v) -> println("Value: {v}")
            Maybe.None() -> println("None")

    # HashMap<string, List<i32>>
    let HashMap<string, List<i32>> groups = HashMap.new()
    let List<i32> evens = List.new()
    evens.push(2)
    groups.insert("evens", evens)
    println("Groups: {groups.len()}")
    groups.free()

    return Result.Ok(0)
```

## Monomorphization

Generics are resolved at compile time through monomorphization.

### How It Works

Generic code is specialized for each concrete type used:

```sushi
struct Box<T>:
    T value

extend Box<T> describe() string:
    return "Box holding {self.value}"

fn main() i32:
    let Box<i32> b1 = Box(value: 42)
    let Box<string> b2 = Box(value: "hello")

    println(b1.describe())  # Specialized describe() for Box<i32>
    println(b2.describe())  # Specialized describe() for Box<string>

    return Result.Ok(0)
```

The compiler generates a distinct specialization for each instantiation — roughly
`describe__Box_i32` and `describe__Box_string` — with no runtime dispatch.

### Automatic Instantiation Detection

The compiler automatically detects which generic instantiations are needed from call sites:

```sushi
fn largest<T>(T a, T b) T:
    if (a > b):
        return Result.Ok(a)
    return Result.Ok(b)

fn main() i32:
    # Compiler generates largest() for i32 and for f64
    let i32 mi = largest(3, 9).realise(0)
    let f64 mf = largest(2.5, 1.5).realise(0.0)

    println("{mi} {mf}")

    return Result.Ok(0)
```

### Multi-Pass Compilation

1. **Pass 1.5**: Collect generic instantiations from call sites
2. **Pass 1.6**: Monomorphize generic types to concrete types
3. **Pass 1.7**: Type resolution with concrete types
4. **Pass 2**: Type validation on specialized code

### Code Size Implications

Each unique instantiation generates separate code:

```sushi
let Box<i32> b1 = Box(value: 1)       # Box<i32> code
let Box<i64> b2 = Box(value: 2)       # Box<i64> code
let Box<string> b3 = Box(value: "3")  # Box<string> code
```

**Best practices:**
- Limit the number of distinct instantiations when possible
- Use LLVM optimizations (O2/O3) to deduplicate similar code
- Profile code size if binary size is critical

## Generic Constraints

Sushi supports perk constraints on generic functions through the **perks system**. (Perk
constraints on generic structs and enums are not yet available.)

### Function Constraints

```sushi
perk Hashable:
    fn hash() u64

fn compute_hash<T: Hashable>(T value) u64:
    return Result.Ok(value.hash())
```

### Multiple Constraints

Use `+` to require multiple perks. Perk methods return bare values, so they are called
without `??`:

```sushi
fn process<T: Hashable + Displayable>(T item) ~:
    let u64 h = item.hash()
    let string s = item.display()
    println("Hash: {h}, Display: {s}")
    return Result.Ok(~)
```

For more information on perks, see the [Perks documentation](perks.md).

## Complete Example

Putting several pieces together — generic structs, a generic function, and a perk-constrained
function:

```sushi
perk Describable:
    fn describe() string

struct Pair<T, U>:
    T first
    U second

struct Robot:
    string name

extend Robot with Describable:
    fn describe() string:
        return "Robot {self.name}"

fn make_pair<T, U>(T first, U second) Pair<T, U>:
    return Result.Ok(Pair(first: first, second: second))

fn announce<T: Describable>(T item) ~:
    println(item.describe())
    return Result.Ok(~)

fn main() i32:
    let Pair<i32, string> p = make_pair(42, "answer").realise(Pair(first: 0, second: ""))
    println("Pair: {p.first}, {p.second}")

    let Robot marvin = Robot(name: "Marvin")
    announce(marvin)

    # Nested generics in a List
    let List<Pair<i32, string>> pairs = List.new()
    pairs.push(Pair(first: 1, second: "one"))
    pairs.push(Pair(first: 2, second: "two"))
    println("Stored pairs: {pairs.len()}")

    return Result.Ok(0)
```

## Best Practices

### 1. Use Descriptive Type Parameters

```sushi
# Good: clear intent
struct KeyValue<Key, Value>:
    Key key
    Value value

# Acceptable: single letter for simple cases
struct Box<T>:
    T value
```

### 2. Provide Concrete Examples

```sushi
# Document with concrete types
# Example: make_pair(42, "answer") returns Pair<i32, string>
fn make_pair<T, U>(T first, U second) Pair<T, U>:
    return Result.Ok(Pair(first: first, second: second))
```

### 3. Prefer Generic Functions Over Generic Extension Methods

Generic functions are more capable than generic extension methods (which cannot extend the
built-in collections; see the warning above). When you need behavior over `List<T>`, write a
function:

```sushi
fn list_is_empty<T>(List<T> list) bool:
    return Result.Ok(list.len() == 0)
```

### 4. Test Multiple Instantiations

```sushi
let Box<i32> b1 = Box(value: 42)
let Box<string> b2 = Box(value: "test")
let Box<bool> b3 = Box(value: true)
```

---

**See also:**
- [Language Reference](language-reference.md) - Complete syntax
- [Standard Library](standard-library.md) - Built-in generic types
- [Perks](perks.md) - Trait-like constraints
