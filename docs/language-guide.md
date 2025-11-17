# Language Guide: A Tour of Sushi

[← Back to Documentation](README.md)

This guide provides a friendly tour of Sushi's features. If you're new to Sushi, start here before diving into the detailed [Language Reference](language-reference.md).

## Table of Contents

- [Hello World](#hello-world)
- [Variables and Types](#variables-and-types)
- [Functions and Returns](#functions-and-returns)
- [Control Flow](#control-flow)
- [Error Handling](#error-handling)
- [Collections](#collections)
- [Structs and Enums](#structs-and-enums)
- [Pattern Matching](#pattern-matching)
- [Generics](#generics)
- [Memory Management](#memory-management)

## Hello World

Every Sushi program starts with a `main` function that serves as the entry point for execution:

```sushi
fn main() i32:
    println("Mostly Harmless")
    return Result.Ok(0)
```

Key points:
- `fn` declares a function
- `i32` is the return type (32-bit integer)
- All functions actually return `Result<T>` - this is Sushi's approach to explicit error handling (more on this later)
- `println` outputs text with a newline to standard output
- The `return Result.Ok(0)` convention indicates successful program termination (exit code 0)

The main function must return an `i32` (which the operating system uses as the exit code), and like all Sushi functions, it must explicitly wrap this value in `Result.Ok()` to indicate successful execution.

## Variables and Types

### Declaration and Rebinding

Sushi uses explicit variable declarations with type annotations for clarity and compile-time safety:

```sushi
fn main() i32:
    # Declare with let
    let i32 answer = 42
    let string question = "Ultimate Question"

    # Rebind with :=
    answer := 54  # Wrong answer!

    return Result.Ok(0)
```

**Important**: Variables must be declared with `let` before you can rebind them with `:=`. This two-step approach makes it clear when a variable is first introduced versus when its value is being changed.

The declaration syntax follows the pattern `let <type> <name> = <value>`, where the type comes before the variable name. This makes it easy to scan code and immediately see what types are being used. Once declared, you can reassign values using the `:=` operator, which indicates mutation rather than declaration.

### Numeric Types

Sushi has explicit numeric types:

```sushi
fn main() i32:
    # Signed integers
    let i8 tiny = 127
    let i32 normal = 2147483647
    let i64 huge = 9223372036854775807

    # Unsigned integers
    let u8 byte = 255
    let u32 unsigned = 4294967295

    # Floating-point
    let f32 pi = 3.14
    let f64 precise = 3.141592653589793

    return Result.Ok(0)
```

### Type Conversion

All conversions must be explicit with `as`:

```sushi
fn main() i32:
    let i32 x = 42
    let f64 y = x as f64      # int to float
    let u32 z = y as u32      # float to unsigned int

    return Result.Ok(0)
```

### Strings

Strings have full UTF-8 support:

```sushi
fn main() i32:
    let string text = "Don't Panic"
    let string emoji = "🐬 🌍"

    println(text)
    println("Length: {text.len()}")     # Character count
    println("Size: {text.size()}")      # Byte count

    return Result.Ok(0)
```

**String methods:**
- `.len() -> i32` - Character count (UTF-8 aware)
- `.size() -> i32` - Byte count
- `.is_empty() -> bool` - Check if string is empty
- `.find(string) -> Maybe<i32>` - Find substring position
- `.split(string) -> string[]` - Split into array by delimiter
- `.trim() -> string` - Remove leading/trailing whitespace
- `.to_upper() -> string` - Convert to uppercase (ASCII only)
- `.to_lower() -> string` - Convert to lowercase (ASCII only)

See [Standard Library: String Methods](standard-library.md#string-methods) for detailed documentation.

### String Interpolation

Embed expressions in strings with `{...}`:

```sushi
fn main() i32:
    let i32 answer = 42
    let string name = "Arthur"

    println("Hello {name}, the answer is {answer}")
    println("Next answer: {answer + 1}")

    return Result.Ok(0)
```

## Functions and Returns

Functions are the building blocks of Sushi programs. Every function follows a consistent pattern: explicit parameter types, explicit return type, and mandatory `Result<T>` wrapping for all returns.

### Basic Functions

```sushi
fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

fn greet(string name) ~:  # ~ is "blank" type (no return value)
    println("Hello, {name}!")
    return Result.Ok(~)

fn main() i32:
    let i32 sum = add(5, 7).realise(0)
    greet("Ford")
    return Result.Ok(0)
```

**Function syntax breakdown**:
- `fn` keyword declares a function
- Parameters: `type name` pairs, comma-separated
- Return type: comes after the parameter list
- `~` ("blank" or "unit" type): used for functions that don't return a meaningful value
- Body: indented block following the colon
- Returns: must be `Result.Ok(value)` or `Result.Err()`

**The blank type (`~`)**: When a function performs an action but doesn't produce a value (like printing or modifying a reference), it returns `~`. You must still wrap it: `return Result.Ok(~)`. This maintains consistency with Sushi's error handling model.

### Multiple Parameters

```sushi
fn describe(string name, i32 age, bool friendly) ~:
    println("{name} is {age} years old")
    if (friendly):
        println("They're quite friendly!")
    return Result.Ok(~)

fn main() i32:
    describe("Zaphod", 200, false)
    return Result.Ok(0)
```

**Parameter passing**:
- **Primitives and strings**: Passed by copy (cheap, no ownership transfer)
- **Dynamic arrays**: Passed by move (ownership transfers to the callee)
- **Structs**: Passed by move if they contain dynamic arrays
- **References**: Use `&T` to pass mutable references without transferring ownership

If you need to keep using a dynamic array after passing it to a function, either pass a reference (`&i32[]`) or explicitly clone it (`.clone()`) before passing.

## Control Flow

### If-Elif-Else

```sushi
fn main() i32:
    let i32 panic_level = 7

    # Parentheses required around conditions
    if (panic_level > 10):
        println("Serious panic!")
    elif (panic_level > 5):
        println("Mild panic")
    else:
        println("Stay calm")

    return Result.Ok(0)
```

### While Loops

```sushi
fn main() i32:
    let i32 countdown = 5

    while (countdown > 0):
        println("T-minus {countdown}")
        countdown := countdown - 1

    println("Liftoff!")
    return Result.Ok(0)
```

### For-Each Loops

```sushi
fn main() i32:
    let string[] names = from(["Arthur", "Ford", "Trillian"])

    foreach(name in names.iter()):
        println("Passenger: {name}")

    return Result.Ok(0)
```

### Break and Continue

```sushi
fn main() i32:
    let i32 x = 0

    while (x < 10):
        x := x + 1

        if (x == 3):
            continue  # Skip 3

        if (x == 8):
            break  # Stop at 8

        println(x)

    return Result.Ok(0)
```

## Error Handling

### Result<T>

Sushi uses `Result<T>` as its fundamental approach to error handling. Every function in Sushi implicitly returns a `Result<T>` type, even if you declare the return type as just `T`. This design choice eliminates entire classes of bugs by making error handling explicit and impossible to ignore.

**The Philosophy**: In many languages, functions can fail silently or throw exceptions that might not be handled. Sushi takes a different approach inspired by Rust: if a function can fail, that failure is encoded in the type system. You must explicitly choose to handle errors or propagate them.

```sushi
fn divide(i32 a, i32 b) i32:
    if (b == 0):
        return Result.Err()  # Error case
    return Result.Ok(a / b)  # Success case

fn main() i32:
    let Result<i32> result = divide(42, 6)

    # Check if successful
    if (result):
        let i32 value = result.realise(0)
        println("Result: {value}")
    else:
        println("Division failed")

    return Result.Ok(0)
```

**Key Concepts**:
- When you declare a function returning `i32`, it actually returns `Result<i32>`
- Success values must be wrapped: `return Result.Ok(value)`
- Failures are signaled with: `return Result.Err()`
- You can use `Result` values directly in conditionals: `if (result)` checks for success
- The `.realise(default)` method extracts the value, using the default if the result is an error

This approach eliminates null pointer exceptions and ensures that error cases are always visible in the code. The compiler enforces that you handle or propagate errors - you cannot accidentally ignore them.

### Unwrapping with .realise()

The `.realise(default)` method provides a safe way to extract values from `Result<T>`, similar to unwrapping in other languages but with a mandatory fallback value:

```sushi
fn get_value() i32:
    return Result.Ok(42)

fn main() i32:
    # Unwrap with default value
    let i32 x = get_value().realise(0)
    println("Value: {x}")

    return Result.Ok(0)
```

The name "realise" reflects the operation of "making the value real" by extracting it from the Result wrapper. Unlike unsafe unwrap operations in other languages, `.realise()` always requires a default value, ensuring your code never crashes from unwrapping an error state.

**Common patterns**:
- Numeric defaults: `.realise(0)` or `.realise(-1)`
- String defaults: `.realise("")` for empty strings
- Boolean defaults: `.realise(false)` for failure states
- Using the default to indicate error: `let i32 result = compute().realise(-1)` where `-1` signals failure

### Error Propagation (?? operator)

The `??` operator provides elegant error propagation - it unwraps successful results and automatically returns errors to the caller. This is one of Sushi's most powerful features for writing clean error-handling code:

```sushi
fn read_config() string:
    # If open fails, ?? returns Err immediately
    let file f = open("config.txt", FileMode.Read())??

    # Only reaches here if open succeeded
    let string content = f.read()
    f.close()
    return Result.Ok(content)

fn main() i32:
    let Result<string> config = read_config()
    # Handle config...
    return Result.Ok(0)
```

**How it works**:
1. If the `Result` is `Ok(value)`, `??` extracts and returns the value
2. If the `Result` is `Err()`, `??` immediately returns `Result.Err()` from the current function
3. The error propagates up the call stack until someone handles it

**RAII Safety**: The `??` operator is fully integrated with Sushi's RAII (Resource Acquisition Is Initialization) system. When an error is propagated, all resources in the current scope are properly cleaned up before the function returns. This means you never leak memory or file handles when errors occur.

**Advantages over manual error checking**:
```sushi
# Without ??: verbose and error-prone
fn process() string:
    let Result<file> f_result = open("data.txt", FileMode.Read())
    if (not f_result):
        return Result.Err()
    let file f = f_result.realise(...)  # How do we get a default file?
    # ... more manual checks

# With ??: clean and safe
fn process() string:
    let file f = open("data.txt", FileMode.Read())??
    let string data = f.read()
    return Result.Ok(data)
```

The `??` operator makes error handling code read almost like non-error-handling code, while maintaining full safety and explicit error propagation.

### Maybe<T> for Optional Values

`Maybe<T>` is Sushi's type-safe way to represent values that might or might not exist. It replaces the dangerous practice of using sentinel values (like `-1`, `null`, or empty strings) to indicate "no value". With `Maybe<T>`, the absence of a value is encoded in the type system, making it impossible to forget to check.

**The Problem with Sentinel Values**: In many languages, you might return `-1` to indicate "not found" or use `null` to mean "no value". This leads to bugs because:
- The sentinel value might be a valid result (what if `-1` is actually in your data?)
- You can forget to check for the sentinel value
- Different functions might use different sentinel values

**The Maybe Solution**: `Maybe<T>` has two variants:
- `Maybe.Some(value)` - contains a value of type `T`
- `Maybe.None()` - represents the absence of a value

```sushi
fn find_first_even(i32[] numbers) Maybe<i32>:
    foreach(n in numbers.iter()):
        if (n % 2 == 0):
            return Result.Ok(Maybe.Some(n))
    return Result.Ok(Maybe.None())

fn main() i32:
    let i32[] data = from([1, 3, 5, 8])
    let Maybe<i32> result = find_first_even(data)

    match result:
        Maybe.Some(value) ->
            println("Found: {value}")
        Maybe.None() ->
            println("No even numbers")

    return Result.Ok(0)
```

**Key Methods on Maybe<T>**:
- `.is_some()` - returns `true` if the Maybe contains a value
- `.is_none()` - returns `true` if the Maybe is empty
- `.realise(default)` - extracts the value or returns the default if None
- `.expect(message)` - extracts the value or terminates with an error message

**Common Use Cases**:
- Search operations (return `Maybe.Some(index)` if found, `Maybe.None()` if not)
- Optional configuration values (return `Maybe.Some(config)` or `Maybe.None()`)
- Nullable references in data structures (use `Maybe<T>` instead of trying to represent null)
- HashMap lookups (`.get()` returns `Maybe<V>` since the key might not exist)

**Composing Maybe with Result**: Since functions return `Result<T>`, you often see `Result<Maybe<T>>` - a result that might be an error, or might be a success with an optional value. The type system helps you handle all cases correctly.

## Collections

Sushi provides multiple collection types, each optimized for different use cases. Understanding when to use each type is key to writing efficient code.

### Arrays

Sushi has two array types: **fixed-size** arrays allocated on the stack, and **dynamic** arrays that can grow and shrink:

```sushi
fn main() i32:
    # Fixed-size array
    let i32[3] fixed = [1, 2, 3]

    # Dynamic array
    let i32[] dynamic = from([1, 2, 3])
    dynamic.push(4)
    dynamic.push(5)

    println("Length: {dynamic.len()}")

    # Iteration
    foreach(n in dynamic.iter()):
        println(n)

    return Result.Ok(0)
```

**Fixed vs Dynamic**:
- **Fixed arrays** (`T[N]`): Size known at compile time, allocated on the stack, cannot be resized. Fast and lightweight.
- **Dynamic arrays** (`T[]`): Size can change at runtime, heap-allocated, supports push/pop operations. The `from([...])` function converts a fixed array literal to a dynamic array.

**Array methods**: `.len()`, `.get()`, `.push()`, `.pop()`, `.clone()`, `.iter()`, `.hash()`

**Memory Management**: Dynamic arrays use RAII - they're automatically deallocated when they go out of scope. The destructor recursively cleans up all elements, so arrays of structs or nested arrays are properly freed. Arrays use move semantics: when you pass a dynamic array to a function, ownership transfers unless you explicitly `.clone()` it.

### List<T>

`List<T>` is a generic growable collection that provides more flexibility than raw dynamic arrays. It's similar to `Vec<T>` in Rust or `ArrayList<T>` in Java:

```sushi
fn main() i32:
    let List<string> passengers = List.new()

    passengers.push("Arthur")
    passengers.push("Ford")
    passengers.push("Trillian")

    println("Passengers: {passengers.len()}")

    let Maybe<string> first = passengers.get(0)
    match first:
        Maybe.Some(name) -> println("First: {name}")
        Maybe.None() -> println("Empty list")

    return Result.Ok(0)
```

**Key Features**:
- **Generic**: Works with any type - `List<i32>`, `List<string>`, `List<MyStruct>`, etc.
- **Automatic growth**: Capacity doubles when full, giving amortized O(1) push operations
- **Safe access**: `.get()` returns `Maybe<T>` instead of crashing on invalid indices
- **Efficient**: Uses `llvm.memmove` for shifting elements during insert/remove operations

**List methods**:
- Creation: `.new()`, `.with_capacity(n)`
- Access: `.get(index)`, `.len()`, `.capacity()`, `.is_empty()`
- Modification: `.push(value)`, `.pop()`, `.insert(index, value)`, `.remove(index)`, `.clear()`
- Memory: `.reserve(additional)`, `.shrink_to_fit()`, `.free()`, `.destroy()`
- Iteration: `.iter()`, `.debug()`

**When to use List vs raw arrays**: Use `List<T>` when you need frequent insertions/removals at arbitrary positions, capacity management, or want the additional safety of `Maybe<T>` returns. Use raw dynamic arrays (`T[]`) for simpler use cases where you just need push/pop at the end.

### HashMap<K, V>

`HashMap<K, V>` provides O(1) average-case key-value lookups using open addressing with linear probing:

```sushi
fn main() i32:
    let HashMap<string, i32> ages = HashMap.new()

    ages.insert("Arthur", 42)
    ages.insert("Ford", 200)
    ages.insert("Trillian", 30)

    let Maybe<i32> age = ages.get("Arthur")
    match age:
        Maybe.Some(a) -> println("Arthur is {a}")
        Maybe.None() -> println("Not found")

    return Result.Ok(0)
```

**Implementation Details**:
- **Open addressing**: Uses linear probing instead of chaining, giving better cache locality
- **Automatic resizing**: Grows at 0.75 load factor to maintain performance
- **Power-of-two capacity**: Allows fast modulo using bitwise AND operations
- **Auto-derived hashing**: Any type with a `.hash()` method can be used as a key

**HashMap methods**: `.new()`, `.insert(key, value)`, `.get(key)`, `.remove(key)`, `.contains(key)`, `.len()`, `.debug()`, `.free()`

**Memory Management**: HashMaps use recursive destruction - when you call `.free()` or when the HashMap goes out of scope, it destroys all entries and their contents. This works correctly even for complex value types like structs containing arrays or nested enums.

**Limitations**: Keys must implement `.hash()` (auto-derived for most types). Enum variants with dynamic array fields currently cause type system errors and cannot be stored as values.

## Structs and Enums

Structs and enums are Sushi's primary ways to create custom data types. Structs group related data together, while enums represent types that can be one of several variants.

### Defining Structs

Structs are product types - they contain multiple fields simultaneously:

```sushi
struct Person:
    string name
    i32 age
    bool friendly

fn main() i32:
    let Person arthur = Person(name: "Arthur", age: 42, friendly: true)

    println("{arthur.name} is {arthur.age} years old")

    # Modify fields
    arthur.age := 43

    return Result.Ok(0)
```

**Key features**:
- **Named field initialization**: Use `StructName(field: value, ...)` syntax for clarity and order-independence
  - Positional also supported: `Person("Arthur", 42, true)` for brevity
  - Named parameters prevent "boolean trap" bugs in structs with multiple bool fields
  - Cannot mix positional and named (all-or-nothing)
- **Field access**: Use dot notation `struct.field` to read or modify fields
- **RAII cleanup**: When a struct goes out of scope, all its fields are recursively destroyed
- **Auto-derived hashing**: Structs automatically get a `.hash()` method combining all field hashes

**Common patterns**:
- **Data transfer objects**: Group related data for passing between functions
- **Configuration**: Struct fields for application settings
- **Complex state**: Multiple related values that belong together

### Defining Enums

Enums are sum types - a value can be exactly one variant at a time:

```sushi
enum Status:
    Ready()
    Working(i32 progress)
    Done()

fn main() i32:
    let Status current = Status.Working(progress: 75)

    match current:
        Status.Ready() ->
            println("Ready to start")
        Status.Working(progress) ->
            println("Progress: {progress}%")
        Status.Done() ->
            println("Completed")

    return Result.Ok(0)
```

**Key features**:
- **Variants with data**: Each variant can contain different types of associated data
- **Type safety**: The compiler ensures you handle all variants through exhaustive pattern matching
- **Memory efficient**: Enums use a discriminant tag plus space for the largest variant
- **RAII for complex variants**: If a variant contains a struct or array, it's properly cleaned up

**How enums are implemented**:
Sushi enums compile to a struct containing:
1. A discriminant (tag) indicating which variant is active
2. Storage for the variant data (union of all variant types)

When you pattern match, the compiler generates a switch on the discriminant, then extracts and interprets the variant data appropriately.

**Common use cases**:
- **State machines**: Represent different states with different associated data
- **Error types**: Different error variants with relevant information
- **Optional complex data**: Use `Maybe<T>` (which is an enum) for values that might not exist
- **Algebraic data types**: Build sophisticated recursive data structures

## Pattern Matching

Pattern matching is Sushi's way of deconstructing enums and handling different cases. The compiler enforces **exhaustiveness checking** - you must handle all possible variants, ensuring you never forget a case.

### Match Expressions

```sushi
enum Response:
    Success(i32 code)
    Error(string message)

fn handle(Response resp) ~:
    match resp:
        Response.Success(code) ->
            println("Success code: {code}")
        Response.Error(msg) ->
            println("Error: {msg}")

    return Result.Ok(~)

fn main() i32:
    handle(Response.Success(code: 200))
    handle(Response.Error(message: "Not found"))
    return Result.Ok(0)
```

**How matching works**:
1. The `match` keyword examines an enum value
2. Each arm pattern specifies a variant: `EnumName.VariantName(bindings)`
3. If the pattern matches, the associated data is extracted and bound to variables
4. The code after `->` executes for the matching pattern
5. The compiler verifies all variants are covered

**Exhaustiveness checking**: If you add a new variant to `Response`, any match expressions that don't handle it will cause compilation errors. This prevents bugs from forgetting to handle new cases.

### Nested Patterns

Patterns can be nested to match complex enum structures in a single expression:

```sushi
fn handle_file(FileResult result) ~:
    match result:
        FileResult.Ok(f) ->
            println("File opened successfully")
        FileResult.Err(FileError.NotFound()) ->
            println("File not found")
        FileResult.Err(FileError.PermissionDenied()) ->
            println("Permission denied")
        FileResult.Err(_) ->
            println("Other error")

    return Result.Ok(~)
```

**Nested pattern matching**: The pattern `FileResult.Err(FileError.NotFound())` matches a `FileResult` whose `Err` variant contains a `FileError` enum with the `NotFound` variant. This lets you handle specific error combinations without nested match statements.

**Wildcard patterns**: The `_` pattern matches anything, acting as a catch-all for remaining cases. It's useful for handling "all other errors" or "default" cases.

**Zero-cost compilation**: Pattern matching compiles to efficient jump tables or switch statements. There's no runtime overhead compared to hand-written if-else chains or switch statements in C.

## Generics

Sushi's generics system provides zero-cost abstraction through compile-time monomorphization. Generic types are instantiated automatically based on usage, generating specialized code for each concrete type combination.

### Generic Structs

```sushi
struct Pair<T, U>:
    T first
    U second

fn main() i32:
    let Pair<i32, string> data = Pair(first: 42, second: "answer")

    println("Number: {data.first}")
    println("Label: {data.second}")

    return Result.Ok(0)
```

**How it works**: The compiler detects that you use `Pair<i32, string>` and generates a specialized version of the struct for that type combination. There's no runtime overhead - the generated code is as efficient as if you'd written a separate struct manually.

**Generic Nesting**: Sushi fully supports nested generics like `Result<Maybe<T>>`, `List<Pair<K, V>>`, or `HashMap<string, List<i32>>`. The type system correctly handles arbitrarily deep nesting.

### Extension Methods

Extension methods let you add functionality to existing types without modifying their definitions. This is similar to extension methods in C#, Kotlin, or Swift, and is inspired by Rust's trait system. In Sushi, this is called **UFCS (Uniform Function Call Syntax)**.

```sushi
extend i32 squared() i32:
    return Result.Ok(self * self)

extend string shout() string:
    return Result.Ok(self + "!!!")

fn main() i32:
    let i32 x = 7
    println("Squared: {x.squared().realise(0)}")

    let string msg = "Don't Panic"
    println(msg.shout().realise(""))

    return Result.Ok(0)
```

**How Extension Methods Work**:
1. You define a function with `extend <type> <method_name>(...) <return_type>`
2. The first implicit parameter is `self`, referring to the value you're calling the method on
3. The call `x.squared()` is transformed at compile-time into `squared(x)`
4. This transformation is zero-cost - there's no runtime overhead

**Generic Extension Methods**:
```sushi
extend<T> List<T> first() Maybe<T>:
    if (self.is_empty()):
        return Result.Ok(Maybe.None())
    return Result.Ok(self.get(0))

fn main() i32:
    let List<i32> numbers = List.new()
    numbers.push(42)

    let Maybe<i32> first = numbers.first()  # Uses generic extension
    return Result.Ok(0)
```

Extension methods can be generic and work across all instantiations of a generic type. The compiler automatically instantiates the method for each type combination used in your program.

**Benefits**:
- **Namespace organization**: Group related functionality with the types they operate on
- **Discoverability**: Methods appear natural on the type, making APIs easier to explore
- **Chainability**: Method syntax enables fluent chaining: `list.first().realise(0)`
- **Zero cost**: Compiles to the same code as a regular function call

**Standard Library Use**: The entire Sushi standard library is built using extension methods. String methods like `.len()`, `.find()`, and `.split()` are all extensions. Collection methods on `List<T>` and array methods are extensions. This means you can add your own methods using the same mechanism the standard library uses.

## Memory Management

Sushi provides memory safety without garbage collection through a combination of RAII (Resource Acquisition Is Initialization), compile-time borrow checking, and move semantics. These features work together to prevent common memory bugs while maintaining C-like performance.

### Borrowing, aka References

Mutable references (`&T`) allow functions to modify values without taking ownership. Sushi's borrow checker ensures that references are always valid and that aliasing rules are enforced at compile time:

```sushi
fn increment(&i32 counter) ~:
    counter := counter + 1
    return Result.Ok(~)

fn main() i32:
    let i32 count = 0
    increment(&count)
    increment(&count)
    println("Count: {count}")  # Prints: Count: 2

    return Result.Ok(0)
```

**Borrowing Rules**:
- **One active borrow per variable**: You cannot have multiple active borrows of the same variable simultaneously
- **References must be valid**: The borrow checker ensures references never outlive the data they point to
- **Zero cost**: References compile to simple pointers with no runtime overhead

**What references allow**:
- Modifying function arguments in-place without copying large structures
- Building efficient data structures that reference existing data
- Avoiding expensive clones when you just need to read or modify a value

**Example - avoiding copies**:
```sushi
struct LargeData:
    i32[1000] values

fn process(&LargeData data) ~:
    # Can access and modify data.values without copying 4000 bytes
    data.values[0] := 42
    return Result.Ok(~)
```

The borrow checker runs at compile time (Pass 3 in the semantic analysis pipeline), so there's no runtime cost to these safety guarantees.

### RAII (Automatic Cleanup)

RAII (Resource Acquisition Is Initialization) is Sushi's core memory management strategy. When a value goes out of scope, its destructor automatically runs, freeing any resources it owns. This applies recursively to nested structures:

```sushi
struct Buffer:
    string[] lines

fn process() ~:
    let Buffer buf = Buffer(lines: from([]))
    buf.lines.push("Line 1")
    buf.lines.push("Line 2")
    # buf and buf.lines automatically destroyed when function returns
    return Result.Ok(~)

fn main() i32:
    process()
    return Result.Ok(0)
```

**How RAII works in Sushi**:
1. When a variable goes out of scope (end of function, end of block, error propagation with `??`), its destructor runs
2. For structs, the destructor recursively destroys each field
3. For arrays, the destructor recursively destroys each element
4. For enums, the destructor examines the discriminant and destroys the active variant's data
5. Primitives and strings require no cleanup

**The Recursive Destructor**: Sushi's backend implements a general `emit_value_destructor()` function that handles cleanup for all types. It recursively traverses your data structures:
- **Structs with arrays**: The array fields are freed, then the struct itself
- **Arrays of structs**: Each struct is destroyed, then the array storage is freed
- **Enums with complex data**: The discriminant determines which variant is active, then that variant's data is cleaned up
- **Nested collections**: `List<HashMap<string, List<i32>>>` correctly frees all levels

**Integration with error handling**: RAII is crucial for the `??` operator. When an error is propagated, all variables in the current scope are destroyed before returning, preventing resource leaks even in error paths.

**Benefits**:
- No manual memory management - no `free()` calls needed (except for explicit `.free()` on collections when you want early cleanup)
- No memory leaks - resources are always freed when they go out of scope
- Exception safety - errors can't cause resource leaks
- Deterministic cleanup - you know exactly when destructors run

### Own<T> for Heap Allocation

`Own<T>` is Sushi's type for heap-allocated, owned values. It's essential for building recursive data structures like linked lists and trees, where a struct needs to contain an optional instance of itself:

```sushi
struct Node:
    i32 value
    Own<Node> next

fn main() i32:
    let Node head = Node(value: 1, next: Own.alloc(Node(value: 2, next: Own.null())))

    println("Head: {head.value}")

    # Access owned value
    if (head.next.is_some()):
        let Node next = head.next.get()
        println("Next: {next.value}")

    return Result.Ok(0)
```

**Why Own<T> exists**: Without `Own<T>`, you cannot create recursive types because the compiler needs to know the size of every struct at compile time. A `Node` containing another `Node` would have infinite size. `Own<T>` breaks the cycle by storing a pointer (fixed size) to heap-allocated data.

**Own<T> methods**:
- `Own.alloc(value)` - Allocates `value` on the heap and returns an `Own<T>`
- `Own.null()` - Creates an empty `Own<T>` (like `nullptr` in C++)
- `.is_some()` - Returns `true` if the Own contains a value
- `.is_null()` - Returns `true` if the Own is empty
- `.get()` - Returns the contained value (unsafe if null)
- `.destroy()` - Explicitly frees the heap memory

**Memory management with Own<T>**: `Own<T>` integrates with RAII - when an `Own<T>` goes out of scope, it automatically calls `.destroy()` on the contained value, freeing the heap memory. This means recursive structures are properly cleaned up when they go out of scope, even if they're deeply nested.

**Common patterns**:
- **Linked lists**: Each node owns the next node
- **Trees**: Each node owns its children
- **Optional heap data**: Use `Own<T>` instead of nullable pointers for optional heap-allocated data

## Next Steps

This guide covered the basics. For more details:

- [Language Reference](language-reference.md) - Complete syntax and semantics
- [Standard Library](standard-library.md) - All built-in types and methods
- [Error Handling](error-handling.md) - Deep dive into Result and Maybe
- [Memory Management](memory-management.md) - RAII, borrowing, and ownership
- [Examples](examples/) - Hands-on code examples

---

**Previous**: [Getting Started](getting-started.md) | **Next**: [Language Reference](language-reference.md)
