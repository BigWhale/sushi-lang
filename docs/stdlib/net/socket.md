# Socket Primitives

[← Back to Standard Library](../../standard-library.md)

The raw BSD socket calls, as `Result`-returning primitives: `sock_tcp_connect`, `sock_tcp_listen`, `sock_tcp_accept`, `sock_send`, `sock_recv`, `sock_close`, the timeouts, the UDP trio, and `sock_dns_resolve`.

## Import

```sushi
use <net/socket>
```

## Overview

`net/socket` is the low-level half of the network stack. It exists because the byte-level work cannot be written in Sushi: an array or a struct may not cross the C ABI (`CE5003`), a `u8[]` has no way to yield a pointer, and a `ptr` is opaque, so `sockaddr` could neither be built nor read.

**Most programs want `<net/tcp>`, `<net/udp>`, `<net/dns>` or `<net/url>` instead.** Those wrap these primitives in types and are what the examples below build on. Reach for `<net/socket>` when you want a descriptor without a wrapper.

Every function answers `Result@(T, NetError)`. A descriptor is a bare `i32`, and closing it is the caller's business: there is no RAII for a socket, exactly as there is none for a `file`.

## Types

`NetError` is a predefined enum, so it needs no import:

```sushi
public enum NetError:
    ConnectionRefused    ConnectionReset      TimedOut
    Closed               AddressInUse         AddressNotAvailable
    NetworkUnreachable   HostUnreachable      ResolveFailed
    PermissionDenied     TooManyOpen          InvalidAddress
    Interrupted          MessageTooLarge      Other
```

Two of the mappings are worth knowing:

- **`TimedOut` covers `EAGAIN`.** Every socket this module creates is blocking, so `EAGAIN` from a read or a write can only mean the timeout you asked for expired — that is how POSIX reports a socket timeout, `connect` reporting `ETIMEDOUT` instead.
- **`Closed` covers `EBADF`.** An operation on a descriptor that was already closed names the condition rather than falling through to `Other`.

`ResolveFailed` is the one variant no `errno` reaches: `getaddrinfo` answers with an `EAI_*` code, which is not `errno` and whose sign is not even the same on macOS and Linux.

## Functions

### `sock_tcp_listen(string host, i32 port, i32 backlog) -> Result@(i32, NetError)`

Bind a listening socket. An empty host means the wildcard address. Port 0 asks the kernel to choose one, and `sock_local_port` reads back what it chose — which is what lets a test bind without naming a port. `SO_REUSEADDR` is always set, so a port whose last connection is still in `TIME_WAIT` binds again at once.

### `sock_local_port(i32 fd) -> Result@(i32, NetError)`

The port a descriptor actually bound, through `getsockname`.

```sushi
use <net/socket>

fn main() i32:
    match sock_tcp_listen("127.0.0.1", 0, 8):
        Result.Ok(fd) ->
            let i32 port = sock_local_port(fd).realise(-1)
            println("listening on {port}")
            sock_close(fd).realise(-1)
        Result.Err(_) -> println("could not listen")

    return Result.Ok(0)
```

### `sock_tcp_connect(string host, i32 port) -> Result@(i32, NetError)`

Connect to a host and port. The host may be a name or a numeric address; a name is resolved and every answer is tried in turn. There is **no connect timeout** — that needs a non-blocking socket and `select` — so an address that answers nothing waits for the kernel to give up.

### `sock_tcp_accept(i32 fd) -> Result@(i32, NetError)`

Take the next connection waiting on a listener. Give the listener a timeout first and this answers `TimedOut` instead of waiting forever.

### `sock_send(i32 fd, u8[] data) -> Result@(i32, NetError)`

Write bytes, and answer how many went. **One write may take fewer bytes than it was offered**; `tcp_send_all` in `<net/tcp>` is the loop. The buffer stays the caller's: this borrows it and never frees it.

### `sock_recv(i32 fd, i32 max) -> Result@(u8[], NetError)`

Read what one read gives, up to `max` bytes.

**An empty answer means the peer closed cleanly.** `recv` sets no `errno` at the end of a stream, so reporting an error there would report a stale one. A timeout is the other case and answers `Err(TimedOut)`, which leaves the two unambiguous — so `while data.len() > 0` is a correct read loop.

### `sock_close(i32 fd) -> Result@(i32, NetError)`

Close a descriptor.

### `sock_peer_ip(i32 fd) -> Result@(string, NetError)` and `sock_peer_port(i32 fd) -> Result@(i32, NetError)`

Who is at the other end. The address is rendered numerically and asks no resolver, so neither call makes a network request. They are separate so that a test can assert the address — which is fixed — without asserting an ephemeral port.

### `sock_set_recv_timeout(i32 fd, i32 ms)` and `sock_set_send_timeout(i32 fd, i32 ms)`

Bound how long a read or a write may wait; both answer `Result@(i32, NetError)`. A bound that expires answers `NetError.TimedOut`. **A listening socket honours the receive bound**, which is what gives `sock_tcp_accept` a bound too.

### `sock_udp_bind(string host, i32 port) -> Result@(i32, NetError)`

Bind a datagram socket. `SO_REUSEADDR` is deliberately **not** set here: on a datagram socket it means several sockets sharing a port, which is a different thing to ask for.

### `sock_udp_send_to(i32 fd, u8[] data, string host, i32 port) -> Result@(i32, NetError)`

Send one datagram. The destination is resolved on every call.

### `sock_udp_recv_from(i32 fd, i32 max) -> Result@(Datagram, NetError)`

Wait for one datagram and answer it with its sender. `Datagram` is a predefined struct:

```sushi
public struct Datagram:
    u8[] data
    string peer_ip
    i32 peer_port
```

The sender rides along with the bytes because an unconnected datagram socket has no `getpeername`: the sender exists only at the instant its datagram arrives.

### `sock_dns_resolve(string host) -> Result@(string[], NetError)`

Resolve a name to numeric address texts. `<net/dns>` reads these into `IpAddr`, which is what most callers want.

## See also

- [TCP](tcp.md) — `TcpStream` and `TcpListener` over these primitives
- [UDP](udp.md) — `UdpSocket`
- [DNS](dns.md) — a name resolved into typed addresses
- [IP addresses](ip.md) — `IpAddr`, parse and format
- [URLs](url.md) — lexical URL splitting
