# Binary Integers

[← Back to Standard Library](../../standard-library.md)

Fixed-width unsigned integers in a byte array and back, in either byte order, as methods
on `u8[]`.

## Import

None. The methods are on `u8[]`, a built-in type, so they are available in every unit with
no import ([Extension visibility](../../design/extension-visibility.md), R1). The module
declares no name of its own, so `use <encoding/binary>` brings nothing.

## Overview

`encoding/binary` is a **Sushi-source** standard-library module: it ships as bundled
`.sushi` source, and the program loads it as a compilation unit when a unit calls one of
its methods. It adds twelve extension methods to `u8[]`: six reads and six writes, for
`u16`, `u32` and `u64`.

Each method name gives the width and the byte order. `le` (little-endian) puts the least
significant byte first. `be` (big-endian) puts the most significant byte first. A value
uses exactly its width in bytes: there is no alignment and no padding. There is no `u8`
method, because `buf[at]` and `buf.push(b)` already read and write one byte.

Every method is **bare**: it has no error channel, and a call gives its answer directly. Do
not write `??` on a call. Every method is total, and nothing traps. A read that does not
fit in the array gives `Maybe.None()`.

A signed integer or a float goes through its unsigned bits:

```sushi
fn main() i32:
    let u8[] header = from([0x00, 0x2a, 0xff, 0xff])
    let u16 count = header.read_u16_be(0).realise(0)
    let i16 delta = header.read_u16_le(2).realise(0) as i16
    println("count={count} delta={delta}")    # count=42 delta=-1
    println(header.read_u32_le(1).is_some())  # false
    return 0
```

For a float, read the bits and use `f64.from_bits` (or `f32.from_bits`), and write
`f.to_bits()`.

A program that declares its own `extend u8[] read_u16_le(i32 at)` (or any other name of this
module) gets [CE2097](../../error-catalog.md#ce2097), with a note at the method of this
module: a stdlib method on a built-in type is visible in every unit, so an extension of
its name could never be called. This holds with or without an import.

## Reads

| Method | Width | Byte order |
|---|---|---|
| `read_u16_le(i32 at) Maybe@(u16)` | 2 bytes | least significant first |
| `read_u16_be(i32 at) Maybe@(u16)` | 2 bytes | most significant first |
| `read_u32_le(i32 at) Maybe@(u32)` | 4 bytes | least significant first |
| `read_u32_be(i32 at) Maybe@(u32)` | 4 bytes | most significant first |
| `read_u64_le(i32 at) Maybe@(u64)` | 8 bytes | least significant first |
| `read_u64_be(i32 at) Maybe@(u64)` | 8 bytes | most significant first |

A read gives `Maybe.Some(v)`, where `v` is the value of the W bytes from `buf[at]` to
`buf[at + W - 1]` in the given byte order (W is the width). The receiver is a plain borrow,
and a read does not change the array and allocates nothing.

The answer is `Maybe.None()` when the bytes are not all in the array:

- `at` is negative.
- `at + W` is more than `len()`. The last offset that gives a value is `len() - W`.
- The array is shorter than W bytes. An empty array always gives `Maybe.None()`.

A very large `at` (to the `i32` maximum) also gives `Maybe.None()`. It does not wrap to a
negative offset.

```sushi
fn main() i32:
    let u8[] buf = from([0x01, 0x02, 0x03, 0x04, 0x05])
    println(buf.read_u16_le(0).realise(0))    # 513
    println(buf.read_u16_be(0).realise(0))    # 258
    println(buf.read_u32_be(1).realise(0))    # 33752069
    println(buf.read_u32_be(2).is_some())     # false
    println(buf.read_u16_le(-1).is_some())    # false

    match buf.read_u64_le(0):
        Maybe.Some(v) -> println(v)
        Maybe.None() -> println("too short")  # too short

    let u8[] raw = from([0x3f, 0xf8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    println(f64.from_bits(raw.read_u64_be(0).realise(0)))   # 1.5
    return 0
```

In a function with an error channel, `or_err` turns a `None` into an error:
`buf.read_u32_be(at).or_err(nom StdError.Error)??`.

## Writes

A push appends the W bytes of a value to the end of the array, and the length of the array
increases by W. W is 2 for a `u16`, 4 for a `u32` and 8 for a `u64`. The name of the method
gives the byte order:

- `le` (little-endian): the least significant byte goes first.
- `be` (big-endian): the most significant byte goes first.

The bytes that are already in the array do not change. There is no alignment and no
padding: the first byte of the value goes directly after the last byte of the array.

Every push is **bare** and total: it has no error channel, and every value of the type
is legal. Do not write `??` on a call. The answer is `~`.

A push writes its receiver, so the receiver must be a value that you can write. A local, a
field of a local and a `poke` parameter are correct. A by-value parameter is a read-only
borrow ([CE2422](../../error-catalog.md#ce2422)), and a `peek` parameter is a read-only
reference ([CE2408](../../error-catalog.md#ce2408)). Declare the parameter `poke u8[]` and
call the function with `poke`.

To push a signed value, cast it to the unsigned type of the same width: `n as u32`. To push
a float, push its bits: `x.to_bits()` gives a `u64` for an `f64` and a `u32` for an `f32`.

### `push_u16_le(poke self, u16 v) ~` and `push_u16_be(poke self, u16 v) ~`

Appends the 2 bytes of `v`. For `v = 0x0102`, `push_u16_le` appends `0x02 0x01` and
`push_u16_be` appends `0x01 0x02`.

```sushi
fn main() i32:
    let u8[] buf = from([])
    buf.push_u16_le(0x0102)
    buf.push_u16_be(0x0102)
    println(buf.len())                              # 4
    println("{buf[0]} {buf[1]} {buf[2]} {buf[3]}")  # 2 1 1 2
    return 0
```

### `push_u32_le(poke self, u32 v) ~` and `push_u32_be(poke self, u32 v) ~`

Appends the 4 bytes of `v`. For `v = 0x0A0B0C0D`, `push_u32_le` appends
`0x0D 0x0C 0x0B 0x0A` and `push_u32_be` appends `0x0A 0x0B 0x0C 0x0D`. The example also
pushes a signed value through `as u32`: `-2` is `0xFFFFFFFE`.

```sushi
fn main() i32:
    let u8[] le = from([])
    le.push_u32_le(0x0A0B_0C0D)
    println("{le[0]} {le[1]} {le[2]} {le[3]}")      # 13 12 11 10
    let u8[] be = from([])
    be.push_u32_be(0x0A0B_0C0D)
    println("{be[0]} {be[1]} {be[2]} {be[3]}")      # 10 11 12 13
    let i32 n = -2
    let u8[] signed = from([])
    signed.push_u32_le(n as u32)
    println("{signed[0]} {signed[3]}")              # 254 255
    return 0
```

### `push_u64_le(poke self, u64 v) ~` and `push_u64_be(poke self, u64 v) ~`

Appends the 8 bytes of `v`. For `v = 0x0102030405060708`, `push_u64_le` appends
`0x08 0x07 ... 0x01` and `push_u64_be` appends `0x01 0x02 ... 0x08`. The example writes
through a `poke` parameter, and pushes the bits of an `f64`: `1.0` is
`0x3FF0000000000000`.

```sushi
fn put_header(poke u8[] out, u64 id, f64 score) ~:
    out.push_u64_be(id)
    out.push_u64_le(score.to_bits())

fn main() i32:
    let u8[] out = from([])
    put_header(poke out, 1, 1.0)
    println(out.len())                              # 16
    println("{out[0]} {out[7]}")                    # 0 1
    println("{out[14]} {out[15]}")                  # 240 63
    return 0
```
