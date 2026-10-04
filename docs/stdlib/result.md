# Result@(T, E)

[← Back to Standard Library](../standard-library.md)

Type-safe error handling with explicit success and error types.

## Overview

`Result@(T, E)` is a generic enum that represents either success (`Ok`) containing a value of type `T`, or failure (`Err`) containing an error of type `E`.

A callable returns `Result@(T, E)` only when its signature has an **error channel**: it
writes `| E` after the return type, or it returns an explicit `Result@(T, E)`. Then:
- `T` is the declared return type
- `E` is the error type that the signature names

There is no default error type. A function, method, lambda or function type with no
channel is **bare**: it returns the value itself, and a call gives the value, not a
`Result`. An FFI external returns the raw C value and never has a channel. `main` is bare
and returns the exit code.

A bare function is the exception, not the default style. Use it only when the function is
total over its inputs and will stay so (a checksum, a pure arithmetic or string helper, a
path join). Write a channel for everything else, and keep a channel on a public function
when you are not sure: a channel that you add later changes the signature and breaks every
caller and every binary `.slib`. See [The error channel](../design/error-channel.md) and
[Error handling](../error-handling.md).

The error type `E` must be an ERROR TYPE: an enum declared with `error`, or one of the
predefined error types below. Any other type is CE2084, in both spellings and in every
position (a signature, a `let`, a field, a generic argument). A plain `enum` gets the help
to declare it with `error`.

## Type Syntax

### Error Channel

```sushi
error ParseError:
    Empty
    NotANumber

fn parse_digit(string s) i32 | ParseError:
    if (s == ""):
        return Result.Err(ParseError.Empty)
    return Result.Ok(7)
# Returns Result@(i32, ParseError)
```

The body with a channel spells both constructors. A bare `return 7` there is CE2030.

### No Channel: the Bare Form

```sushi
fn add(i32 a, i32 b) i32:
    return a + b
# Returns i32, not a Result
```

A bare body returns the value. `return Result.Ok(...)` in it is CE2091, and `??` in it is
CE0131. On the call of a bare function, `??` is CE2507 and `.realise(...)` is CE2008.

### StdError and the Predefined Error Types

`StdError` is a built-in error type with one variant, `StdError.Error`. A signature names
it like any other error type: `fn f() i32 | StdError:`. It is not a default.

A predefined error type is used in the same way. `MathError` comes with `use <math>`:

```sushi
use <math>

fn divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)
# Returns Result@(i32, MathError)
```

Do not declare a type with the name of a predefined enum (for example `error MathError`).
The compiler always knows the predefined enums, also in a unit that does not import their
home module, so a second declaration is CE2046.

### Explicit Syntax

```sushi
fn foo() Result@(i32, ParseError):
    return Result.Ok(42)
```

A declaration uses one of the two forms. `fn foo() Result@(i32, ParseError) | ParseError`
mixes them and is CE2085.

## Standard Error Types

Sushi provides built-in enums. Seven of them are error types: `StdError`, `MathError`,
`FileError`, `IoError`, `NetError`, `ProcessError` and `EnvError`. `FileMode` and
`SeekFrom` are plain enums, so they cannot be the `E` of a `Result`. Each one but `StdError` has a HOME module, and the import
of that module brings the bare name into a unit (see
[Unit namespaces](../design/unit-namespaces.md)). A module whose calls answer an enum
re-exports its home (`public use`), so the import that a program already writes is the
one that brings the name:

| enum | home | also brought by |
|---|---|---|
| `StdError` | none -- in scope everywhere | |
| `MathError` | `use <math>` | |
| `FileMode` | `use <io/fs>` | |
| `IoError`, `FileError` | `use <io/error>` | `<io/contracts>`, `<io/fs>`, `<io/buf>`, `<io/files>`, `<net/tcp>` |
| `SeekFrom` | `use <io/contracts>` | `<io/fs>`, `<io/buf>`, `<net/tcp>` |
| `NetError` | `use <net/error>` | `<net/tcp>`, `<net/udp>`, `<net/dns>`, `<net/ip>` |
| `ProcessError` | `use <sys/process>` | |
| `EnvError` | `use <sys/env>` | |

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
- `FileError.IsDirectory` - Path refers to a directory
- `FileError.DiskFull` - No space left on device
- `FileError.TooManyOpen` - Too many open files
- `FileError.InvalidPath` - Invalid file path
- `FileError.IOError` - Generic I/O error
- `FileError.Other` - Any other error

### IoError

The errors of a read, a write, a seek, `open()` and `close()`. Every `Reader`, `Writer`
and `Seek` method answers `IoError`.

- `IoError.NotFound` - The file does not exist
- `IoError.PermissionDenied` - Insufficient permissions
- `IoError.AlreadyExists` - The file already exists
- `IoError.IsDirectory` - The path refers to a directory
- `IoError.ConnectionReset` - The peer reset the connection
- `IoError.TimedOut` - The operation timed out
- `IoError.Closed` - The handle or the connection is closed
- `IoError.Interrupted` - A signal interrupted the call
- `IoError.WouldBlock` - The call would block
- `IoError.DiskFull` - No space left on the device
- `IoError.TooManyOpen` - Too many open files
- `IoError.InvalidInput` - An argument is not valid
- `IoError.Os(i32)` - The raw `errno` value
- `IoError.Other` - Any other error

### NetError

The errors of a network construction or address operation (`connect`, `listen`,
`accept`, name resolution).

- `NetError.ConnectionRefused`, `NetError.ConnectionReset`, `NetError.TimedOut`,
  `NetError.Closed`
- `NetError.AddressInUse`, `NetError.AddressNotAvailable`, `NetError.InvalidAddress`
- `NetError.NetworkUnreachable`, `NetError.HostUnreachable`, `NetError.ResolveFailed`
- `NetError.PermissionDenied`, `NetError.TooManyOpen`, `NetError.Interrupted`,
  `NetError.MessageTooLarge`, `NetError.Other`

See [Net errors](net/error.md).

### FileMode

The mode argument of `open()`. It is not an error type.

- `FileMode.Read`, `FileMode.Write`, `FileMode.Append` - Text modes (`"r"`, `"w"`, `"a"`)
- `FileMode.ReadB`, `FileMode.WriteB`, `FileMode.AppendB` - Binary modes

### SeekFrom

The origin argument of `seek()`. It is not an error type.

- `SeekFrom.Start`, `SeekFrom.Current`, `SeekFrom.End`

### ProcessError

Process management errors.

- `ProcessError.SpawnFailed` - Failed to spawn process
- `ProcessError.ExitFailure` - Process exited with error
- `ProcessError.SignalReceived` - Process terminated by signal

### EnvError

Environment variable errors.

- `EnvError.NotFound` - Environment variable not found
- `EnvError.InvalidValue` - Invalid environment variable value
- `EnvError.PermissionDenied` - Insufficient permissions

## Constructors

### `Result.Ok(value)`

Create a success result containing a value.

```sushi
fn get_answer() i32 | StdError:
    return Result.Ok(42)
```

### `Result.Err(error)`

Create an error result containing an error value.

```sushi
use <math>

fn divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)
```

**Important:** `Result.Err()` requires an error value. Calling it with zero arguments is a compile error (**CE2050**, wrong argument count for the `Err` variant). As a pattern, `Result.Err()` with no binding is **CE2044**; write `Result.Err(_)` to discard the value.

## Methods

The examples in this section call `divide` from the block above, so they need
`use <math>`.

### `.is_ok() -> bool`

Check if the Result is an Ok variant.

```sushi
let Result@(i32, MathError) result = divide(10, 2)
if (result.is_ok()):
    println("Success!")
```

### `.is_err() -> bool`

Check if the Result is an Err variant.

```sushi
let Result@(i32, MathError) result = divide(10, 0)
if (result.is_err()):
    println("Division failed")
```

### `.err() -> Maybe@(E)`

Extract the error value if present, otherwise return `Maybe.None()`.

```sushi
let Result@(i32, MathError) result = divide(10, 0)
let Maybe@(MathError) err = result.err()

match err:
    Maybe.Some(MathError.DivisionByZero) ->
        println("Error occurred: division by zero")
    Maybe.Some(_) ->
        println("Error occurred")
    Maybe.None() ->
        println("No error")
```

An enum value goes into an interpolation hole through the predefined perk `Display`: it
prints as `Enum.Variant`. A `Result` and a `Maybe` do not go into a hole at
the top level (CE2035), and `println` refuses them (CE2037 for a `Result`, CE2115 for a
`Maybe`). Match on the value to print it, or use `.realise(default)`. A struct or an enum
that HOLDS a `Result` or a `Maybe` prints it, for example `Result.Ok(1)` and `Maybe.None`.

### `.expect(message: string) -> T`

Unwrap the Ok value or panic with the given message if Err.

```sushi
let Result@(i32, MathError) result = divide(10, 2)
let i32 value = result.expect("Division should not fail")
# Prints "ERROR: Division should not fail" and exits if Err
```

**Warning:** Use `expect()` sparingly. It will terminate the program if the Result is Err.

### `.realise(default: T) -> T`

Extract the Ok value or return a default value if Err.

```sushi
let Result@(i32, MathError) result = divide(10, 0)
let i32 value = result.realise(0)  # Returns 0 on error
```

### `.map_err(f) -> Result@(T, F)`

Convert the error with the function `f`, and keep the Ok value. `Ok(v)` stays `Ok(v)`, and
`Err(e)` becomes `Err(f(e))`. The signature, written as an extension, is
`extend Result@(T, E) map_err@(F)(nom self, fn(nom E) -> F f) Result@(T, F)`. It is a
built-in method and needs no import.

```sushi
error LowError:
    Bad(i32)

error AppError:
    Low(LowError)

fn low(i32 n) i32 | LowError:
    if (n < 0):
        return Result.Err(LowError.Bad(n))
    return Result.Ok(n)

fn app(i32 n) i32 | AppError:
    let i32 v = low(n).map_err(|nom LowError e| AppError.Low(e))??
    return Result.Ok(v + 1)

fn main() i32:
    println(app(1).realise(-1))     # 2
    match app(-5):
        Result.Ok(v) -> println(v)
        Result.Err(e) -> println(e) # AppError.Low(LowError.Bad(-5))
    return 0
```

- `f` takes the error `nom`, because the error moves into the new value. A mode is part of
  a function type, so a lambda writes `|nom LowError e|`; a lambda without `nom` is CE2006.
- `F` is solved from `f`, and it must be an error type (CE2084).
- A lambda parameter cannot have an owning type. For an error type that holds a `string`,
  pass a named function: `fn wrap(nom ParseError e) AppError`.
- The receiver is `nom self`. A named `Result` that owns something is spent by the call
  (CE2435). A BORROWED `Result` that owns something (a parameter, a binding, a get-out) is
  CE2411, because the error moves into `f`; write `r.clone().map_err(f)`.
- Exactly one argument (CE2009).

`map_err` converts at one site. When many sites need one conversion, declare it once with
`extend LowError as AppError:`, and `??` calls it (see
[Error Conversion](../error-handling.md#error-conversion)).

## Error Propagation with `??`

The `??` operator unwraps a Result or propagates the error to the caller.

```sushi
fn compute() i32 | MathError:
    let i32 x = divide(10, 2)??  # Unwraps or returns early
    let i32 y = divide(20, 5)??
    return Result.Ok(x + y)
```

`??` takes a `Result@(T, E)` and nothing else. On a `Maybe@(T)` it is CE2507: write the
error value with [`or_err`](maybe.md#or_errnom-e) first.

### Error Type Matching

The `??` operator propagates an error of the same type unchanged. Two different error
types need a declared conversion:

```sushi
error ErrorA:
    Error

error ErrorB:
    Error

fn inner() i32 | ErrorA:
    return Result.Ok(42)

fn outer() i32 | ErrorB:
    let i32 x = inner()??  # CE2511: cannot propagate ErrorA to ErrorB
    return Result.Ok(x)
```

The unit that declares `ErrorB` can declare the conversion, and then `??` calls it:

```sushi
extend ErrorA as ErrorB:
    return ErrorB.Error

fn outer() i32 | ErrorB:
    let i32 x = inner()??  # ErrorA as ErrorB
    return Result.Ok(x)
```

`e as ErrorB` calls the same conversion explicitly. Or use the same error type in both
functions:

```sushi
fn outer() i32 | ErrorA:
    let i32 x = inner()??  # correct: both use ErrorA
    return Result.Ok(x)
```

### `??` Is Not Legal in main()

`main` is bare: it returns the exit code and has no error channel (`| E` on `main` is
CE0106). So `??` in `main` is CE0131. Handle the error in `main` explicitly:

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn main() i32:
    match risky():
        Result.Ok(x) ->
            println("Got: {x}")
            return 0
        Result.Err(_) ->
            println("Failed")
            return 1
```

A common shape puts the `??` chain in a helper with a channel, and `main` turns the
result into an exit code:

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn run() i32 | StdError:
    let i32 x = risky()??
    return Result.Ok(x)

fn main() i32:
    match run():
        Result.Ok(_) -> return 0
        Result.Err(_) -> return 1
```

## Pattern Matching

Match on both success and error cases:

```sushi
match divide(10, 2):
    Result.Ok(value) ->
        println("Result: {value}")
    Result.Err(MathError.DivisionByZero) ->
        println("Cannot divide by zero")
    Result.Err(_) ->
        println("Other error")
```

## Usage in Conditionals

A condition must be a `bool`. A `Result` in an `if` or a `while` condition, or as an
operand of `and`/`or`/`xor`/`not`, is CE2516. Test it with `.is_ok()` or `.is_err()`:

```sushi
if (divide(10, 2).is_ok()):
    println("Success!")
else:
    println("Failed")
```

## Best Practices

- **Always handle errors explicitly** - Don't ignore Result values
- **Write a channel on a function that can fail** - The bare form is for a total function only
- **Use `??` for error propagation** - In function chains with one error type, or with a
  declared conversion between two
- **Use `.realise(default)` for fallback values** - When a default makes sense
- **Use pattern matching for detailed error handling** - When you need different behavior per error variant
- **Avoid `expect()` in production code** - It terminates the program on error
- **No `??` in main()** - `main` is bare (CE0131); use explicit error handling
- **Keep error types consistent** - Makes error propagation easier
- **Declare custom error types with `error`** - For domain-specific error conditions

## Examples

### Basic Error Handling

```sushi
use <collections/strings>

error ValidationError:
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

`open()` and `read_all()` both answer `IoError`, so the function declares the same
channel and `??` propagates with no conversion:

```sushi
use <io/fs>

fn read_config() string | IoError:
    let File f = open("config.txt", FileMode.Read())??
    let string content = f.read_all()??
    return Result.Ok(content)
```

### Combining with Maybe

```sushi
use <math>

fn safe_divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)

fn process() i32 | MathError:
    let Result@(i32, MathError) result = safe_divide(10, 2)
    let Maybe@(MathError) err = result.err()

    if (err.is_some()):
        return Result.Err(err.realise(MathError.DivisionByZero))

    let i32 value = result.realise(0)
    return Result.Ok(value)
```
