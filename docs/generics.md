# Generics

[← Back to Documentation](README.md)

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
- **Extension methods** - Add methods to any type
- **Zero runtime overhead** - All generic code specialized at compile time

## Generic Structs

### Single Type Parameter

```sushi
struct Box<T>:
    T value

fn main() i32:
    let Box<i32> int_box = Box(value: 42)
    let Box<string> str_box = Box(value: "hello")

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

    return Result.Ok(0)
```

### Generic Struct with Arrays

```sushi
struct Container<T>:
    T[] items
    i32 capacity

fn main() i32:
    let Container<string> names = Container(
        items: from(["Alice", "Bob"]),
        capacity: 10
    )

    names.items.push("Charlie")
    println("Count: {names.items.len()}")

    return Result.Ok(0)
```

## Generic Enums

### Defining Generic Enums

```sushi
enum Option<T>:
    Some(T value)
    None()

fn main() i32:
    let Option<i32> num = Option.Some(value: 42)
    let Option<string> text = Option.None()

    match num:
        Option.Some(v) -> println("Value: {v}")
        Option.None() -> println("No value")

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
    let i32 x = identity(42)??      # T inferred as i32
    let string s = identity("hello")??  # T inferred as string

    return Result.Ok(0)
```

### Multiple Type Parameters

```sushi
fn make_pair<T, U>(T first, U second) Pair<T, U>:
    return Result.Ok(Pair(first: first, second: second))

fn main() i32:
    # T=i32, U=string inferred from arguments
    let auto p = make_pair(42, "answer")??
    println("Pair: {p.first}, {p.second}")

    return Result.Ok(0)
```

### Type Inference

Type parameters are automatically inferred from function arguments:

```sushi
fn wrap<T>(T value) Box<T>:
    return Result.Ok(Box(value: value))

fn main() i32:
    # Type inferred as Box<i32> from argument 42
    let auto b1 = wrap(42)??

    # Type inferred as Box<string> from argument "hello"
    let auto b2 = wrap("hello")??

    println("Wrapped int: {b1.value}")
    println("Wrapped string: {b2.value}")

    return Result.Ok(0)
```

### Perk Constraints

Generic functions can require type parameters to satisfy perk constraints:

```sushi
perk Hashable:
    fn hash() u64

fn compute_hash<T: Hashable>(T value) u64:
    return value.hash()

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        let u64 h = 0 as u64
        return h

fn main() i32:
    let Point p = Point(x: 10, y: 20)
    let u64 h = compute_hash(p)??  # T=Point inferred, Hashable verified

    println("Hash: {h}")
    return Result.Ok(0)
```

### Multiple Constraints

Functions can require multiple perk constraints:

```sushi
perk Displayable:
    fn display() string

fn process<T: Hashable + Displayable>(T item) ~:
    let u64 h = item.hash()??
    let string s = item.display()??
    println("Hash: {h}, Display: {s}")
    return Result.Ok(~)
```

### Known Limitations

1. **Type parameters must be inferrable from function parameters**
   - Cannot use generic functions with no parameters
   - Type arguments must appear in parameter types

2. **Cannot extract from complex generic types**
   - Cannot infer from `Pair<T, U>` parameters
   - Cannot infer from `T[]` array parameters

3. **No explicit type arguments**
   - Cannot write `identity<i32>(42)`
   - Must rely on inference from arguments

## Extension Methods

Add methods to existing types using `extend`:

### Basic Extension

```sushi
extend i32 squared() i32:
    return Result.Ok(self * self)

extend i32 is_even() bool:
    return Result.Ok(self % 2 == 0)

fn main() i32:
    let i32 x = 7

    let i32 sq = x.squared().realise(0)
    println("Squared: {sq}")

    if (x.is_even().realise(false)):
        println("Even")
    else:
        println("Odd")

    return Result.Ok(0)
```

### Generic Extension Methods

```sushi
extend Box<T> unwrap() T:
    return Result.Ok(self.value)

extend Box<T> map<U>(U new_value) Box<U>:
    return Result.Ok(Box(value: new_value))

fn main() i32:
    let Box<i32> b = Box(value: 42)

    let i32 val = b.unwrap().realise(0)
    println("Unwrapped: {val}")

    return Result.Ok(0)
```

### Extension for Arrays

```sushi
extend i32[] sum() i32:
    let i32 total = 0
    foreach(n in self.iter()):
        total := total + n
    return Result.Ok(total)

fn main() i32:
    let i32[] numbers = from([1, 2, 3, 4, 5])

    let i32 sum = numbers.sum().realise(0)
    println("Sum: {sum}")  # 15

    return Result.Ok(0)
```

### Extension for Strings

```sushi
extend string shout() string:
    return Result.Ok(self + "!!!")

extend string repeat(i32 times) string:
    let string result = ""
    let i32 i = 0
    while (i < times):
        result := result + self
        i := i + 1
    return Result.Ok(result)

fn main() i32:
    let string msg = "Don't Panic"

    println(msg.shout().realise(""))        # Don't Panic!!!
    println("Ha".repeat(3).realise(""))     # HaHaHa

    return Result.Ok(0)
```

## Nested Generics

Sushi supports arbitrarily nested generic types.

### Two Levels

```sushi
fn main() i32:
    # Result<Maybe<i32>>
    let Result<Maybe<i32>> result = parse_optional("42")

    match result:
        Result.Ok(maybe) ->
            match maybe:
                Maybe.Some(value) ->
                    println("Value: {value}")
                Maybe.None() ->
                    println("No value")
        Result.Err() ->
            println("Parse error")

    return Result.Ok(0)
```

### Three Levels

```sushi
fn main() i32:
    # Maybe<Maybe<Maybe<i32>>>
    let Maybe<Maybe<Maybe<i32>>> deeply_nested =
        Maybe.Some(Maybe.Some(Maybe.Some(42)))

    match deeply_nested:
        Maybe.Some(level2) ->
            match level2:
                Maybe.Some(level3) ->
                    match level3:
                        Maybe.Some(value) ->
                            println("Value: {value}")
                        Maybe.None() -> println("Level 3 None")
                Maybe.None() -> println("Level 2 None")
        Maybe.None() -> println("Level 1 None")

    return Result.Ok(0)
```

### Collections of Generics

```sushi
use <collections>

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
    groups.insert("evens", List.new())
    groups.insert("odds", List.new())

    return Result.Ok(0)
```

## Monomorphization

Generics are resolved at compile time through monomorphization.

### How It Works

Generic code is specialized for each concrete type used:

```sushi
extend Box<T> debug() ~:
    println("Box value: {self.value}")
    return Result.Ok(~)

fn main() i32:
    let Box<i32> b1 = Box(value: 42)
    let Box<string> b2 = Box(value: "hello")

    b1.debug()  # Generates debug() for Box<i32>
    b2.debug()  # Generates debug() for Box<string>

    return Result.Ok(0)
```

The compiler generates:

```
debug__Box_i32(Box<i32> self)
debug__Box_string(Box<string> self)
```

### Automatic Instantiation Detection

The compiler automatically detects which generic instantiations are needed:

```sushi
extend List<T> contains(T value) bool:
    foreach(item in self.iter()):
        if (item == value):
            return Result.Ok(true)
    return Result.Ok(false)

fn main() i32:
    let List<i32> nums = List.new()
    nums.push(1)
    nums.push(2)

    # Compiler generates contains() for List<i32>
    if (nums.contains(2).realise(false)):
        println("Found 2")

    return Result.Ok(0)
```

### Multi-Pass Compilation

1. **Pass 1.5**: Collect generic instantiations from method calls
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
- Limit number of distinct instantiations when possible
- Use LLVM optimizations (O2/O3) to deduplicate similar code
- Profile code size if binary size is critical

## Generic Constraints

Sushi supports perk constraints on generic functions, structs, and enums through the **perks system**.

### Function Constraints

Generic functions can require type parameters to satisfy perk interfaces:

```sushi
perk Hashable:
    fn hash() u64

fn compute_hash<T: Hashable>(T value) u64:
    return value.hash()
```

### Multiple Constraints

Use `+` to require multiple perks:

```sushi
fn process<T: Hashable + Displayable>(T item) ~:
    let u64 h = item.hash()??
    let string s = item.display()??
    return Result.Ok(~)
```

### Struct and Enum Constraints

**Coming soon:** Perk constraints for generic structs and enums (Phase 4B)

```sushi
# Future syntax:
struct Cache<K: Hashable, V>:
    K key
    V value
```

For more information on perks, see the [Perks documentation](PERKS.md) in the repository root

## Complete Example

Putting it all together:

```sushi
use <collections>

struct Pair<T, U>:
    T first
    U second

extend Pair<T, U> swap<T, U>() Pair<U, T>:
    return Result.Ok(Pair(first: self.second, second: self.first))

extend Pair<T, U> debug<T, U>() ~:
    println("Pair({self.first}, {self.second})")
    return Result.Ok(~)

fn main() i32:
    let Pair<i32, string> p1 = Pair(first: 42, second: "answer")
    p1.debug()  # Pair(42, answer)

    let Pair<string, i32> p2 = p1.swap().realise(Pair(first: "", second: 0))
    p2.debug()  # Pair(answer, 42)

    # Nested generics
    let List<Pair<i32, string>> pairs = List.new()
    pairs.push(Pair(first: 1, second: "one"))
    pairs.push(Pair(first: 2, second: "two"))

    foreach(pair in pairs.iter()):
        pair.debug()

    return Result.Ok(0)
```

## Best Practices

### 1. Use Descriptive Type Parameters

```sushi
# Good: Clear intent
struct Map<Key, Value>:
    Key[] keys
    Value[] values

# Acceptable: Single letter for simple cases
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

### 3. Prefer Extension Methods

```sushi
# Good: Natural method call syntax
extend List<T> is_empty() bool:
    return Result.Ok(self.len() == 0)

list.is_empty()

# Less ergonomic: Standalone function
fn is_list_empty<T>(List<T> list) bool:
    return Result.Ok(list.len() == 0)

is_list_empty(list)
```

### 4. Test Multiple Instantiations

```sushi
# Test with different types
let Box<i32> b1 = Box(value: 42)
let Box<string> b2 = Box(value: "test")
let Box<bool> b3 = Box(value: true)
```

---

**See also:**
- [Language Reference](language-reference.md) - Complete syntax
- [Standard Library](standard-library.md) - Built-in generic types
- [Examples](examples/) - Generic programming patterns
