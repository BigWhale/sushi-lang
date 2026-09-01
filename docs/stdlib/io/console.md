# Console I/O

[← Back to Standard Library](../../standard-library.md)

Console input and output operations for interacting with standard streams.

## Import

```sushi
use <io/fs>
```

## Overview

`print` and `println` are statements and need no import at all.

`stdin`, `stdout` and `stderr` are ordinary **`File` constants** over descriptors 0, 1
and 2, declared in [`<io/fs>`](fs.md) -- so they carry the whole `File` surface, and a
function that takes a `File` takes either a file or the console:

```sushi
use <io/fs>

fn banner(File out) ~ | IoError:
    out.writeln("Mostly Harmless")??
    return Result.Ok(~)

fn main() i32:
    banner(stdout)
    return Result.Ok(0)
```

Two consequences worth knowing:

- **The console cannot be closed.** A constant lives in read-only memory, so
  `stdout.close()` is **CE2400** -- refused while compiling, because `close()` needs a
  mutable receiver and a constant has no frame slot to borrow.
- **The three are not typed apart.** One `File` type means `stdin.write("x")` compiles;
  it fails at run time with `EBADF`. The type used to forbid it. That is the price of a
  single handle type, and it is what makes a buffered writer over the console possible
  at all.

Every route to the console is the descriptor. `print`, `println` and `stdout.write()`
all reach descriptor 1 through one `write(2)` seam, so bytes arrive in the order the
program wrote them -- which was not true while `print` went through buffered `printf`
and a file write went straight to the kernel.

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

#### stdin.readln

Read a line from stdin (blocks until newline).

```sushi
use <io/fs>

fn stdin.readln().realise('') -> string
```

**Returns:**
- String containing the line (without newline character)

**Example:**

```sushi
use <io/fs>

fn main() i32:
    println("Enter your name:")
    let string name = stdin.readln().realise('')

    println("Hello, {name}!")

    return Result.Ok(0)
```

**Interactive prompt:**

```sushi
use <io/fs>
use <collections/strings>

fn main() i32:
    println("Enter your age:")
    let string age_str = stdin.readln().realise('')

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
use <io/fs>

fn stdin.read_bytes(i32 n) -> u8[]
```

**Parameters:**
- `n` - Number of bytes to read

**Returns:**
- Byte array of length N

**Example:**

```sushi
use <io/fs>

fn main() i32:
    println("Enter 4 bytes:")
    let u8[] nothing = from([])
    let u8[] data = stdin.read_bytes(4).realise(nothing)

    println("Read {data.len()} bytes")

    foreach(byte in data.iter()):
        println("Byte: {byte}")

    return Result.Ok(0)
```

**Binary data:**

```sushi
use <io/fs>

fn main() i32:
    # Read a fixed-size header
    let u8[] nothing = from([])
    let u8[] header = stdin.read_bytes(16).realise(nothing)

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
use <io/fs>

fn stdout.write_bytes(u8[] data) -> ~
```

**Parameters:**
- `data` - Byte array to write

**Example:**

```sushi
use <io/fs>

fn main() i32:
    let u8[] data = from([72 as u8, 101 as u8, 108 as u8, 108 as u8, 111 as u8])
    stdout.write_bytes(data)
    println("")  # Newline

    return Result.Ok(0)
```

**Output:** `Hello`

**Binary output:**

```sushi
use <io/fs>
use <collections/strings>

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
use <io/fs>

fn stderr.write_bytes(u8[] data) -> ~
```

**Parameters:**
- `data` - Byte array to write

**Example:**

```sushi
use <io/fs>
use <collections/strings>

fn main() i32:
    let string error = "ERROR: Something went wrong\n"
    let u8[] error_bytes = error.to_bytes()
    stderr.write_bytes(error_bytes)

    return Result.Ok(1)
```

**Error logging:**

```sushi
use <io/fs>
use <collections/strings>

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

### is_terminal

Ask whether a stream is attached to a terminal. This is the one method all three
streams answer.

```sushi
use <io/fs>

fn stdin.is_terminal() -> bool
fn stdout.is_terminal() -> bool
fn stderr.is_terminal() -> bool
```

**Returns:** `true` when the stream is a terminal, `false` when it is a pipe, a file, or
anything else. The answer is about THAT stream alone: a program whose output is piped
into a pager still has a terminal on `stderr`.

It returns a bare `bool` and not a `Result`. The question has no failure: a stream that
is not a terminal is the `false` answer, not an error.

**Example:** colour only when a person is reading.

```sushi
use <io/fs>

fn main() i32:
    let string green = ""
    let string reset = ""
    if (stdout.is_terminal()):
        green := "\x1b[32m"
        reset := "\x1b[0m"

    println("{green}Mostly Harmless{reset}")

    return Result.Ok(0)
```

**Output:** `Mostly Harmless` in green on a terminal, and the same words with no escape
bytes when the output is captured.

**Example:** tell an interactive run from a piped one.

```sushi
use <io/fs>

fn main() i32:
    if (stdin.is_terminal()):
        stdout.write("Enter your name: ")
        let string name = stdin.readln().realise('')
        println("Hello, {name}!")
    else:
        # Reading a piped list, so there is nobody to prompt.
        foreach(line in stdin.lines()):
            println("read: {line}")

    return Result.Ok(0)
```

### flush

Push the stream buffer to the operating system. The two writing streams answer it.

```sushi
use <io/fs>

fn stdout.flush() -> ~
fn stderr.flush() -> ~
```

**Example:**

```sushi
use <io/fs>

fn main() i32:
    stdout.write("Working... ")
    stdout.flush()  # The prompt is visible while the work runs
    stdout.write("done\n")

    return Result.Ok(0)
```

**Note:** `println()` output usually appears immediately because a terminal line-buffers. A pipe or a file does not; `flush()` forces the bytes out in every case.

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
use <io/fs>
use <collections/strings>

fn log_info(string message) ~:
    println("[INFO] {message}")
    return Result.Ok(~)

fn log_error(string message) ~:
    let string formatted = "[ERROR] {message}\n"
    let u8[] bytes = formatted.to_bytes()
    stderr.write_bytes(bytes)
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
use <io/fs>

fn main() i32:
    # This appears immediately
    println("Immediate")

    # This may be buffered
    print("Buffered")

    # Force the buffer out without a newline
    stdout.flush()

    return Result.Ok(0)
```

### stderr buffering

Standard error is unbuffered for immediate error visibility:

```sushi
use <io/fs>
use <collections/strings>

fn main() i32:
    # Appears immediately, even without newline
    let u8[] bytes = "Error".to_bytes()
    stderr.write_bytes(bytes)

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
- [String Methods](../../standard-library.md) - String manipulation for input parsing
- [Standard Library Reference](../../standard-library.md) - Complete stdlib reference
- [Error Handling](../../error-handling.md) - Result and Maybe types
