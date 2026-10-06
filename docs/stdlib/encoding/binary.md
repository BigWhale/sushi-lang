# Binary Integers

[← Back to Standard Library](../../standard-library.md)

Fixed-width unsigned integers in a byte array and back, in either byte order, as methods
on `u8[]`.

## Import

```sushi
use <encoding/binary>
```

## Overview

`encoding/binary` is a **Sushi-source** standard-library module: it ships as bundled
`.sushi` source and is merged as a compilation unit when you import it. It adds twelve
extension methods to `u8[]`: six reads and six writes, for `u16`, `u32` and `u64`.

Each method name gives the width and the byte order. `le` (little-endian) puts the least
significant byte first. `be` (big-endian) puts the most significant byte first. A value
uses exactly its width in bytes: there is no alignment and no padding. There is no `u8`
method, because `buf[at]` and `buf.push(b)` already read and write one byte.

Every method is **bare**: it has no error channel, and a call gives its answer directly. Do
not write `??` on a call. Every method is total, and nothing traps. A read that does not
fit in the array gives `Maybe.None()`.

A signed integer or a float goes through its unsigned bits:

```sushi
use <encoding/binary>

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

**Import the module in each unit that calls a method.** An extension method is found on its
type, so a call compiles in every unit of a program that loads the module, also in a unit
that does not import it. Do not depend on that: the unused-use lint (`--warn-unused`)
expects the import in the unit that calls the method, and a unit with no import of its own
breaks when the other unit stops importing the module.

A program that declares its own `extend u8[] read_u16_le(i32 at)` (or any other name of
this module) and imports `<encoding/binary>` gets the duplicate-function error
[CE0101](../../error-catalog.md#ce0101): an extension method of one name on one type is
global. A program that does not import the module is not affected.

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
use <encoding/binary>

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

TODO(worker)
