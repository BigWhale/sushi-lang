# Error Handling

[← Back to Documentation](README.md)

Comprehensive guide to error handling in Sushi using `Result<T>`, `Maybe<T>`, and the `??` operator.

## Table of Contents

- [Philosophy](#philosophy)
- [Result<T>](#resultt)
- [Maybe<T>](#maybet)
- [Error Propagation (??)](#error-propagation-)
- [Patterns and Best Practices](#patterns-and-best-practices)

## Philosophy

Sushi makes errors explicit and impossible to ignore:

1. **All functions return `Result<T>`** - Errors are part of the type system
2. **Compiler-enforced handling** - Cannot ignore errors accidentally
3. **No exceptions** - Control flow is always visible
4. **Zero runtime cost** - Compiles to efficient LLVM code

## Result<T>

All functions implicitly return `Result<T>` where `T` is the declared return type.

### Declaration

```sushi
# Function signature shows i32, but returns Result<i32>
fn divide(i32 a, i32 b) i32:
    if (b == 0):
        return Result.Err()  # Error variant
    return Result.Ok(a / b)  # Success variant
```

### Creating Results

```sushi
# Success
return Result.Ok(value)

# Failure
return Result.Err()
```

### Handling Results

#### Using `.realise(default)`

```sushi
fn main() i32:
    let i32 x = divide(10, 2).realise(0)   # x = 5
    let i32 y = divide(10, 0).realise(-1)  # y = -1 (error case)

    return Result.Ok(0)
```

**Key points:**
- Default parameter is mandatory
- Forces explicit thinking about error cases
- Never panics - always produces a value

#### Using Conditionals

```sushi
fn main() i32:
    let Result<i32> result = divide(10, 2)

    if (result):
        # Success case
        let i32 value = result.realise(0)
        println("Result: {value}")
    else:
        # Error case
        println("Division failed")
        return Result.Err()

    return Result.Ok(0)
```

#### Using Pattern Matching

```sushi
fn main() i32:
    match divide(10, 2):
        Result.Ok(value) ->
            println("Result: {value}")
        Result.Err() ->
            println("Division failed")

    return Result.Ok(0)
```

### Compiler Enforcement

```sushi
fn get_value() i32:
    return Result.Ok(42)

fn main() i32:
    # ERROR CE2505: Cannot assign Result<i32> to i32
    # let i32 x = get_value()

    # CORRECT: Use .realise()
    let i32 x = get_value().realise(0)

    # CORRECT: Store as Result<T>
    let Result<i32> result = get_value()
    let i32 y = result.realise(0)

    # WARNING CW2001: Unused Result<T> value
    # get_value()  # Must handle result

    return Result.Ok(0)
```

## Maybe<T>

`Maybe<T>` represents optional values, replacing sentinel values (`-1`, `null`, empty strings) with compile-time checked optionals.

### Creating Maybe Values

```sushi
# Value present
return Result.Ok(Maybe.Some(value))

# Value absent
return Result.Ok(Maybe.None())
```

### Checking Maybe Values

```sushi
let Maybe<i32> m = find_value()

if (m.is_some()):
    println("Has value")

if (m.is_none()):
    println("No value")
```

### Extracting Values

#### Using `.realise(default)`

```sushi
let Maybe<i32> m = Maybe.Some(42)
let i32 x = m.realise(0)  # x = 42

let Maybe<i32> empty = Maybe.None()
let i32 y = empty.realise(-1)  # y = -1
```

#### Using `.expect(message)`

```sushi
let Maybe<i32> m = Maybe.Some(42)
let i32 x = m.expect("Expected value")  # x = 42

# Panics at runtime if None
let Maybe<i32> empty = Maybe.None()
# let i32 y = empty.expect("Value required")  # Runtime panic!
```

**Warning:** Use `.expect()` only when absence is truly impossible.

#### Using Pattern Matching

```sushi
match find_value():
    Maybe.Some(value) ->
        println("Found: {value}")
    Maybe.None() ->
        println("Not found")
```

### Example: Find First Even

```sushi
fn find_first_even(i32[] numbers) Maybe<i32>:
    foreach(n in numbers.iter()):
        if (n % 2 == 0):
            return Result.Ok(Maybe.Some(n))
    return Result.Ok(Maybe.None())

fn main() i32:
    let i32[] data = from([1, 3, 5, 8, 9])
    let Maybe<i32> result = find_first_even(data)

    match result:
        Maybe.Some(value) ->
            println("Found even: {value}")
        Maybe.None() ->
            println("No even numbers")

    return Result.Ok(0)
```

### Result vs Maybe

**Use `Result<T>` when:**
- Operation can succeed or fail
- Failure is an error condition
- Example: File I/O, parsing, validation

**Use `Maybe<T>` when:**
- Value might or might not exist
- Absence is not an error
- Example: Dictionary lookup, search, optional config

### Combining Result and Maybe

Functions can return `Result<Maybe<T>>` for three states:

1. **Success with value**: `Result.Ok(Maybe.Some(value))`
2. **Success without value**: `Result.Ok(Maybe.None())`
3. **Failure**: `Result.Err()`

```sushi
fn load_optional_config() Maybe<string>:
    match open("config.txt", FileMode.Read()):
        FileResult.Ok(f) ->
            let string content = f.read()
            f.close()
            return Result.Ok(Maybe.Some(content))  # Found config
        FileResult.Err(FileError.NotFound()) ->
            return Result.Ok(Maybe.None())  # No config (OK!)
        FileResult.Err(_) ->
            return Result.Err()  # Real error (permission, I/O)

fn main() i32:
    let Maybe<string> config = load_optional_config().realise(Maybe.None())

    match config:
        Maybe.Some(content) ->
            println("Config: {content}")
        Maybe.None() ->
            println("Using defaults")

    return Result.Ok(0)
```

## Error Propagation (??)

The `??` operator unwraps `Result<T>` or `Maybe<T>`, propagating errors automatically.

### Basic Usage

**Without `??`:**

```sushi
fn read_config() string:
    let FileResult result = open("config.txt", FileMode.Read())
    match result:
        FileResult.Ok(f) ->
            let string content = f.read()
            f.close()
            return Result.Ok(content)
        FileResult.Err(_) ->
            return Result.Err()
```

**With `??`:**

```sushi
fn read_config() string:
    let file f = open("config.txt", FileMode.Read())??
    let string content = f.read()
    f.close()
    return Result.Ok(content)
```

### How It Works

For `Result<T>`:
- `Result.Ok(value)?? → value` (unwraps)
- `Result.Err()?? → return Result.Err()` (propagates)

For `Maybe<T>`:
- `Maybe.Some(value)?? → value` (unwraps)
- `Maybe.None()?? → return Result.Err()` (propagates as error)

### Chaining Operations

```sushi
fn process() i32:
    let i32 step1 = calculate()??
    let i32 step2 = validate(step1)??
    let i32 step3 = transform(step2)??
    return Result.Ok(step3)
```

Stops at first error and returns immediately.

### RAII Safety

The `??` operator automatically cleans up resources on error:

```sushi
fn process_with_cleanup(bool succeed) i32:
    let i32[] data = from([1, 2, 3])

    # If might_fail() returns Err:
    # 1. data is automatically freed
    # 2. Error is propagated
    let i32 value = might_fail(succeed)??

    return Result.Ok(value + data.len())
```

**Resources automatically cleaned:**
- Dynamic arrays
- Struct fields (dynamic arrays, nested structs)
- File handles (when implemented)

### Using ?? with Maybe<T>

```sushi
fn find_and_parse(string text) i32:
    # If find() returns None, ?? propagates as Err
    let i32 pos = text.find("x")??
    return Result.Ok(pos * 2)

fn main() i32:
    # Success case
    let i32 result1 = find_and_parse("hello x world").realise(-1)
    println("Found: {result1}")  # Found: 12

    # Failure case (None → Err)
    let i32 result2 = find_and_parse("hello world").realise(-1)
    println("Not found: {result2}")  # Not found: -1

    return Result.Ok(0)
```

### Compile-Time Safety

```sushi
# ERROR CE2507: Using ?? on non-Result/non-Maybe type
# let i32 x = 5??

# ERROR CE2508: Using ?? outside Result-returning function
extend i32 squared() i32:
    # let i32 x = might_fail()??  # Not allowed here
    return Result.Ok(self * self)
```

## Patterns and Best Practices

### 1. Always Provide Meaningful Defaults

```sushi
# Good: Clear what -1 means
let i32 index = find_position().realise(-1)  # -1 = not found

# Better: Use Maybe<T> and match
match find_position():
    Maybe.Some(pos) -> println("At {pos}")
    Maybe.None() -> println("Not found")
```

### 2. Early Return on Error

```sushi
fn validate_input(i32 x) i32:
    if (x < 0):
        return Result.Err()
    if (x > 100):
        return Result.Err()

    return Result.Ok(x * 2)
```

### 3. Use ?? for Sequential Operations

```sushi
fn process_pipeline() string:
    let file f = open("input.txt", FileMode.Read())??
    let string raw = f.read()
    f.close()

    let string cleaned = parse(raw)??
    let string validated = validate(cleaned)??
    let string transformed = transform(validated)??

    return Result.Ok(transformed)
```

### 4. Propagate Errors, Handle at Top Level

```sushi
fn low_level() i32:
    # Just propagate
    let i32 x = risky_operation()??
    return Result.Ok(x)

fn mid_level() i32:
    # Just propagate
    let i32 y = low_level()??
    return Result.Ok(y * 2)

fn main() i32:
    # Handle at top level
    let Result<i32> result = mid_level()

    if (result):
        println("Success: {result.realise(0)}")
    else:
        println("Pipeline failed")
        return Result.Err()

    return Result.Ok(0)
```

### 5. Result<Maybe<T>> for Three States

```sushi
fn lookup(HashMap<string, i32> map, string key) Maybe<i32>:
    # Three possible states:
    # 1. Found value: Ok(Some(value))
    # 2. Key not found: Ok(None)  - not an error!
    # 3. Internal error: Err()     - map corrupted, etc.

    if (map_is_corrupted()):
        return Result.Err()

    return Result.Ok(map.get(key))
```

### 6. Avoid Silent Failures

```sushi
# Bad: Silently returns default
fn get_config() string:
    return Result.Ok(load().realise("default"))

# Good: Caller decides how to handle
fn load_config() string:
    return load()  # Returns Result<string>

fn main() i32:
    let Result<string> config = load_config()
    if (config):
        println("Loaded: {config.realise("")}")
    else:
        println("Using default config")

    return Result.Ok(0)
```

## Error Codes

Common error codes related to error handling:

- **CE2502**: `.realise()` wrong argument count
- **CE2503**: `.realise()` default type mismatch
- **CE2505**: Assigning `Result<T>` to non-Result without handling
- **CE2507**: Using `??` on non-Result/non-Maybe type
- **CE2508**: Using `??` outside Result-returning function
- **CW2001**: Unused `Result<T>` value (warning)

---

**See also:**
- [Standard Library](standard-library.md) - Complete Result<T> and Maybe<T> API
- [Language Reference](language-reference.md) - Syntax details
- [Examples](examples/) - Error handling patterns in practice
