# URLs

[← Back to Standard Library](../../standard-library.md)

`Url`: an absolute URL split into its RFC 3986 parts. Pure Sushi, no system call.

## Import

```sushi
use <net/url>
```

## Overview

`net/url` is a **Sushi-source** standard-library module: it ships as bundled `.sushi` source and is merged as a compilation unit when you import it.

The split is **lexical** and stops there: no name lookup, no percent decoding, and no address validation. A host is kept as written, so a caller that wants an `IpAddr` hands `u.host` to `<net/ip>` itself — which is why this module deliberately does not import that one, and why a program that only parses URLs never compiles the IPv6 parser.

## Types

```sushi
public struct Url:
    string scheme
    string userinfo
    string host
    i32 port
    string path
    string query
    string fragment

public enum UrlError:
    Empty    NoScheme    BadScheme
    NoHost   BadPort     UnclosedHost
```

## Functions

### `parse_url(string text) -> Result@(Url, UrlError)`

Split an absolute URL. The scheme and the host are lowercased; every other part is kept as written. A part the text did not carry is empty, and a port it did not carry is **0**.

```sushi
use <net/url>

fn main() i32:
    match parse_url("https://omakase.lubica.net/api/v1/packages?q=json"):
        Result.Ok(u) ->
            println("{u.scheme} {u.host} {u.port_or_default()} {u.path}")
        Result.Err(e) -> println("{e.text()}")

    return Result.Ok(0)
```

Two decisions worth knowing:

- **A port of 0 means the text did not carry one.** The scheme's default is a separate question, so the struct never claims a port that was not written.
- **A URL with an authority and no path gets `/`**, as RFC 3986 §6.2.3 requires. That is what makes `http://host` and `http://host/` one address.

The query is the **raw** text after `?`, not a list of pairs: whether `&` or `;` separates them and what a repeated key means is the caller's policy, not this module's.

`parse_url` is the one free function. The questions a parsed URL answers are **bare** extension methods — none of them can fail, so none carries a wrapper.

### `u.port_or_default() i32`

The written port, or the scheme's default. The known schemes are `http` 80, `https` 443, `ws` 80, `wss` 443, `ftp` 21 and `ssh` 22; anything else with no port answers `-1`.

### `u.host_is_ipv6_literal() bool`

Whether the host was written bracketed, as `http://[::1]:8080/`. The brackets are not kept in `host`, so this is the only way to tell a literal from a name after parsing.

### `u.text() string`

Rebuild the URL as canonical text. A port equal to the scheme's default is dropped, an IPv6 host is bracketed again, and an empty userinfo, query or fragment writes no delimiter.

### `e.text() string`

One stable line per `UrlError` refusal.

## Limitations

An absolute URL only: a relative reference is refused, because there is no base to resolve it against. No percent decoding and no IDNA. A port is read as written and is not checked against the scheme.

## See also

- [IP addresses](ip.md) — reading `u.host` when it is a literal
- [TCP](tcp.md) — connecting to `u.host` and `u.port_or_default()`
