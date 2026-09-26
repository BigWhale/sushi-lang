# MessagePack

[← Back to Standard Library](../../standard-library.md)

A MessagePack decoder, written in Sushi: `decode`, the map readers (`map_index`,
`map_get`, `map_get_str`, `map_get_bool`), and `show`. Decode-only — there is no encoder.

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

`decode` reads one whole buffer as exactly one MessagePack value and returns a
`MsgValue` tree. Decode errors are values, not exits.

## Types

Both are `public`: a consumer names them, so they are API. The module's own `MpCursor` is
not, which is what keeps it changeable.

```
public enum MsgValue:
    Nil()
    Bool(bool)
    Int(i64)                       # every signed int, and every uint that fits i64
    UInt(u64)                      # only uint64 values above the i64 maximum
    Float(f64)                     # float32 widens to f64
    Str(string)
    Bin(u8[])
    Arr(MsgValue[])
    Map(MsgValue[], MsgValue[])    # parallel keys/values, wire order kept

public enum MpError:
    Truncated(i32)      # byte offset where the input ran out
    Unsupported(u8)     # ext, fixext, timestamp, or the never-used 0xc1 tag
    BadUtf8(i32)        # offset just past the offending str payload
    Trailing(i32)       # bytes remain after the root value
```

A map is two parallel arrays, not a hash table: MessagePack keys are not limited to
strings, and the wire order stays visible and deterministic.

## Functions

### `decode(u8[] buf) -> MsgValue | MpError`

Decode the whole buffer as one value. Trailing bytes after the root value give
`MpError.Trailing`.

```sushi
use <encoding/msgpack>

fn show_or_err(u8[] buf) string:
    match decode(buf):
        Result.Ok(v) ->
            return Result.Ok(show(v)??)
        Result.Err(_) ->
            return Result.Ok("decode error")

fn main() i32:
    let u8[] buf = from([0x82, 0xa1, 0x61, 0x01, 0xa1, 0x62, 0x91, 0x02])
    println(show_or_err(buf).realise("error"))    # {"a":1,"b":[2]}
    return Result.Ok(0)
```

### `map_get(MsgValue m, string key) -> Maybe@(MsgValue)`

Scan a `Map` in wire order for a string key. The found value comes back as a clone, so
the tree stays intact. To read a string or a bool, use a typed leaf reader; to walk a
subtree, borrow it through `map_index`. A missing key, a non-string key match, or a non-map argument gives
`Maybe.None`.

```sushi
use <encoding/msgpack>

fn lookup(MsgValue m, string key) string:
    let Maybe@(MsgValue) found = map_get(m, key)??
    match found:
        Maybe.Some(v) ->
            return Result.Ok(show(v)??)
        Maybe.None() ->
            return Result.Ok("missing")

fn main() i32:
    let u8[] buf = from([0x81, 0xa1, 0x6b, 0x2a])
    match decode(buf):
        Result.Ok(m) ->
            println(lookup(m, "k").realise("error"))    # 42
        Result.Err(_) ->
            println("decode error")
    return Result.Ok(0)
```

### `map_index(MsgValue m, string key) -> Maybe@(i32)`

The position of a string key in a `Map`, in wire order; the first key that matches wins.
A missing key, or a non-map argument, gives `Maybe.None`. Nothing is copied: use the
position to borrow the value in place, inside a `MsgValue.Map(keys, vals)` arm.

```sushi
use <encoding/msgpack>

fn count_items(MsgValue m, string key) i32:
    match m:
        MsgValue.Map(_, vals) ->
            match map_index(m, key)??:
                Maybe.Some(i) ->
                    match vals[i]:
                        MsgValue.Arr(items) -> return Result.Ok(items.len())
                        _ -> return Result.Ok(0)
                Maybe.None() -> return Result.Ok(0)
        _ ->
            return Result.Ok(0)

fn main() i32:
    let u8[] buf = from([0x81, 0xa1, 0x61, 0x92, 0x01, 0x02])
    match decode(buf):
        Result.Ok(m) ->
            println(count_items(m, "a").realise(0 - 1))    # 2
        Result.Err(_) ->
            println("decode error")
    return Result.Ok(0)
```

### `map_get_str(MsgValue m, string key) -> Maybe@(string)`

### `map_get_bool(MsgValue m, string key) -> Maybe@(bool)`

The typed leaf readers. Each one copies only the leaf, never the map. A missing key, a
value of a different kind, or a non-map argument gives `Maybe.None`.

```sushi
use <encoding/msgpack>

fn describe(MsgValue m) string:
    let string name = map_get_str(m, "name")??.realise("")
    let bool on = map_get_bool(m, "on")??.realise(false)
    return Result.Ok("{name} {on}")

fn main() i32:
    let u8[] buf = from([0x82, 0xa4, 0x6e, 0x61, 0x6d, 0x65, 0xa2, 0x6f, 0x6b,
                         0xa2, 0x6f, 0x6e, 0xc3])
    match decode(buf):
        Result.Ok(m) ->
            println(describe(m).realise("error"))    # ok true
        Result.Err(_) ->
            println("decode error")
    return Result.Ok(0)
```

### `show(MsgValue v) -> string`

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
    match decode(buf):
        Result.Ok(v) ->
            return Result.Ok(show(v)??)
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
- `show` does not escape string contents.
- The decoder targets buffers below 2 GiB. A length prefix is checked against what is
  left in the buffer as it is read, so a length the input cannot supply -- a hostile
  `0xffffffff` included -- is `Truncated` at the header rather than part way through the
  payload.

## See also

- [Files](../io/files.md) — `read_bytes` for reading a MessagePack file
- [Arrays](../collections/arrays.md) — the `u8[]` input type
