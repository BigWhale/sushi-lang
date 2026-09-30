# File-System Operations

[← Back to Standard Library](../../standard-library.md)

The `File` handle, `open()`, the console handles, and the composed file-system
operations `stat`, `walk`, `mkdir_all` and `remove_all`.

## Import

```sushi
use <io/fs>
```

## Overview

`io/fs` is a **Sushi-source** standard-library module: it ships as bundled `.sushi`
source and is merged as a compilation unit when you import it. It composes the
`<io/files>` primitives with the `<io/path>` algebra.

It is also where the file HANDLE lives. `File` owns its descriptor, moves to one owner,
and closes when that owner leaves scope. Every method on it is either a perk
implementation (`Reader`, `Writer`, `Seek`, `Drop`) or an ordinary extension method, each
written over the `<io/files>` descriptor primitives -- there is no compiler magic behind
any of them. The full method reference is in [File Operations](files.md), the console
handles are in [Console I/O](console.md), and the buffered layer above the handle is
[Buffered I/O](buf.md).

## Types

The import brings four predefined enums beside the struct. `FileMode` is homed here.
`IoError` -- the channel `open()` and every method answer -- and `FileError` are
[`<io/error>`](error.md)'s, and `SeekFrom` is [`<io/contracts>`](contracts.md)'s; this
module re-exports `<io/contracts>`, which re-exports `<io/error>`, so `use <io/fs>` alone
lets a unit write all four bare, and `use <io/fs> as fs` puts them behind the dot
(`fs.FileMode.Read()`, `fs.IoError.NotFound`). The perks `Reader`, `Writer` and `Seek`
ride along the same way.

### `FileMode`

```sushi
public enum FileMode:
    Read      Write      Append
    ReadB     WriteB     AppendB
```

The mode `open()` takes. POSIX does not separate text from binary, so each `B` form
opens the file exactly as its plain twin does.

### `FileError`

The variants and the errno values each one covers are listed on [I/O errors](error.md).
`FileError` is what the path utilities (`stat`, `walk`, `mkdir_all`, `remove_all`) and the `fd_*`
primitives answer. A handle method answers `IoError` instead; `to_io()` converts inside
the stdlib.

### `File`

```sushi
public struct File:
    i32 fd
    bool owned
```

One open file. `owned` says whether dropping this handle closes the descriptor: `open()`
sets it true, and the three console handles set it false, because a program does not
own the descriptors it was started with. A `string` carries the same bit for the same
reason -- a literal frees to a no-op.

`File` implements `Drop`, so it is a moving type: `.clone()` is CE2431. The one way to
a second owner is [`share()`](files.md#share), and it is a second DESCRIPTOR over the
same open file description rather than a copy of the value -- the offset is shared.

### `stdin`, `stdout`, `stderr`

```sushi
public var File stdin  = File(fd: STDIN_FD, owned: false)
public var File stdout = File(fd: STDOUT_FD, owned: false)
public var File stderr = File(fd: STDERR_FD, owned: false)
```

Unit variables (`var`, [the reference](../../language-reference.md#unit-variables)), so
each has an address the `poke self` contract methods reach, and a program may rebind one
for a run: `stdout := open("log.txt", FileMode.Write())??` puts every later
`stdout.write(...)` into the file, and `stdout := File(fd: STDOUT_FD, owned: false)` puts
it back. A unit variable is never moved out of, so closing one is refused while
compiling (CE2436): `stdout.close()` would take the handle. `STDIN_FD`, `STDOUT_FD` and
`STDERR_FD` are public too, for the caller that wants the number -- or a fresh handle
over it.

### `FileStat`

```sushi
public struct FileStat:
    i64 size
    i64 mtime
    i64 ctime
    i32 mode
    bool is_symlink
```

## Functions

### `stat(string path) FileStat | FileError`

Read the metadata of a path into one `FileStat`. Each field is one `<io/files>` read, so the call costs one system call per field.

```sushi
use <io/fs>

fn main() i32:
    match stat("build/output"):
        Result.Ok(st) ->
            println("size {st.size}, modified {st.mtime}")
        Result.Err(_) -> println("no such path")

    return 0
```

### `walk(string path) string[] | FileError`

Walk a directory tree and collect the regular files, as full joined paths. A directory symlink is not followed, so a loop cannot form. The order follows `read_dir` and is unspecified.

```sushi
use <io/fs>

fn main() i32:
    match walk("src"):
        Result.Ok(files) ->
            foreach(p in files.iter()):
                println(p)
        Result.Err(_) -> println("walk failed")

    return 0
```

### `mkdir_all(string path, i32 dir_mode) ~ | FileError`

Create a directory and every missing parent. An existing directory on the way is kept; losing the creation race to another process counts as success.

```sushi
use <io/fs>

fn main() i32:
    match mkdir_all("out/cache/objects", 0o755):
        Result.Ok(_) -> println("tree is there")
        Result.Err(_) -> println("cannot build the tree")

    return 0
```

### `remove_all(string path) ~ | FileError`

Remove a path and, for a directory, everything under it. A missing path (ENOENT) is
success: the goal state already holds. Any other failure to read the path is an error,
and nothing is removed. A path that goes through a regular file -- `dir/plainfile/child`,
where `plainfile` is not a directory (ENOTDIR) -- is such an error and answers
`Err(FileError.InvalidPath)`, the `shutil.rmtree` answer and not the `rm -rf` one. A
symlink is removed as the link; its target stays.

```sushi
use <io/fs>

fn main() i32:
    match remove_all("out/cache"):
        Result.Ok(_) -> println("cache cleared")
        Result.Err(_) -> println("something is still in use")

    return 0
```

## Extension methods

Two extension methods on the predefined enums are public, because an extension is as
visible as its target type. The stdlib uses them to talk to the descriptor layer; a
program needs them only when it calls an `fd_*` primitive itself.

### `FileMode.intent() i32`

The intent that [`fd_open`](files.md#the-descriptor-layer)
takes: `0` read, `1` write, `2` append. A `B` form answers the same number as its plain
twin.

### `SeekFrom.whence() i32`

The `whence` that `fd_seek` takes: `0` from the start, `1` from the current position, `2`
from the end.

```sushi
use <io/fs>

fn main() i32:
    println("append is intent {FileMode.Append().intent()}")
    println("the end is whence {SeekFrom.End().whence()}")
    return 0
```

## See also

- [File I/O](files.md) — the `File` method reference, and the primitives underneath
  (`read_dir`, `mkdir`, `remove`, the stat fields)
- [I/O contracts](contracts.md) — `Reader`, `Writer` and `Seek`, which is what a function
  names when it wants a capability rather than a type
- [Buffered I/O](buf.md) — `BufReader` and `BufWriter` over any handle
- [Path algebra](path.md) — the joins this module builds its paths with
