# ASCII Bytes

[← Back to Standard Library](../../standard-library.md)

ASCII character classes and case maps, as methods on `u8`.

## Import

None. The methods are on `u8`, a built-in type, so they are available in every unit with
no import ([Extension visibility](../../design/extension-visibility.md), R1). The module
declares no name of its own, so `use <text/ascii>` brings nothing.

## Overview

`text/ascii` is a **Sushi-source** standard-library module: it ships as bundled `.sushi`
source, and the program loads it as a compilation unit when a unit calls one of its
methods. It adds ten extension methods to `u8`.

Each method tests the byte VALUE alone. No character set and no locale is read. A byte from
128 to 255 is never a digit, a letter, a hex digit or a space, and the two case maps return
it unchanged. This is the rule of Rust (`u8::is_ascii_*`) and Zig (`std.ascii`). It is not
the rule of C `<ctype.h>`, where the answer changes with the locale.

Every method is **bare**: it has no error channel, and a call gives the value itself. Do not
write `??` on a call. Each method is total over the 256 byte values, and allocates nothing.

A byte literal (`a'x'`) is a `u8` by default, so `a'7'.is_ascii_digit()` needs no cast, and a
string byte `s[i]` is a `u8` too.

A program that declares its own `extend u8 is_ascii_digit()` (or any other name of this
module) gets [CE2097](../../error-catalog.md#ce2097), with a note at the method of this
module: a stdlib method on a built-in type is visible in every unit, so an extension of
its name could never be called. This holds with or without an import.

## Methods

### `is_ascii_digit() bool`

Tells if the byte is an ASCII decimal digit.

The answer is `true` for the bytes 48 to 57 (`a'0'` to `a'9'`). It is `false` for every other
byte.

```sushi
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

Answers `true` when the byte is an ASCII uppercase letter.

The set is the bytes 65 to 90, `a'A'` to `a'Z'`. Every other byte gives `false`, a byte
from 128 to 255 too.

```sushi
fn main() i32:
    println(a'Q'.is_ascii_upper())      # true
    println(a'q'.is_ascii_upper())      # false
    println(a'\xc9'.is_ascii_upper())   # false
    return 0
```

### `is_ascii_lower() bool`

Answers `true` when the byte is an ASCII lowercase letter.

The set is the bytes 97 to 122, `a'a'` to `a'z'`. Every other byte gives `false`, a byte
from 128 to 255 too.

```sushi
fn main() i32:
    println(a'q'.is_ascii_lower())      # true
    println(a'Q'.is_ascii_lower())      # false
    println(a'\xe9'.is_ascii_lower())   # false
    return 0
```

### `is_ascii_alpha() bool`

Answers `true` when the byte is an ASCII letter, uppercase or lowercase.

The set is 65-90 (`a'A'` to `a'Z'`) and 97-122 (`a'a'` to `a'z'`). Every other byte gives
`false`.

```sushi
fn main() i32:
    println(a'k'.is_ascii_alpha())    # true
    println(a'K'.is_ascii_alpha())    # true
    println(a'4'.is_ascii_alpha())    # false
    println(a'\xe9'.is_ascii_alpha()) # false
    return 0
```

### `is_ascii_alnum() bool`

Answers `true` when the byte is an ASCII letter or an ASCII decimal digit.

The set is 48-57 (`a'0'` to `a'9'`), 65-90 (`a'A'` to `a'Z'`) and 97-122 (`a'a'` to
`a'z'`). Every other byte gives `false`.

```sushi
fn main() i32:
    println(a'4'.is_ascii_alnum())    # true
    println(a'g'.is_ascii_alnum())    # true
    println(a'_'.is_ascii_alnum())    # false
    println(a'\xe9'.is_ascii_alnum()) # false
    return 0
```

### `is_ascii_hex() bool`

Tells if the byte is an ASCII hexadecimal digit.

The answer is `true` for the bytes 48 to 57 (`a'0'` to `a'9'`), 65 to 70 (`a'A'` to `a'F'`)
and 97 to 102 (`a'a'` to `a'f'`). It is `false` for every other byte.

```sushi
fn main() i32:
    println(a'c'.is_ascii_hex())       # true
    println(a'G'.is_ascii_hex())       # false
    let i32 n = 0
    foreach(b in "0xBeef!".to_bytes().iter()):
        if (b.is_ascii_hex()):
            n := n + 1
    println(n)                         # 5
    return 0
```

### `is_ascii_space() bool`

Answers true when the byte is ASCII white space.

The set is the C `isspace` set in the C locale: 9 (`a'\t'`), 10 (`a'\n'`), 11 (`a'\x0b'`,
vertical tab), 12 (`a'\x0c'`, form feed), 13 (`a'\r'`) and 32 (`a' '`). Every other byte gives
false. A byte from 128 to 255 gives false, so `a'\xa0'` (a no-break space in Latin-1) is not
white space.

```sushi
fn main() i32:
    println(a' '.is_ascii_space())       # true
    println(a'\t'.is_ascii_space())      # true
    println(a'_'.is_ascii_space())       # false
    println(a'\xa0'.is_ascii_space())    # false
    return 0
```

### `to_ascii_lower() u8`

Answers the lowercase byte for an ASCII uppercase letter, and the byte unchanged for every
other byte.

The bytes that change are 65 to 90 (`a'A'` to `a'Z'`). Each one gives the byte plus 32, so
the result is 97 to 122 (`a'a'` to `a'z'`). Every other byte, 128 to 255 included, is
returned unchanged.

```sushi
fn main() i32:
    println(a'G'.to_ascii_lower())      # 103 (a'g')
    println(a'g'.to_ascii_lower())      # 103 (unchanged)
    println(a'5'.to_ascii_lower())      # 53 (unchanged)
    println(a'\xc9'.to_ascii_lower())   # 201 (unchanged)
    return 0
```

### `to_ascii_upper() u8`

Answers the uppercase byte for an ASCII lowercase letter, and the byte unchanged for every
other byte.

The bytes that change are 97 to 122 (`a'a'` to `a'z'`). Each one gives the byte minus 32,
so the result is 65 to 90 (`a'A'` to `a'Z'`). Every other byte, 128 to 255 included, is
returned unchanged.

```sushi
fn main() i32:
    println(a'g'.to_ascii_upper())      # 71 (a'G')
    println(a'G'.to_ascii_upper())      # 71 (unchanged)
    println(a'5'.to_ascii_upper())      # 53 (unchanged)
    println(a'\xe9'.to_ascii_upper())   # 233 (unchanged)
    return 0
```

### `hex_value() Maybe@(u8)`

Answers the value of an ASCII hexadecimal digit, or `Maybe.None()` when the byte is not one.

48-57 (`a'0'` to `a'9'`) give `Maybe.Some(0)` to `Maybe.Some(9)`. 65-70 (`a'A'` to `a'F'`) and
97-102 (`a'a'` to `a'f'`) give `Maybe.Some(10)` to `Maybe.Some(15)`. Every other byte gives
`Maybe.None()`, and so does each byte from 128 to 255.

The answer is a `Maybe@(u8)`, not a `Result`, so `??` does not apply to it directly. Read it
with `.realise(default)` when a default value is correct, for example `255` as a mark for "not a
digit". Use `match` when the two cases must do different things. In a `| E` body,
`b.hex_value().or_err(nom e)??` makes a missing digit an error.

```sushi
fn main() i32:
    println(a'7'.hex_value().realise(255))    # 7
    println(a'b'.hex_value().realise(255))    # 11
    println(a'F'.hex_value().realise(255))    # 15
    println(a'g'.hex_value().realise(255))    # 255
    match a'x'.hex_value():
        Maybe.Some(v) -> println("digit {v}")
        Maybe.None() -> println("not a hex digit")    # not a hex digit
    return 0
```
