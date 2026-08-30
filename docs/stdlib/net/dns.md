# DNS

[← Back to Standard Library](../../standard-library.md)

`resolve`: a host name turned into typed addresses.

## Import

```sushi
use <net/dns>
```

## Overview

`net/dns` is a **Sushi-source** standard-library module: it ships as bundled `.sushi` source and is merged as a compilation unit when you import it. It is `sock_dns_resolve` from `<net/socket>` with `parse_ip` from `<net/ip>` over each answer, so a caller is handed `IpAddr` and never a string it has to read for itself.

An answer the parser cannot read is **dropped** rather than failing the call, so one unusual family does not lose the usable answers.

## Functions

### `resolve(string host) -> Result@(IpAddr[], NetError)`

Resolve a name, or a numeric address, into addresses. **A text that is already an address answers with exactly that address and makes no network request.**

```sushi
use <net/dns>
use <net/ip>

fn main() i32:
    match resolve("localhost"):
        Result.Ok(addresses) ->
            foreach(a in addresses.iter()):
                println("{a.text()}")
        Result.Err(_) -> println("no answer")

    return Result.Ok(0)
```

### `resolve_first(string host, bool want_v4) -> Result@(IpAddr, NetError)`

The first answer of one family. `true` asks for IPv4, `false` for IPv6. When no answer is of that family the error is `NetError.InvalidAddress` rather than an empty list.

## Limitations

The order of the answers is the resolver's and is not stable between calls, so do not depend on it. There is no reverse lookup and no SRV.

## See also

- [IP addresses](ip.md) — what the answers are
- [TCP](tcp.md) — `tcp_connect` resolves a name for you
