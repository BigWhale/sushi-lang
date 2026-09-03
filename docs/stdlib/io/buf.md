# io/buf

`BufReader@(R)` and `BufWriter@(W)`: one system call per WINDOW instead of one per line.
Line ITERATION lives here too, and only here -- a `File` keeps no line loop, because an
unbuffered handle yielding lines is one system call per line.

## Import

```sushi
use <io/buf>
```

The module is bundled Sushi source, merged as a compilation unit when imported. It needs
`<io/fs>` too, for the `File` and `IoError` names a program using it will write.

## Overview

`BufReader@(R)` wraps anything that satisfies `Reader`, `BufWriter@(W)` anything that
satisfies `Writer`. A `File` and a `TcpStream` are buffered by the same code. Both
buffered types satisfy the contract they wrap: a `BufReader@(File)` is a `Reader` and a
`BufWriter@(File)` is a `Writer`, so a generic written over the contract takes the
buffered handle and the plain one alike.

| type | constructor | reads / writes | ends with |
|---|---|---|---|
| `BufReader@(R)` | `buf_reader(nom src, i32 cap)` | `read_line`, `read`, `read_bytes`, `read_all`, `fill` | `lines`, `into_inner` |
| `BufWriter@(W)` | `buf_writer(nom dst, i32 cap)` | `write`, `write_bytes`, `write_line`, `flush` | `finish`, `into_inner` |
| `Lines@(R)` | `r.lines()` | `next` | -- |

Both constructors TAKE the handle: the buffer owns it, closes it when the buffer is
destroyed, and `into_inner()` is how a caller gets it back.

```sushi
use <io/fs>
use <io/buf>

fn longest_line(string path) i32 | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = buf_reader(nom f, 8192)??
    let i32 longest = 0
    foreach(line?? in r.lines()):
        if (line.len() > longest):
            longest := line.len()
    return Result.Ok(longest)

fn main() i32:
    match longest_line("/etc/hosts"):
        Result.Ok(n) -> println("longest line {n}")
        Result.Err(_) -> println("could not read it")
    return Result.Ok(0)
```

## Reading

### `read_line() -> Maybe@(string) | IoError`

One line, without its newline.

A blank line is `Some("")` and the end of input is `None`, so the two are never the same
answer. A last line with no newline after it is still a line, and the `None` arrives on the
call after it. The line is copied out of the window in bulk, never a byte at a time, and a
line longer than the window spans as many refills as it needs.

### `read(i32 max) -> string | IoError` and `read_bytes(i32 max) -> u8[] | IoError`

What the window still has, up to `max`, refilling it if it is spent.

The answer never crosses a refill, so a short answer is not an end of input. An EMPTY answer
is -- that is what lets a caller loop until the answer is empty. `read`'s bound counts
BYTES, so a multi-byte character can be split across two calls.

### `read_all() -> string | IoError`

Everything left, held in memory at once. A large input wants `read_line()` in a loop.

### `fill() -> bool | IoError`

Reads the next window, replacing the one held; answers whether any byte arrived. Every read
above goes through it, so end of input is decided in one place and stays decided: once a
refill has answered nothing, no further call reads the handle again.

A caller rarely needs `fill()`. It is part of the surface because an extension method
carries no visibility marker -- it is as visible as the type it extends.

A refill costs ONE allocation and no copy: the read primitive allocates its answer and the
window takes it whole. Measured over a 512 MB file at an 8 KB window, that allocation is
about six percent of a refill from the page cache, and less from a disk or a socket. Why
`read_bytes` returns an array rather than filling one is in [io/contracts](contracts.md).

### `into_inner() -> R`

Hands the handle back and ends the reader. A later mention of the reader is refused while
compiling.

Whatever was buffered and not read is DISCARDED, so the handle comes back positioned where
the last refill left the kernel and not where the cursor was.

### `lines() -> Lines@(R)`

Turns the reader into a line iterator, TAKING the reader. The iterator owns the reader and
the reader owns the handle, so one drop closes the descriptor. A later mention of the
reader is **CE2435**.

`Lines@(R)` carries `next()`, which answers `Maybe@(Result@(string, IoError))` -- and that
makes it walkable by `foreach`, with no `Iterator` type and no perk in sight
(`docs/language-reference.md`, For-Each Loops).

```sushi
use <io/fs>
use <io/buf>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = buf_reader(nom f, 8192)??
    foreach(line?? in r.lines()):
        println(line)
    return Result.Ok(~)
    # The Lines drops here: it destroys the BufReader, which closes the File.

fn main() i32:
    match show("/etc/hosts"):
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

### `Lines@(R).next() -> Maybe@(Result@(string, IoError))`

The outer `Maybe` says whether the input has more; the inner `Result` says whether reading
it worked. They are never the same answer: a blank line is `Some(Ok(""))` and the end is
`None`.

**The stop is STICKY.** A read failure reaches the caller once, as `Some(Err(e))`, and
every call after it answers `None`. Nothing retries and nothing is skipped, so a loop over
this iterator cannot spin on a descriptor that keeps failing.

The `??` on the binder is the short form -- the first failure leaves the function. Without
it the item is the plain `Result`, and a body that wants to report a failure and carry on
writes the `match` itself:

```sushi
use <io/fs>
use <io/buf>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = buf_reader(nom f, 8192)??
    foreach(item in r.lines()):
        match item:
            Result.Ok(line) -> println(line)
            Result.Err(_) -> println("<unreadable>")
    return Result.Ok(~)

fn main() i32:
    match show("/etc/hosts"):
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

## Writing

### `write(string data)`, `write_bytes(u8[] data)`, `write_line(string data)`

Bytes go into the buffer. Nothing is promised to have reached the handle when these return.

`write_line` appends the newline into the same buffer, so a line costs no system call of its
own. The buffer drains itself when it reaches `cap`.

### `flush() -> ~ | IoError`

Sends everything waiting, then flushes the handle under it. The repeatable checked drain:
call it as often as you like, and it answers Ok when the buffer is already empty.

### `finish() -> ~ | IoError`

Ends the writer, sending everything waiting first -- and it **consumes**. Nothing can forget
to flush after `finish()`, because there is nothing left to forget with. The handle closes
as the call returns.

```sushi
use <io/fs>
use <io/buf>

fn write_report(string path) ~ | IoError:
    let File f = open(path, FileMode.Write())??
    let BufWriter@(File) w = buf_writer(nom f, 8192)??
    w.write_line("Mostly Harmless")??
    w.write_line("")??
    w.write("no newline after this one")??
    w.finish()??                    # the last drain, checked
    return Result.Ok(~)

fn main() i32:
    match write_report("/tmp/report.txt"):
        Result.Ok(_) -> println("written")
        Result.Err(_) -> println("could not write it")
    return Result.Ok(0)
```

### `into_inner() -> W | IoError`

Sends everything waiting, then hands the handle back and ends the writer.

## A dropped BufWriter flushes, and loses the error

`BufWriter@(W)` implements `Drop`: whatever is waiting when the writer is destroyed is
sent. A destructor has nowhere to put a `Result`, so a failure there is **lost**. That is
what `finish()` is for -- it is the same drain with the failure still visible.

Forgetting `finish()` does not lose the bytes. It loses the answer to "did they arrive?".

## Both types implement their contract

`BufReader@(R)` implements `Reader` and `BufWriter@(W)` implements `Writer`. This used to
be refused: a buffered read MOVES the cursor, so its receiver had to be `poke self`, and
a perk implementation must match its contract's receiver exactly (**CE4004**), while the
contracts declared a read-only receiver -- which they could not widen, because the
console handles were `File` CONSTANTS and a `poke self` method on a constant is
**CE2400**.

The ruling on #546 took both moves the mainstream answers agree on: every contract
method takes `poke self` (Rust's `&mut self`, Go's pointer receiver), and the console
handles became unit variables (`public var File stdout`, Go's `os.Stdout`), so
`stdout.write(...)` kept its spelling. A generic over a contract now takes its handle
`poke`:

```sushi
use <io/fs>
use <io/buf>
use <io/contracts>

fn emit@(W: Writer)(poke W dst, string line) ~ | IoError:
    dst.write(line)??
    dst.write("\n")??
    dst.flush()??
    return Result.Ok(~)

fn run() ~ | IoError:
    emit(poke stdout, "to the console")??
    let BufWriter@(File) w = buf_writer(nom File(fd: STDOUT_FD, owned: false), 4096)??
    emit(poke w, "through a buffer")??
    w.finish()??
    return Result.Ok(~)

fn main() i32:
    match run():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

The extra verbs only a buffer can offer -- `read_line`, `read_all`, `lines`, `fill`,
`write_line` -- stay concrete methods of the buffered type. A contract for that direction
(Rust's `BufRead`) is a separate, later question.

## Cost

One instantiation is one copy of the module's code. Going from one buffered instantiation to
four -- `BufReader` and `BufWriter` over both `File` and `TcpStream` -- measured +312 KB of
emitted IR and +25 functions on a program that also imports `<net/tcp>`, about a fifth more
module text for three more instantiations. An instantiation the program never names costs
nothing.

`write()` copies the string's bytes twice, once into `to_bytes()` and once into the buffer.

## See also

- [Iteration (design)](../../design/iteration.md) -- why the failure rides in the item
- [io/contracts](contracts.md) -- the `Reader`, `Writer` and `Seek` perks
- [io/fs](fs.md) -- `File`, `open()` and the console handles
- [io/files](files.md) -- the `fd_*` primitives under both
