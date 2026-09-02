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

### `File`

```sushi
public struct File:
    i32 fd
    bool owned
```

One open file. `owned` says whether dropping this handle closes the descriptor: `open()`
sets it true, and the three console constants set it false, because a program does not
own the descriptors it was started with. A `string` carries the same bit for the same
reason -- a literal frees to a no-op.

`File` implements `Drop`, so it is a moving type: `.clone()` is CE2431. The one way to
a second owner is [`share()`](files.md#share), and it is a second DESCRIPTOR over the
same open file description rather than a copy of the value -- the offset is shared.

### `stdin`, `stdout`, `stderr`

```sushi
public const File stdin  = File(fd: STDIN_FD, owned: false)
public const File stdout = File(fd: STDOUT_FD, owned: false)
public const File stderr = File(fd: STDERR_FD, owned: false)
```

Constants, so they live in read-only memory and closing one is refused while compiling
(CE2400). `STDIN_FD`, `STDOUT_FD` and `STDERR_FD` are public too, for the rare caller
that wants the number.

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

### `stat(string path) -> Result@(FileStat, FileError)`

Read the metadata of a path into one `FileStat`. Each field is one `<io/files>` read, so the call costs one system call per field.

```sushi
use <io/fs>

fn main() i32:
    match stat("build/output"):
        Result.Ok(st) ->
            println("size {st.size}, modified {st.mtime}")
        Result.Err(_) -> println("no such path")

    return Result.Ok(0)
```

### `walk(string path) -> Result@(string[], FileError)`

Walk a directory tree and collect the regular files, as full joined paths. A directory symlink is not followed, so a loop cannot form. The order follows `read_dir` and is unspecified.

```sushi
use <io/fs>

fn main() i32:
    match walk("src"):
        Result.Ok(files) ->
            foreach(p in files.iter()):
                println(p)
        Result.Err(_) -> println("walk failed")

    return Result.Ok(0)
```

### `mkdir_all(string path, i32 dir_mode) -> Result@(~, FileError)`

Create a directory and every missing parent. An existing directory on the way is kept; losing the creation race to another process counts as success.

```sushi
use <io/fs>

fn main() i32:
    match mkdir_all("out/cache/objects", 0o755):
        Result.Ok(_) -> println("tree is there")
        Result.Err(_) -> println("cannot build the tree")

    return Result.Ok(0)
```

### `remove_all(string path) -> Result@(~, FileError)`

Remove a path and, for a directory, everything under it. A missing path is success: the goal state already holds. A symlink is removed as the link; its target stays.

```sushi
use <io/fs>

fn main() i32:
    match remove_all("out/cache"):
        Result.Ok(_) -> println("cache cleared")
        Result.Err(_) -> println("something is still in use")

    return Result.Ok(0)
```

## See also

- [File I/O](files.md) — the `File` method reference, and the primitives underneath
  (`read_dir`, `mkdir`, `remove`, the stat fields)
- [I/O contracts](contracts.md) — `Reader`, `Writer` and `Seek`, which is what a function
  names when it wants a capability rather than a type
- [Buffered I/O](buf.md) — `BufReader` and `BufWriter` over any handle
- [Path algebra](path.md) — the joins this module builds its paths with
