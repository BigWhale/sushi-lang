# Socket handles

[← Back to Standard Library](../../standard-library.md)

`close_socket`: the release of a socket descriptor, for every socket handle of the net
modules.

## Import

```sushi
use <net/handle>

fn main() i32:
    return 0
```

[`<net/tcp>`](tcp.md) and [`<net/udp>`](udp.md) re-export this module (`public use`), so
a program that imports one of them can call `close_socket` too.

## Functions

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

```sushi
use <net/handle>

fn main() i32:
    let i32 slot = -1
    match close_socket(poke slot):
        Result.Ok(_) -> println("closed")
        Result.Err(_) -> println("close_socket failed")
    return 0
```

## See also

- [Net errors](error.md) — `NetError`, which `close_socket` answers
- [Socket primitives](socket.md) — `sock_close`, the call under it
