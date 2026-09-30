# Error Handling

[← Back to Documentation](index.md)

Comprehensive guide to error handling in Sushi using `Result@(T, E)`, `Maybe@(T)`, and the `??` operator.

## Table of Contents

- [Philosophy](#philosophy)
- [The Rule: A Channel Is Written](#the-rule-a-channel-is-written)
  - [A Bare Function Is the Exception](#a-bare-function-is-the-exception)
- [Result@(T, E)](#resultt-e)
  - [Error Type Syntax](#error-type-syntax)
  - [Standard Error Enums](#standard-error-enums)
  - [Creating Results](#creating-results)
  - [Handling Results](#handling-results)
  - [Result Methods](#result-methods)
- [Maybe@(T)](#maybet)
- [Error Propagation](#error-propagation)
- [Error Channels on Methods](#error-channels-on-methods)
- [Patterns and Best Practices](#patterns-and-best-practices)
- [Traps Are Not Errors](#traps-are-not-errors)
- [Error Codes](#error-codes)

## Philosophy

Sushi makes errors explicit and impossible to ignore:

1. **A function that can fail says so** - Its signature writes the error channel `| E`, and the call returns `Result@(T, E)`
2. **Compiler-enforced handling** - Cannot ignore errors accidentally
3. **No exceptions** - Control flow is always visible
4. **Type-safe error propagation** - Error types must match for propagation
5. **Zero runtime cost** - Compiles to efficient LLVM code

## The Rule: A Channel Is Written

A callable has an error channel only when its signature writes `| E`, or when it returns
an explicit `Result@(T, E)`. The rule is the same for a free function, an extension or
perk method, a lambda and a function type. There is no default error type.

```sushi
use <collections/strings>

fn parse(string text) i32 | StdError:    # a channel: the call returns Result@(i32, StdError)
    if (text.is_empty()):
        return Result.Err(StdError.Error)
    return Result.Ok(text.len())

fn double(i32 x) i32:                    # bare: the call returns i32
    return x * 2

fn main() i32:
    let i32 n = parse("42").realise(0)
    return double(n) - 4
```

- A body with a channel spells both constructors: `return Result.Ok(x)` and
  `return Result.Err(e)`. A bare `return x` there is **CE2030**.
- A BARE body returns the value itself. `return Result.Ok(...)` and `return Result.Err(...)`
  are **CE2091**, and a `??` is **CE0131**. A bare function that calls a fallible one
  handles the error in its body (`match`, `.realise(default)`), or its signature writes
  `| E`.
- The call of a bare function returns the value. `??` on it is **CE2507**, and `.realise`
  on it is **CE2008**, because the value is not a `Result`.
- A body that returns a value, or a Result, ends in a `return` on every path (**CE0107**).
  A bare `~` body returns nothing and can reach its end.
- A function declared in an `unsafe external "C"` block (the FFI) is bare too. A C
  function returns a raw value, so `libc.strlen(s)` yields a plain `i64`. See
  [Foreign Function Interface](ffi.md).

A function that writes no `| E` has no error channel, and there is no default error type.
The decision record is [The error channel is opt-in](design/error-channel.md).

### A Bare Function Is the Exception

A bare function is the exception. It is not the default style. Show the channel form
first, and use the bare form seldom.

Use a bare function only when it is needed: the function is total over its inputs and will
stay so (a checksum, a pure arithmetic or string helper, a path join), and a channel would
only force a dead `.realise` or `??` on every caller.

Write a channel for everything else: a function that does I/O, parses, allocates on a size
it is given, or can gain a failure later. The reason for a public function is the ABI: a
channel added later changes the signature, and that breaks every caller and every binary
`.slib`. A public function keeps a channel when there is any doubt.

The compiler does not enforce this rule. There is no lint.

## Result@(T, E)

A function with a channel returns `Result@(T, E)`, where:
- `T` is the declared return type (success value)
- `E` is the error type that the signature writes

### Error Type Syntax

#### Custom Error Type with | Syntax

```sushi
use <math>

fn divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)
# Returns Result@(i32, MathError)
```

`MathError` is a predefined enum, and `use <math>` brings its name. Do not declare your own
`enum MathError`: a predefined name cannot be declared again (**CE2046**), also in a unit
with no `use <math>`. Give your own error enum a new name. The examples below use this
`divide`.

The error type `E` must be an enum, in both spellings (`T | E` and `Result@(T, E)`). Do not
mix the two spellings in one signature (**CE2085**).

#### Explicit Result@(T, E) Syntax

```sushi
fn foo() Result@(i32, MyError):
    return Result.Ok(42)
```

### Standard Error Enums

Sushi provides built-in error types for common error conditions. Each but `StdError` has
a HOME module, and the import brings the bare name (`use <math>` for `MathError`). A module
that re-exports the home module brings the name too: `use <io/fs>` brings `IoError` and
`FileError`.

- **StdError** - Generic fallback (`StdError.Error`); in scope everywhere, no import
- **MathError** (`<math>`) - Mathematical errors (`DivisionByZero`, `Overflow`, `Underflow`, `InvalidInput`)
- **FileError** (`<io/error>`, also through `<io/fs>`) - Path and descriptor errors (`NotFound`, `PermissionDenied`, `AlreadyExists`, `IsDirectory`, `DiskFull`, `TooManyOpen`, `InvalidPath`, `IOError`, `Other`)
- **IoError** (`<io/error>`, also through `<io/contracts>`, `<io/fs>` and `<io/buf>`) - What every read, write, seek, `open()` and `close()` answers (`NotFound`, `PermissionDenied`, `AlreadyExists`, `IsDirectory`, `ConnectionReset`, `TimedOut`, `Closed`, `Interrupted`, `WouldBlock`, `DiskFull`, `TooManyOpen`, `InvalidInput`, `Os(i32)`, `Other`)
- **NetError** (`<net/error>`) - Connect, bind and resolve errors (`ConnectionRefused`, `TimedOut`, `ResolveFailed`, ...)
- **ProcessError** (`<sys/process>`) - Process management (`SpawnFailed`, `ExitFailure`, `SignalReceived`)
- **EnvError** (`<sys/env>`) - Environment variables (`NotFound`, `InvalidValue`, `PermissionDenied`)

See [Result@(T, E) API Reference](stdlib/result.md) for complete details.

### Creating Results

```sushi
# Success - Always provide the value
return Result.Ok(value)

# Failure - always include the error value
use <math>

fn divide(i32 a, i32 b) i32 | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)  # Error with data
    return Result.Ok(a / b)
```

**Important:** `Result.Err()` without an error value is a **compile error** (**CE2050**, wrong argument count for the `Err` variant). Always include the error value.

**Every constructor is spelled.** The compiler never wraps a bare value into `Ok`. A
`return value` in a body that answers a Result is **CE2030**, and a `~` success is
`return Result.Ok(~)`. The rule is the same for a function, a lambda block body, and an
extension or perk method that declares an error channel `| E`:

```sushi
enum OddError:
    TooOdd

extend i32 half_checked() i32 | OddError:
    if (self % 2 == 1):
        return Result.Err(OddError.TooOdd)
    return Result.Ok(self / 2)          # `return self / 2` is CE2030
```

A body with NO channel is the other way round: it returns the value itself, and both
constructors are refused there (**CE2091**). The rule is the same for a function, a lambda
and a method. See [Error Channels on Methods](#error-channels-on-methods).

A body that can reach its end with no `return` is **CE0107**. This includes a `~` function
with a channel, a `| E` method and a lambda block body with a channel: such a `~` body ends
with `return Result.Ok(~)`. A BARE `~` body returns nothing and can reach its end. A
statement after a statement that always ends the path (a `return` in every branch) is
**CE0140**.

### Handling Results

#### Using `.realise(default)`

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn main() i32:
    let i32 x = divide(10, 2).realise(0)   # x = 5
    let i32 y = divide(10, 0).realise(-1)  # y = -1 (error case)

    return 0
```

**Key points:**
- Default parameter is mandatory
- Forces explicit thinking about error cases
- Never panics - always produces a value

#### Using Conditionals

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn main() i32:
    let Result@(i32, MathError) result = divide(10, 2)

    if (result.is_ok()):
        # Success case
        let i32 value = result.realise(0)
        println("Result: {value}")
    else:
        # Error case: main is bare, so it returns an exit code
        println("Division failed")
        return 1

    return 0
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

On a `Result@(bool, E)`, `if (f())` could ask for the Ok tag or for the bool in it. The
compiler does not choose one, so you write the question.

**A field of the value is not a field of the wrapper either.** A `Result@(T, E)` and a
`Maybe@(T)` are enums, and an enum carries variants, so a dot on the wrapper is
**CE2106**. Take the value first, with the same three tools.

```sushi
struct Point:
    i32 x
    i32 y

fn main() i32:
    let Point[] pts = from([Point(11, 22), Point(33, 44)])
    # println("{pts.get(0).x}")        # CE2106 -- 'Maybe@(Point)' has no field 'x'
    let Point first = pts.get(0).realise(Point(0, 0))
    println("{first.x}")
    return 0
```

#### Using Pattern Matching

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn main() i32:
    match divide(10, 2):
        Result.Ok(value) ->
            println("Result: {value}")
        Result.Err(MathError.DivisionByZero) ->
            println("Cannot divide by zero")
        Result.Err(e) ->
            println("Other error")

    return 0
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
fn get_value() i32 | StdError:
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

    return 0
```

## Maybe@(T)

`Maybe@(T)` represents optional values, replacing sentinel values (`-1`, `null`, empty strings) with compile-time checked optionals.

### Creating Maybe Values

```sushi
# In a bare function that returns Maybe@(T)
return Maybe.Some(value)     # value present
return Maybe.None()          # value absent

# In a function with a channel, `Maybe@(T) | E`
return Result.Ok(Maybe.Some(value))
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
            return Maybe.Some(n)
    return Maybe.None()

fn main() i32:
    let i32[] data = from([1, 3, 5, 8, 9])
    let Maybe@(i32) result = find_first_even(data)

    match result:
        Maybe.Some(value) ->
            println("Found even: {value}")
        Maybe.None() ->
            println("No even numbers")

    return 0
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
        Result.Ok(nom f) ->
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

    return 0
```

## Error Propagation

The `??` operator unwraps `Result@(T, E)` or `Maybe@(T)`, propagating errors automatically.

**Important:** For Result@(T, E), error types must match exactly. The `??` operator does not perform automatic error type conversion.

### Basic Usage

**Without `??`:**

```sushi
use <io/fs>

fn read_config() string | IoError:
    let Result@(File, IoError) result = open("config.txt", FileMode.Read())
    match nom result:
        Result.Ok(nom f) ->
            match f.read_all():
                Result.Ok(nom content) -> return Result.Ok(content)
                Result.Err(e) -> return Result.Err(e)
        Result.Err(e) ->
            return Result.Err(e)
```

A bare pattern binding borrows the payload. `read_all()` needs a `File` that it can change,
and `return` gives the string away, so both bindings TAKE their payload with `nom`. To take
from a named local, write `match nom result:`. See
[Binding Modes](language-guide.md#binding-modes).

**With `??`:**

```sushi
use <io/fs>

fn read_config() string | IoError:
    let File f = open("config.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)
```

### How It Works

For `Result@(T, E)`:
- `Result.Ok(value)?? → value` (unwraps)
- `Result.Err(e)?? → return Result.Err(e)` (propagates; `E` must be the error type of the
  enclosing function)

For `Maybe@(T)`:
- `Maybe.Some(value)?? → value` (unwraps)
- `Maybe.None()??` returns early with an `Err` (propagates as an error)

### Chaining Operations

```sushi
fn process() i32 | StdError:
    let i32 step1 = calculate()??
    let i32 step2 = validate(step1)??
    let i32 step3 = transform(step2)??
    return Result.Ok(step3)
```

Stops at first error and returns immediately.

### RAII Safety

The `??` operator automatically cleans up resources on error:

```sushi
fn process_with_cleanup(bool succeed) i32 | StdError:
    let i32[] data = from([1, 2, 3])

    # If might_fail() returns Err:
    # 1. data is automatically freed
    # 2. Error is propagated
    let i32 value = might_fail(succeed)??

    return Result.Ok(value + data.len())
```

**Resources automatically cleaned:**
- Dynamic arrays, `List@(T)`, `HashMap@(K, V)`, `Own@(T)`, strings
- Struct fields (dynamic arrays, nested structs)
- Anything implementing the `Drop` perk — a `File`, a `TcpStream`, a `BufWriter@(W)` —
  whose `drop()` runs on the propagation path like any other destructor
- A `foreach` iterator the loop owns, on every exit path: the end of the input, a
  `break`, a `return`, and the propagation path a `??` binder takes

### `??` over a named Result

`??` moves the payload out of its wrapper. Over a call the wrapper is a temporary and the
payload simply lands where it is taken. Over a NAMED `Result` or `Maybe` that owns
something -- a string, an array, a handle, in EITHER arm -- the `??` spends the local:

```sushi
use <collections/strings>

fn make() string | StdError:
    let string base = "Mostly"
    return Result.Ok(base.concat(" Harmless"))

fn run() string | StdError:
    let Result@(string, StdError) r = make()
    let string got = r??          # `r` is spent here; `got` owns the buffer
    return Result.Ok(got)         # a mention of `r` after this line is CE2405

fn main() i32:
    match run():
        Result.Ok(s) -> println(s)
        Result.Err(_) -> println("err")
    return 0
```

A `Result` that owns nothing (`Result@(i32, StdError)`) is copied out of, and the local
stays usable. A BORROWED `Result` -- a parameter, a `match` or `foreach` binding -- is read
through, not spent: `let string s = r??` binds a borrow that frees nothing, and consuming
the read (`return Result.Ok(r??)`) is **CE2411**, with `.clone()` as the escape. The design
record is `docs/design/borrow-model.md` §10d.

### `??` on a `foreach` binder

`foreach` walks any type carrying `next()` that answers `Maybe@(T)`. When `T` is a
`Result` — a fallible iterator puts its failure IN the item — `??` on the BINDER is the
short form for "leave the function on the first failure":

```sushi
use <io/fs>
use <io/buf>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = BufReader.new(nom f, 8192)
    foreach(line?? in r.lines()):        # the first read failure leaves show()
        println(line)
    return Result.Ok(~)

fn main() i32:
    match show("/etc/hosts"):
        Result.Ok(_) -> return 0
        Result.Err(_) -> return 1
```

It is the same `??`, in one more position: the error types must match exactly (CE2511),
the loop's own scope is cleaned up on the way out, and it is refused in `main` (CE0131),
because `main` is bare.

Drop the marker and the item is the plain `Result`, which is what lets a body report one
failure and carry on:

```sushi
use <io/fs>
use <io/buf>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = BufReader.new(nom f, 8192)
    foreach(item in r.lines()):
        match item:
            Result.Ok(line) -> println(line)
            Result.Err(_) -> println("<unreadable>")
    return Result.Ok(~)

fn main() i32:
    match show("/etc/hosts"):
        Result.Ok(_) -> return 0
        Result.Err(_) -> return 1
```

A `??` binder over an item that is not a `Result` has nothing to unwrap: **CE2517**.

### Using ?? with Maybe@(T)

```sushi
use <collections/strings>

fn find_and_parse(string text) i32 | StdError:
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

    return 0
```

### Compile-Time Safety

```sushi
# ERROR CE2507: Using ?? on non-Result/non-Maybe type
# let i32 x = 5??

# ERROR CE0131: ?? in a BARE body (no `| E`), which has no Result to return
extend i32 squared() i32:
    # let i32 x = might_fail()??  # Not allowed here; declare `| E` on the method instead
    return self * self

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

### `main` Is Bare

`main` returns the exit code of the program. It is bare: it returns an integer type
(`return 0`), and it has no error channel. A `| E` on `main`, or a `Result@(T, E)` return,
is **CE0106**. A `??` in `main` is **CE0131**, as in every bare body. Handle each failure in
the body with `match` or `.realise(default)`, and return a code:

<!-- docs-sweep: skip (calls a helper defined in an earlier block on this page) -->
```sushi
fn main() i32:
    # let i32 x = risky()??        # CE0131: main is bare

    match risky():
        Result.Ok(x) ->
            println("Success: {x}")
        Result.Err(e) ->
            println("Error occurred")
            return 1

    return 0
```

### A Total Helper Can Be Bare

A private helper that cannot fail, and will not gain a failure, can be bare. The caller
uses the value directly: no `??` and no `.realise` (a `??` on it is **CE2507**). The bare
helper composes with a caller of any channel:

```sushi
enum LexError:
    BadByte(i32)

fn at(peek u8[] src, i32 i) u8:
    if (i < 0 or i >= src.len()):
        return 0
    return src[i]

fn first_byte(u8[] src) i32 | LexError:
    let u8 c = at(peek src, 0)
    if (c == 0):
        return Result.Err(LexError.BadByte(0))
    return Result.Ok(c as i32)

fn main() i32:
    let u8[] bytes = from([65, 66])
    match first_byte(bytes):
        Result.Ok(v) -> println("{v}")
        Result.Err(_) -> println("error")
    return 0
```

A helper that CAN fail declares the error type of its caller, or `??` does not compose
through it (**CE2511**). Keep a channel on a public helper when there is any doubt (see
[A Bare Function Is the Exception](#a-bare-function-is-the-exception)).

## Error Channels on Methods

An extension method and a perk method follow the rule of a function. With no `| E`, the
method is BARE: no `Result`, no `Result.Ok(...)` in the body (**CE2091**), and no `??` in
the body (**CE0131**). Handle a Result inside such a body with `match` or
`.realise(default)`.

A method that can fail declares an error channel `| E`, as a function does. Then:

- the call answers `Result@(T, E)`, and the caller handles it with `??`, `.realise()` or
  `match`
- the body spells both constructors, `return Result.Ok(...)` and `return Result.Err(...)`;
  a bare `return value` is **CE2030**
- `??` is legal in the body, and the error types must match exactly (**CE2511**)

```sushi
enum OddError:
    TooOdd

extend i32 half_checked() i32 | OddError:
    if (self % 2 == 1):
        return Result.Err(OddError.TooOdd)
    return Result.Ok(self / 2)

extend i32 quarter_checked() i32 | OddError:
    let i32 half = self.half_checked()??
    return Result.Ok(half.half_checked()??)

fn main() i32:
    println(12.quarter_checked().realise(-1))    # 3
    println(6.quarter_checked().realise(-1))     # 3 is odd: -1
    return 0
```

A method chain stops at a channel that is still unhandled. `n.half_checked().squared()` is
**CE2515**, because `.squared()` is a method of `i32` and not of the `Result`. Write
`n.half_checked()??.squared()` in a body with the same channel, or handle the Result first.

On a perk method, the contract and every implementation must declare the same channel
(**CE0133**). See [Perks](perks.md#error-channels-on-perk-methods).

## Patterns and Best Practices

### 1. Always Provide Meaningful Defaults

```sushi
# Good: clear what -1 means
let i32 count = count_lines(path).realise(-1)  # -1 = the file could not be read

# Better for a search: a Maybe@(T) says "not found" in the type
match text.find("x"):
    Maybe.Some(pos) -> println("At {pos}")
    Maybe.None() -> println("Not found")
```

### 2. Early Return on Error

```sushi
fn validate_input(i32 x) i32 | StdError:
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

fn process_pipeline() string | StdError:
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
fn low_level() i32 | StdError:
    # Just propagate
    let i32 x = risky_operation()??
    return Result.Ok(x)

fn mid_level() i32 | StdError:
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
        return 1

    return 0
```

### 5. Result@(Maybe@(T), E) for Three States

```sushi
use <collections/hashmap>

fn lookup(HashMap@(string, i32) map, string key, bool corrupted) Maybe@(i32) | StdError:
    # Three possible states:
    # 1. Found value: Ok(Some(value))
    # 2. Key not found: Ok(None)  - not an error
    # 3. Internal error: Err(StdError.Error)
    if (corrupted):
        return Result.Err(StdError.Error)
    return Result.Ok(map.get(key))

fn main() i32:
    let HashMap@(string, i32) map = HashMap.new()
    map.insert("answer", 42)
    match lookup(map, "answer", false):
        Result.Ok(Maybe.Some(v)) -> println("Found {v}")
        Result.Ok(Maybe.None) -> println("No such key")
        Result.Err(_) -> println("The map is damaged")
    map.free()
    return 0
```

### 6. Avoid Silent Failures

<!-- docs-sweep: skip (calls load, which the narrative owns) -->
```sushi
# Bad: Silently returns default
fn get_config() string:
    return load().realise("default")

# Good: Caller decides how to handle
fn load_config() string | StdError:
    let string data = load()??  # Forward the loaded value
    return Result.Ok(data)

fn main() i32:
    let Result@(string, StdError) config = load_config()
    if (config.is_ok()):
        let string value = config.realise("")
        println("Loaded: {value}")
    else:
        println("Using default config")

    return 0
```

## Traps Are Not Errors

A runtime error is a defect, not an error: RE2020 for an index out of bounds, RE2021 for a
failed allocation, and the other `RExxxx` codes. A trap stops the program. A bare function
can trap exactly as a function with a channel can, and a channel does not catch a trap. Use
the checked form when the failure is data: `.get(i)` returns `Maybe@(T)`, and `arr[i]`
traps.

## Error Codes

Common error codes related to error handling:

- **CE0106**: `main` returns a type that is not a bare integer, or declares a channel
- **CE0107**: A body can reach its end with no `return`
- **CE0131**: `??` in a bare body: a function, a method, a lambda or `main` (no `| E`)
- **CE0133**: A perk implementation and its contract declare different error channels
- **CE0140**: A statement after a statement that always ends the path
- **CE2008**: `.realise()` on the call of a bare function (the value is not a Result)
- **CE2009**: `.realise()` wrong argument count (the code of every miscount)
- **CE2030**: A bare `return value` in a body that answers a Result
- **CE2050**: `Result.Err()` with no error value
- **CE2085**: `| E` together with an explicit `Result@(T, E)` return type
- **CE2091**: `Result.Ok(...)` or `Result.Err(...)` in a bare body: a function, a method or a lambda (no `| E`)
- **CE2106**: A field read on a `Result` or a `Maybe` (take the value first)
- **CE2503**: `.realise()` default type mismatch
- **CE2505**: Assigning a `Result@(T, E)` to a non-Result without handling
- **CE2507**: Using `??` on a non-Result, non-Maybe type, the call of a bare function included
- **CE2511**: `??` with an error type that differs from the function's error type
- **CE2515**: A method chain continues past an unhandled channel
- **CE2516**: A `Result` or a `Maybe` used as a condition
- **CE2517**: A `??` binder in `foreach` over an item that is not a `Result`
- **CW2001**: Unused `Result@(T, E)` value (warning)

`main` is bare, so a `??` in `main()` is CE0131.

---

**See also:**
- [Standard Library](standard-library.md) - Complete Result@(T, E) and Maybe@(T) API
- [Language Reference](language-reference.md) - Syntax details
- [Examples](examples/README.md) - Error handling patterns in practice
