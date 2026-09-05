# I/O errors

[← Back to Standard Library](../../standard-library.md)

`IoError` and `FileError`: the error vocabulary every io module answers.

## Import

No program writes this import for the two enums. `<io/contracts>` re-exports this module,
and `<io/fs>` and `<io/buf>` re-export `<io/contracts>`, so any of those brings both names:

```sushi
use <io/fs>              # IoError and FileError ride along
```

`<io/files>` hands `FileError` on the same way. The module itself is importable, and
`use <io/error> as ioe` puts the two names behind a dot (`ioe.IoError.NotFound`).

## Overview

Both are predefined enums -- the compiler synthesizes them, and no unit declares them --
and this module is their HOME. The import gates the bare name exactly as `<net/error>`
gates `NetError` and `<collections/hashmap>` gates `HashMap`; the re-export is what makes
the home reachable through the module whose calls answer the enum
(`docs/design/unit-namespaces.md`, section 8.1).

```sushi
public enum IoError:
    NotFound            PermissionDenied     AlreadyExists
    IsDirectory         DiskFull             TooManyOpen
    InvalidInput        Interrupted          TimedOut
    ConnectionReset     Closed               Other
    Os(i32)
```

`IoError` is the ONE channel every io contract method answers -- a read, a write, a
seek, `open()` and `close()`, on a `File`, a `TcpStream` or a buffered handle alike. A
contract carries one signature and has no `Self`, so a method cannot answer `FileError`
on a `File` and `NetError` on a `TcpStream`; the domain enums stay on construction,
addressing and options. `Os(i32)` carries an `errno` no other variant names.

```sushi
public enum FileError:
    NotFound          PermissionDenied    AlreadyExists
    IsDirectory       DiskFull            TooManyOpen
    InvalidPath       IOError             Other
```

`FileError` is what the path utilities (`stat`, `walk`, `mkdir_all`, `remove_all`,
`exists`, `remove`, `read_dir`) and the `fd_*` primitives answer.

The variant ORDER of each is the ABI: the index is the tag the descriptor layer stores
into a Result payload, so a variant is only ever appended.

## Functions

### `to_io() -> IoError`

```sushi
extend FileError to_io() IoError
```

Turns a file-system error into the one channel the io contracts answer. Every `FileError`
variant with no twin in `IoError` belongs to an open rather than to a read or a write, so
nothing a contract method can answer is lost; `IOError` has already thrown its `errno`
away and collapses to `Other`. The conversion runs INSIDE the stdlib -- `open()` is
`fd_open` with its error passed through `to_io()` -- and a program never needs to call it.

## Example

```sushi
use <io/fs>

fn first_line(string path) string | IoError:
    let File f = open(path, FileMode.Read())??
    return Result.Ok(f.read_line()??.realise(""))

fn main() i32:
    match first_line("/etc/hostname"):
        Result.Ok(line) -> println(line)
        Result.Err(IoError.NotFound) -> println("no such file")
        Result.Err(IoError.PermissionDenied) -> println("not allowed")
        Result.Err(_) -> println("failed")
    return Result.Ok(0)
```

## See also

- [I/O Contracts](contracts.md) -- `Reader`, `Writer`, `Seek`: the methods that answer `IoError`
- [File-system ops](fs.md) -- `File`, `open()`, and the path utilities that answer `FileError`
- [Net errors](../net/error.md) -- `NetError`, the same shape for the net modules
- [Unit namespaces](../../design/unit-namespaces.md) -- why a predefined enum has a home, and how a re-export reaches it
