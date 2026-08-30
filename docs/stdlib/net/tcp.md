# TCP

[← Back to Standard Library](../../standard-library.md)

`TcpStream` and `TcpListener`: connect, listen, accept, send and receive over a stream socket.

## Import

```sushi
use <net/tcp>
```

## Overview

`net/tcp` is a **Sushi-source** standard-library module: it ships as bundled `.sushi` source and is merged as a compilation unit when you import it. It composes the `<net/socket>` primitives, and a struct here is a typed name for a descriptor and carries nothing else.

Every function is fallible and therefore a free function. An extension method returns a bare value and has no error channel, which a socket operation needs.

## Types

```sushi
public struct TcpStream:
    i32 fd

public struct TcpListener:
    i32 fd
```

## Closing, and the one rule to remember

There is **no RAII for a socket**, exactly as there is none for a `file`. A `TcpStream` owns no heap, so it *copies*, and a copy holds the same descriptor.

> **One binding owns a socket.** `tcp_close(poke s)` ends it and writes `-1` into the binding, so closing that same binding again is a success rather than an `EBADF`. A copy taken before the close keeps a number the kernel has freed and may have given to somebody else, and using it is a bug the compiler cannot catch.

`poke` is what makes this workable: the mutation is visible at the call site, a temporary cannot be closed at all, and the binding is exclusive for the duration of the call.

## Functions

### `tcp_listen(string host, i32 port, i32 backlog) -> Result@(TcpListener, NetError)`

Bind a listening socket. Port 0 asks the kernel to choose; `tcp_local_port` reads it back.

### `tcp_connect(string host, i32 port) -> Result@(TcpStream, NetError)`

Connect to a host and port. There is no connect timeout — an unreachable address waits for the kernel.

### `tcp_accept(TcpListener l) -> Result@(TcpStream, NetError)`

Take the next connection. With `tcp_listener_set_timeout` set, this answers `TimedOut` rather than waiting.

### `tcp_send_all(TcpStream s, u8[] data) -> Result@(~, NetError)` and `tcp_recv_exact(TcpStream s, i32 count) -> Result@(u8[], NetError)`

The two loops every caller would otherwise write. One send may take fewer bytes than it was offered and one receive answers whatever arrived, so anything that depends on a byte count wants these rather than `tcp_send` and `tcp_recv`.

`tcp_recv_exact` treats a peer that closes early as an error: a caller asking for an exact count has no use for a partial answer.

A whole exchange, in one process. A blocking `connect()` to a listening socket completes inside the kernel, so `accept()` finds the connection already waiting — listen, then connect, then accept, and one thread is enough:

```sushi
use <net/tcp>
use <collections/strings>

fn main() i32:
    let TcpListener listener = tcp_listen("127.0.0.1", 0, 8).realise(TcpListener(-1))
    tcp_listener_set_timeout(listener, 5000)

    let i32 port = tcp_local_port(listener).realise(-1)
    let TcpStream client = tcp_connect("127.0.0.1", port).realise(TcpStream(-1))
    let TcpStream served = tcp_accept(listener).realise(TcpStream(-1))
    tcp_set_timeouts(client, 5000, 5000)
    tcp_set_timeouts(served, 5000, 5000)

    let u8[] greeting = "Mostly Harmless".to_bytes()
    tcp_send_all(client, greeting)
    match tcp_recv_exact(served, 15):
        Result.Ok(heard) -> println("the server heard {heard.to_string()}")
        Result.Err(_) -> println("the server heard nothing")

    tcp_close(poke client)
    tcp_close(poke served)
    tcp_listener_close(poke listener)

    return Result.Ok(0)
```

### `tcp_send(TcpStream s, u8[] data) -> Result@(i32, NetError)` and `tcp_recv(TcpStream s, i32 max) -> Result@(u8[], NetError)`

One write and one read. `tcp_recv` answers an empty array when the peer closed cleanly; a timeout is an error instead, so the two never read alike.

### `tcp_set_timeouts(TcpStream s, i32 recv_ms, i32 send_ms)` and `tcp_listener_set_timeout(TcpListener l, i32 accept_ms)`

Bound how long a call may wait; both answer `Result@(~, NetError)`. **Set these before anything blocks.** Without them a read waits for as long as the peer stays silent.

### `tcp_peer_ip(TcpStream s)`, `tcp_peer_port(TcpStream s)`, `tcp_local_port(TcpListener l)`, `tcp_stream_local_port(TcpStream s)`

Who is at each end. All four answer a `Result`.

### `tcp_close(poke TcpStream s)` and `tcp_listener_close(poke TcpListener l)`

Close, and write `-1` into the binding. Both answer `Result@(~, NetError)`, and both are safe to call twice on the same binding.

## Limitations

Blocking sockets only. There is no connect timeout, and no non-blocking mode.

## See also

- [Socket primitives](socket.md) — the layer underneath
- [DNS](dns.md) — resolving a name before connecting
- [URLs](url.md) — pulling a host and port out of a URL
