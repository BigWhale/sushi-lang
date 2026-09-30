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

Every function in this module is **bare**: it has no error channel, and a call gives the
`string` itself. Do not write `??` or `.realise(...)` on a call. A `??` on it is CE2507, and
a `.realise(...)` on it is CE2008.

A bare function is the exception in Sushi. These functions are bare because each one is
total over its input and will stay so: a path join cannot fail. A function that does I/O,
parses or can gain a failure later writes an error channel. [The error
channel](../../design/error-channel.md) gives the rule.

### `join(string base, string child) string`

Join two path segments with a single separator. An absolute child replaces the base. An empty child keeps the base and adds a trailing separator, so the result names a directory.

```sushi
use <io/path>

fn main() i32:
    println(join("src", "main.sushi"))   # src/main.sushi
    println(join("src/", "main.sushi"))  # src/main.sushi
    println(join("src", "/etc/hosts"))   # /etc/hosts
    return 0
```

### `basename(string path) string`

The part after the last separator. A path with a trailing separator has an empty basename.

```sushi
use <io/path>

fn main() i32:
    println(basename("/a/b.txt"))  # b.txt
    println(basename("a/"))        # (empty)
    return 0
```

### `dirname(string path) string`

The part before the last separator, trailing separators stripped. A path with no separator has an empty dirname.

```sushi
use <io/path>

fn main() i32:
    println(dirname("/a/b.txt"))  # /a
    println(dirname("/"))         # /
    return 0
```

### `extension(string path) string`

The extension of the last component, without the leading dot. The leading dot of a hidden file does not count.

```sushi
use <io/path>

fn main() i32:
    println(extension("archive.tar.gz"))  # gz
    println(extension(".bashrc"))         # (empty)
    return 0
```

### `normalize(string path) string`

Normalize a path lexically: doubled separators collapse, `.` components vanish, and a `..` removes the component before it when one exists. A rooted path keeps its root, and a root of exactly two separators stays `//` (the POSIX implementation-defined form). An empty result becomes `.`.

```sushi
use <io/path>

fn main() i32:
    println(normalize("/a/./b/../c"))  # /a/c
    println(normalize("a//b"))         # a/b
    return 0
```

## See also

- [File I/O](files.md) — the file system calls the paths feed into
- [Strings](../collections/strings.md) — the methods this module is built from
