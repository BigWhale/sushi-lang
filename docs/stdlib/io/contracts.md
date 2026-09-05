# io/contracts

`Reader`, `Writer` and `Seek`: what a handle can DO, named apart from what a handle IS.

## Import

```sushi
use <io/contracts>
```

## Overview

The module declares three perks and nothing else -- no struct, no function, no constant. A
consumer writes `@(R: Reader)` and takes a `File`, a `TcpStream`, or anything later that
satisfies the contract.

| perk | methods | who implements it |
|---|---|---|
| `Reader` | `read`, `read_bytes` | `File`, `TcpStream`, `BufReader@(R)` |
| `Writer` | `write`, `write_bytes`, `flush` | `File`, `TcpStream`, `BufWriter@(W)` |
| `Seek` | `seek` | `File` |

A `TcpListener` implements none of them: a listener only accepts, and a stream has no
position to move.

```sushi
public perk Reader:
    fn read(poke self, i32 max) string | IoError
    fn read_bytes(poke self, i32 max) u8[] | IoError

public perk Writer:
    fn write(poke self, string data) ~ | IoError
    fn write_bytes(poke self, u8[] data) ~ | IoError
    fn flush(poke self) ~ | IoError

public perk Seek:
    fn seek(poke self, i64 offset, SeekFrom origin) i64 | IoError
```

## Every contract method takes `poke self`

A buffered read MOVES its cursor, and a buffered write fills a buffer, so a
`BufReader@(R)` or a `BufWriter@(W)` can only implement the contract if the contract's
receiver is writable. A perk implementation must match its contract's receiver exactly
(**CE4004**), so the mode is on the contract and on every implementation alike: `File`
and `TcpStream` take `poke self` too, though a descriptor's position lives in the kernel
and the mode costs them nothing.

Two things follow for a caller:

- **A generic over a contract takes its handle `poke`**: `fn emit@(W: Writer)(poke W dst,
  ...)`, called as `emit(poke stdout, ...)`, `emit(poke f, ...)`. A generic written
  `(W dst)` is a borrow, and a write through it is **CE2422**.
- **A handle you write through must be writable storage**: a `let` local, a `poke`
  parameter, a `nom` or `poke` match binding (`Result.Ok(nom f) -> f.write(...)`), or a
  unit variable. The console handles are `public var File` declarations in `<io/fs>`,
  which is what gives `stdout.write(...)` an address to reach (the ruling on #546;
  `docs/design/unit-storage.md`).

## Why every contract method answers IoError

A perk contract carries ONE signature, and Sushi has no `Self` type and no associated
type. So `Reader.read` cannot answer `FileError` on a `File` and `NetError` on a
`TcpStream`. One channel is also what lets a generic propagate with `??`.

The detailed enums stay where their detail is real: `open()` keeps its own errors on
`IoError` too, while `connect()`, `listen()`, `accept()` and the address and option
methods keep `NetError`. Every `NetError` variant with no twin in `IoError` -- `AddressInUse`,
`HostUnreachable`, `ResolveFailed` and the rest -- belongs to a connect or a bind, and never
to a read or a write.

## read against read_bytes

The two mean the same thing: ONE read, bounded by the caller, answering what arrived. They
differ only in what they hand back.

`read` returns a fresh `string`; `read_bytes` returns a fresh `u8[]`. Neither fills a buffer
the caller owns. For `read` that is forced: a `string` is `{data, size, owned}` -- a size
and no capacity -- so there is nowhere to write into. For `read_bytes` it was measured
against the one consumer that refills in a loop, `BufReader`: a refill costs exactly one
allocation and no copy; that allocation is about six percent of an 8 KB refill from the
page cache and about four percent at 64 KB; and a `(poke u8[] into)` form could not remove
it -- a `u8[]` crosses to the descriptor primitive BY VALUE, so the primitive cannot set
the caller's length, and a fill-into body allocates the same array and copies it once more.

The bound counts BYTES, and one read may therefore split a multi-byte sequence; on a
socket the network can split one whatever the bound says. **The caller's answer is to
accumulate bytes and convert once**, the way `read_all()` and `read_line()` do --
converting each chunk on its own cuts the character in two. A character bound would need a
character index the string layer does not have.

An EMPTY answer means end of input, which is what lets a caller loop until the answer is
empty. A short answer is not an end: a pipe hands over whatever has arrived.

## write and write_bytes answer nothing

Both write EVERYTHING, or answer an error. The descriptor primitives loop past a short
write, so there is no partial count to report.

A caller that needs to know how much ONE attempt took wants the concrete method on the
handle instead: `TcpStream.send` answers a count, because a socket's partial write tells you
what the peer's window took. A file's does not. There is no `recv` beside it: one read on a
socket is `read_bytes`, and every `NetError` a read can answer has its `IoError` twin, so a
second name would have carried nothing of its own.

## flush

A handle's `flush` is a successful no-op: a descriptor is not buffered, so a write has
already reached the kernel by the time it returns. The call is in the contract because the
buffered writer is where it earns its name, and a caller that moves up a layer should not
have to add it back.

Getting bytes onto the DISK is `fsync`, a much stronger promise, and not what `flush` ever
meant.

## Examples

### One function, two kinds of handle

```sushi
use <io/contracts>
use <io/fs>

fn greet@(W: Writer)(poke W dst, string who) ~ | IoError:
    dst.write("Mostly Harmless, ")??
    dst.write(who)??
    dst.write("\n")??
    dst.flush()??
    return Result.Ok(~)

fn main() i32:
    match greet(poke stdout, "world"):
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

`stdout` is a `File` unit variable, so the console goes through the contract like any
other handle, and `poke stdout` is how a writable handle is passed. A `BufWriter@(File)`
over the console goes through the same `greet`.

### Asking for two contracts at once

```sushi
use <io/contracts>
use <io/fs>

fn copy_text@(R: Reader, W: Writer)(poke R src, poke W dst) ~ | IoError:
    let string chunk = src.read(4096)??
    dst.write(chunk)??
    dst.flush()??
    return Result.Ok(~)

fn main() i32:
    println("Mostly Harmless")
    return Result.Ok(0)
```

## Limitations

- A perk has no default implementations, so a convenience above these methods is a free
  generic function rather than a provided method.
- A perk cannot carry a type parameter (CE4010), so there is no `Reader@(T)`.
- A perk method beside a concrete method of the same name on one type is CE4007. Each name
  has exactly one home.
