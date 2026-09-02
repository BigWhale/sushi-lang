# Error Handling

[← Back to Documentation](index.md)

Comprehensive guide to error handling in Sushi using `Result@(T, E)`, `Maybe@(T)`, and the `??` operator.

## Table of Contents

- [Philosophy](#philosophy)
- [Result@(T, E)](#result)
  - [Error Type Syntax](#error-type-syntax)
  - [Standard Error Enums](#standard-error-enums)
  - [Creating Results](#creating-results)
  - [Handling Results](#handling-results)
  - [Result Methods](#result-methods)
- [Maybe@(T)](#maybe)
- [Error Propagation (??)](#error-propagation)
- [Patterns and Best Practices](#patterns-and-best-practices)

## Philosophy

Sushi makes errors explicit and impossible to ignore:

1. **All functions return `Result@(T, E)`** - Errors are part of the type system with explicit error types
2. **Compiler-enforced handling** - Cannot ignore errors accidentally
3. **No exceptions** - Control flow is always visible
4. **Type-safe error propagation** - Error types must match for propagation
5. **Zero runtime cost** - Compiles to efficient LLVM code

## Result@(T, E)

All functions implicitly return `Result@(T, E)` where:
- `T` is the declared return type (success value)
- `E` is the error type (defaults to `StdError` if not specified)

> **The one exception: foreign functions.** Functions declared in an
> `unsafe external "C"` block (the FFI) are **not** wrapped in `Result`. A C
> function returns a raw value with no error channel, so `libc.strlen(s)` yields
> a plain `i64` and cannot be used with `??` or `.realise()` (that is a clean
> `CE2507` type error). The safe-wrapper pattern restores Result-clean Sushi
> around the raw call. See [Foreign Function Interface](ffi.md).

### Error Type Syntax

#### Implicit with Default Error (StdError)

```sushi
fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)
# Actually returns Result@(i32, StdError)
```

#### Custom Error Type with | Syntax

```sushi
enum MathError:
    DivisionByZero
    Overflow

fn divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)
# Returns Result@(i32, MathError)
```

#### Explicit Result@(T, E) Syntax

```sushi
fn foo() Result@(i32, MyError):
    return Result.Ok(42)
```

### Standard Error Enums

Sushi provides six built-in error types for common error conditions:

- **StdError** - Generic fallback (`StdError.Error`)
- **MathError** - Mathematical errors (`DivisionByZero`, `Overflow`, `Underflow`, `InvalidInput`)
- **FileError** - File system errors (`NotFound`, `PermissionDenied`, `AlreadyExists`, `InvalidPath`, `IoError`)
- **IoError** - I/O operation errors (`Read`, `Write`, `Flush`)
- **ProcessError** - Process management (`Spawn`, `Exit`, `Signal`)
- **EnvError** - Environment variables (`NotFound`, `InvalidValue`, `PermissionDenied`)

See [Result@(T, E) API Reference](stdlib/result.md) for complete details.

### Creating Results

```sushi
# Success - Always provide the value
return Result.Ok(value)

# Failure - Must now include error data
enum MathError:
    DivisionByZero

fn divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)  # Error with data
    return Result.Ok(a / b)
```

**Important:** `Result.Err()` without error data is a **compile error** (**CE2050** — wrong argument count for the `Err` variant), not a deprecation. Always include the error value.

### Handling Results

#### Using `.realise(default)`

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
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

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn main() i32 | MathError:
    let Result@(i32, MathError) result = divide(10, 2)

    if (result.is_ok()):
        # Success case
        let i32 value = result.realise(0)
        println("Result: {value}")
    else:
        # Error case
        println("Division failed")
        return Result.Err(MathError.DivisionByZero)

    return Result.Ok(0)
```

**A condition is a bool, and nothing else is one.** A `Result@(T, E)` and a `Maybe@(T)`
are both refused in an `if`, in a `while`, and in an operand of `and`, `or`, `xor` or
`not`. The code is **CE2516**, and the escape is to name the question: `.is_ok()`,
`.is_err()`, `.is_some()` or `.is_none()` answers with a bool, while `??`,
`.realise(default)` and `match` take the value.

<!-- docs-sweep: skip (a fragment; divide comes from an earlier block on this page) -->
```sushi
# if (divide(10, 2)):            # CE2516 -- which does it ask, the wrapper or the value?
if (divide(10, 2).is_ok()):      # the success test
    println("ok")
```

The wrapper used to answer for its Ok tag, so `if (f())` on a `Result@(bool, E)` ran the
true branch for `Ok(false)` and never read the bool. Both readings were legal, so the
compiler could not choose one (#522).

#### Using Pattern Matching

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn main() i32 | MathError:
    match divide(10, 2):
        Result.Ok(value) ->
            println("Result: {value}")
        Result.Err(MathError.DivisionByZero) ->
            println("Cannot divide by zero")
        Result.Err(e) ->
            println("Other error")

    return Result.Ok(0)
```

### Result Methods

Result@(T, E) provides several methods for working with success and error values:

#### `.is_ok() -> bool` and `.is_err() -> bool`

Check which variant the Result contains:

```sushi
let Result@(i32, MathError) result = divide(10, 2)

if (result.is_ok()):
    println("Success!")

if (result.is_err()):
    println("Failed!")
```

#### `.err() -> Maybe@(E)`

Extract the error value as a Maybe:

```sushi
let Result@(i32, MathError) result = divide(10, 0)
let Maybe@(MathError) error = result.err()

match error:
    Maybe.Some(MathError.DivisionByZero) ->
        println("Division by zero!")
    Maybe.None() ->
        println("No error")
```

#### `.expect(message: string) -> T`

Unwrap the Ok value or panic with a custom message:

```sushi
let Result@(i32, MathError) result = divide(10, 2)
let i32 value = result.expect("Division should succeed")
# Prints "ERROR: Division should succeed" and exits if Err
```

**Warning:** Use `.expect()` sparingly. It terminates the program on error.

See [Result@(T, E) API Reference](stdlib/result.md) for complete method documentation.

### Compiler Enforcement

```sushi
fn get_value() i32:
    return Result.Ok(42)

fn main() i32:
    # ERROR CE2505: Cannot assign Result@(i32, StdError) to i32
    # let i32 x = get_value()

    # CORRECT: Use .realise()
    let i32 x = get_value().realise(0)

    # CORRECT: Store as Result@(T, E)
    let Result@(i32, StdError) result = get_value()
    let i32 y = result.realise(0)

    # WARNING CW2001: Unused Result@(T, E) value
    # get_value()  # Must handle result

    return Result.Ok(0)
```

## Maybe@(T)

`Maybe@(T)` represents optional values, replacing sentinel values (`-1`, `null`, empty strings) with compile-time checked optionals.

### Creating Maybe Values

```sushi
# Value present
return Result.Ok(Maybe.Some(value))

# Value absent
return Result.Ok(Maybe.None())
```

### Checking Maybe Values

```sushi
let Maybe@(i32) m = find_value()

if (m.is_some()):
    println("Has value")

if (m.is_none()):
    println("No value")
```

### Extracting Values

#### Using `.realise(default)`

```sushi
let Maybe@(i32) m = Maybe.Some(42)
let i32 x = m.realise(0)  # x = 42

let Maybe@(i32) empty = Maybe.None()
let i32 y = empty.realise(-1)  # y = -1
```

#### Using `.expect(message)`

```sushi
let Maybe@(i32) m = Maybe.Some(42)
let i32 x = m.expect("Expected value")  # x = 42

# Panics at runtime if None
let Maybe@(i32) empty = Maybe.None()
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
fn find_first_even(i32[] numbers) Maybe@(i32):
    foreach(n in numbers.iter()):
        if (n % 2 == 0):
            return Result.Ok(Maybe.Some(n))
    return Result.Ok(Maybe.None())

fn main() i32:
    let i32[] data = from([1, 3, 5, 8, 9])
    let Maybe@(i32) result = find_first_even(data).realise(Maybe.None())

    match result:
        Maybe.Some(value) ->
            println("Found even: {value}")
        Maybe.None() ->
            println("No even numbers")

    return Result.Ok(0)
```

### Result vs Maybe

**Use `Result@(T, E)` when:**
- Operation can succeed or fail
- Failure is an error condition with specific error types
- Example: File I/O, parsing, validation

**Use `Maybe@(T)` when:**
- Value might or might not exist
- Absence is not an error
- Example: Dictionary lookup, search, optional config

### Combining Result and Maybe

Functions can return `Result@(Maybe@(T), E)` for three states:

1. **Success with value**: `Result.Ok(Maybe.Some(value))`
2. **Success without value**: `Result.Ok(Maybe.None())`
3. **Failure**: `Result.Err(error)`

```sushi
use <io/fs>

fn load_optional_config() Maybe@(string) | IoError:
    match open("config.txt", FileMode.Read()):
        Result.Ok(f) ->
            return Result.Ok(Maybe.Some(f.read_all()??))  # Found config
        Result.Err(IoError.NotFound()) ->
            return Result.Ok(Maybe.None())                # No config (OK!)
        Result.Err(e) ->
            return Result.Err(e)                          # Real error

fn main() i32:
    match load_optional_config():
        Result.Ok(Maybe.Some(content)) ->
            println("Config: {content}")
        Result.Ok(Maybe.None) ->
            println("Using defaults")
        Result.Err(_) ->
            println("Could not read the config")

    return Result.Ok(0)
```

## Error Propagation (??)

The `??` operator unwraps `Result@(T, E)` or `Maybe@(T)`, propagating errors automatically.

**Important:** For Result@(T, E), error types must match exactly. The `??` operator does not perform automatic error type conversion.

### Basic Usage

**Without `??`:**

```sushi
use <io/fs>

fn read_config() string | IoError:
    let Result@(File, IoError) result = open("config.txt", FileMode.Read())
    match result:
        Result.Ok(f) ->
            match f.read_all():
                Result.Ok(content) -> return Result.Ok(content)
                Result.Err(e) -> return Result.Err(e)
        Result.Err(e) ->
            return Result.Err(e)
```

**With `??`:**

```sushi
use <io/fs>

fn read_config() string | IoError:
    let File f = open("config.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)
```

### How It Works

For `Result@(T)`:
- `Result.Ok(value)?? → value` (unwraps)
- `Result.Err()?? → return Result.Err()` (propagates)

For `Maybe@(T)`:
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

### Using ?? with Maybe@(T)

```sushi
use <collections/strings>

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

# ERROR CE2511: Error type mismatch in propagation
enum ErrorA:
    Error

enum ErrorB:
    Error

fn inner() i32 | ErrorA:
    return Result.Ok(42)

fn outer() i32 | ErrorB:
    # let i32 x = inner()??  # Cannot propagate ErrorA to ErrorB
    return Result.Ok(0)
```

### Warning: Avoid ?? in main()

Using `??` in the `main()` function generates a compiler warning (CW2511) and is highly discouraged:

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn main() i32:
    # ⚠️ Warning CW2511: ?? operator used in main function
    # let i32 x = risky()??

    # Instead, use explicit error handling:
    match risky():
        Result.Ok(x) ->
            println("Success: {x}")
        Result.Err(e) ->
            println("Error occurred")

    return Result.Ok(0)
```

### An Infallible Helper Shares Its Caller's Error Channel

A helper that cannot fail still declares the caller's error type, or `??` does not
compose through it (CE2511). This is by design: the error arm is part of the signature,
and the signature is where a reader learns which channel a call sits on. Name the
channel, return only `Result.Ok`, and say so in a comment:

```sushi
enum LexError:
    BadByte(i32)

# It cannot fail; it shares the LexError channel so ?? composes in the caller.
fn at(peek u8[] src, i32 i) u8 | LexError:
    if (i < 0 or i >= src.len()):
        return Result.Ok(0)
    return Result.Ok(src[i])

fn first_byte(u8[] src) i32 | LexError:
    let u8 c = at(peek src, 0)??
    return Result.Ok(c as i32)

fn main() i32:
    let u8[] bytes = from([65, 66])
    match first_byte(bytes):
        Result.Ok(v) -> println("{v}")
        Result.Err(_) -> println("error")
    return Result.Ok(0)
```

The cost is one word per signature, and `<encoding/msgpack>` pays it throughout: its
infallible helpers carry `| MpError` so the fallible ones compose over them. A helper
shared between two modules with different error types takes a wrapper in one of them.

## Patterns and Best Practices

### 1. Always Provide Meaningful Defaults

```sushi
# Good: Clear what -1 means
let i32 index = find_position().realise(-1)  # -1 = not found

# Better: Use Maybe@(T) and match
match find_position():
    Maybe.Some(pos) -> println("At {pos}")
    Maybe.None() -> println("Not found")
```

### 2. Early Return on Error

```sushi
fn validate_input(i32 x) i32:
    if (x < 0):
        return Result.Err(StdError.Error())
    if (x > 100):
        return Result.Err(StdError.Error())

    return Result.Ok(x * 2)
```

### 3. Use ?? for Sequential Operations

`??` requires the error types to match EXACTLY, so a function that reads a file and then
calls `StdError` helpers is two functions: one per channel, with a `match` at the seam
that converts.

<!-- docs-sweep: skip (calls parse/validate/transform, which the narrative owns) -->
```sushi
use <io/fs>

fn read_input() string | IoError:
    let File f = open("input.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)

fn process_pipeline() string:
    let string raw = ""
    match read_input():
        Result.Ok(text) -> raw := text.clone()          # the one conversion point
        Result.Err(_) -> return Result.Err(StdError.Error())

    let string cleaned = parse(raw)??
    let string validated = validate(cleaned)??
    let string transformed = transform(validated)??

    return Result.Ok(transformed)
```

### 4. Propagate Errors, Handle at Top Level

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
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
    let Result@(i32, StdError) result = mid_level()

    if (result.is_ok()):
        let i32 value = result.realise(0)
        println("Success: {value}")
    else:
        println("Pipeline failed")
        return Result.Err(StdError.Error())

    return Result.Ok(0)
```

### 5. Result@(Maybe@(T)) for Three States

```sushi
fn lookup(HashMap@(string, i32) map, string key) Maybe@(i32):
    # Three possible states:
    # 1. Found value: Ok(Some(value))
    # 2. Key not found: Ok(None)  - not an error!
    # 3. Internal error: Err()     - map corrupted, etc.

    if (map_is_corrupted()):
        return Result.Err(StdError.Error())

    return Result.Ok(map.get(key))
```

### 6. Avoid Silent Failures

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
# Bad: Silently returns default
fn get_config() string:
    return Result.Ok(load().realise("default"))

# Good: Caller decides how to handle
fn load_config() string:
    let string data = load()??  # Forward the loaded value
    return Result.Ok(data)

fn main() i32:
    let Result@(string, StdError) config = load_config()
    if (config):
        let string value = config.realise("")
        println("Loaded: {value}")
    else:
        println("Using default config")

    return Result.Ok(0)
```

## Error Codes

Common error codes related to error handling:

- **CE2502**: `.realise()` wrong argument count
- **CE2503**: `.realise()` default type mismatch
- **CE2505**: Assigning `Result@(T)` to non-Result without handling
- **CE2507**: Using `??` on non-Result/non-Maybe type
- **CE2508**: Using `??` outside Result-returning function
- **CW2001**: Unused `Result@(T)` value (warning)

---

**See also:**
- [Standard Library](standard-library.md) - Complete Result@(T) and Maybe@(T) API
- [Language Reference](language-reference.md) - Syntax details
- [Examples](examples/README.md) - Error handling patterns in practice
