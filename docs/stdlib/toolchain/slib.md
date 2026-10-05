# Slib reader

[← Back to Standard Library](../../standard-library.md)

A `.slib` reader, written in Sushi: `read_metadata`, `sizes` and `bitcode_size` for the
metadata and the section lengths, and `read_library` and `check_manifest` for a whole
library whose manifest shape is checked. It mirrors the Python reader of `--lib-info`.

## Import

```sushi
use <toolchain/slib>

fn main() i32:
    return 0
```

## Overview

`toolchain/slib` is a **Sushi-source** standard-library module. It reads the fixed
52-byte little-endian header and the MessagePack metadata map of a version-5 `.slib`
library (see [Library Format](../../library-format.md)). The metadata comes back as a
[`MsgValue`](../encoding/msgpack.md) tree. The reader stops after the metadata blob; it
reads the length of a payload section, never the payload. A container of another version
is `SlibError.BadVersion`. A version-4 library is refused, because its records do not say
which callables are bare.

The module imports `<io/fs>`, `<encoding/msgpack>` and `<collections/strings>`. It
re-exports `<io/error>` (`public use`), so
`use <toolchain/slib>` alone lets a program name the `IoError` that `SlibError.Io`
carries.

## Types

```
public error SlibError:
    Io(IoError)                         # the open or a read failed; the IoError names the cause
    BadMagic()                          # the 16 magic bytes do not match
    BadVersion(u32)                     # header version is not 5
    Truncated(SlibSection, u64, u64)    # the section, the bytes it needs, the bytes left
    TooLarge(u64)                       # the file is larger than 1 GiB; the file size
    Decode(MpError)                     # the metadata blob does not decode
    Invalid(string)                     # the manifest has the wrong shape; the reason

public enum SlibSection:
    Metadata()          # the header and the metadata blob
    Source()            # the source section and its length field
    Bitcode()           # the bitcode section and its length field

public struct SlibSizes:
    u64 source          # the length of the source section
    u64 bitcode         # the length of the bitcode section

public struct SlibLibrary:
    MsgValue metadata   # the manifest, with its shape checked
    SlibSizes sizes     # the lengths of the two payload sections
```

Every function answers the one `SlibError`. The readers walk one header, so a damaged
file gives the same fault from each of them. `e.text()` gives one stable line for a
`SlibError`, with no payload, so a caller can compare it.

## Functions

### `read_metadata(string path) MsgValue | SlibError`

Read the metadata map of a `.slib` file. The four spare header fields are read and not
validated, the same as the Python reader.

```sushi
use <encoding/msgpack>
use <toolchain/slib>

fn library_name(string path) string | StdError:
    match read_metadata(path):
        Result.Ok(meta) ->
            let Maybe@(MsgValue) found = map_get(meta, "library_name")
            match found:
                Maybe.Some(v) ->
                    return Result.Ok(show(v))
                Maybe.None() ->
                    return Result.Ok("missing")
        Result.Err(_) ->
            return Result.Ok("read error")

fn main() i32:
    println(library_name("mylib.slib").realise("error"))
    return 0
```

### `sizes(string path) SlibSizes | SlibError`

The length of both payload sections, in one pass over the file. A version-5 container
puts a length-prefixed source section between the metadata and the bitcode, so a reader
steps over the source to reach the bitcode length. A source library records no bitcode,
and a binary one no source.

```sushi
use <toolchain/slib>

fn main() i32:
    match sizes("mylib.slib"):
        Result.Ok(sizes) ->
            println("source {sizes.source}, bitcode {sizes.bitcode}")
        Result.Err(_) ->
            println("read error")
    return 0
```

### `bitcode_size(string path) u64 | SlibError`

The `bitcode` field of `sizes`, on its own. The reader reads only the two 8-byte
length fields, never a payload.

### `read_library(string path) SlibLibrary | SlibError`

Read a whole library: the manifest and the lengths of both payload sections. The checks
are the ones the Python reader of `sushic --lib-info` makes, in the same order, so both
halves of the command report the same fault for a damaged file: the magic first, then the
1 GiB limit, then each declared length against what is left of the file, before any bytes
are read. The manifest must then pass `check_manifest`.

```sushi
use <toolchain/slib>

fn describe(string path) string:
    match read_library(path):
        Result.Ok(library) ->
            return "source {library.sizes.source}, bitcode {library.sizes.bitcode}"
        Result.Err(SlibError.Truncated(_, need, have)) ->
            return "truncated: needs {need} bytes, has {have}"
        Result.Err(SlibError.Invalid(reason)) ->
            return "not a manifest: {reason}"
        Result.Err(_) ->
            return "cannot read {path}"

fn main() i32:
    println(describe("mylib.slib"))
    return 0
```

### `check_manifest(MsgValue meta) ~ | SlibError`

Check that a metadata map has the shape of a manifest: every required field is present
and has its type. Every function, helper and method record must state `has_channel`, a
`bool` that says whether the callable has an error channel. The Python reader checks the
same rows (`MANIFEST_SCHEMA` in
`sushi_lang/backend/library_format.py`) and gives the same reason, as
`SlibError.Invalid(reason)` here and [CE3512](../../error-catalog.md#ce3512) there. `read_metadata` does not call it, so a
partial map still reads.

## Error handling

```sushi
use <toolchain/slib>

fn classify(string path) string:
    match read_metadata(path):
        Result.Ok(_) ->
            return "ok"
        Result.Err(e) ->
            match e:
                SlibError.Io(IoError.NotFound) ->
                    return "no such file: {path}"
                SlibError.Io(_) ->
                    return "cannot read {path}"
                SlibError.BadMagic() ->
                    return "not a .slib library"
                SlibError.BadVersion(v) ->
                    return "unsupported version {v}"
                SlibError.Truncated(_, need, have) ->
                    return "truncated file: needs {need} bytes, has {have}"
                SlibError.TooLarge(size) ->
                    return "file too large: {size} bytes"
                SlibError.Decode(_) ->
                    return "metadata does not decode"
                SlibError.Invalid(reason) ->
                    return "not a manifest: {reason}"

fn main() i32:
    println(classify("missing.slib"))    # no such file: missing.slib
    return 0
```

A directory opens, and its first read fails, so it is `Io(IoError.IsDirectory)`. A file
with no read permission is `Io(IoError.PermissionDenied)`. `Truncated` is only a file that
ends too early; a read that fails is never reported as one.

## The slib-info tool

`toolchain/src/slib_info.sushi` (in the repository, not in the wheel) renders the same
report as `sushic --lib-info`. A repo checkout builds it with `./toolchain/build.py`,
and `sushic --lib-info` then delegates to the binary. See `toolchain/README.md`, and
[the design records](../../design/slib-info.md) of the tool.

## Limitations

- Read-only. The module reads the header, the metadata and the two section lengths, and
  never a payload. Writing a `.slib` is done by the compiler (`sushic --lib`).
- No typed manifest structs: consumers walk the `MsgValue` tree with the accessors of
  `<encoding/msgpack>` (`map_get`, `map_get_str`, `map_get_bool`, `map_index`).
- A declared length is compared with the size of the file before any bytes are read. A
  metadata length larger than the rest of the file is `SlibError.Truncated(...)` from
  every reader. Every reader refuses a file larger than 1 GiB (`SLIB_MAX_FILE_SIZE`) with
  `SlibError.TooLarge(size)`.
- `sizes` and `bitcode_size` do not compare the bitcode length with the file;
  `read_library` does.

## See also

- [MessagePack](../encoding/msgpack.md) — the `MsgValue` tree and its accessors
- [Library Format](../../library-format.md) — the `.slib` container specification
