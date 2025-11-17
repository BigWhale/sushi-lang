# Console I/O

[← Back to Standard Library](../../standard-library.md)

Console input and output operations for interacting with standard streams.

## Import

```sushi
use <io/stdio>
```

## Overview

The console I/O module provides functions for reading from standard input (stdin) and writing to standard output (stdout) and standard error (stderr). All console operations are built-in and don't require explicit imports, but stdin/stdout/stderr stream methods require `use <io/stdio>`.

## Console Output

### println

Print a message with a newline.

```sushi
fn println(string message) -> ~
```

**Parameters:**
- `message` - String to print

**Example:**

```sushi
fn main() i32:
    println("Hello, World!")
    println("Multiple lines")
    println("work perfectly")

    return Result.Ok(0)
```

**String interpolation:**

```sushi
fn main() i32:
    let string name = "Arthur"
    let i32 age = 42

    println("Hello, {name}!")
    println("{name} is {age} years old")

    return Result.Ok(0)
```

### print

Print a message without a newline.

```sushi
fn print(string message) -> ~
```

**Parameters:**
- `message` - String to print

**Example:**

```sushi
fn main() i32:
    print("Loading")
    print(".")
    print(".")
    print(".")
    println(" Done!")

    return Result.Ok(0)
```

**Output:** `Loading... Done!`

**Progress indicators:**

```sushi
use <time>

fn main() i32:
    let i32 total = 10

    foreach(i in 0..total):
        print("*")
        msleep(100 as i64)??

    println("")
    println("Complete!")

    return Result.Ok(0)
```

## Standard Streams

### stdin

Read input from standard input.

#### stdin.read_line

Read a line from stdin (blocks until newline).

```sushi
fn stdin.read_line() -> string
```

**Returns:**
- String containing the line (without newline character)

**Example:**

```sushi
use <io/stdio>

fn main() i32:
    println("Enter your name:")
    let string name = stdin.read_line()

    println("Hello, {name}!")

    return Result.Ok(0)
```

**Interactive prompt:**

```sushi
use <io/stdio>

fn main() i32:
    println("Enter your age:")
    let string age_str = stdin.read_line()

    match age_str.to_i32():
        Maybe.Some(age) ->
            if (age >= 18):
                println("You are an adult")
            else:
                println("You are a minor")
        Maybe.None() ->
            println("Invalid age")

    return Result.Ok(0)
```

#### stdin.read_bytes

Read exactly N bytes from stdin.

```sushi
fn stdin.read_bytes(i32 n) -> u8[]
```

**Parameters:**
- `n` - Number of bytes to read

**Returns:**
- Byte array of length N

**Example:**

```sushi
use <io/stdio>

fn main() i32:
    println("Enter 4 bytes:")
    let u8[] data = stdin.read_bytes(4)

    println("Read {data.len()} bytes")

    foreach(byte in data.iter()):
        println("Byte: {byte}")

    return Result.Ok(0)
```

**Binary data:**

```sushi
use <io/stdio>

fn main() i32:
    # Read a fixed-size header
    let u8[] header = stdin.read_bytes(16)

    # Process header bytes
    let string text = header.to_string()
    println("Header: {text}")

    return Result.Ok(0)
```

### stdout

Write to standard output.

#### stdout.write_bytes

Write raw bytes to stdout.

```sushi
fn stdout.write_bytes(u8[] data) -> ~
```

**Parameters:**
- `data` - Byte array to write

**Example:**

```sushi
use <io/stdio>

fn main() i32:
    let u8[] data = from([72 as u8, 101 as u8, 108 as u8, 108 as u8, 111 as u8])
    stdout.write_bytes(data)
    println("")  # Newline

    return Result.Ok(0)
```

**Output:** `Hello`

**Binary output:**

```sushi
use <io/stdio>

fn main() i32:
    # Write UTF-8 encoded text
    let string text = "Hello, World!"
    let u8[] bytes = text.to_bytes()
    stdout.write_bytes(bytes)

    return Result.Ok(0)
```

### stderr

Write to standard error.

#### stderr.write_bytes

Write raw bytes to stderr.

```sushi
fn stderr.write_bytes(u8[] data) -> ~
```

**Parameters:**
- `data` - Byte array to write

**Example:**

```sushi
use <io/stdio>

fn main() i32:
    let string error = "ERROR: Something went wrong\n"
    let u8[] error_bytes = error.to_bytes()
    stderr.write_bytes(error_bytes)

    return Result.Ok(1)
```

**Error logging:**

```sushi
use <io/stdio>

fn log_error(string message) ~:
    let string formatted = "[ERROR] {message}\n"
    let u8[] bytes = formatted.to_bytes()
    stderr.write_bytes(bytes)
    return Result.Ok(~)

fn main() i32:
    log_error("Invalid configuration")??
    log_error("Failed to connect")??

    return Result.Ok(1)
```

## Combining Streams

### Redirecting output

Shell redirection works as expected:

```bash
# Redirect stdout to file
./program > output.txt

# Redirect stderr to file
./program 2> errors.txt

# Redirect both
./program > output.txt 2> errors.txt

# Redirect stderr to stdout
./program 2>&1

# Pipe stdout to another program
./program | grep "pattern"
```

### Example: Logging with levels

```sushi
use <io/stdio>

fn log_info(string message) ~:
    println("[INFO] {message}")
    return Result.Ok(~)

fn log_error(string message) ~:
    let string formatted = "[ERROR] {message}\n"
    stderr.write_bytes(formatted.to_bytes())
    return Result.Ok(~)

fn main() i32:
    log_info("Starting application")??
    log_info("Processing data")??
    log_error("Failed to open file")??
    log_info("Application finished")??

    return Result.Ok(1)
```

**Run with:**
```bash
./program > info.log 2> error.log
```

## Buffering Behavior

### stdout buffering

Standard output is line-buffered when connected to a terminal:
- `println()` flushes immediately (contains newline)
- `print()` may be buffered until newline or buffer fills

```sushi
fn main() i32:
    # This appears immediately
    println("Immediate")

    # This may be buffered
    print("Buffered")

    # Force flush with newline
    println("")

    return Result.Ok(0)
```

### stderr buffering

Standard error is unbuffered for immediate error visibility:

```sushi
use <io/stdio>

fn main() i32:
    # Appears immediately, even without newline
    stderr.write_bytes("Error".to_bytes())

    return Result.Ok(1)
```

## Unicode Support

All console operations support UTF-8 encoded text:

```sushi
fn main() i32:
    println("Hello, World! 🌍")
    println("Café")
    println("日本語")
    println("Привет")

    return Result.Ok(0)
```

## See Also

- [File Operations](files.md) - File I/O operations
- [String Methods](../../standard-library.md#string-methods) - String manipulation for input parsing
- [Standard Library Reference](../../standard-library.md) - Complete stdlib reference
- [Error Handling](../../error-handling.md) - Result and Maybe types
