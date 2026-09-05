# UDP

[← Back to Standard Library](../../standard-library.md)

`UdpSocket`: bind a datagram socket, send to a peer, and receive from one.

## Import

```sushi
use <net/udp>
```

## Overview

`net/udp` is a **Sushi-source** standard-library module: it ships as bundled `.sushi` source and is merged as a compilation unit when you import it.

A datagram socket has no peer of its own, so every send names its destination and every receive answers with a `Datagram`: the bytes, and who sent them. That is not a convenience — an unconnected socket cannot be asked afterwards, because the sender exists only at the instant its datagram arrives.

**One binding owns a socket.** `s.close()` ends it and writes `-1` back; the receiver is `poke self` here, because a `UdpSocket` does not implement `Drop` yet — so the guarded second close stays reachable, which is not true of `<net/tcp>`'s handles.

`bind` is the one free function; everything with a receiver is an extension method with the `| NetError` channel.

## Types

```sushi
public struct UdpSocket:
    i32 fd
```

`Datagram` is a predefined struct, so it needs no import:

```sushi
public struct Datagram:
    u8[] data
    string peer_ip
    i32 peer_port
```

## Constructor

### `bind(string host, i32 port) -> Result@(UdpSocket, NetError)`

Bind a datagram socket. Port 0 asks the kernel to choose; `s.local_port()` reads it back.

## Methods

### `s.send_to(u8[] data, string host, i32 port) i32 | NetError`

Send one datagram. The destination is resolved on every call, so a tight loop to one peer resolves that peer each time round.

### `s.recv_from(i32 max) Datagram | NetError`

Wait for one datagram. A datagram longer than `max` is truncated and the rest is lost, which is how the protocol works. A sender whose address cannot be rendered leaves `peer_ip` empty rather than failing the receive: the bytes did arrive.

```sushi
use <net/udp>
use <collections/strings>
use <net/error>

fn exchange() ~ | NetError:
    let UdpSocket a = bind("127.0.0.1", 0)??
    let UdpSocket b = bind("127.0.0.1", 0)??
    b.set_timeouts(5000, 5000)??

    let i32 port_b = b.local_port()??
    let u8[] greeting = "Mostly Harmless".to_bytes()
    a.send_to(greeting, "127.0.0.1", port_b)??

    match b.recv_from(64):
        Result.Ok(dg) -> println("{dg.peer_ip} said {dg.data.to_string()}")
        Result.Err(_) -> println("nothing arrived")

    a.close()??
    b.close()??
    return Result.Ok(~)

fn main() i32:
    match exchange():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

### `s.local_port() i32 | NetError`, `s.set_timeouts(i32 recv_ms, i32 send_ms) ~ | NetError`, `s.close() ~ | NetError`

The port that was bound, the bounds on a wait, and the close. The close takes `poke self` and writes `-1` into the binding.

## Limitations

Blocking sockets only. A datagram longer than the maximum asked for is truncated.

## See also

- [Socket primitives](socket.md) — the layer underneath
- [TCP](tcp.md) — the stream half
