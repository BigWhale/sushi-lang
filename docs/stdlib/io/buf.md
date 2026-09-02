# io/buf

`BufReader@(R)` and `BufWriter@(W)`: one system call per WINDOW instead of one per line.

## Import

```sushi
use <io/buf>
```

The module is bundled Sushi source, merged as a compilation unit when imported. It needs
`<io/fs>` too, for the `File` and `IoError` names a program using it will write.

## Overview

`BufReader@(R)` wraps anything that satisfies `Reader`, `BufWriter@(W)` anything that
satisfies `Writer`. A `File` and a `TcpStream` are buffered by the same code.

| type | constructor | reads / writes | ends with |
|---|---|---|---|
| `BufReader@(R)` | `buf_reader(nom src, i32 cap)` | `read_line`, `read`, `read_bytes`, `read_all`, `fill` | `into_inner` |
| `BufWriter@(W)` | `buf_writer(nom dst, i32 cap)` | `write`, `write_bytes`, `write_line`, `flush` | `finish`, `into_inner` |

Both constructors TAKE the handle: the buffer owns it, closes it when the buffer is
destroyed, and `into_inner()` is how a caller gets it back.

```sushi
use <io/fs>
use <io/buf>

fn count_lines(string path) i32 | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = buf_reader(nom f, 8192)??
    let i32 n = 0
    let bool done = false
    while (not done):
        match r.read_line()??:
            Maybe.Some(_) ->
                n := n + 1
            Maybe.None ->
                done := true
    return Result.Ok(n)

fn main() i32:
    match count_lines("/etc/hosts"):
        Result.Ok(n) -> println("lines {n}")
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

### `into_inner() -> R`

Hands the handle back and ends the reader. A later mention of the reader is refused while
compiling.

Whatever was buffered and not read is DISCARDED, so the handle comes back positioned where
the last refill left the kernel and not where the cursor was.

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

## Neither type implements Reader or Writer

This is a refusal rather than an omission, and it is worth stating plainly because the names
are the contracts' names.

A buffered read MOVES the cursor, so its receiver has to be `poke self`. A perk
implementation must match its contract's receiver exactly (**CE4004**), and the contracts
declare a read-only receiver. Widening them to `poke self` is not available either: the
console handles are `File` CONSTANTS, and a `poke self` method on a constant is
**CE2400** -- so `stdout.write(...)` would stop compiling.

So a function written `@(R: Reader)` takes a `File` or a `TcpStream` and does not take a
`BufReader@(File)`. Call the buffered methods directly instead. The two ways out, neither
taken yet, are a second perk for the buffered direction or
receiver modes on a contract that a constant can still satisfy.

## Cost

One instantiation is one copy of the module's code. Going from one buffered instantiation to
four -- `BufReader` and `BufWriter` over both `File` and `TcpStream` -- measured +312 KB of
emitted IR and +25 functions on a program that also imports `<net/tcp>`, about a fifth more
module text for three more instantiations. An instantiation the program never names costs
nothing.

`write()` copies the string's bytes twice, once into `to_bytes()` and once into the buffer.

## See also

- [io/contracts](contracts.md) -- the `Reader`, `Writer` and `Seek` perks
- [io/fs](fs.md) -- `File`, `open()` and the console handles
- [io/files](files.md) -- the `fd_*` primitives under both
