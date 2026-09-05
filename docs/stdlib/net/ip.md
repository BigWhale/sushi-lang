# IP Addresses

[← Back to Standard Library](../../standard-library.md)

`IpAddr`: parse and format internet addresses, in both families. Pure Sushi, no system call.

## Import

```sushi
use <net/ip>
```

The import brings `NetError`, the channel `parse_ip` answers: the module re-exports [`<net/error>`](error.md), so a unit that matches on an error writes no second line.

## Overview

`net/ip` is a **Sushi-source** standard-library module: it ships as bundled `.sushi` source and is merged as a compilation unit when you import it. Everything works on the text alone — no system call, no name lookup, no network. RFC 4291 decides which forms are read and RFC 5952 decides the text that comes back.

The parsers and the named constants are free functions. Everything an address answers about itself is a **bare** extension method — no wrapper, so a predicate goes straight into a condition: `if (a.is_loopback()):`.

## Types

```sushi
public enum IpAddr:
    V4(u32)
    V6(u64, u64)
```

The payload is numeric on purpose. A `V4` carries one `u32` and a `V6` carries the address as two `u64`, so an `IpAddr` **owns no heap**: it copies, it needs no `.clone()`, and a `match` arm binds a number rather than a buffer.

## Functions

### `parse_ip(string text) -> Result@(IpAddr, NetError)`

Read an address of either family. A text holding a dot and no colon is read as IPv4; anything else is read as IPv6. The error is `NetError.InvalidAddress`, which is what lets this compose with `<net/dns>` under `??` without a third error enum.

```sushi
use <net/ip>

fn main() i32:
    match parse_ip("192.168.0.1"):
        Result.Ok(a) -> println("{a.text()} is private: {a.is_private()}")
        Result.Err(_) -> println("not an address")

    return Result.Ok(0)
```

**What is refused, and why it matters.** A leading zero in a dotted quad is refused, exactly as `inet_pton` refuses it — so `010.0.0.1` is not an address here and can never be read as octal. A zone identifier (`fe80::1%eth0`) is refused. A trailing character is a refusal rather than an ignored suffix, so `1.2.3.4a` does not quietly become `1.2.3.4`.

### `parse_ip_v4(string text)` and `parse_ip_v6(string text)`

The same, family by family, when you already know which one you want. Both answer `Result@(IpAddr, NetError)`.

### `a.text() string`

The canonical text, as a plain string — rendering cannot fail. IPv4 prints as a dotted quad. IPv6 follows RFC 5952, which is stricter than it looks:

- lowercase hex, and no leading zero inside a group;
- `::` covers the **longest** run of zero groups;
- the **leftmost** run wins when two tie, so `2001:db8:0:0:1:0:0:1` prints as `2001:db8::1:0:0:1`;
- a run of **one** zero group is never compressed, because `::` there is no shorter;
- an IPv4-mapped address prints in the mixed form, `::ffff:192.168.0.1`.

### `a.is_loopback()`, `a.is_unspecified()`, `a.is_private()`, `a.is_v4()`, `a.is_v6()`, `a.is_v4_mapped()`

The questions an address answers about itself. Each answers a plain `bool`.

`is_private` covers 10/8, 172.16/12 and 192.168/16 for IPv4, and fc00::/7 for IPv6.

### `v4_loopback()`, `v4_any()`, `v6_loopback()`, `v6_any()`

The four addresses worth having by name: `127.0.0.1`, `0.0.0.0`, `::1` and `::`.

## Limitations

No zone identifiers. Surrounding space is not trimmed. Only the IPv4-mapped form prints in the mixed notation; the deprecated IPv4-compatible form prints as hex.

## See also

- [DNS](dns.md) — a name resolved into these
- [Socket primitives](socket.md) — `NetError`
