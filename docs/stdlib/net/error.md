# Net errors

[← Back to Standard Library](../../standard-library.md)

`NetError`: the error vocabulary every net module answers.

## Import

```sushi
use <net/error>
```

## Overview

`NetError` is a predefined error type -- the compiler synthesizes it, and no unit declares it --
and this module is its HOME. The import is what brings the bare name into a unit, exactly
as `<io/fs>` brings `FileMode` and `<collections/hashmap>` brings `HashMap`; `use
<net/error> as ne` puts it behind the dot instead (`ne.NetError.TimedOut`). `<net/tcp>`,
`<net/udp>`, `<net/dns>` and `<net/ip>` re-export this module
(`docs/design/unit-namespaces.md`, section 8.1), so a unit that matches on their errors
needs no second import: `use <net/tcp>` alone brings `NetError`. `<net/url>` answers its
own `UrlError` and does not re-export this module.

```sushi
public error NetError:
    ConnectionRefused    ConnectionReset      TimedOut
    Closed               AddressInUse         AddressNotAvailable
    NetworkUnreachable   HostUnreachable      ResolveFailed
    PermissionDenied     TooManyOpen          InvalidAddress
    Interrupted          MessageTooLarge      Other
```

The variant ORDER is the ABI: the index is the tag the socket layer stores into a Result
payload, so a variant is only ever appended. The mapping from `errno` is on the
[socket primitives](socket.md) page.

## Conversion into `IoError`

The io contracts answer `IoError`, so a `TcpStream` read or write turns its `NetError` into
an `IoError` through the conversion `NetError as IoError`. A conversion lives in the unit
that declares its TARGET, so this conversion is in `<io/error>`, not in this module; that
module imports `<net/error>` for the name. See
[I/O errors](../io/error.md#neterror-as-ioerror). This module declares nothing but the
error type itself.

## Example

```sushi
use <net/tcp>

fn main() i32:
    match connect("localhost", 1):
        Result.Ok(_) -> println("connected")
        Result.Err(NetError.ConnectionRefused) -> println("nothing listens there")
        Result.Err(_) -> println("failed")
    return 0
```

## See also

- [Socket primitives](socket.md) -- the `errno` mapping behind each variant
- [I/O errors](../io/error.md) -- `IoError`, the channel a contract method answers, and the conversion `NetError as IoError`
- [Unit namespaces](../../design/unit-namespaces.md) -- why a predefined enum has a home
