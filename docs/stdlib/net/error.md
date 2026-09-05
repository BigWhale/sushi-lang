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
<net/error> as ne` puts it behind the dot instead (`ne.NetError.TimedOut`). A unit that
matches on a net module's error imports this module beside the one it calls.

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

## Example

```sushi
use <net/tcp>
use <net/error>

fn main() i32:
    match connect("localhost", 1):
        Result.Ok(_) -> println("connected")
        Result.Err(NetError.ConnectionRefused) -> println("nothing listens there")
        Result.Err(_) -> println("failed")
    return Result.Ok(0)
```

## See also

- [Socket primitives](socket.md) -- the `errno` mapping behind each variant
- [I/O Contracts](../io/contracts.md) -- `IoError`, the channel a contract method answers
- [Unit namespaces](../../design/unit-namespaces.md) -- why a predefined enum has a home
