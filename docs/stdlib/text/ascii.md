# ASCII Bytes

[← Back to Standard Library](../../standard-library.md)

ASCII character classes and case maps, as methods on `u8`.

## Import

```sushi
use <text/ascii>
```

## Overview

`text/ascii` is a **Sushi-source** standard-library module: it ships as bundled `.sushi`
source and is merged as a compilation unit when you import it. It adds ten extension
methods to `u8`.

Each method tests the byte VALUE alone. No character set and no locale is read. A byte from
128 to 255 is never a digit, a letter, a hex digit or a space, and the two case maps return
it unchanged. This is the rule of Rust (`u8::is_ascii_*`) and Zig (`std.ascii`). It is not
the rule of C `<ctype.h>`, where the answer changes with the locale.

Every method is **bare**: it has no error channel, and a call gives the value itself. Do not
write `??` on a call. Each method is total over the 256 byte values, and allocates nothing.

A byte literal (`a'x'`) is a `u8` by default, so `a'7'.is_ascii_digit()` needs no cast, and a
string byte `s[i]` is a `u8` too.

## Methods

### `is_ascii_digit() bool`

Tells if the byte is an ASCII decimal digit.

The answer is `true` for the bytes 48 to 57 (`a'0'` to `a'9'`). It is `false` for every other
byte.

```sushi
use <text/ascii>
use <collections/strings>

fn main() i32:
    println(a'7'.is_ascii_digit())     # true
    println(a'x'.is_ascii_digit())     # false
    let i32 n = 0
    foreach(b in "r2d2".to_bytes().iter()):
        if (b.is_ascii_digit()):
            n := n + 1
    println(n)                         # 2
    return 0
```

### `is_ascii_upper() bool`

TODO(worker)

### `is_ascii_lower() bool`

TODO(worker)

### `is_ascii_alpha() bool`

TODO(worker)

### `is_ascii_alnum() bool`

TODO(worker)

### `is_ascii_hex() bool`

TODO(worker)

### `is_ascii_space() bool`

TODO(worker)

### `to_ascii_lower() u8`

TODO(worker)

### `to_ascii_upper() u8`

TODO(worker)

### `hex_value() Maybe@(u8)`

TODO(worker)
