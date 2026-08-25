# MessagePack

[← Back to Standard Library](../../standard-library.md)

A MessagePack decoder, written in Sushi: `mp_decode`, `mp_map_get`, and `mp_show`.
Decode-only — there is no encoder.

## Import

```sushi
use <encoding/msgpack>

fn main() i32:
    return Result.Ok(0)
```

## Overview

`encoding/msgpack` is a **Sushi-source** standard-library module: it ships as bundled
`.sushi` source and is merged as a compilation unit when you import it. It is the first
source module with concrete (non-generic) public functions.

`mp_decode` reads one whole buffer as exactly one MessagePack value and returns a
`MsgValue` tree. Decode errors are values, not exits.

## Types

```
enum MsgValue:
    Nil()
    Bool(bool)
    Int(i64)                       # every signed int, and every uint that fits i64
    UInt(u64)                      # only uint64 values above the i64 maximum
    Float(f64)                     # float32 widens to f64
    Str(string)
    Bin(u8[])
    Arr(MsgValue[])
    Map(MsgValue[], MsgValue[])    # parallel keys/values, wire order kept

enum MpError:
    Truncated(i32)      # byte offset where the input ran out
    Unsupported(u8)     # ext, fixext, timestamp, or the never-used 0xc1 tag
    BadUtf8(i32)        # offset just past the offending str payload
    Trailing(i32)       # bytes remain after the root value
```

A map is two parallel arrays, not a hash table: MessagePack keys are not limited to
strings, and the wire order stays visible and deterministic.

## Functions

### `mp_decode(u8[] buf) -> MsgValue | MpError`

Decode the whole buffer as one value. Trailing bytes after the root value give
`MpError.Trailing`.

```sushi
use <encoding/msgpack>

fn show_or_err(u8[] buf) string:
    match mp_decode(buf):
        Result.Ok(v) ->
            return Result.Ok(mp_show(v)??)
        Result.Err(_) ->
            return Result.Ok("decode error")

fn main() i32:
    let u8[] buf = from([0x82, 0xa1, 0x61, 0x01, 0xa1, 0x62, 0x91, 0x02])
    println(show_or_err(buf).realise("error"))    # {"a":1,"b":[2]}
    return Result.Ok(0)
```

### `mp_map_get(MsgValue m, string key) -> Maybe@(MsgValue)`

Scan a `Map` in wire order for a string key. The found value comes back as a clone, so
the tree stays intact. A missing key, a non-string key match, or a non-map argument gives
`Maybe.None`.

```sushi
use <encoding/msgpack>

fn lookup(MsgValue m, string key) string:
    let Maybe@(MsgValue) found = mp_map_get(m, key)??
    match found:
        Maybe.Some(v) ->
            return Result.Ok(mp_show(v)??)
        Maybe.None() ->
            return Result.Ok("missing")

fn main() i32:
    let u8[] buf = from([0x81, 0xa1, 0x6b, 0x2a])
    match mp_decode(buf):
        Result.Ok(m) ->
            println(lookup(m, "k").realise("error"))    # 42
        Result.Err(_) ->
            println("decode error")
    return Result.Ok(0)
```

### `mp_show(MsgValue v) -> string`

Render a value on one line, deterministically:

| value | rendering |
|---|---|
| `Nil` | `nil` |
| `Bool` | `true` / `false` |
| `Int(42)` | `42` |
| `UInt(v)` | `18446744073709551615u` (`u` suffix) |
| `Float(x)` | `f64(4614253070214989087)` — the raw IEEE-754 bits of the f64 |
| `Str("s")` | `"s"` — quoted, NOT escaped |
| `Bin` | `bin(1,2,3)` |
| `Arr` | `[1,[2],nil]` |
| `Map` | `{"k":42,"other":[1]}` |

Floats render through `.to_bits()` so the output never depends on float formatting.

## Error handling

```sushi
use <encoding/msgpack>

fn classify(u8[] buf) string:
    match mp_decode(buf):
        Result.Ok(v) ->
            return Result.Ok(mp_show(v)??)
        Result.Err(e) ->
            match e:
                MpError.Truncated(off) ->
                    return Result.Ok("input ended at byte {off}")
                MpError.Unsupported(t) ->
                    return Result.Ok("unsupported tag {t}")
                MpError.BadUtf8(off) ->
                    return Result.Ok("invalid UTF-8 before byte {off}")
                MpError.Trailing(p) ->
                    return Result.Ok("trailing bytes after offset {p}")

fn main() i32:
    let u8[] truncated = from([0xa5, 0x68])
    println(classify(truncated).realise("error"))    # input ended at byte 2
    return Result.Ok(0)
```

## Limitations

- Decode-only. No encoder, no streaming entry point.
- `ext`, `fixext`, and timestamp tags give `MpError.Unsupported` with the tag byte.
- `mp_show` does not escape string contents.
- The decoder targets buffers below 2 GiB. A length prefix is checked against what is
  left in the buffer as it is read, so a length the input cannot supply -- a hostile
  `0xffffffff` included -- is `Truncated` at the header rather than part way through the
  payload.

## See also

- [Files](../io/files.md) — `read_bytes` for reading a MessagePack file
- [Arrays](../collections/arrays.md) — the `u8[]` input type
