# Slib reader

[← Back to Standard Library](../../standard-library.md)

A `.slib` metadata reader, written in Sushi: `slib_read_metadata` and
`slib_bitcode_size`. It mirrors the Python reader `LibraryFormat.read_metadata_only`.

## Import

```sushi
use <toolchain/slib>

fn main() i32:
    return Result.Ok(0)
```

## Overview

`toolchain/slib` is a **Sushi-source** standard-library module. It reads the fixed
52-byte little-endian header and the MessagePack metadata map of a version-3 `.slib`
library (see [Library Format](../../library-format.md)). The metadata comes back as a
[`MsgValue`](../encoding/msgpack.md) tree. The reader stops after the metadata blob and
never reads the bitcode.

The module imports `<encoding/msgpack>` and `<io/files>` — the first source module that
imports another source module.

## Types

```
enum SlibError:
    OpenFailed(string)  # the path that did not open
    BadMagic()          # the 16 magic bytes do not match
    BadVersion(u32)     # header version is not 3
    Truncated()         # the file ends inside the header or the blob
    Decode(MpError)     # the metadata blob does not decode
```

## Functions

### `slib_read_metadata(string path) -> MsgValue | SlibError`

Read the metadata map of a `.slib` file. The four spare header fields are read and not
validated, the same as the Python reader.

```sushi
use <encoding/msgpack>
use <toolchain/slib>

fn library_name(string path) string:
    match slib_read_metadata(path):
        Result.Ok(meta) ->
            let Maybe@(MsgValue) found = mp_map_get(meta, "library_name")??
            match found:
                Maybe.Some(v) ->
                    return Result.Ok(mp_show(v)??)
                Maybe.None() ->
                    return Result.Ok("missing")
        Result.Err(_) ->
            return Result.Ok("read error")
    return Result.Ok("read error")

fn main() i32:
    println(library_name("mylib.slib").realise("error"))
    return Result.Ok(0)
```

### `slib_bitcode_size(string path) -> u64 | SlibError`

The length of the bitcode that follows the metadata blob. The reader reads only the
8-byte length field, never the bitcode itself.

## Error handling

```sushi
use <toolchain/slib>

fn classify(string path) string:
    match slib_read_metadata(path):
        Result.Ok(_) ->
            return Result.Ok("ok")
        Result.Err(e) ->
            match e:
                SlibError.OpenFailed(p) ->
                    return Result.Ok("cannot open {p}")
                SlibError.BadMagic() ->
                    return Result.Ok("not a .slib library")
                SlibError.BadVersion(v) ->
                    return Result.Ok("unsupported version {v}")
                SlibError.Truncated() ->
                    return Result.Ok("truncated file")
                SlibError.Decode(_) ->
                    return Result.Ok("metadata does not decode")

fn main() i32:
    println(classify("missing.slib").realise("error"))    # cannot open missing.slib
    return Result.Ok(0)
```

## The slib-info tool

`toolchain/src/slib_info.sushi` (in the repository, not in the wheel) renders the same
report as `sushic --lib-info`. A repo checkout builds it with `./toolchain/build.py`,
and `sushic --lib-info` then delegates to the binary. See `toolchain/README.md`.

## Limitations

- Read-only, metadata-only. Writing a `.slib` stays in Python.
- No typed manifest structs: consumers walk the `MsgValue` tree with `mp_map_get`.
- Metadata above 2 GiB is not supported; a hostile length reads nothing and reports a
  decode error on the empty blob.

## See also

- [MessagePack](../encoding/msgpack.md) — the `MsgValue` tree and its accessors
- [Library Format](../../library-format.md) — the `.slib` container specification
