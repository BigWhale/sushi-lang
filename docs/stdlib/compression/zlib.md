# Compression (zlib)

[← Back to Standard Library](../../standard-library.md)

DEFLATE and the zlib container, written in Sushi: `zlib_compress`,
`zlib_uncompress`, `deflate_raw`, `inflate_raw`, `adler32` and `zlib_error_text`.
Data this module writes is readable by any zlib, and data any zlib writes is
readable here.

## Import

```sushi
use <compression/zlib>

fn main() i32:
    return Result.Ok(0)
```

## Overview

`compression/zlib` is a **Sushi-source** standard-library module: it ships as bundled
`.sushi` source and is merged as a compilation unit when you import it. There is no C
library behind it and no FFI — the whole codec is Sushi.

It implements two formats:

| Format | Entry points | Framing |
|---|---|---|
| RFC 1951, raw DEFLATE | `deflate_raw`, `inflate_raw` | none |
| RFC 1950, zlib | `zlib_compress`, `zlib_uncompress` | two-byte header, Adler-32 trailer |

**The decoder is complete.** It reads all three block types — stored, fixed Huffman and
dynamic Huffman — so it accepts anything a conforming encoder produces.

**The encoder is not.** It emits stored blocks and fixed-Huffman blocks, never a dynamic
Huffman block, so its output is correct but larger than a full encoder's. On ordinary
prose expect about 1.2 times the size of `zlib` at level 6; on highly repetitive input the
gap is wider, because a dynamic tree would spend two or three bits on a symbol where the
fixed tree spends eight.

gzip (RFC 1952) is not handled, so there is no CRC-32 here.

## Types

```
public enum ZError:
    Truncated(i32)              # byte offset where the input ran out
    BadBlockType(i32)           # BTYPE 3 is reserved
    BadStoredLength(i32, i32)   # LEN and NLEN, which must be complements
    BadCode()                   # incomplete or over-subscribed Huffman code
    BadDistance(i32, i32)       # the distance asked for, and the bytes available
    BadSymbol(i32)              # a length or distance symbol with no meaning
    BadHeader(i32)              # the zlib header word that failed its check
    BadChecksum(u32, u32)       # the Adler-32 expected, and the one computed
    BadLevel(i32)               # a level outside 0 to 9
    DictNeeded()                # FDICT is set; a preset dictionary is not supported
```

Every payload carries the detail a caller might want to report. `zlib_error_text` turns
any of them into one stable line, so a tool does not have to match every variant.

## Functions

### `zlib_compress(u8[] src, i32 level) -> u8[] | ZError`

Compress into an RFC 1950 stream. `level` is 0 for stored blocks only, and 1 through 9 to
compress — the level sets how far the match search walks each hash chain, so a higher
level trades time for a smaller result. A level outside that range is `ZError.BadLevel`.

```sushi
use <compression/zlib>

fn run() i32 | ZError:
    let u8[] data = from([77, 111, 115, 116, 108, 121, 32, 72, 97, 114, 109, 108, 101, 115, 115])
    let u8[] packed = zlib_compress(data, 6)??
    println("{data.len()} bytes in, {packed.len()} bytes out")
    return Result.Ok(0)

fn main() i32:
    return Result.Ok(run().realise(1))
# 15 bytes in, 23 bytes out
```

Fifteen bytes of text do not compress: the header, the trailer and the coded literals cost
more than the input. Compression needs something to repeat.

### `zlib_uncompress(u8[] src) -> u8[] | ZError`

Decompress an RFC 1950 stream. The header is checked, the body inflated, and the trailing
Adler-32 compared against the bytes recovered. A mismatch is `ZError.BadChecksum`, so a
corrupted stream is reported rather than returned.

```sushi
use <compression/zlib>

fn run() i32 | ZError:
    let u8[] data = from([65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65])
    let u8[] packed = zlib_compress(data, 6)??
    let u8[] back = zlib_uncompress(packed)??
    println("recovered {back.len()} bytes")
    return Result.Ok(0)

fn main() i32:
    return Result.Ok(run().realise(1))
# recovered 12 bytes
```

### `deflate_raw(u8[] src, i32 level) -> u8[] | ZError`

Compress with no container: no header, no checksum. This is the stream that
`zlib.compressobj(wbits=-15)` produces in Python and that `inflate_raw` reads back.

Incompressible input falls back to stored blocks, so the result never exceeds the input by
more than five bytes per 65535.

### `inflate_raw(u8[] src) -> u8[] | ZError`

Decompress a raw DEFLATE stream. Stored, fixed and dynamic blocks are all read, and a
stream of several blocks is followed to its final one.

```sushi
use <compression/zlib>

fn run() i32 | ZError:
    # a stored block: BFINAL set, BTYPE 00, LEN 3, NLEN its complement
    let u8[] blob = from([0x01, 0x03, 0x00, 0xfc, 0xff, 0x61, 0x62, 0x63])
    let u8[] out = inflate_raw(blob)??
    println("{out.len()} bytes: {out.to_string()}")
    return Result.Ok(0)

fn main() i32:
    return Result.Ok(run().realise(1))
# 3 bytes: abc
```

### `adler32(u8[] data) -> u32`

The RFC 1950 checksum: two running sums modulo 65521, packed with the high sum first. An
empty input gives 1. It cannot fail, so `.realise(0)` is the natural way to take the value.

```sushi
use <compression/zlib>

fn main() i32:
    let u8[] data = from([77, 111, 115, 116, 108, 121, 32, 72, 97, 114, 109, 108, 101, 115, 115])
    println("{adler32(data).realise(0)}")
    return Result.Ok(0)
# 777324008
```

### `zlib_error_text(ZError e) -> string`

One stable line for any error. The text does not include the payload values, so it is safe
to compare against.

## Error handling

```sushi
use <compression/zlib>

fn decode(u8[] blob) i32:
    match zlib_uncompress(blob):
        Result.Ok(out) ->
            println("ok, {out.len()} bytes")
            return Result.Ok(0)
        Result.Err(e) ->
            println("failed: {zlib_error_text(e)??}")
            return Result.Ok(1)
    return Result.Ok(1)

fn main() i32:
    # a zlib header whose 31-check fails
    let u8[] bad = from([0x78, 0x9d, 0x03, 0x00, 0x00, 0x00, 0x00, 0x01])
    return Result.Ok(decode(bad).realise(1))
# failed: bad zlib header
```

To act on a specific failure, match the variant instead and read its payload:

```sushi
use <compression/zlib>

fn report(ZError e) ~:
    match e:
        ZError.BadChecksum(want, got) ->
            println("checksum {want} expected, {got} computed")
        ZError.BadDistance(dist, have) ->
            println("distance {dist} with only {have} bytes decoded")
        ZError.Truncated(at) ->
            println("input ended at byte {at}")
        _ ->
            println("{zlib_error_text(e)??}")
    return Result.Ok(~)

fn main() i32:
    report(ZError.BadChecksum(777324008 as u32, 12345 as u32))
    return Result.Ok(0)
# checksum 777324008 expected, 12345 computed
```

## Limitations

- The encoder never emits a dynamic Huffman block, so its ratio is short of a full
  encoder's. Decoding dynamic blocks is fully supported.
- gzip (RFC 1952) is not handled. There is no CRC-32 and no gzip header or trailer.
- A preset dictionary (`FDICT` set in the zlib header) is rejected with
  `ZError.DictNeeded`.
- Buffers of 2 GiB or more are out of range, because a length casts to a negative count.
  The read then reports `Truncated` rather than misbehaving.
- The whole input and the whole output are held in memory; there is no streaming entry
  point.
- Speed has not been tuned. Every array index is bounds-checked and the bit writer works
  one bit at a time, so this is far slower than a C zlib.

## See also

- [MessagePack](../encoding/msgpack.md) — the other pure-Sushi codec
- [Arrays](../collections/arrays.md) — the `u8[]` operations these entry points take
- [Files](../io/files.md) — reading and writing the bytes to compress
