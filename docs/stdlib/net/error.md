# Net errors

[← Back to Standard Library](../../standard-library.md)

`NetError`: the error vocabulary every net module answers.

## Import

```sushi
use <net/error>
```

## Overview

`NetError` is a predefined enum -- the compiler synthesizes it, and no unit declares it --
and this module is its HOME. The import is what brings the bare name into a unit, exactly
as `<io/fs>` brings `FileMode` and `<collections/hashmap>` brings `HashMap`; `use
<net/error> as ne` puts it behind the dot instead (`ne.NetError.TimedOut`). Every net
module re-exports this one (`docs/design/unit-namespaces.md`, section 8.1), so a unit
that matches on a net module's error needs no second import: `use <net/tcp>` alone
brings `NetError`.

```sushi
public enum NetError:
    ConnectionRefused    ConnectionReset      TimedOut
    Closed               AddressInUse         AddressNotAvailable
    NetworkUnreachable   HostUnreachable      ResolveFailed
    PermissionDenied     TooManyOpen          InvalidAddress
    Interrupted          MessageTooLarge      Other
```

The variant ORDER is the ABI: the index is the tag the socket layer stores into a Result
payload, so a variant is only ever appended. The mapping from `errno` is on the
[socket primitives](socket.md) page.

## Functions

### `to_io() -> IoError`

```sushi
extend NetError to_io() IoError
```

Turns a socket error into the one channel the io contracts answer. Every `NetError`
variant with no twin in `IoError` belongs to a connect, a bind or a resolve, so nothing a
contract method can answer is lost; the rest map to `IoError.Other`. The conversion runs
INSIDE the stdlib -- `TcpStream.read` is `sock_recv` with its error passed through
`to_io()` -- and a program never needs to call it.

### `close_socket(poke i32 fd) ~ | NetError`

```sushi
public fn close_socket(poke i32 fd) ~ | NetError
```

Releases a socket descriptor exactly once, and writes `-1` over the slot. A slot that
already reads `-1` is a success, and nothing is closed. This is the one place the guard
and the close are spelled: `close()` and the destructor of every handle in
[`<net/tcp>`](tcp.md) and [`<net/udp>`](udp.md) call it. A program that holds its sockets
as `TcpStream`, `TcpListener` or `UdpSocket` values never needs to call it; it is for a
descriptor that no handle owns.

## Example

```sushi
use <net/tcp>

fn main() i32:
    match connect("localhost", 1):
        Result.Ok(_) -> println("connected")
        Result.Err(NetError.ConnectionRefused) -> println("nothing listens there")
        Result.Err(_) -> println("failed")
    return Result.Ok(0)
```

## See also

- [Socket primitives](socket.md) -- the `errno` mapping behind each variant
- [I/O errors](../io/error.md) -- `IoError`, the channel a contract method answers
- [Unit namespaces](../../design/unit-namespaces.md) -- why a predefined enum has a home
