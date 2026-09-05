# Console I/O

[← Back to Standard Library](../../standard-library.md)

Console input and output operations for interacting with standard streams.

## Import

```sushi
use <io/fs>
```

## Overview

`print` and `println` are statements and need no import at all.

`stdin`, `stdout` and `stderr` are ordinary **`File` unit variables** over descriptors 0,
1 and 2, declared `public var` in [`<io/fs>`](fs.md) -- so they carry the whole `File`
surface, and a function that takes a `poke File` takes either a file or the console:

```sushi
use <io/fs>

fn banner(poke File out) ~ | IoError:
    out.writeln("Mostly Harmless")??
    return Result.Ok(~)

fn main() i32:
    match banner(poke stdout):
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

The parameter is `poke` because every write and every read is a `poke self` method (the
contracts in [`<io/contracts>`](contracts.md) declare them so, for the buffered handles'
sake), and a write through a plain borrow is **CE2422**.

Three consequences worth knowing:

- **The console cannot be closed.** A unit variable is storage the program keeps and is
  never moved out of, so `stdout.close()` -- a `nom self` method -- is **CE2436**, refused
  while compiling.
- **The console can be redirected.** `stdout := open("run.log", FileMode.Write())??`
  drops the console handle (it owns nothing, so nothing closes) and every later
  `stdout.write(...)` lands in the file; `stdout := File(fd: STDOUT_FD, owned: false)`
  puts the terminal back. `println` reaches descriptor 1 directly and never sees the
  variable.
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
    println("Mostly Harmless")
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
fn File.readln() Maybe@(string) | IoError
```

**Returns:** the line without its newline, or `Maybe.None` when standard input ends. A
blank line is `Maybe.Some("")`, so pressing Return alone is not the end of the input.

**Example:**

```sushi
use <io/fs>

fn main() i32:
    println("Enter your name:")
    match stdin.readln():
        Result.Ok(Maybe.Some(name)) -> println("Hello, {name}!")
        Result.Ok(Maybe.None) -> println("Nothing to read.")
        Result.Err(_) -> println("Could not read the name.")

    return Result.Ok(0)
```

**Interactive prompt:**

```sushi
use <io/fs>
use <collections/strings>

fn main() i32:
    println("Enter your age:")
    let string age_str = ""
    match stdin.readln():
        Result.Ok(Maybe.Some(entered)) ->
            age_str := entered.clone()
        Result.Ok(Maybe.None) -> ~
        Result.Err(_) -> ~

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

Take ONE read from standard input, as bytes.

```sushi
fn File.read_bytes(i32 max) u8[] | IoError
```

**Parameters:**
- `max` - The most bytes this one read may take

**Returns:** the bytes that arrived, which may be FEWER than `max` -- a terminal hands
over a line at a time and a pipe hands over whatever has been written so far. An EMPTY
answer means the input has ended. To insist on a count, loop until you have it, or wrap
the handle in a [`BufReader`](buf.md).

**Example:**

```sushi
use <io/fs>

fn dump(i32 max) ~ | IoError:
    let u8[] data = stdin.read_bytes(max)??
    println("Read {data.len()} bytes")

    foreach(byte in data.iter()):
        println("Byte: {byte}")

    return Result.Ok(~)

fn main() i32:
    println("Enter some bytes:")
    match dump(4):
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

**Binary data:** a fixed-size header needs the loop, because one read is not a promise.

```sushi
use <io/fs>

fn read_exact(i32 count) u8[] | IoError:
    let u8[] header = from([])
    while (header.len() < count):
        let u8[] chunk = stdin.read_bytes(count - header.len())??
        if (chunk.len() == 0):
            return Result.Err(IoError.Closed)     # input ended early
        header.extend(chunk)
    return Result.Ok(header)

fn main() i32:
    match read_exact(16):
        Result.Ok(header) -> println("Header: {header.to_string()}")
        Result.Err(_) -> println("short header")

    return Result.Ok(0)
```

### stdout

Write to standard output.

#### stdout.write_bytes

Write raw bytes to stdout.

```sushi
use <io/fs>

fn File.write_bytes(u8[] data) ~ | IoError
```

**Parameters:**
- `data` - Byte array to write. The buffer stays the caller's.

Every byte goes or the call is an error: the primitive underneath loops past a short
write, which is why the answer is `~` and not a count.

**Example:**

```sushi
use <io/fs>

fn emit_bytes() ~ | IoError:
    let u8[] data = from([70 as u8, 111 as u8, 114 as u8, 100 as u8])
    stdout.write_bytes(data)??
    stdout.write("\n")??
    return Result.Ok(~)

fn main() i32:
    match emit_bytes():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

**Output:** `Ford`

**Binary output:**

```sushi
use <io/fs>
use <collections/strings>

fn emit_text() ~ | IoError:
    # Write UTF-8 encoded text
    let string text = "Mostly Harmless"
    let u8[] bytes = text.to_bytes()
    stdout.write_bytes(bytes)??
    return Result.Ok(~)

fn main() i32:
    match emit_text():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

### stderr

Write to standard error.

#### stderr.write_bytes

Write raw bytes to stderr.

```sushi
use <io/fs>

fn File.write_bytes(u8[] data) ~ | IoError
```

**Parameters:**
- `data` - Byte array to write. The buffer stays the caller's.

**Example:**

```sushi
use <io/fs>
use <collections/strings>

fn complain() ~ | IoError:
    let string error = "ERROR: Something went wrong\n"
    let u8[] error_bytes = error.to_bytes()
    stderr.write_bytes(error_bytes)??
    return Result.Ok(~)

fn main() i32:
    match complain():
        Result.Ok(_) -> return Result.Ok(1)
        Result.Err(_) -> return Result.Ok(1)
```

**Error logging:** `writeln` is the shorter route -- it takes a string and appends the
newline, so nothing has to reach for `to_bytes()`.

```sushi
use <io/fs>

fn log_error(string message) ~ | IoError:
    stderr.writeln("[ERROR] {message}")??
    return Result.Ok(~)

fn report() ~ | IoError:
    log_error("Invalid configuration")??
    log_error("Failed to connect")??
    return Result.Ok(~)

fn main() i32:
    match report():
        Result.Ok(_) -> return Result.Ok(1)
        Result.Err(_) -> return Result.Ok(1)
```

### is_terminal

Ask whether a stream is attached to a terminal. This is the one method all three
streams answer.

```sushi
fn File.is_terminal() bool
```

One method on one type, so `stdin`, `stdout` and `stderr` each answer it, and so does a
handle from `open()`.

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

fn greet() ~ | IoError:
    if (stdin.is_terminal()):
        stdout.write("Enter your name: ")??
        match stdin.readln()??:
            Maybe.Some(name) -> println("Hello, {name}!")
            Maybe.None -> println("Nobody there.")
    else:
        # Reading a piped list, so there is nobody to prompt.
        println("reading a pipe")

    return Result.Ok(~)

fn main() i32:
    match greet():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

### flush

```sushi
fn File.flush() ~ | IoError
```

**On a console handle it does nothing, successfully.** There is no buffer of Sushi's to
push: `write` hands the bytes to `write(2)`, so they are the kernel's before the call
returns. A prompt with no newline is already visible.

The method is on the `Writer` contract so that a function written against `Writer` keeps
compiling when the handle it is given is swapped for a [`BufWriter`](buf.md), where the
call drains a real buffer. Writing the call is therefore free and never wrong.

**Example:**

```sushi
use <io/fs>

fn work() ~ | IoError:
    stdout.write("Working... ")??      # already on the terminal
    stdout.flush()??                   # costs nothing, and survives a swap to BufWriter
    stdout.write("done\n")??
    return Result.Ok(~)

fn main() i32:
    match work():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
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
use <io/fs>

fn log_info(string message) ~ | IoError:
    stdout.writeln("[INFO] {message}")??
    return Result.Ok(~)

fn log_error(string message) ~ | IoError:
    stderr.writeln("[ERROR] {message}")??
    return Result.Ok(~)

fn run() ~ | IoError:
    log_info("Starting application")??
    log_info("Processing data")??
    log_error("Failed to open file")??
    log_info("Application finished")??
    return Result.Ok(~)

fn main() i32:
    match run():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

**Run with:**
```bash
./program > info.log 2> error.log
```

## Buffering Behavior

**Nothing here is buffered, and the two streams cannot get out of order.**

`print`, `println`, `stdout.write()` and `stdout.write_bytes()` all reach descriptor 1
through one `write(2)` seam, and `stderr` reaches descriptor 2 through the same one. So
the bytes of a run arrive in the order the program wrote them, on each stream and between
the two, with no `flush()` anywhere.

That is a deliberate choice and it was not always true. While `print` went through C's
buffered `printf` and a file write went straight to the kernel, a program that mixed the
two got its output in FLUSH order: a `println` could appear after a file write that ran
later. One route removed the class of bug, and it removed the need to reason about
buffering at all.

The cost is one system call per call, so a loop of a million `print`s pays a million
times. When that matters, buffer explicitly and say where the drain happens:

```sushi
use <io/fs>
use <io/buf>

fn emit_many(i32 count) ~ | IoError:
    let BufWriter@(File) out = BufWriter.new(nom stdout.share()??, 8192)??
    foreach(i in 0..count):
        out.write_line("line {i}")??
    out.finish()??               # the ONE drain, and its failure is seen
    return Result.Ok(~)
    # Nothing has reached the terminal before finish() runs.

fn main() i32:
    match emit_many(1000):
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

A `BufWriter` takes a handle it OWNS, and the console handle is a unit variable that is
never moved out of (`nom stdout` is **CE2436**) -- so `stdout.share()` hands the writer a
second descriptor over the same terminal, and dropping the writer closes that one only.
`File(fd: STDOUT_FD, owned: false)` is the other spelling: a fresh handle that closes
nothing.

Note what changes with the buffer: ordering against `stderr` is no longer free, because
`stderr` still writes immediately while the buffered `stdout` waits for its drain. That
is the trade a buffer always makes, and it is why the handles do not make it for you.

## Unicode Support

All console operations support UTF-8 encoded text:

```sushi
fn main() i32:
    println("Mostly Harmless 🌍")
    println("Café")
    println("日本語")
    println("Привет")

    return Result.Ok(0)
```

## See Also

- [File Operations](files.md) - File I/O operations
- [Buffered I/O](buf.md) - `BufReader` and `BufWriter` over any handle
- [I/O Contracts](contracts.md) - `Reader`, `Writer` and `Seek`
- [String Methods](../../standard-library.md) - String manipulation for input parsing
- [Standard Library Reference](../../standard-library.md) - Complete stdlib reference
- [Error Handling](../../error-handling.md) - Result and Maybe types
