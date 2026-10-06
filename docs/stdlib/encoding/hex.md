# Hex

[← Back to Standard Library](../../standard-library.md)

Hexadecimal text of bytes, and the bytes of hexadecimal text.

## Import

```sushi
use <encoding/hex>
```

## Overview

`encoding/hex` is a **Sushi-source** standard-library module: it ships as bundled `.sushi`
source, and the compiler merges it as a compilation unit when you import it.

`encode` writes two LOWER-case digits for each byte, the high half first. `decode` reads
upper case, lower case and a mix of the two. It reads nothing other than the digits
`0`-`9`, `a`-`f` and `A`-`F`: a prefix such as `0x`, a separator or white space is an error.

A decode error is a value, not an exit: `decode` writes the `HexError` channel, so a call
gives `Result@(u8[], HexError)`. `encode` is bare: it cannot fail, and a call gives the
text itself.

`<encoding/base64>` also exports `encode` and `decode`. A program that imports the two
modules flat and calls `encode` gets [`CE3012`](../../error-catalog.md#ce3012). Import
them with `as`:

```sushi
use <encoding/hex> as hex
use <encoding/base64> as b64

fn main() i32:
    let u8[] data = from([0x4d, 0x48])
    println(hex.encode(data))    # 4d48
    println(b64.encode(data))    # TUg=
    return 0
```

## Types

```
public error HexError:
    OddLength       # the text holds an odd count of digits
    BadDigit(i32)   # the byte offset of the first byte that is not a hex digit
```

## Functions

### `encode(u8[] data) -> string`

Writes each byte as two lower-case hex digits. The text has two digits for each byte, and
an empty array gives `""`.

```sushi
use <collections/strings>
use <encoding/hex>

fn main() i32:
    let u8[] data = from([0xde, 0xad, 0xbe, 0xef])
    println(encode(data))                 # deadbeef
    println(encode("foobar".to_bytes()))  # 666f6f626172
    return 0
```

### `decode(string text) -> u8[] | HexError`

Reads hex text as bytes, two digits for each byte. `""` gives an empty array.

The errors, in the order of the checks:

1. `HexError.OddLength`: the count of digits is odd. This check comes first, so `"zzz"`
   is `OddLength` and not `BadDigit`.
2. `HexError.BadDigit(at)`: `at` is the BYTE offset in the text of the first byte that is
   not a hex digit. A multi-byte UTF-8 character counts as its bytes.

```sushi
use <encoding/hex>

fn show(string text) string:
    match decode(text):
        Result.Ok(bytes) -> return "{bytes}"
        Result.Err(HexError.OddLength) -> return "odd length"
        Result.Err(HexError.BadDigit(at)) -> return "bad digit at {at}"

fn main() i32:
    println(show("DEad01"))   # [222, 173, 1]
    println(show("abc"))      # odd length
    println(show("0g"))       # bad digit at 1
    return 0
```
