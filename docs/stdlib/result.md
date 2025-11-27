# Result<T, E>

[← Back to Standard Library](../standard-library.md)

Type-safe error handling with explicit success and error types.

## Overview

`Result<T, E>` is a generic enum that represents either success (`Ok`) containing a value of type `T`, or failure (`Err`) containing an error of type `E`.

All functions in Sushi implicitly return `Result<T, E>` where:
- `T` is the declared return type
- `E` is the error type (defaults to `StdError` if not specified)

## Type Syntax

### Implicit Return with Default Error

```sushi
fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)
# Actually returns Result<i32, StdError>
```

### Custom Error Type

```sushi
enum MathError:
    DivisionByZero
    Overflow

fn divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)
# Returns Result<i32, MathError>
```

### Explicit Syntax

```sushi
fn foo() Result<i32, MyError>:
    return Result.Ok(42)
```

## Standard Error Enums

Sushi provides six built-in error types:

### StdError

Generic fallback for simple errors.

- `StdError.Error` - Generic error condition

### MathError

Mathematical operation errors.

- `MathError.DivisionByZero` - Division by zero
- `MathError.Overflow` - Arithmetic overflow
- `MathError.Underflow` - Arithmetic underflow
- `MathError.InvalidInput` - Invalid mathematical input

### FileError

File system operation errors.

- `FileError.NotFound` - File does not exist
- `FileError.PermissionDenied` - Insufficient permissions
- `FileError.AlreadyExists` - File already exists
- `FileError.InvalidPath` - Invalid file path
- `FileError.IoError` - Generic I/O error

### IoError

I/O operation errors.

- `IoError.Read` - Read operation failed
- `IoError.Write` - Write operation failed
- `IoError.Flush` - Flush operation failed

### ProcessError

Process management errors.

- `ProcessError.Spawn` - Failed to spawn process
- `ProcessError.Exit` - Process exited with error
- `ProcessError.Signal` - Process terminated by signal

### EnvError

Environment variable errors.

- `EnvError.NotFound` - Environment variable not found
- `EnvError.InvalidValue` - Invalid environment variable value
- `EnvError.PermissionDenied` - Insufficient permissions

## Constructors

### `Result.Ok(value)`

Create a success result containing a value.

```sushi
fn get_answer() i32:
    return Result.Ok(42)
```

### `Result.Err(error)`

Create an error result containing an error value.

```sushi
fn divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)
```

**Important:** `Result.Err()` must now include an error value. The old syntax without error data is deprecated.

## Methods

### `.is_ok() -> bool`

Check if the Result is an Ok variant.

```sushi
let Result<i32, MathError> result = divide(10, 2)
if (result.is_ok()):
    println("Success!")
```

### `.is_err() -> bool`

Check if the Result is an Err variant.

```sushi
let Result<i32, MathError> result = divide(10, 0)
if (result.is_err()):
    println("Division failed")
```

### `.err() -> Maybe<E>`

Extract the error value if present, otherwise return `Maybe.None()`.

```sushi
let Result<i32, MathError> result = divide(10, 0)
let Maybe<MathError> error = result.err()

match error:
    Maybe.Some(e) ->
        println("Error occurred: {e}")
    Maybe.None() ->
        println("No error")
```

### `.expect(message: string) -> T`

Unwrap the Ok value or panic with the given message if Err.

```sushi
let Result<i32, MathError> result = divide(10, 2)
let i32 value = result.expect("Division should not fail")
# Prints "ERROR: Division should not fail" and exits if Err
```

**Warning:** Use `expect()` sparingly. It will terminate the program if the Result is Err.

### `.realise(default: T) -> T`

Extract the Ok value or return a default value if Err.

```sushi
let Result<i32, MathError> result = divide(10, 0)
let i32 value = result.realise(0)  # Returns 0 on error
```

## Error Propagation with `??`

The `??` operator unwraps a Result or propagates the error to the caller.

```sushi
fn compute() i32 | MathError:
    let i32 x = divide(10, 2)??  # Unwraps or returns early
    let i32 y = divide(20, 5)??
    return Result.Ok(x + y)
```

### Error Type Matching

The `??` operator requires error types to match exactly:

```sushi
enum ErrorA:
    Error

enum ErrorB:
    Error

fn inner() i32 | ErrorA:
    return Result.Ok(42)

fn outer() i32 | ErrorB:
    let i32 x = inner()??  # ❌ Error: cannot propagate ErrorA to ErrorB
    return Result.Ok(x)
```

To use `??`, the inner function's error type must match the outer function's error type:

```sushi
fn outer() i32 | ErrorA:
    let i32 x = inner()??  # ✅ Both use ErrorA
    return Result.Ok(x)
```

### Warning: Do NOT Use `??` in main()

Using `??` in the `main()` function generates a compiler warning and is highly discouraged:

```sushi
fn main() i32:
    let i32 x = risky()??  # ⚠️ Warning CW2511
    return Result.Ok(0)
```

Instead, use explicit error handling:

```sushi
fn main() i32:
    match risky():
        Result.Ok(x) ->
            println("Got: {x}")
            return Result.Ok(0)
        Result.Err(e) ->
            println("Failed")
            return Result.Ok(1)
```

## Pattern Matching

Match on both success and error cases:

```sushi
match divide(10, 2):
    Result.Ok(value) ->
        println("Result: {value}")
    Result.Err(MathError.DivisionByZero) ->
        println("Cannot divide by zero")
    Result.Err(e) ->
        println("Other error: {e}")
```

## Usage in Conditionals

Result can be used directly in `if` statements (checks for Ok):

```sushi
if (divide(10, 2)):
    println("Success!")
else:
    println("Failed")
```

## Best Practices

- **Always handle errors explicitly** - Don't ignore Result values
- **Use `??` for error propagation** - In function chains with matching error types
- **Use `.realise(default)` for fallback values** - When a default makes sense
- **Use pattern matching for detailed error handling** - When you need different behavior per error variant
- **Avoid `expect()` in production code** - It terminates the program on error
- **Avoid `??` in main()** - Use explicit error handling instead
- **Keep error types consistent** - Makes error propagation easier
- **Define custom error enums** - For domain-specific error conditions

## Examples

### Basic Error Handling

```sushi
enum ValidationError:
    TooShort
    TooLong
    InvalidCharacters

fn validate_username(string name) ~ | ValidationError:
    if (name.len() < 3):
        return Result.Err(ValidationError.TooShort)
    if (name.len() > 20):
        return Result.Err(ValidationError.TooLong)
    return Result.Ok(~)
```

### Error Propagation Chain

```sushi
fn read_config() string | FileError:
    let file f = open("config.txt", FileMode.Read())??
    let string content = f.read()??
    return Result.Ok(content)
```

### Combining with Maybe

```sushi
fn safe_divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)

fn process() i32 | MathError:
    let Result<i32, MathError> result = safe_divide(10, 2)
    let Maybe<MathError> error = result.err()

    if (error.is_some()):
        return Result.Err(error.realise(MathError.DivisionByZero))

    let i32 value = result.realise(0)
    return Result.Ok(value)
```
