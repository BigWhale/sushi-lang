# TCP

[← Back to Standard Library](../../standard-library.md)

`TcpStream` and `TcpListener`: connect, listen, accept, send and receive over a stream socket.

## Import

```sushi
use <net/tcp>
```

## Overview

`net/tcp` is a **Sushi-source** standard-library module: it ships as bundled `.sushi` source and is merged as a compilation unit when you import it. It composes the `<net/socket>` primitives, and a struct here is a typed name for a descriptor and carries nothing else.

The two constructors are free functions; everything with a receiver is an extension method with an error channel, so a call site handles each one with `??`, `.realise(default)`, `match` or `.is_ok()`. The domain methods -- `accept`, `send`, the addresses, the timeouts, `close` -- answer `| NetError`. The contract methods a `TcpStream` shares with a `File` (`read`, `read_bytes`, `write`, `write_bytes`, `flush`, see [I/O Contracts](../io/contracts.md)) and `share()` answer `| IoError`.

## Types

```sushi
public struct TcpStream:
    i32 fd

public struct TcpListener:
    i32 fd
```

## Closing, and who owns a socket

**A socket owns its descriptor.** `TcpStream` and `TcpListener` implement the `Drop` perk, so each one MOVES to exactly one owner and closes itself when that owner leaves scope. There is nothing to remember and nothing to leak.

> **One binding owns a socket, and the compiler enforces it.** Handing a stream to a `nom` parameter transfers it, and reading the old binding afterwards is `CE2405`. A socket has no `.clone()` — a deep copy would copy the descriptor number and leave two values that both close it, which is `CE2431`. The operation that means a second handle is `share()`, and a listener has it.

```sushi
use <net/tcp>

fn serve(nom TcpStream client) ~:
    println("serving")
    return Result.Ok(~)          # client closes here

fn main() i32:
    let TcpListener server = listen("127.0.0.1", 0, 1).realise(TcpListener(-1))
    println("{server.local_port().realise(0) > 0}")
    return Result.Ok(0)          # server closes here
```

`close()` stays, for the caller who has to **see** that the close failed: a destructor has nowhere to put a `Result`, so a failure at drop is lost. It declares `nom self` and CONSUMES the handle, so the descriptor is released exactly once and the scope exit that follows has nothing to close. A use after a close — a second `close()`, a `read`, a `local_port()` — is **CE2435** while compiling, rather than an `EBADF` at run time.

## Constructors

### `listen(string host, i32 port, i32 backlog) -> Result@(TcpListener, NetError)`

Bind a listening socket. Port 0 asks the kernel to choose; `l.local_port()` reads it back.

### `connect(string host, i32 port) -> Result@(TcpStream, NetError)`

Connect to a host and port. There is no connect timeout — an unreachable address waits for the kernel.

## Methods

### `l.accept() TcpStream | NetError`

Take the next connection. With `l.set_timeout(ms)` set, this answers `TimedOut` rather than waiting.

### `l.share() TcpListener | IoError`

A second listener over the SAME socket: `dup(2)`. Whichever handle calls `accept()` first takes the waiting connection, which is the shared-listener pattern — several workers accepting on one port — and the reason a listener gets `share()` while it implements no contract. Closing either handle leaves the other accepting, and each one closes its own descriptor on drop.

The receiver is a plain borrow, so `let TcpListener twin = l.share()??` leaves `l` usable, and closing `twin` spends only `twin`. A socket has no `.clone()` (`CE2431`); this is the operation that means a second handle, and its name says so.

```sushi
use <net/tcp>

fn main() i32:
    let TcpListener server = listen("127.0.0.1", 0, 1).realise(TcpListener(-1))
    match server.share():
        Result.Ok(twin) -> println("two listeners: {twin.is_open() and server.is_open()}")
        Result.Err(_) -> println("no second listener")
    return Result.Ok(0)
```

### `s.send_all(u8[] data) ~ | NetError` and `s.recv_exact(i32 count) u8[] | NetError`

The two loops every caller would otherwise write. One send may take fewer bytes than it was offered and one receive answers whatever arrived, so anything that depends on a byte count wants these rather than `send` and `read_bytes`.

`recv_exact` treats a peer that closes early as an error: a caller asking for an exact count has no use for a partial answer.

A whole exchange, in one process. A blocking `connect()` to a listening socket completes inside the kernel, so `accept()` finds the connection already waiting — listen, then connect, then accept, and one thread is enough:

```sushi
use <net/tcp>
use <collections/strings>

fn exchange() ~ | NetError:
    let TcpListener listener = listen("127.0.0.1", 0, 8)??
    listener.set_timeout(5000)??

    let i32 port = listener.local_port()??
    let TcpStream client = connect("127.0.0.1", port)??
    let TcpStream served = listener.accept()??
    client.set_timeouts(5000, 5000)??
    served.set_timeouts(5000, 5000)??

    let u8[] greeting = "Mostly Harmless".to_bytes()
    client.send_all(greeting)??
    match served.recv_exact(15):
        Result.Ok(heard) -> println("the server heard {heard.to_string()}")
        Result.Err(_) -> println("the server heard nothing")

    client.close()??
    served.close()??
    listener.close()??
    return Result.Ok(~)

fn main() i32:
    match exchange():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

### `s.send(u8[] data) i32 | NetError`

One write, answering how many bytes it took. This is the one write that answers a COUNT: a socket's partial write says what the peer's window took, and the contract's `write_bytes` -- which writes everything -- cannot say that.

There is no `recv` beside it. One read on a socket is the contract's `read_bytes(i32 max)`: it answers an empty array when the peer closed cleanly, and a timeout is an error instead, so the two never read alike. Every `NetError` a read can answer has its `IoError` twin, so a second name would have carried nothing of its own.

### `s.set_timeouts(i32 recv_ms, i32 send_ms) ~ | NetError` and `l.set_timeout(i32 accept_ms) ~ | NetError`

Bound how long a call may wait. **Set these before anything blocks.** Without them a read waits for as long as the peer stays silent.

### `s.peer_ip()`, `s.peer_port()`, `s.local_port()`, `l.local_port()`

Who is at each end. All four carry the `| NetError` channel. `local_port` exists on both types: the listener's is the one that reads back a port the kernel chose, and the stream's is this end of a connection.

### `s.close(nom self) ~ | NetError` and `l.close(nom self) ~ | NetError`

Close, and CONSUME the handle. A use after the close is **CE2435**, which is what makes a second close unreachable rather than merely harmless.

Neither is required. A socket closes itself when its owner leaves scope; `close()` is for the caller who must see a failure the destructor would swallow. A socket held in a struct FIELD cannot be closed explicitly — a field read is a borrow, and consuming one is **CE2411**.

## Limitations

Blocking sockets only. There is no connect timeout, and no non-blocking mode.

## See also

- [Socket primitives](socket.md) — the layer underneath
- [DNS](dns.md) — resolving a name before connecting
- [URLs](url.md) — pulling a host and port out of a URL
