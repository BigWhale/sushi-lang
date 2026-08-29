# Path Algebra

[← Back to Standard Library](../../standard-library.md)

Lexical path manipulation: join, split, extension, normalize. POSIX separators only.

## Import

```sushi
use <io/path>
```

## Overview

`io/path` is a **Sushi-source** standard-library module: it ships as bundled `.sushi` source and is merged as a compilation unit when you import it. Every function works on the string alone — no OS call, no file system read. The rules mirror Python's `posixpath`, and a differential test holds them there.

**Limitations:** the separator is `/` only. `normalize` is lexical — it resolves `..` without looking at symlinks, so the result can name a different file than the input on a tree that uses them. There is no `canonicalize()`.

## Functions

### `join(string base, string child) -> Result@(string)`

Join two path segments with a single separator. An absolute child replaces the base.

```sushi
use <io/path>

fn main() i32:
    println(join("src", "main.sushi").realise("?"))   # src/main.sushi
    println(join("src/", "main.sushi").realise("?"))  # src/main.sushi
    println(join("src", "/etc/hosts").realise("?"))   # /etc/hosts
    return Result.Ok(0)
```

### `basename(string path) -> Result@(string)`

The part after the last separator. A path with a trailing separator has an empty basename.

```sushi
use <io/path>

fn main() i32:
    println(basename("/a/b.txt").realise("?"))  # b.txt
    println(basename("a/").realise("?"))        # (empty)
    return Result.Ok(0)
```

### `dirname(string path) -> Result@(string)`

The part before the last separator, trailing separators stripped. A path with no separator has an empty dirname.

```sushi
use <io/path>

fn main() i32:
    println(dirname("/a/b.txt").realise("?"))  # /a
    println(dirname("/").realise("?"))         # /
    return Result.Ok(0)
```

### `extension(string path) -> Result@(string)`

The extension of the last component, without the leading dot. The leading dot of a hidden file does not count.

```sushi
use <io/path>

fn main() i32:
    println(extension("archive.tar.gz").realise("?"))  # gz
    println(extension(".bashrc").realise("?"))         # (empty)
    return Result.Ok(0)
```

### `normalize(string path) -> Result@(string)`

Normalize a path lexically: doubled separators collapse, `.` components vanish, and a `..` removes the component before it when one exists. An empty result becomes `.`.

```sushi
use <io/path>

fn main() i32:
    println(normalize("/a/./b/../c").realise("?"))  # /a/c
    println(normalize("a//b").realise("?"))         # a/b
    return Result.Ok(0)
```

## See also

- [File I/O](files.md) — the file system calls the paths feed into
- [Strings](../collections/strings.md) — the methods this module is built from
