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
| `Reader` | `read`, `read_bytes` | `File`, `TcpStream` |
| `Writer` | `write`, `write_bytes`, `flush` | `File`, `TcpStream` |
| `Seek` | `seek` | `File` |

A `TcpListener` implements none of them: a listener only accepts, and a stream has no
position to move.

```sushi
public perk Reader:
    fn read(i32 max) string | IoError
    fn read_bytes(i32 max) u8[] | IoError

public perk Writer:
    fn write(string data) ~ | IoError
    fn write_bytes(u8[] data) ~ | IoError
    fn flush() ~ | IoError

public perk Seek:
    fn seek(i64 offset, SeekFrom origin) i64 | IoError
```

## Why every contract method answers IoError

A perk contract carries ONE signature, and Sushi has no `Self` type and no associated
type. So `Reader.read` cannot answer `FileError` on a `File` and `NetError` on a
`TcpStream`. One channel is also what lets a generic propagate with `??`.

The detailed enums stay where their detail is real: `open()` keeps its own errors on
`IoError` too, while `tcp_connect()`, `tcp_listen()`, `accept()` and the address and option
methods keep `NetError`. Every `NetError` variant with no twin in `IoError` -- `AddressInUse`,
`HostUnreachable`, `ResolveFailed` and the rest -- belongs to a connect or a bind, and never
to a read or a write.

## read against read_bytes

The two mean the same thing: ONE read, bounded by the caller, answering what arrived. They
differ only in what they hand back.

`read` returns a fresh `string`; `read_bytes` returns a fresh `u8[]`. Neither fills a buffer
the caller owns, and for `read` that is forced: a `string` is `{data, size, owned}` -- a size
and no capacity -- so there is nowhere to write into.

The bound counts BYTES. A multi-byte character can therefore be split across two calls, and
on a socket the network can split one whatever the bound says.

An EMPTY answer means end of input, which is what lets a caller loop until the answer is
empty. A short answer is not an end: a pipe hands over whatever has arrived.

## write and write_bytes answer nothing

Both write EVERYTHING, or answer an error. The descriptor primitives loop past a short
write, so there is no partial count to report.

A caller that needs to know how much ONE attempt took wants the concrete method on the
handle instead: `TcpStream.send` answers a count, because a socket's partial write tells you
what the peer's window took. A file's does not.

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

fn greet@(W: Writer)(W dst, string who) ~ | IoError:
    dst.write("Mostly Harmless, ")??
    dst.write(who)??
    dst.write("\n")??
    dst.flush()??
    return Result.Ok(~)

fn main() i32:
    greet(stdout, "world")
    return Result.Ok(0)
```

`stdout` is a `File` constant, so the console goes through the contract like any other
handle.

### Asking for two contracts at once

```sushi
use <io/contracts>
use <io/fs>

fn copy_text@(R: Reader, W: Writer)(R src, W dst) ~ | IoError:
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
