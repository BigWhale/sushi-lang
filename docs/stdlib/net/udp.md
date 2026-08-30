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

The close rule is `<net/tcp>`'s: **one binding owns a socket**, `udp_close(poke s)` ends it and writes `-1` back, and a copy taken before the close holds a descriptor the kernel may have given to somebody else.

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

## Functions

### `udp_bind(string host, i32 port) -> Result@(UdpSocket, NetError)`

Bind a datagram socket. Port 0 asks the kernel to choose; `udp_local_port` reads it back.

### `udp_send_to(UdpSocket s, u8[] data, string host, i32 port) -> Result@(i32, NetError)`

Send one datagram. The destination is resolved on every call, so a tight loop to one peer resolves that peer each time round.

### `udp_recv_from(UdpSocket s, i32 max) -> Result@(Datagram, NetError)`

Wait for one datagram. A datagram longer than `max` is truncated and the rest is lost, which is how the protocol works. A sender whose address cannot be rendered leaves `peer_ip` empty rather than failing the receive: the bytes did arrive.

```sushi
use <net/udp>
use <collections/strings>

fn main() i32:
    let UdpSocket a = udp_bind("127.0.0.1", 0).realise(UdpSocket(-1))
    let UdpSocket b = udp_bind("127.0.0.1", 0).realise(UdpSocket(-1))
    udp_set_timeouts(b, 5000, 5000)

    let i32 port_b = udp_local_port(b).realise(-1)
    let u8[] greeting = "Mostly Harmless".to_bytes()
    udp_send_to(a, greeting, "127.0.0.1", port_b)

    match udp_recv_from(b, 64):
        Result.Ok(dg) -> println("{dg.peer_ip} said {dg.data.to_string()}")
        Result.Err(_) -> println("nothing arrived")

    udp_close(poke a)
    udp_close(poke b)

    return Result.Ok(0)
```

### `udp_local_port(UdpSocket s)`, `udp_set_timeouts(UdpSocket s, i32 recv_ms, i32 send_ms)`, `udp_close(poke UdpSocket s)`

The port that was bound, the bounds on a wait, and the close. All three answer a `Result`.

## Limitations

Blocking sockets only. A datagram longer than the maximum asked for is truncated.

## See also

- [Socket primitives](socket.md) — the layer underneath
- [TCP](tcp.md) — the stream half
