# Base64

[← Back to Standard Library](../../standard-library.md)

Base64 text of bytes (RFC 4648), and the bytes of Base64 text.

## Import

```sushi
use <encoding/base64>
```

## Overview

`encoding/base64` is a **Sushi-source** standard-library module: it ships as bundled
`.sushi` source, and the compiler merges it as a compilation unit when you import it.

The alphabet is the standard alphabet of RFC 4648 section 4: `A`-`Z`, `a`-`z`, `0`-`9`,
`+` and `/`, with `=` as the padding. The URL-safe alphabet (`-` and `_`) is not
supported.

`encode` always writes the padding. `decode` requires it: the length of the text must be
a multiple of 4. `decode` does not skip white space or line breaks. When the bits that
are not used in the last group are not zero (`"Zh=="`), `decode` accepts the text and
ignores those bits.

A decode error is a value, not an exit: `decode` writes the `Base64Error` channel, so a
call gives `Result@(u8[], Base64Error)`. `encode` is bare: it cannot fail, and a call
gives the text itself.

`<encoding/hex>` also exports `encode` and `decode`. A program that imports the two
modules flat and calls `encode` gets [`CE3012`](../../error-catalog.md#ce3012). Import
them with `as`:

```sushi
use <encoding/base64> as b64
use <encoding/hex> as hex

fn main() i32:
    let u8[] data = from([0x4d, 0x48])
    println(b64.encode(data))    # TUg=
    println(hex.encode(data))    # 4d48
    return 0
```

## Types

```
public error Base64Error:
    BadLength       # the length of the text is not a multiple of 4
    BadChar(i32)    # the byte offset of the first byte outside the alphabet
    BadPadding      # a `=` that is not part of the padding at the end
```

## Functions

### `encode(u8[] data) -> string`

Writes bytes as Base64 text. Each group of three bytes gives four characters. When the
last group has one byte, the text ends with `==`; when it has two bytes, the text ends with
`=`. An empty array gives `""`.

```sushi
use <collections/strings>
use <encoding/base64>

fn main() i32:
    println(encode("Mostly Harmless".to_bytes()))  # TW9zdGx5IEhhcm1sZXNz
    println(encode("f".to_bytes()))                # Zg==
    println(encode("fo".to_bytes()))               # Zm8=
    return 0
```

### `decode(string text) -> u8[] | Base64Error`

Reads Base64 text as bytes. `""` gives an empty array.

The errors, in the order of the checks:

1. `Base64Error.BadLength`: the length of the text is not a multiple of 4. This check
   comes first, so `"Zg== "` is `BadLength`.
2. Then `decode` reads the text from the start, and the first wrong byte gives the error:
   - `Base64Error.BadChar(at)`: the byte is not in the alphabet and is not `=`. `at` is
     its BYTE offset in the text. White space is a `BadChar`.
   - `Base64Error.BadPadding`: the byte is a `=` that is not one of the 1 or 2 padding
     bytes at the end of the text (`"Zm=v"`, `"Z==="`, `"===="`).

```sushi
use <encoding/base64>

fn show(string text) string:
    match decode(text):
        Result.Ok(bytes) -> return "{bytes}"
        Result.Err(Base64Error.BadLength) -> return "bad length"
        Result.Err(Base64Error.BadChar(at)) -> return "bad char at {at}"
        Result.Err(Base64Error.BadPadding) -> return "bad padding"

fn main() i32:
    println(show("Zm9v"))       # [102, 111, 111]
    println(show("Zg="))        # bad length
    println(show("Zm9v!A=="))   # bad char at 4
    println(show("Zm=v"))       # bad padding
    return 0
```
