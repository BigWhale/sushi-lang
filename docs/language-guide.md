# Language Guide: A Tour of Sushi

[← Back to Documentation](index.md)

This guide provides a friendly tour of Sushi's features. If you're new to Sushi, start here before diving into the detailed [Language Reference](language-reference.md).

## Table of Contents

- [Hello World](#hello-world)
- [Variables and Types](#variables-and-types)
- [Functions and Returns](#functions-and-returns)
- [Documenting Code](#documenting-code)
- [Control Flow](#control-flow)
- [Error Handling](#error-handling)
- [Collections](#collections)
- [Structs and Enums](#structs-and-enums)
- [Tuples](#tuples)
- [Pattern Matching](#pattern-matching)
- [Generics](#generics)
- [Units and Imports](#units-and-imports)
- [Memory Management](#memory-management)
- [Files and Handles](#files-and-handles)

## Hello World

Every Sushi program starts with a `main` function that serves as the entry point for execution:

```sushi
fn main() i32:
    println("Mostly Harmless")
    return 0
```

Key points:
- `fn` declares a function
- `i32` is the return type (32-bit integer)
- `main` is bare: it has no error channel, and it returns the exit code itself
- `println` outputs text with a newline to standard output
- `return 0` ends the program with exit code 0 (success)

The main function must return a bare integer type, usually `i32` (the operating system uses it as the exit code). Another return type is [CE0106](error-catalog.md#ce0106), and so is an error channel `| E` on `main`. A `??` in `main` is [CE0131](error-catalog.md#ce0131): handle each failure in the body with `match` or `.realise(default)`, and return a code.

To read the command line, `main` takes exactly one parameter, `string[] args`. The first element is the program name. No other parameter list is accepted: the type must be `string[]` and the name must be `args`.

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

    return 0
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

    # Underscores group digits, one between two digits, in every base
    let i64 big = 1_000_000
    let u32 mask = 0xFF_FF

    println("{tiny} {normal} {huge} {byte} {unsigned} {pi} {precise} {big} {mask}")
    return 0
```

A bare literal takes its type from the context: the annotation, the parameter, the field or
the other operand. A value out of range for that type is [`CE2073`](error-catalog.md#ce2073). With no context, an
integer literal is an `i32` and a float literal is an `f64`. A byte literal `a'x'` (the
ASCII code of `x`, see the [reference](language-reference.md#numeric-literals)) is a `u8`
when no context types it.

### Constants, Unit Variables and Visibility

A `const` at the top of a unit is a value that the compiler computes at compile time. A
`var` at the top of a unit is STORAGE: one per program, set before `main` runs, and you can
rebind it. A declaration is private to its unit by default; write `public` to export it to
the units that import yours:

```sushi
public const i32 MAX_DEPTH = 32         # another unit may read it
const i32[3] PRIMES = [2, 3, 5]         # this unit only
var i32 next_id = 0                     # unit-level storage

fn fresh_id() i32:
    next_id := next_id + 1
    return next_id

fn main() i32:
    let i32 a = fresh_id()
    let i32 b = fresh_id()
    println("{MAX_DEPTH} {PRIMES[2]} {a} {b}")
    return 0
```

A constant cannot be written ([`CE2096`](error-catalog.md#ce2096)). A `var` is never moved out of: `f(nom v)` or
`let T x = v` on a `var` whose type owns a resource is [`CE2436`](error-catalog.md#ce2436). `public` works on `fn`,
`const`, `var`, `struct`, `enum` and `perk`. See [Visibility](design/visibility.md) and
[Unit Storage](design/unit-storage.md).

### Integer Overflow

A value has the width its type gives it, and the compiler tells you when a value you
wrote cannot fit that width. It reads what it can read: a `const`, and an expression
built from literals and constants. An operation whose result the type cannot hold is a
compile error (**[CE2077](error-catalog.md#ce2077)**), so a wrong answer never reaches the program:

<!-- docs-sweep: error CE2077 -->
```sushi
fn main() i32:
    let u8 sum = 200 + 100     # CE2077: '+' gives 300, out of range for u8
    println(sum)
    return 0
```

The answer is a wider type, or an `as` cast when the bit pattern is what you want:
`(200 as u16) + 100` counts to 300 in a `u16`, and `300 as u8` keeps the low eight bits
and gives 44. The checked operators are `+`, `-`, `*`, `/`, `%` and unary minus. The
bitwise operators ask a different question, because they move and combine bits: `&`, `|`,
`^`, `~`, `<<` and `>>` work at the width and lose whatever leaves it, so `200 << 1` on
a `u8` is 144 and nothing reports it.

Nothing is checked at run time. The compiler cannot read the value of a variable, so a
sum of two locals wraps at the width, as it does in C:

```sushi
fn main() i32:
    let u8 a = 200
    let u8 b = 100
    let u8 sum = a + b
    println(sum)               # 44
    return 0
```

### Type Conversion

All conversions must be explicit with `as`:

```sushi
fn main() i32:
    let i32 x = 42
    let f64 y = x as f64      # int to float
    let u32 z = y as u32      # float to unsigned int

    return 0
```

`as` also converts between two error types when the program declares the conversion
(see [Error Types and Conversion](#error-types-and-conversion)). Every other cast is
[`CE2014`](error-catalog.md#ce2014).

### Strings

Strings have full UTF-8 support:

```sushi
use <collections/strings>

fn main() i32:
    let string text = "Don't Panic"
    let string emoji = "🐬 🌍"

    println(text)
    println("Length: {text.len()}")     # Character count
    println("Size: {text.size()}")      # Byte count

    return 0
```

**String methods:**
- `.len() -> i32` - Character count (UTF-8 aware)
- `.size() -> i32` - Byte count
- `.is_empty() -> bool` - Check if string is empty
- `.find(string) -> Maybe@(i32)` - Find substring position
- `.split(string) -> string[]` - Split into array by delimiter
- `.trim() -> string` - Remove leading/trailing whitespace
- `.upper() -> string` - Convert to uppercase (ASCII only)
- `.lower() -> string` - Convert to lowercase (ASCII only)

Every built-in string method needs `use <collections/strings>` in the unit that calls it.
Without the import, the call is [CE3015](error-catalog.md#ce3015). Interpolation and your own extensions on `string`
need no import.

See [Standard Library: String Methods](standard-library.md) for detailed documentation.

### String Interpolation

Embed expressions in strings with `{...}`:

```sushi
fn main() i32:
    let i32 answer = 42
    let string name = "Arthur"

    println("Hello {name}, the answer is {answer}")
    println("Next answer: {answer + 1}")

    return 0
```

## Functions and Returns

Functions are the building blocks of Sushi programs. Every function has explicit parameter types and an explicit return type. A function that can fail writes an error channel `| E` after its return type. Then every return spells `Result.Ok(...)` or `Result.Err(...)`. A function with no `| E` is BARE: it returns the value itself.

### Basic Functions

```sushi
use <collections/strings>

fn parse_count(string text) i32 | StdError:  # a channel: the call returns Result@(i32, StdError)
    if (text.is_empty()):
        return Result.Err(StdError.Error)
    return Result.Ok(text.len())

fn add(i32 a, i32 b) i32:  # bare: the call returns i32
    return a + b

fn greet(string name) ~:  # ~ is "blank" type (no return value)
    println("Mostly Harmless, {name}!")

fn main() i32:
    let i32 sum = add(5, 7)
    let i32 n = parse_count("42").realise(0)
    greet("Ford")
    return 0
```

**A bare function is the exception.** Use it seldom: only when the function is total over its inputs and will stay so (a checksum, a pure arithmetic or string helper, a path join), and a channel would only force a dead `.realise` or `??` on every caller. Write a channel for everything else. A public function keeps a channel when there is any doubt, because a channel added later changes the signature and breaks every caller and every binary `.slib`. The compiler does not enforce this. See [The error channel is opt-in](design/error-channel.md).

**Function syntax breakdown**:
- `fn` keyword declares a function
- Parameters: `type name` pairs, comma-separated
- Return type: comes after the parameter list
- `~` ("blank" or "unit" type): used for functions that don't return a meaningful value
- Body: indented block following the colon
- Returns with a channel: `Result.Ok(value)` or `Result.Err(error)`; a bare `return value` there is [CE2030](error-catalog.md#ce2030)
- Returns in a bare function: `return value`; `Result.Ok(...)` there is [CE2091](error-catalog.md#ce2091), and `??` there is [CE0131](error-catalog.md#ce0131)
- Every path must end with a `return`: a body that can reach its end is [CE0107](error-catalog.md#ce0107), also for a
  `~` function with a channel (end it with `return Result.Ok(~)`). A bare `~` function can
  reach its end
- A statement after a statement that always ends the path is dead code, and it is [CE0140](error-catalog.md#ce0140)

**The blank type (`~`)**: When a function performs an action but doesn't produce a value (like printing or modifying a reference), it returns `~`. A bare `~` function needs no `return`. A `~` function with a channel (`~ | E`) ends with `return Result.Ok(~)`.

### Multiple Parameters

```sushi
fn describe(string name, i32 age, bool friendly) ~:
    println("{name} is {age} years old")
    if (friendly):
        println("They're quite friendly!")

fn main() i32:
    describe("Zaphod", 200, false)
    return 0
```

**Parameter passing**: every parameter declares a **mode**, and the mode says who frees the value.
A marked mode is written at the declaration and at the call site alike; the default is written at
neither.

- **unmarked** — `fn f(string x)`, called `f(s)`. A *borrow*: the caller keeps the value and frees
  it, so `s` stays usable after the call. This is the default for every type.
- **`nom`** — `fn f(nom string x)`, called `f(nom s)`. A *consume*: the callee becomes the owner and
  frees the value, so using `s` afterwards is [`CE2405`](error-catalog.md#ce2405) (use-after-move).
- **`peek`** — `fn f(peek string x)`, called `f(peek s)`. A read-only borrow *by pointer*; many at
  once.
- **`poke`** — `fn f(poke string x)`, called `f(poke s)`. A read-write borrow by pointer, so the
  callee's writes reach the caller's value; one at a time, exclusive.

Every struct and enum gets an auto-derived `.clone()`, which is how a caller hands over a value it
wants to keep: `f(nom s.clone())`. The one refusal is a type that declares a resource or holds one
(a `File`, a type that implements `Drop`): `.clone()` on it is [`CE2431`](error-catalog.md#ce2431), and `.share()` is the
escape where the type has it.

(One special case: `main`'s `string[] args` is a borrowed view of the process argv. Passing it to an
ordinary borrow parameter is fine; handing it to a `nom` one is [`CE2410`](error-catalog.md#ce2410).)

## Documenting Code

A documentation block is part of the declaration, not a comment near it. The compiler reads it,
and checks what it says against the declaration beside it.

A block opens with `##:` and closes with `:##`:

```sushi
##: The answer to life, the universe and everything. :##
const i32 ANSWER = 42

##:
Adds two numbers.

- Parameter a: The first addend.
- Parameter b: The second addend.
- Returns: The sum.
- Errors: Never, in practice.
:##
fn add(i32 a, i32 b) i32:
    return a + b

fn main() i32:
    let i32 sum = add(ANSWER, 1)
    println("{sum}")
    return 0
```

The block attaches to the declaration on the **next line**. A blank line breaks the attachment,
and so does an ordinary `#` comment. A block that attaches to nothing warns, unless it is the
first item in the file — that one documents the unit.

A block may also stand first in a body, where it documents the function around it:

```sushi
fn make_tea(u8 strength) ~:
    ##:
    Makes a cup of tea. A block that is first in a body documents the function.
    :##
    println("{strength}")

fn main() i32:
    make_tea(7 as u8)
    return 0
```

Four tags are recognised, and each one is an ordinary Markdown list item:
`- Parameter <name>:`, `- Returns:`, `- Errors:` and `- Example:`. Everything else is prose, and
the first paragraph is the summary.

Because the compiler owns the block, it can tell you that a `- Parameter q:` names no parameter
of this function, that one parameter is documented twice, or that `- Retruns:` is a misspelling
of `- Returns:`. A misspelled tag would be silently invisible in a system that reads the block
as text.

An `- Example:` introduces a fenced code block, and `python tests/docs_sweep.py` compiles and
runs it: an example that stops compiling is documentation that has drifted.

Those checks are always on, because each one finds a claim that contradicts the declaration.
What is simply MISSING is a matter of policy, so it waits for a flag: compile with
`./sushic --warn-missing-docs` and the compiler names every declaration, parameter, return
value, error arm and unit with no documentation.

See [Documentation Blocks](documentation-blocks.md) for the full reference.

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

    return 0
```

### While Loops

```sushi
fn main() i32:
    let i32 countdown = 5

    while (countdown > 0):
        println("T-minus {countdown}")
        countdown := countdown - 1

    println("Liftoff!")
    return 0
```

### For-Each Loops

```sushi
fn main() i32:
    let string[] names = from(["Arthur", "Ford", "Trillian"])

    foreach(name in names.iter()):
        println("Passenger: {name}")

    foreach(i in 0..3):          # a range: 0 1 2; `0..=3` includes the end
        println(i)

    return 0
```

`foreach` walks an `Iterator@(T)` (`.iter()`, a range, `.keys()`, `.values()`,
`.entries()`) or any type that has a `next()` method that answers `Maybe@(T)`. No perk is
necessary. See [Iteration](design/iteration.md).

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

    return 0
```

### Assertions

`assert(cond)` stops the program with the runtime error [RE2026](error-catalog.md#re2026) when `cond` is false. An
optional second argument is a `string` message, which the program builds only on failure.
The output names the file, the line and the column of the `assert`:

```sushi
fn main() i32:
    let i32[] xs = from([1, 2, 3])
    assert(xs.len() == 3, "expected three values, got {xs.len()}")
    println("Mostly Harmless")
    return 0
```

An assert is for a bug, not for an error that a caller can handle: use the error channel
for that. See [Assertions](language-reference.md#assertions).

## Error Handling

### Result@(T, E)

Sushi uses `Result@(T, E)` as its fundamental approach to error handling. A function that can fail writes its error type in the signature, `T | E`, and its call returns a `Result@(T, E)`. A function with no `| E` is bare and cannot return an error. There is no default error type. This design choice eliminates entire classes of bugs by making error handling explicit and impossible to ignore.

**The Philosophy**: In many languages, functions can fail silently or throw exceptions that might not be handled. Sushi puts the failure in the type instead: if a function can fail, that failure is part of what it returns, so the compiler can tell you where you have not dealt with it. You must explicitly choose to handle errors or propagate them.

```sushi
fn divide(i32 a, i32 b) i32 | StdError:
    if (b == 0):
        return Result.Err(StdError.Error)  # Error case
    return Result.Ok(a / b)  # Success case

fn main() i32:
    let Result@(i32, StdError) result = divide(42, 6)

    # Check if successful
    if (result.is_ok()):
        let i32 value = result.realise(0)
        println("Result: {value}")
    else:
        println("Division failed")

    return 0
```

**Key Concepts**:
- A function declared `i32 | StdError` returns `Result@(i32, StdError)`; a function declared `i32` returns `i32`
- The `E` of a `Result` is an error type: `StdError`, another predefined error type, or an enum that your program declares with `error` (any other type is [CE2084](error-catalog.md#ce2084))
- In a function with a channel, success values must be wrapped: `return Result.Ok(value)`
- Failures are signaled with: `return Result.Err(StdError.Error)`
- A condition is a bool and nothing else, so a `Result` is tested with `.is_ok()` or
  `.is_err()`; `if (result)` on its own is [CE2516](error-catalog.md#ce2516)
- The `.realise(default)` method extracts the value, using the default if the result is an error

This approach eliminates null pointer exceptions and ensures that error cases are always visible in the code. The compiler enforces that you handle or propagate errors - you cannot accidentally ignore them.

### Unwrapping with .realise()

The `.realise(default)` method provides a safe way to extract values from `Result@(T, E)`, similar to unwrapping in other languages but with a mandatory fallback value:

```sushi
fn get_value() i32:
    return 42

fn main() i32:
    # Unwrap with default value
    let i32 x = get_value()
    println("Value: {x}")

    return 0
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
use <io/files>
use <io/fs>

fn read_config() string | IoError:
    # If open fails, ?? returns Err immediately
    let File f = open("config.txt", FileMode.Read())??

    # Only reaches here if open succeeded
    let string content = f.read_all()??
    f.close()??
    return Result.Ok(content)

fn main() i32:
    let Result@(string, IoError) config = read_config()
    # Handle config...
    return 0
```

**How it works**:
1. If the `Result` is `Ok(value)`, `??` extracts and returns the value
2. If the `Result` is `Err(e)`, `??` immediately returns `Result.Err(e)` from the current function
3. The error propagates up the call stack until someone handles it

`??` takes a `Result@(T, E)` and nothing else ([CE2507](error-catalog.md#ce2507)). The error type of the operand must be
the error type of the current function, or the program declares a conversion between the two
([CE2511](error-catalog.md#ce2511) otherwise).

**RAII Safety**: The `??` operator is fully integrated with Sushi's RAII (Resource Acquisition Is Initialization) system. When an error is propagated, all resources in the current scope are properly cleaned up before the function returns. This means you never leak memory or file handles when errors occur.

**Advantages over manual error checking**:
```sushi
use <io/fs>

# Without ??: verbose, and there is no default handle to fall back on
fn process() string | IoError:
    match open("data.txt", FileMode.Read()):
        Result.Ok(nom f) ->
            match f.read_all():
                Result.Ok(nom data) -> return Result.Ok(data)
                Result.Err(e) -> return Result.Err(e)
        Result.Err(e) ->
            return Result.Err(e)

# With ??: clean and safe
fn process() string | IoError:
    let File f = open("data.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)
```

The `??` operator makes error handling code read almost like non-error-handling code, while maintaining full safety and explicit error propagation.

### Maybe@(T) for Optional Values

`Maybe@(T)` is Sushi's type-safe way to represent values that might or might not exist. It replaces the dangerous practice of using sentinel values (like `-1`, `null`, or empty strings) to indicate "no value". With `Maybe@(T)`, the absence of a value is encoded in the type system, making it impossible to forget to check.

**The Problem with Sentinel Values**: In many languages, you might return `-1` to indicate "not found" or use `null` to mean "no value". This leads to bugs because:
- The sentinel value might be a valid result (what if `-1` is actually in your data?)
- You can forget to check for the sentinel value
- Different functions might use different sentinel values

**The Maybe Solution**: `Maybe@(T)` has two variants:
- `Maybe.Some(value)` - contains a value of type `T`
- `Maybe.None()` - represents the absence of a value

```sushi
fn find_first_even(i32[] numbers) Maybe@(i32) | StdError:
    foreach(n in numbers.iter()):
        if (n % 2 == 0):
            return Result.Ok(Maybe.Some(n))
    return Result.Ok(Maybe.None())

fn main() i32:
    let i32[] data = from([1, 3, 5, 8])
    # find_first_even writes `| StdError`, so its call returns Result@(Maybe@(i32), StdError)
    let Result@(Maybe@(i32), StdError) result = find_first_even(data)

    match result:
        Result.Ok(found) ->
            match found:
                Maybe.Some(value) ->
                    println("Found: {value}")
                Maybe.None() ->
                    println("No even numbers")
        Result.Err(_) ->
            println("Search failed")

    return 0
```

**Key Methods on Maybe@(T)**:
- `.is_some()` - returns `true` if the Maybe contains a value
- `.is_none()` - returns `true` if the Maybe is empty
- `.realise(default)` - extracts the value or returns the default if None
- `.expect(message)` - extracts the value or terminates with an error message
- `.or_err(nom e)` - turns the Maybe into a `Result`: `Some(v)` becomes `Ok(v)` and `None` becomes `Err(e)`

A `Maybe` holds no error value, so `??` on a `Maybe` is [CE2507](error-catalog.md#ce2507). Write the error value with
`or_err`, and then propagate: `let i32 v = xs.get(0).or_err(nom AppError.Empty)??`.

**Common Use Cases**:
- Search operations (return `Maybe.Some(index)` if found, `Maybe.None()` if not)
- Optional configuration values (return `Maybe.Some(config)` or `Maybe.None()`)
- Nullable references in data structures (use `Maybe@(T)` instead of trying to represent null)
- HashMap lookups (`.get()` returns `Maybe@(V)` since the key might not exist)

**Composing Maybe with Result**: Since functions return `Result@(T, E)`, you often see `Result@(Maybe@(T), E)` - a result that might be an error, or might be a success with an optional value. The type system helps you handle all cases correctly.

### Error Types and Conversion

An error type is declared with `error`. Its body is the body of an `enum`. A conversion
`extend <Source> as <Target>:` turns one error type into another, and `??` calls it when the
two error types differ:

```sushi
error ParseError:
    Empty

error AppError:
    Parse(ParseError)

extend ParseError as AppError:
    return AppError.Parse(self)

fn parse(string s) i32 | ParseError:
    if (s == ""):
        return Result.Err(ParseError.Empty)
    return Result.Ok(7)

fn run(string s) i32 | AppError:
    let i32 n = parse(s)??          # ParseError as AppError
    return Result.Ok(n * 6)

fn main() i32:
    println(run("x").realise(-1))   # 42
    match run(""):
        Result.Ok(n) -> println(n)
        Result.Err(e) -> println(e) # AppError.Parse(ParseError.Empty)
    return 0
```

- `e as AppError` calls the same conversion on one value.
- A conversion is one step, and only the unit that declares the target type may declare it.
- For one call, `r.map_err(f)` converts with no declaration:
  `parse(s).map_err(|nom ParseError e| AppError.Parse(e))??`.

The full guide is [Error Handling](error-handling.md#error-conversion).

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

    return 0
```

**Fixed vs Dynamic**:
- **Fixed arrays** (`T[N]`): Size known at compile time, allocated on the stack, cannot be resized. Fast and lightweight.
- **Dynamic arrays** (`T[]`): Size can change at runtime, heap-allocated, supports push/pop operations. The `from([...])` function converts a fixed array literal to a dynamic array.

**Array methods**: `.len()`, `.get()`, `.first()`, `.last()`, `.contains()`, `.index_of()`,
`.push()`, `.pop()`, `.insert()`, `.remove()`, `.fill()`, `.reverse()`, `.clear()`, `.truncate()`, `.clone()`, `.iter()`,
`.hash()`, and the bulk copies `.extend(src)`, `.extend_range(src, start, count)`,
`.s(start, end)` and `.ss(start, count)`. See [Arrays](stdlib/collections/arrays.md).

**Indices are `i32`**: an index, a count and a range bound are `i32` positions. A bare literal
is an `i32`; a typed value of another integer type is [`CE2121`](error-catalog.md#ce2121) ([`CE2006`](error-catalog.md#ce2006) as a method argument,
for example to `.get()`), and the fix is `as i32`.

**Writing one element**: `arr[i] := value` works on both array kinds and on every element type. The
index is bounds-checked exactly like a read, so an index past the end aborts with [`RE2020`](error-catalog.md#re2020) (and an
index the compiler can read -- a literal or a named constant -- is rejected at compile time: [`CE2012`](error-catalog.md#ce2012) past the end of a fixed array, [`CE2056`](error-catalog.md#ce2056)
if it is negative):

```sushi
fn main() i32:
    let i32[3] fixed = [1, 2, 3]
    fixed[0] := 42

    let string[] words = from(["towel", "guide"])
    words[1] := "babel fish"        # the old element is freed, the new one adopted

    println("{fixed[0]} {words[1]}")
    return 0
```

An indexed assignment takes ownership of the value, so the rules are the ones every other owning
position uses: an owned source is moved (using it afterwards is [`CE2405`](error-catalog.md#ce2405)), and a value read out of
a container needs `.clone()` (`words[0] := words[1]` is [`CE2411`](error-catalog.md#ce2411)). You may only write where the
write can reach the owner — not through a `peek` parameter, a match binding, or a constant.

**Elements that fill more than one slot**: a table does not have to be spelled out.
`value; count` fills `count` slots with one value, and a range fills the slots it spans.
Both mix with plain elements:

```sushi
fn main() i32:
    let i32[10] tally = [0; 10]           # ten zeros
    let i32[6]  mixed = [1, 0;3, 9, 7]    # 1 0 0 0 9 7
    let i32[6]  table = [0..=5]           # 0 1 2 3 4 5
    let i32[]   head  = from([-1; 1000])  # a thousand, on the heap

    println("{tally[9]} {mixed[2]} {table[5]} {head.len()}")
    return 0
```

**Where the count must be readable depends on the position.** A fixed array's length is
part of its type and a constant needs its values, so both need a count the compiler can
read. A `from()` array carries its length at run time, so a count or a bound there may be
any `i32` expression:

```sushi
fn main() i32:
    let i32 n = 4
    let i32[] slots = from([0; n])        # four zeros, sized at run time
    let i32[] index = from([0..n])        # 0 1 2 3

    println("{slots.len()} {index[3]}")
    return 0
```

The repeated value is a **borrow**, and every slot takes its own copy, so a `string`
works and the source stays yours. A range yields `i32`. See the
[Language Reference](language-reference.md#a-repeated-element) for the full rules.

**Arrays of arrays**: an array element can be an array. Read the suffixes from left to
right: `i32[3][]` is a dynamic array of `i32[3]`, and `i32[2][3]` is a fixed array of 3
`i32[2]`. That is the reverse of C. An index removes the last suffix, and a chained index
writes into the inner array in place:

```sushi
fn main() i32:
    let i32[][] grid = from([from([1, 2]), from([3])])
    grid[1].push(4)
    grid[0][1] := 20
    println("{grid.len()} {grid[0][1]} {grid[1][1]}")   # 2 20 4
    return 0
```

See [Array Types](language-reference.md#array-types) for the full order table.

**Memory Management**: Dynamic arrays use RAII - they're automatically deallocated when they go out of scope. The destructor recursively cleans up all elements, so arrays of structs or strings are properly freed. A dynamic array owns heap, so it MOVES: `let i32[] b = a` hands the buffer to `b`. Passing it to an unmarked parameter is a borrow, and the caller keeps it; only a `nom` parameter takes it (`f(nom a)`, and a later use of `a` is [`CE2405`](error-catalog.md#ce2405)).

### List@(T)

`List@(T)` is a generic growable collection that provides more flexibility than raw dynamic arrays. It is similar to a vector in Rust or an ArrayList in Java:

```sushi
fn main() i32:
    let List@(string) passengers = List.new()

    passengers.push("Arthur")
    passengers.push("Ford")
    passengers.push("Trillian")

    println("Passengers: {passengers.len()}")

    let Maybe@(string) first = passengers.get(0)
    match first:
        Maybe.Some(name) -> println("First: {name}")
        Maybe.None() -> println("Empty list")

    return 0
```

**Key Features**:
- **Generic**: Works with any type - `List@(i32)`, `List@(string)`, `List@(MyStruct)`, etc.
- **Automatic growth**: Capacity doubles when full, giving amortized O(1) push operations
- **Safe access**: `.get()` returns `Maybe@(T)` instead of crashing on invalid indices
- **Efficient**: Uses `llvm.memmove` for shifting elements during insert/remove operations

**List methods**:
- Creation: `.new()`, `.with_capacity(n)`
- Access: `.get(index)`, `.len()`, `.capacity()`, `.is_empty()`
- Modification: `.push(value)`, `.pop()`, `.insert(index, value)`, `.remove(index)`, `.clear()`
- Memory: `.reserve(additional)`, `.shrink_to_fit()`, `.free()`, `.destroy()`
- Iteration: `.iter()`, `.debug()`

Every index and count argument (`get`, `insert`, `remove`, `reserve`, `truncate`,
`List.with_capacity`) is an `i32`. A typed value of another integer type is [`CE2006`](error-catalog.md#ce2006); write
`as i32`.

**When to use List vs raw arrays**: Use `List@(T)` when you need frequent insertions/removals at arbitrary positions, capacity management, or want the additional safety of `Maybe@(T)` returns. Use raw dynamic arrays (`T[]`) for simpler use cases where you just need push/pop at the end.

### HashMap@(K, V)

`HashMap@(K, V)` provides O(1) average-case key-value lookups using open addressing with linear probing:

```sushi
use <collections/hashmap>

fn main() i32:
    let HashMap@(string, i32) ages = HashMap.new()

    ages.insert("Arthur", 42)
    ages.insert("Ford", 200)
    ages.insert("Trillian", 30)

    let Maybe@(i32) age = ages.get("Arthur")
    match age:
        Maybe.Some(a) -> println("Arthur is {a}")
        Maybe.None() -> println("Not found")

    return 0
```

**Implementation Details**:
- **Open addressing**: Uses linear probing instead of chaining, giving better cache locality
- **Automatic resizing**: Grows at 0.75 load factor to maintain performance
- **Power-of-two capacity**: Allows fast modulo using bitwise AND operations
- **Auto-derived hashing**: a key needs a hash AND an equality test. A primitive, a `string`,
  and a struct or enum built from them are keys, and so is a `List@(T)` of a key type. The
  equality is the predefined `Eq`; a type with no equality (a function value, a `ptr`) is not a
  key unless it implements `Eq` ([`CE2055`](error-catalog.md#ce2055))

**HashMap methods**: `.new()`, `.insert(key, value)`, `.get(key)`, `.remove(key)`, `.contains_key(key)`, `.len()`, `.keys()`, `.values()`, `.entries()`, `.debug()`, `.free()`

**Memory Management**: HashMaps use recursive destruction - when you call `.free()` or when the HashMap goes out of scope, it destroys all entries and their contents. This works correctly even for complex value types like structs containing arrays or nested enums.

**Limitations**: A key needs a hash (auto-derived for most types) and an equality test. See
[Key Requirements](stdlib/collections/hashmap.md#key-requirements).

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

    return 0
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
    Working(i32)
    Done()

fn main() i32:
    let Status current = Status.Working(75)

    match current:
        Status.Ready() ->
            println("Ready to start")
        Status.Working(progress) ->
            println("Progress: {progress}%")
        Status.Done() ->
            println("Completed")

    return 0
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
- **Error types**: declare them with `error` in place of `enum`, so they can be the `E` of a `Result`
- **Optional complex data**: Use `Maybe@(T)` (which is an enum) for values that might not exist
- **Algebraic data types**: Build sophisticated recursive data structures

## Tuples

A tuple groups two or more values with no names. It is the shape for a function that
returns more than one value:

```sushi
fn min_max(i32[] xs) (i32, i32):
    let i32 low = xs[0]
    let i32 high = xs[0]
    foreach(x in xs.iter()):
        if (x < low):
            low := x
        if (x > high):
            high := x
    return (low, high)

fn main() i32:
    let i32[] values = from([4, 8, 15, 16, 23, 42])
    let (low, high) = min_max(values)       # a destructure: one name per element
    println("{low} to {high}")              # 4 to 42

    let (i32, string) answer = (42, "the answer")
    println(answer.1)                       # an element read: .0, .1, ...
    answer.0 := 43                          # an element write
    println("{answer}")                     # (43, "the answer")
    return 0
```

**Key features**:
- **The type and the literal** are both written in parentheses: `(i32, string)` and
  `(42, "Arthur")`. A tuple type stands wherever a type does: a parameter, a field, a type
  argument (`List@((string, i32))`), an array element, and a return with a channel
  (`fn divmod(i32 a, i32 b) (i32, i32) | MathError`)
- **Element access** is `.0`, `.1`, ... and `.0.1` for a nested tuple. The index is a
  literal, so `t[i]` is not a tuple access
- **A destructure** takes a typed binder (`i32 q`), a bare binder (`q`), a `_` that
  discards, or a nested destructure: `let ((a, b), c) = nested()`
- **Ownership**: each binder OWNS its element when the value is owned (a temporary, or a
  local that the destructure spends), and BORROWS it when the value is a borrow (a
  parameter or a field). So a destructure takes two owned strings out of one value, which
  two field reads cannot do
- **Comparison, hashing and printing** come from the elements: `==` compares element by
  element, `<` is lexicographic, and a tuple of hashable elements is a `HashMap` key

**When to use a struct instead**: a value whose parts have a meaning (a point, a file
status, a configuration) is a struct, because a struct names its fields. There are no
named tuple elements, and there is no tuple constant.

A `foreach` destructures each item, and a tuple on the left of `:=` rebinds more than one
place at once:

```sushi
fn main() i32:
    let (string, i32)[] scores = from([("arthur", 42), ("ford", 7)])
    foreach((name, score) in scores.iter()):     # one name per element of each item
        println("{name}: {score}")

    let i32 a = 0
    let i32 b = 1
    foreach(_ in 0..10):
        (a, b) := (b, a + b)                     # the right side first, then a, then b
    println(a)                                   # 55
    return 0
```

- **A `foreach` destructure** follows the rules of a `let` destructure. The binders of a
  borrowed item (`.iter()`) borrow, and the binders of an owned item (a `next()` iterator)
  own
- **A destructuring rebind** takes any tuple value on the right. Each target is a place
  that `:=` takes: a name, a field, an array element or a tuple element. The whole right
  side is evaluated first, so `(a, b) := (b, a)` is a swap, and an owning swap moves the
  values and frees nothing

A `match` reads a tuple with a tuple pattern (see [Tuple patterns](#tuple-patterns)).

The standard library answers a tuple where a function has two results. A destructure takes
them apart at the call:

```sushi
use <math>
use <collections/strings>
use <collections/iter>

fn main() i32:
    let (i32 q, i32 r) = divmod(17, 5).realise((0, 0))
    println("{q} {r}")                                   # 3 2
    let (key, value) = "port=8080".split_once("=").realise(("", ""))
    println("{key}: {value}")                            # port: 8080
    let List@(string) names = List.new()
    names.push("Arthur")
    names.push("Ford")
    foreach((i, name) in names.enumerate().iter()):
        println("{i} {name}")                            # 0 Arthur, then 1 Ford
    return 0
```

The others are `split` and `split_extension` in `<io/path>`, `zip`, `partition` and
`unzip` in `<collections/iter>`, `.pairs()` on a `HashMap`, `TcpListener.accept()` and
`TcpStream.peer()` in `<net/tcp>`, `BufReader.into_parts()` in `<io/buf>`,
`decode_prefix` in `<encoding/msgpack>` and `inflate_raw_prefix` in `<compression/zlib>`.

## Pattern Matching

Pattern matching is Sushi's way of deconstructing enums and handling different cases. The compiler enforces **exhaustiveness checking** - you must handle all possible variants, ensuring you never forget a case.

### Match Expressions

```sushi
enum Response:
    Success(i32)
    Error(string)

fn handle(Response resp) ~:
    match resp:
        Response.Success(code) ->
            println("Success code: {code}")
        Response.Error(msg) ->
            println("Error: {msg}")


fn main() i32:
    handle(Response.Success(200))
    handle(Response.Error("Not found"))
    return 0
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
use <io/fs>

fn handle_file(Result@(File, IoError) result) ~:
    match result:
        Result.Ok(_) ->
            println("File opened successfully")
        Result.Err(IoError.NotFound) ->
            println("File not found")
        Result.Err(IoError.PermissionDenied) ->
            println("Permission denied")
        Result.Err(_) ->
            println("Other error")


fn main() i32:
    handle_file(open("/no/such/file", FileMode.Read()))
    return 0
```

**Nested pattern matching**: The pattern `Result.Err(IoError.NotFound)` matches a `Result@(File, IoError)` whose `Err` variant contains the `IoError` variant `NotFound`. This lets you handle specific error combinations without nested match statements. A nested pattern must name the enum that the value really holds: `FileError.NotFound` here is [`CE2107`](error-catalog.md#ce2107), because `open()` answers `IoError`.

**Integer patterns**: a `match` on an integer takes literal arms, and a trailing `_` arm is required ([`CE2074`](error-catalog.md#ce2074)):

```sushi
fn describe(i32 n) string:
    match n:
        0 -> return "zero"
        42 -> return "the answer"
        _ -> return "a number"

fn main() i32:
    println(describe(42))
    return 0
```

**String patterns**: a `match` on a `string` takes string literal arms, in double quotes or in single quotes. A trailing `_` arm is required ([`CE2074`](error-catalog.md#ce2074)). The match compares bytes: the case counts, and a prefix does not match. The arms are tested in source order, and the first arm that matches runs:

```sushi
fn command(string cmd) string:
    match cmd:
        "start" -> return "starting"
        "stop" -> return "stopping"
        'status' -> return "all systems go"
        _ -> return "unknown command: {cmd}"

fn main() i32:
    println(command("status"))
    println(command("Stop"))
    return 0
```

A string pattern cannot hold an interpolation hole: `"{x}" ->` is [`CE2123`](error-catalog.md#ce2123). A named constant is not a pattern, and one arm holds one literal.

**Literals inside a pattern**: an integer literal and a string literal are also legal in a payload (`Maybe.Some(0)`, `Maybe.Some("--help")`) and in a tuple element (`(0, n)`, `("go", dir)`).

**Wildcard patterns**: The `_` pattern matches anything, acting as a catch-all for remaining cases. It's useful for handling "all other errors" or "default" cases.

**Every value and every arm**: one checker reads every match, nested patterns included. A value that no arm matches is [`CE2040`](error-catalog.md#ce2040), and the message names the missing pattern, for example `Maybe.Some(Color.Green)`. An arm that the arms above it already cover can never run, and that is the error [`CE2118`](error-catalog.md#ce2118):

<!-- docs-sweep: error CE2118 -->
```sushi
enum Color:
    Red
    Green

fn name(Maybe@(Color) m) i32:
    match m:
        Maybe.Some(_) -> return 1
        Maybe.Some(Color.Red) -> return 2      # CE2118: the arm above matches it first
        Maybe.None -> return 0

fn main() i32:
    return name(Maybe.None)
```

**Zero-cost compilation**: Pattern matching compiles to efficient jump tables or switch statements. There's no runtime overhead compared to hand-written if-else chains or switch statements in C. A string match is the exception: it compares the value with each arm in turn, as an if-else chain does.

### Binding Modes

A payload binding says what it does with the value, and it says it the way a parameter
does. A bare binding BORROWS -- it reads, and it is the case almost every arm wants.

```sushi
enum Box:
    Rows(i32[])
    Empty

fn take(nom i32[] xs) ~:
    println("took {xs.len()}")

fn make() Box:
    return Box.Rows(from([1, 2, 3]))

fn main() i32:
    match make():
        Box.Rows(r) -> println("read {r.len()}")     # a borrow: read only
        Box.Empty -> println("empty")

    match make():
        Box.Rows(poke r) -> r.push(9)                # a pointer into the payload
        Box.Empty -> println("empty")

    match make():
        Box.Rows(nom r) -> take(nom r)               # the arm owns it now
        Box.Empty -> println("empty")

    return 0
```

**Taking needs a scrutinee the match owns.** `make()??` is a temporary: nothing else will
ever free it, so the match may give it away. A local is different -- `match r:` leaves `r`
the owner, and taking out of it would be a double free. Say `match nom r:` and the local
is handed over, exactly as `take(nom r)` hands it over; `r` may not be read afterwards.

**An arm takes the variant whole.** If one binding in an arm is `nom`, every other owning
payload of that variant must be `nom` as well -- what stops the match freeing the value is
the whole scrutinee, not one slot of it.

### Tuple Patterns

A tuple pattern matches each element of a tuple. Its items are the items of a payload: an
enum pattern, an integer literal, a string literal, a binding in one of the three modes, a
`_`, or another tuple pattern. With a tuple literal as the scrutinee, a `match` reads two values at once:

```sushi
enum Light:
    Red
    Green

enum Event:
    Timer
    Button

fn next(Light light, Event event) Light:
    match (light, event):
        (Light.Red, Event.Timer) -> return Light.Green
        (Light.Green, Event.Timer) -> return Light.Red
        (l, Event.Button) -> return l

fn main() i32:
    match next(Light.Red, Event.Timer):
        Light.Green -> println("green")
        Light.Red -> println("red")
    return 0
```

`match (light, event):` builds no tuple. Each element is read once and matched in place,
with the rules of a named scrutinee, so `light` and `event` are still usable after the
match. `match nom (a, b):` hands each element to the match, and then a `nom` binding may
take it.

## Generics

Sushi's generics system provides zero-cost abstraction through compile-time monomorphization. Generic types are instantiated automatically based on usage, generating specialized code for each concrete type combination.

### Generic Structs

```sushi
struct Pair@(T, U):
    T first
    U second

fn main() i32:
    let Pair@(i32, string) data = Pair(first: 42, second: "answer")

    println("Number: {data.first}")
    println("Label: {data.second}")

    return 0
```

**How it works**: The compiler detects that you use `Pair@(i32, string)` and generates a specialized version of the struct for that type combination. There's no runtime overhead - the generated code is as efficient as if you'd written a separate struct manually.

**A generic body is checked where it is written**: the compiler checks the body of a generic function, extension or perk implementation one time, also when nothing calls it. A type parameter is opaque there. The body can hold, move and pass on a `T`; a method, an operator, a hole or `.clone()` on it needs a constraint that promises it (`@(T: Eq)` for `==`, `Ord` for `<`, `Display` for `"{x}"`, `Hashable` for `.hash()`, `Clone` for `.clone()`, or a perk of your own). Arithmetic on a `T` is refused. A fault is reported at the template, and a type argument that does not satisfy a constraint is an error at the call ([CE4006](error-catalog.md#ce4006)). See [Generics](generics.md#a-template-is-checked-where-it-is-written) and [Checked generics](design/checked-generics.md).

```sushi
fn largest@(T: Ord)(nom T a, nom T b) T:
    if (a > b):         # Ord promises '>'
        return a
    return b

fn main() i32:
    println("{largest(nom 3, nom 9)} {largest(nom 'Arthur', nom 'Ford')}")
    return 0
```

**Generic Nesting**: Sushi fully supports nested generics like `Result@(Maybe@(T))`, `List@(Pair@(K, V))`, or `HashMap@(string, List@(i32))`. The type system correctly handles arbitrarily deep nesting.

### Extension Methods

Extension methods let you add functionality to existing types without modifying their definitions -- the same idea as extension methods in C#, Kotlin, or Swift. In Sushi, this is called **UFCS (Uniform Function Call Syntax)**.

```sushi
extend i32 squared() i32:
    return self * self

extend string shout() string:
    return "{self}!!!"

fn main() i32:
    let i32 x = 7
    println("Squared: {x.squared()}")

    let string msg = "Don't Panic"
    println(msg.shout())

    return 0
```

**How Extension Methods Work**:
1. You define a function with `extend <type> <method_name>(...) <return_type>`
2. The first implicit parameter is `self`, referring to the value you're calling the method on
3. The call `x.squared()` is transformed at compile-time into `squared(x)`
4. This transformation is zero-cost - there's no runtime overhead

**Static methods -- a method with no receiver**: put `static` before the method name and
the method is called on the TYPE, not on a value. This is how a type carries its own
constructor.

```sushi
struct Vec:
    i32 x
    i32 y

extend Vec static at(i32 x, i32 y) Vec:
    return Vec(x, y)

extend Vec length_squared() i32:
    return self.x * self.x + self.y * self.y

fn main() i32:
    let Vec v = Vec.at(3, 4)
    println("{v.length_squared()}")
    return 0
```

`Vec.at(3, 4)` reads like `List.new()` and `HashMap.new()`, and it is the same rule: a
name behind a type's dot is a member of that type. A static has no `self` -- naming one
in the signature or in the body is [CE0134](error-catalog.md#ce0134) -- and everything else about it is an ordinary
method: the parameter modes, the owning return, the `| E` channel, and no visibility
marker of its own.

`new` is a legal static name (`extend Box static new(i32 n) Box:`), which is one thing a
free function cannot be called.

**No `??` in a BARE extension body**: an extension method with no `| E` is bare, as a
function with no `| E` is. It returns a bare value, not a `Result@(T, E)` (a `Result.Ok(...)`
return is [CE2091](error-catalog.md#ce2091)). A bare body has no error channel, so `??` has nothing to propagate into
and is rejected with [CE0131](error-catalog.md#ce0131).
Handle the Result in the body instead -- match on it, or use `.realise(default)`:

```sushi
extend i32 tagged() i32:
    return tag(self).realise(0)   # tag() writes `| StdError`, so it returns Result@(i32, StdError)
```

The same rule applies to perk implementation methods. A `??` inside a LAMBDA in such a
body is legal when the lambda's type writes `| E` -- the lambda then has its own Result
channel. A lambda never takes a channel from its body:

```sushi
extend i32 fixed() i32:
    let fn(i32) -> i32 | StdError f = |i32 x| tag(x)??   # legal: propagates into the lambda's Result
    return f(self).realise(0)
```

**The opt-in error channel `| E`**: a method that declares `| E` after its return type
HAS a channel — the call yields `Result@(T, E)`, `??` is legal in the body, and
a body that returns spells both constructors, as a free function does:
`return Result.Ok(x)` and `return Result.Err(e)` (a `~` success is
`return Result.Ok(~)`). The compiler wraps nothing: a bare `return x` in a channel body
is [CE2030](error-catalog.md#ce2030).

```sushi
error OddError:
    TooOdd

extend i32 half_checked() i32 | OddError:
    if (self % 2 == 1):
        return Result.Err(OddError.TooOdd)
    return Result.Ok(self / 2)

fn use_it() i32 | OddError:
    let i32 four = 4
    let i32 half = four.half_checked()??   # the call yields Result@(i32, OddError)
    return Result.Ok(half)

fn main() i32:
    println("{use_it().realise(-1)}")
    return 0
```

A channel method stops a chain until it is handled: `b.checked().other()` is [CE2515](error-catalog.md#ce2515),
and `b.checked()??.other()` is the fix. Methods on the wrapper itself (`.realise`)
stay legal.

A **perk method** takes the channel the same way, and the perk states it in the
CONTRACT so every implementation answers the same error type:

```sushi
error SourceError:
    Closed

perk Source:
    fn read_one() i32 | SourceError

struct Counter:
    i32 value
    bool closed

extend Counter with Source:
    fn read_one() i32 | SourceError:
        if (self.closed):
            return Result.Err(SourceError.Closed)
        return Result.Ok(self.value)

fn main() i32:
    let Counter c = Counter(42, false)
    println("{c.read_one().realise(0)}")
    return 0
```

The contract and the implementation must agree. A contract that declares a channel and
an implementation that omits it, an implementation that declares one the contract has
not got, and two channels over different error types are all [CE0133](error-catalog.md#ce0133), which points at
both ends.

**Array extension targets**: a concrete element extends one array type
(`extend i32[] sum()`); a bare undeclared name binds a type parameter, so
`extend T[]` applies to every element type. Anything else in the element position is
[CE2101](error-catalog.md#ce2101).

**Method-level type parameters**: a method may declare its own `@(U)` after its name,
solved from the arguments at each call:

```sushi
extend i32 pick@(U)(nom U a, nom U b) U:
    if (self > 0):
        return a
    return b

fn main() i32:
    let i32 plus = 1
    println("{plus.pick(nom 7, nom 9)}")     # U = i32, from the arguments
    return 0
```

There is no call-site `@(...)` on a method, so every method-level parameter must be
solvable from the arguments — a bare-param lambda cannot be ([CE2063](error-catalog.md#ce2063); annotate it:
`|i32 x| ...`). A method-level name that repeats a receiver parameter is [CE2064](error-catalog.md#ce2064).

**Generic Extension Methods**:
```sushi
struct Box@(T):
    T value

extend Box@(T) unwrap(nom self) T:
    return nom self.value   # a T moves, so the method takes the box: nom self

fn main() i32:
    let Box@(i32) b = Box(42)
    let i32 value = b.unwrap()  # Uses generic extension; b is spent
    println("Unwrapped: {value}")
    return 0
```

Extension methods can be generic over generic types, user-defined and built-in alike: the type parameter (`T`) is declared on the receiver type (`Box@(T)`, `List@(T)`), and the compiler instantiates the method for each concrete type used in your program.

**Bounds in the target**: a generic extension inherits every bound that its type declares (on `struct Keyed@(K: Hashable)`, the body of `extend Keyed@(K)` can call `.hash()` on a `K`; `extend HashMap@(K, V)` gets `K: Hashable + Eq`). The target can ADD a bound: `extend Box@(T: Display) describe()`, or `extend (T: Clone)[] second()` for an array target. A receiver whose type argument does not satisfy the added bound is [CE4006](error-catalog.md#ce4006) at the call. A bound is legal only at the top level of an `extend` target ([CE6110](error-catalog.md#ce6110) elsewhere), and a bound on a name that is a type is [CE2124](error-catalog.md#ce2124).

```sushi
struct Box@(T):
    T value

extend Box@(T: Display) describe() string:
    return "Box holding {self.value}"

fn main() i32:
    let Box@(i32) b = Box(42)
    println(b.describe())
    return 0
```

**A concrete type argument in the target is a constraint.** `extend Box@(i32)` extends `Box@(i32)` and nothing else, so one method name can serve several instantiations with a body written for each:

```sushi
extend Box@(i32) tag() i32:
    return self.value * 10

extend Box@(string) tag() i32:
    return self.value.len()     # needs `use <collections/strings>`
```

Two rules come with that. A **template and a concrete target for the same method name** overlap, and Sushi rejects the overlap rather than picking the more specific one — there is no specialization:

```sushi
extend Box@(T) tag() i32: ...
extend Box@(i32) tag() i32: ...    # error [CE0101]: duplicate function
                                   #   'extension method 'tag' for 'Box@(i32)''
```

And a **partially concrete target** is rejected too — name every type parameter, or make every argument concrete:

```sushi
extend Pair@(i32, U) tag() i32: ...  # error [CE2098]: extension target 'Pair@(i32, U)'
                                     #   mixes concrete type arguments with type parameters
```

To give one instantiation its own behaviour where a template already covers it, implement a perk on the concrete target — a perk implementation outranks extension methods (see below).

**Benefits**:
- **Namespace organization**: Group related functionality with the types they operate on
- **Discoverability**: Methods appear natural on the type, making APIs easier to explore
- **Chainability**: Method syntax enables fluent chaining: `words.get(0).realise("none")`
- **Zero cost**: Compiles to the same code as a regular function call

**You cannot override a built-in method**: a method the compiler defines is always chosen before an extension method of the same name, so an extension that collides with one would be compiled and then never called. Sushi rejects it outright rather than letting it sit there looking like it works:

```sushi
extend i32 hash() u64:      # error [CE2097]: extension method 'hash()' conflicts
    return 1 as u64         #                 with the built-in 'i32.hash()'
```

This covers every built-in family: the `hash()` and `clone()` the compiler derives for every struct and enum, the primitive and string methods (`to_str`, `to_bits`, `len`, `trim`, ...), the array methods, and the methods of `Result`, `Maybe`, `Own`, `List` and `HashMap`. Pick a different name, or use a perk.

**Perks are the way to replace a built-in**: a perk implementation takes precedence at every layer, by design. `Hashable` is predefined -- it is the contract of the derived `hash()`, and every type with a derived hash satisfies it -- so the implementation below is the override and nothing is declared.

```sushi
struct Point:
    i32 x
    i32 y

extend Point with Hashable:       # allowed -- this is the supported override
    fn hash() u64:
        return 999999 as u64
```

**Standard Library Use**: much of the Sushi standard library is exposed through this same method-call syntax. String methods like `.len()`, `.find()` and `.split()`, the collection methods on `List@(T)`, and the array methods all reach you as `receiver.method(...)`. They are built-in providers rather than ordinary extensions, though, which is why the names above are reserved -- your own extensions live alongside them, not on top of them.

The full precedence chain and its rationale are in [docs/design/method-resolution.md](design/method-resolution.md).

## Units and Imports

Each source file is a unit. `use` brings another unit's names into this one, and every
`use` stands at the top of the file, above the first declaration:

```sushi
use "helpers/geometry"      # another unit of this program
use <collections/hashmap>   # a standard-library module
```

By default an import puts the names it brings straight into this unit's scope, so you
write them bare. Add `as NAME` and they go behind a dot instead:

<!-- docs-sweep: skip (two units) -->
```sushi
use "helpers/geometry" as geo

fn main() i32:
    let f64 area = geo.circle_area(2.0).realise(0.0)
    println("{area} {geo.MAX_SIDES}")
    return 0
```

Reach for the alias when two units disagree about a name, or when the reader of a call
should be able to see where the name came from. The two forms compose: import one unit
flat and another behind a dot, and each brings what it says and no more.

The dot works wherever a name is written, not only in front of a call. A type, a
constructor, a match arm and a perk constraint all take one:

<!-- docs-sweep: skip (two units) -->
```sushi
use "helpers/geometry" as geo

fn describe(geo.Vec v) string:
    match geo.Sign.Plus:
        geo.Sign.Plus -> return "north of {v.y}"
        geo.Sign.Minus -> return "south of {v.y}"

fn main() i32:
    let geo.Vec here = geo.Vec(1, 2)
    let string where = describe(here)
    println(where)
    return 0
```

An interpolation hole cannot hold a string literal, and a `{` in a double-quoted string always
opens a hole. Put the value in a `let` first, as above. For a literal brace, use a
single-quoted string, which does not interpolate.

One place refuses it: a fixed array's size. `i32[geo.SIZE]` is an error, because the
size is read while your file is being parsed and an alias is bound long after that.
Declare the constant in your own unit and name it bare.

An alias behaves like any other name in the unit. A local variable of the same name
shadows it, one name cannot hold two namespaces, and the alias is yours alone -- a unit
that imports yours never sees it. Privacy holds behind the dot: `geo.helper` where `helper` is
private to `geometry` is an error that says so, not one that says the name does not
exist.

Two units may export one name and the program still builds. It only becomes a problem
where you write that name bare with nothing to say which one you mean, and then the
compiler stops at that line and lists the candidates. Your own unit's declaration always
wins, so it is never the ambiguous one -- and that is also why `use <math>` does not
take `sin` away from a unit that declares a `sin` of its own.

### What an import reaches

Your unit sees what it declares and what its own `use` statements bring. Nothing else.
An import is not passed on: if the unit you imported imported something in its turn, that
something is its business and not yours. Write the `use` yourself and you have the name.

That holds for every kind of import. A standard-library module is in the scope of the
file that wrote `use <math>`; the built-in `HashMap` is a name in the file that wrote
`use <collections/hashmap>`; and an `unsafe external` block binds its namespace in the
file that declares it. It also holds behind the dot: `geo.circle_area` reaches what
`geometry` **declares**, never what `geometry` imported.

One consequence is worth knowing before it surprises you. A function you can call may
return a type you cannot write:

<!-- docs-sweep: skip (three units) -->
```sushi
# geometry.sushi        # shapes.sushi            # main.sushi
public struct Vec:      use "geometry"            use "shapes"
    f64 x               public fn origin() Vec:   fn main() i32:
                            return Vec(...)           let Vec v = origin()
```

`main` may call `origin()`, because `origin` is in scope. It may not write `Vec` until it
imports `geometry` too -- and it cannot avoid writing it, because a `let` needs a type.
So the rule is short: **to name a type, import the unit that declares it.** The compiler
says which import is missing.

The unit in the middle can spare its importers that line. `public use` re-exports: it
takes what an import brings and hands it on as the unit's own.

<!-- docs-sweep: skip (three units) -->
```sushi
# geometry.sushi        # shapes.sushi                  # main.sushi
public struct Vec:      public use "geometry"          use "shapes"
    f64 x               public fn origin() Vec:        fn main() i32:
                            return Vec(...)                let Vec v = origin()
```

Now `shapes` says "whoever imports me gets `geometry` too", and `main` writes `Vec` with
one import; `use "shapes" as sh` gives `sh.Vec` beside `sh.origin`. Only a `public use`
does this -- a plain `use` stays private to the unit that wrote it -- and only the public
names travel. A re-exported name behaves like any imported one: your own declaration wins
over it, and two re-exports that offer different declarations of one name stop at the
bare use with the candidates listed. The standard library leans on it: `use <io/fs>` alone
brings `IoError`, because the module whose calls answer it hands the name on.

## Memory Management

Sushi provides memory safety without garbage collection through a combination of RAII (Resource Acquisition Is Initialization), compile-time borrow checking, and move semantics. These features work together to prevent common memory bugs while maintaining C-like performance.

### Borrowing, aka References

A parameter never takes ownership unless it says `nom`, so a plain `f(x)` already leaves `x` yours.
`peek` and `poke` go further: they pass a **pointer** instead of the value.

- **`peek T`** - Read-only borrow by pointer (multiple allowed simultaneously)
- **`poke T`** - Read-write borrow by pointer (exclusive access), so the callee's writes reach the
  caller's value

Reach for them when the callee must write back (`poke`), or when copying the value would be
expensive — a large fixed array or plain struct.

Sushi's borrow checker ensures that references are always valid and that aliasing rules are enforced at compile time:

```sushi
fn increment(poke i32 counter) ~:
    counter := counter + 1

fn display(peek i32 value) ~:
    println("Value: {value}")

fn main() i32:
    let i32 count = 0
    increment(poke count)
    increment(poke count)
    display(peek count)  # Read-only access
    println("Count: {count}")  # Prints: Count: 2

    return 0
```

**Borrowing Rules**:
- **Multiple `peek` allowed**: You can have multiple read-only borrows of the same variable simultaneously
- **One `poke` at a time**: Mutable borrows require exclusive access
- **Cannot mix modes**: Cannot have `peek` and `poke` borrows of the same variable at the same time
- **`poke` coerces to `peek`**: A mutable reference can be passed where a read-only reference is expected
- **References must be valid**: The borrow checker ensures references never outlive the data they point to
- **Zero cost**: References compile to simple pointers with no runtime overhead

**What references allow**:
- Reading function arguments without copying (use `peek`)
- Modifying function arguments in-place without copying large structures (use `poke`)
- Building efficient data structures that reference existing data
- Avoiding expensive clones when you just need to read or modify a value

**Example - avoiding copies**:
```sushi
struct LargeData:
    i32[1000] values

fn process(poke LargeData data) ~:
    # Can access and modify data.values without copying 4000 bytes
    data.values[0] := 42

fn read_only(peek LargeData data) i32:
    # Read-only access - cannot modify
    return data.values[0]
```

**Reference bindings**: `let poke T x = <place>` and `let peek T x = <place>` bind a pointer into
a place (a local, a field or index chain off one, or `Own@(T).get()`). The binding lives until the
end of its block. While it lives, the owner is frozen ([`CE2412`](error-catalog.md#ce2412)), and only one `poke` binding can
exist at a time ([`CE2403`](error-catalog.md#ce2403)). A write through a `peek` binding is [`CE2408`](error-catalog.md#ce2408):

```sushi
struct Ship:
    i32 crew

fn main() i32:
    let Ship[] fleet = from([Ship(3), Ship(5)])
    if (true):
        let poke Ship s = fleet[1]
        s.crew := 42
    println(fleet[1].crew)       # 42
    return 0
```

The borrow checker runs at compile time (the `borrow` pass of the semantic analysis pipeline), so there's no runtime cost to these safety guarantees.

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

fn main() i32:
    process()
    return 0
```

**How RAII works in Sushi**:
1. When a variable goes out of scope (end of function, end of block, error propagation with `??`), its destructor runs
2. For structs, the destructor recursively destroys each field
3. For arrays, the destructor recursively destroys each element
4. For enums, the destructor examines the discriminant and destroys the active variant's data
5. Primitives require no cleanup. A `string` owns heap and is freed like an array; the one
   exception is a string bound directly from a literal, which owns no heap
6. A type that implements `Drop` runs its `drop()` first, then its fields are destroyed
7. At the end of a scope, the locals are destroyed in reverse declaration order

**The Recursive Destructor**: Sushi's backend implements a general `emit_value_destructor()` function that handles cleanup for all types. It recursively traverses your data structures:
- **Structs with arrays**: The array fields are freed, then the struct itself
- **Arrays of structs**: Each struct is destroyed, then the array storage is freed
- **Enums with complex data**: The discriminant determines which variant is active, then that variant's data is cleaned up
- **Nested collections**: `List@(HashMap@(string, List@(i32)))` correctly frees all levels

**The `Drop` perk**: a type that owns something no field walk can see (a file descriptor, a
socket) implements the predefined perk `Drop` with `fn drop(poke self) ~`. Such a type owns a
resource: it moves, `.clone()` on it is [`CE2431`](error-catalog.md#ce2431), and only the unit that declares the type may
implement `Drop` for it ([`CE4012`](error-catalog.md#ce4012)). No unit declares a built-in type such as `i32[]`, so
`Drop` on one is [`CE4016`](error-catalog.md#ce4016):

```sushi
struct Guard:
    string name

extend Guard with Drop:
    fn drop(poke self) ~:
        println("drop {self.name}")

fn main() i32:
    let Guard a = Guard(name: "first")
    let Guard b = Guard(name: "second")
    println("end of main")
    return 0
```

This prints `end of main`, then `drop second`, then `drop first`. See
[Memory Management](memory-management.md).

**Integration with error handling**: RAII is crucial for the `??` operator. When an error is propagated, all variables in the current scope are destroyed before returning, preventing resource leaks even in error paths.

**Benefits**:
- No manual memory management - no `free()` calls needed (except for explicit `.free()` on collections when you want early cleanup)
- No memory leaks - resources are always freed when they go out of scope
- Exception safety - errors can't cause resource leaks
- Deterministic cleanup - you know exactly when destructors run

### Own@(T) for Heap Allocation

`Own@(T)` is Sushi's type for heap-allocated, owned values. It's essential for building recursive data structures like linked lists and trees, where a struct needs to contain an optional instance of itself:

```sushi
struct Point:
    i32 x
    i32 y

# Recursive struct: the optional next pointer is Maybe@(Own@(Node))
struct Node:
    i32 value
    Maybe@(Own@(Node)) next

fn main() i32:
    # Allocate a Point on the heap, then dereference it
    let Own@(Point) ptr = Own.alloc(Point(10, 20))
    let Point loaded = ptr.get()
    println("Point: ({loaded.x}, {loaded.y})")

    # A linked-list node; next is an optional owned pointer
    let Node head = Node(1, Maybe.None())
    println("Head: {head.value}")

    if (head.next.is_some()):
        println("List has more nodes")
    else:
        println("End of list")

    return 0
```

**Why Own@(T) exists**: Without `Own@(T)`, you cannot create recursive types because the compiler needs to know the size of every struct at compile time. A `Node` containing another `Node` would have infinite size. `Own@(T)` breaks the cycle by storing a pointer (fixed size) to heap-allocated data.

**Own@(T) methods**:
- `Own.alloc(value)` - Allocates `value` on the heap and returns an `Own@(T)`
- `.get()` - Returns the contained value
- `.destroy()` - Explicitly frees the heap memory (RAII calls this automatically)

`Own@(T)` itself is always populated. To model an *optional* owned pointer (such as the `next` field of a list node), wrap it in `Maybe@(Own@(T))` and use `Maybe.Some(...)` / `Maybe.None()` together with `.is_some()` / `.is_none()`.

**Memory management with Own@(T)**: `Own@(T)` integrates with RAII - when an `Own@(T)` goes out of scope, it automatically calls `.destroy()` on the contained value, freeing the heap memory. This means recursive structures are properly cleaned up when they go out of scope, even if they're deeply nested.

**Common patterns**:
- **Linked lists**: Each node owns the next node
- **Trees**: Each node owns its children
- **Optional heap data**: Use `Own@(T)` instead of nullable pointers for optional heap-allocated data

## Files and Handles

A file is a value. `open()` answers a `File` that OWNS its operating-system descriptor:
it moves to exactly one owner, and when that owner leaves scope the descriptor closes.
There is nothing to remember and nothing to pair.

```sushi
use <io/fs>

fn log_it(string message) ~ | IoError:
    let File f = open("out.log", FileMode.Append())??
    f.writeln(message)??
    return Result.Ok(~)
    # f drops here, and the descriptor closes.

fn main() i32:
    match log_it("Mostly Harmless"):
        Result.Ok(_) -> println("logged")
        Result.Err(_) -> println("could not log")
    return 0
```

Three rules follow from the ownership, and each one is a compile error rather than a
run-time surprise:

- **A handle cannot be copied.** `.clone()` on a `File` is [`CE2431`](error-catalog.md#ce2431): a field-by-field
  copy would duplicate the descriptor number and leave two owners that both close it.
  `.share()` is the operation that means "a second handle", and it says so: it is
  `dup(2)`, so both handles sit over one open file description and one offset. For
  concurrent reads of one file, `read_at()` and `write_at()` take the offset as an
  argument and share nothing.
- **`close()` CONSUMES the handle.** Use it only where the failure has to be SEEN -- a
  destructor cannot answer a `Result`. A read after a close is [`CE2435`](error-catalog.md#ce2435) while compiling.
- **The channel is `IoError` from the open to the last read**, so one `??` chain covers
  the whole function with no conversion in the middle.

### What a handle can do

`stdin`, `stdout` and `stderr` are `File` unit variables (`public var`, storage with an
address) over descriptors 0, 1 and 2, so a function that takes a `poke File` takes either
a file or the console. Better still, a function can name the CAPABILITY it needs instead
of the type. `<io/contracts>` declares three perks -- `Reader`, `Writer` and `Seek` --
whose methods take `poke self`, and a `TcpStream` or a `BufWriter@(File)` satisfies the
first two just as a `File` does:

```sushi
use <io/fs>

fn emit@(W: Writer)(poke W dst, string line) ~ | IoError:
    dst.write(line)??
    dst.flush()??
    return Result.Ok(~)

fn main() i32:
    match emit(poke stdout, "Mostly Harmless\n"):
        Result.Ok(_) -> return 0
        Result.Err(_) -> return 1
```

### Buffering is a type you opt into

A handle does NOT buffer: every read and every write is one system call. That keeps the
model honest -- there is no hidden buffer to flush at a point nobody wrote, and no
destructor swallowing the failure of a flush the program never asked for.

When a loop makes that cost matter, wrap the handle. `<io/buf>` gives `BufReader@(R)`
over any `Reader` and `BufWriter@(W)` over any `Writer`, each reading or writing one
WINDOW per system call. The wrapper TAKES the handle (`nom`), so there is never a moment
where both the handle and its buffer are usable.

`r.lines()` then takes the reader in turn and answers a line iterator that `foreach`
walks. The `??` on the binder is the short form: the item is an ordinary
`Result@(string, IoError)`, and the marker leaves the function on the first read failure
exactly as `??` does anywhere else. Drop it and the body gets the `Result` itself, which
is what lets a loop report one bad line and carry on.

```sushi
use <io/fs>
use <io/buf>

fn number_lines(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = BufReader.new(nom f, 8192)

    let i32 n = 0
    foreach(line?? in r.lines()):
        n := n + 1
        println("{n}: {line}")

    return Result.Ok(~)
    # The iterator drops here: it destroys the reader, which closes the descriptor.

fn main() i32:
    match number_lines("large.txt"):
        Result.Ok(_) -> return 0
        Result.Err(_) -> return 1
```

A `BufWriter@(W)` flushes when it drops, but a drop cannot report a failure. `finish()`
is the checked drain, and it CONSUMES the writer -- so once you have called it, nothing
can forget to flush afterwards:

```sushi
use <io/fs>
use <io/buf>

fn write_report(string path, i32 count) ~ | IoError:
    let File f = open(path, FileMode.Write())??
    let BufWriter@(File) w = BufWriter.new(nom f, 8192)
    w.write_line("Report")??
    w.write_line("Items processed: {count}")??
    w.finish()??               # the ONE drain, and its failure is seen
    return Result.Ok(~)

fn main() i32:
    match write_report("report.txt", 42):
        Result.Ok(_) -> println("written")
        Result.Err(_) -> println("could not write it")
    return 0
```

`into_inner()` is the way back out: it flushes, then hands the handle over.

## Next Steps

This guide covered the basics. For more details:

- [Language Reference](language-reference.md) - Complete syntax and semantics
- [Standard Library](standard-library.md) - All built-in types and methods
- [Error Handling](error-handling.md) - Deep dive into Result and Maybe
- [Memory Management](memory-management.md) - RAII, borrowing, and ownership
- [File Operations](stdlib/io/files.md), [Buffered I/O](stdlib/io/buf.md) and
  [I/O Contracts](stdlib/io/contracts.md) - the handle layer in full
- [Examples](examples/README.md) - Hands-on code examples

---

**Previous**: [Getting Started](getting-started.md) | **Next**: [Language Reference](language-reference.md)
