# File Operations

[← Back to Standard Library](../../standard-library.md)

File system operations for reading, writing, and managing files.

## Import

`<io/files>` holds the path utilities -- `exists()`, `remove()`, `read_dir()` -- and the
descriptor layer. **`File`, `open()` and the three console handles live in
[`<io/fs>`](fs.md)**, beside `stat()` and `walk()`, so most programs want both:

```sushi
use <io/files>
use <io/fs>
```

## Overview

A `File` OWNS its descriptor. It moves to exactly one owner and closes itself when that
owner leaves scope, so an explicit `close()` is only needed where the failure has to be
seen -- a destructor cannot answer a `Result`. The compiler enforces the one-owner rule;
nothing here has to be remembered.

```sushi
use <io/fs>

fn log_it() ~ | FileError:
    let File f = open("out.log", FileMode.Append())??
    f.writeln("Mostly Harmless")??
    return Result.Ok(~)
    # f drops here. The descriptor closes.
```

Three things follow from that, and each surprises somebody:

- **A `File` cannot be copied.** `.clone()` is **CE2431**: a field-by-field copy would
  duplicate the descriptor number and leave two owners that both close it.
- **A `match` binding is a read-only view.** `Result.Ok(f) -> f.close()` is **CE2414**,
  because `close()` writes -1 into the handle. Write `Result.Ok(poke f)`, or -- better --
  delete the `close()` and let the drop do it.
- **Reads and writes are UNBUFFERED.** Each one is a system call. That is Rust's rule
  too, and the buffered layer is a separate type.

Every method is a plain borrow except `close()`. A file's position lives in the KERNEL,
not in the struct, so reading and writing need no mutable receiver.

`stdin`, `stdout` and `stderr` are `File` values too -- see [Console I/O](console.md).

## Opening Files

### open

Open a file with a specific mode.

```sushi
use <io/fs>

fn open(string path, FileMode mode) -> Result@(File, IoError)
```

**Parameters:**
- `path` - File path (relative or absolute)
- `mode` - File access mode

**Returns:**
- `Result.Ok(file)` - Successfully opened file
- `Result.Err(error)` - Error occurred (see Error Handling section)

**File Modes:**

- `FileMode.Read()` - Open for reading only (file must exist)
- `FileMode.Write()` - Open for writing only (creates file or truncates existing)
- `FileMode.Append()` - Open for appending (creates file if doesn't exist)

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32:
    match open("data.txt", FileMode.Read()):
        Result.Ok(poke f) ->
            println("File opened successfully")
            f.close()
        Result.Err(e) ->
            println("Failed to open file")

    return Result.Ok(0)
```

**With error propagation:**

```sushi
use <io/files>
use <io/fs>

fn read_config() string | IoError:
    let File f = open("config.txt", FileMode.Read())??
    let string content = f.read_all().realise('')
    f.close()
    return Result.Ok(content)

fn main() i32:
    match read_config():
        Result.Ok(config) ->
            println("Config: {config}")
        Result.Err(_) ->
            println("Failed to read config")

    return Result.Ok(0)
```

## File Methods

The whole surface, and where each method comes from. A method a contract provides is
callable on a `TcpStream` too, which is what `<io/contracts>` is for.

| method | signature | provider |
|---|---|---|
| `read` | `(i32 max) string \| IoError` -- one read, as text | `Reader` |
| `read_bytes` | `(i32 max) u8[] \| IoError` -- one read, as bytes | `Reader` |
| `write` | `(string data) ~ \| IoError` -- every byte, or an error | `Writer` |
| `write_bytes` | `(u8[] data) ~ \| IoError` | `Writer` |
| `flush` | `() ~ \| IoError` -- a successful no-op on a descriptor | `Writer` |
| `seek` | `(i64 offset, SeekFrom origin) i64 \| IoError` -- answers the NEW position | `Seek` |
| `read_all` | `() string \| IoError` -- the whole file, from the current position | `File` |
| `readln` | `() string \| IoError` -- one line, newline stripped | `File` |
| `readch` | `() string \| IoError` -- one byte, as text | `File` |
| `writeln` | `(string data) ~ \| IoError` | `File` |
| `tell` | `() i64 \| IoError` | `File` |
| `is_open` | `() bool` -- false once `close()` has run | `File` |
| `is_terminal` | `() bool` -- true only for a terminal | `File` |
| `close` | `(poke self) ~ \| IoError` | `File` |
| `lines` | `() Iterator@(string)` -- still a compiler builtin | `File` |

A `File` closes itself when its owner leaves scope, so `close()` is only needed where the
failure has to be SEEN. Every method is a plain borrow except `close()`: a file's position
lives in the kernel, not in the struct, so a read and a write need no mutable receiver.

### read

Read entire file contents as a string.

```sushi
use <io/fs>

fn File.read_all().realise('') -> string
```

**Returns:**
- String containing entire file contents

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32:
    match open("data.txt", FileMode.Read()):
        Result.Ok(poke f) ->
            let string content = f.read_all().realise('')
            f.close()
            println("Content: {content}")
        Result.Err(_) ->
            println("Failed to read file")

    return Result.Ok(0)
```

**Processing file content:**

```sushi
use <io/files>
use <io/fs>
use <collections/strings>

fn main() i32 | IoError:
    let File f = open("numbers.txt", FileMode.Read())??
    let string content = f.read_all().realise('')
    f.close()

    let string[] lines = content.split("\n")

    foreach(line in lines.iter()):
        if (not line.is_empty()):
            println("Line: {line}")

    return Result.Ok(0)
```

### readln

Read a single line from the file.

```sushi
use <io/fs>

fn File.readln().realise('') -> string
```

**Returns:**
- String containing one line (without newline character)

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    let File f = open("data.txt", FileMode.Read())??

    let string first_line = f.readln().realise('')
    println("First: {first_line}")

    let string second_line = f.readln().realise('')
    println("Second: {second_line}")

    f.close()

    return Result.Ok(0)
```

**Line-by-line processing:**

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    let File f = open("log.txt", FileMode.Read())??
    let i32 line_count = 0

    # Read until empty line (EOF)
    let bool done = false
    while (not done):
        let string line = f.readln().realise('')

        if (line.is_empty()):
            done := true
        else:
            line_count := line_count + 1
            println("{line_count}: {line}")

    f.close()
    println("Total lines: {line_count}")

    return Result.Ok(0)
```

### write

Write a string to the file.

```sushi
use <io/fs>

fn File.write(string data) -> ~
```

**Parameters:**
- `data` - String to write

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    let File f = open("output.txt", FileMode.Write())??

    f.write("Hello, World!")
    f.write("\n")
    f.write("Second line")

    f.close()

    return Result.Ok(0)
```

**Writing formatted data:**

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    let File f = open("report.txt", FileMode.Write())??

    f.write("Report\n")
    f.write("======\n\n")

    let i32 count = 42
    let string line = "Items processed: {count}\n"
    f.write(line)

    f.close()

    return Result.Ok(0)
```

**Appending to file:**

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    let File f = open("log.txt", FileMode.Append())??

    f.write("New log entry\n")

    f.close()

    return Result.Ok(0)
```

### flush

Push the stream buffer to the operating system.

```sushi
use <io/fs>

fn File.flush() -> ~
```

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    let File f = open("progress.log", FileMode.Write())??
    f.write("step 1 done")
    f.flush()  # The bytes reach the file before the next step runs
    f.close()

    return Result.Ok(0)
```

**Note:** `close()` also flushes. Use `flush()` when the file stays open and the bytes must be visible now: a log line before a risky operation, or a file another process reads.

### close

Close the file and release resources.

```sushi
use <io/fs>

fn File.close() -> ~
```

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    let File f = open("data.txt", FileMode.Read())??
    let string content = f.read_all().realise('')
    f.close()  # Always close files

    println(content)

    return Result.Ok(0)
```

**Important:** Always close files after use to release system resources. File handles are limited by the operating system.

## Error Handling

### Which channel a call answers

Two enums, and the line between them is what the call DOES rather than which module it is
in.

| the call | channel |
|---|---|
| `open()`, `close()`, and every read, write and seek on a `File` | `IoError` |
| the path utilities -- `exists`, `remove`, `rename`, `stat`, `mkdir_all`, `read_dir` | `FileError` |
| the `fd_*` descriptor primitives | `FileError` |

A read, a write and a seek answer `IoError` because those are the `Reader` / `Writer` /
`Seek` contract methods (`<io/contracts>`), and a perk contract carries one signature. A
`TcpStream`'s read answers the same `IoError`, which is what lets one generic serve both.

`open()` and `close()` answer `IoError` too, so a function that opens a file and then reads
it carries ONE channel from end to end and needs no conversion in the middle.

### IoError

```sushi
enum IoError:
    NotFound()          # ENOENT - the path does not exist
    PermissionDenied()  # EACCES, EPERM - insufficient permissions
    AlreadyExists()     # EEXIST - the path is already there
    IsDirectory()       # EISDIR - the path is a directory
    ConnectionReset()   # ECONNRESET, ECONNABORTED
    TimedOut()          # ETIMEDOUT
    Closed()            # EPIPE, ENOTCONN, EBADF
    Interrupted()       # EINTR
    WouldBlock()        # EAGAIN, EWOULDBLOCK
    DiskFull()          # ENOSPC - no space left on device
    TooManyOpen()       # EMFILE, ENFILE - too many open files
    InvalidInput()      # EINVAL, ENAMETOOLONG
    Os(i32 errno)       # the raw errno, for a failure with no variant of its own
    Other()             # anything else
```

`Os(i32)` is how detail survives without a global `last_errno()`, which would not be
thread-safe. Match it when you need the number:

```sushi
use <io/fs>

fn main() i32:
    match open("config.txt", FileMode.Read()):
        Result.Ok(f) -> println(f.read_all().realise(''))
        Result.Err(IoError.NotFound) -> println("no such file")
        Result.Err(IoError.Os(code)) -> println("errno {code}")
        Result.Err(_) -> println("could not open it")
    return Result.Ok(0)
```

### FileError

The path utilities and the descriptor primitives keep their own enum:

```sushi
enum FileError:
    NotFound()          # ENOENT - file does not exist
    PermissionDenied()  # EACCES, EPERM - insufficient permissions
    AlreadyExists()     # EEXIST - file already exists
    IsDirectory()       # EISDIR - path refers to a directory
    DiskFull()          # ENOSPC - no space left on device
    TooManyOpen()       # EMFILE, ENFILE - too many open files
    InvalidPath()       # ENAMETOOLONG - invalid path or filename
    IOError()           # EIO - generic I/O error
    Other()             # any other error
```

`<io/fs>` converts one into the other at its own boundary, with
`extend FileError to_io() IoError`, so a caller never writes the conversion.

### Error Patterns

#### Pattern matching all errors

```sushi
use <io/files>
use <io/fs>

fn main() i32:
    match open("config.txt", FileMode.Read()):
        Result.Ok(poke f) ->
            let string data = f.read_all().realise('')
            f.close()
            println(data)
        Result.Err(IoError.NotFound()) ->
            println("File not found")
        Result.Err(IoError.PermissionDenied()) ->
            println("Permission denied")
        Result.Err(_) ->
            println("Other error")

    return Result.Ok(0)
```

#### Nested error handling

```sushi
use <io/files>
use <io/fs>

fn main() i32:
    match open("data.txt", FileMode.Read()):
        Result.Ok(poke f) ->
            match open("output.txt", FileMode.Write()):
                Result.Ok(poke out) ->
                    let string data = f.read_all().realise('')
                    out.write(data)
                    f.close()
                    out.close()
                    println("File copied")
                Result.Err(_) ->
                    println("Failed to open output file")
                    f.close()
        Result.Err(IoError.NotFound()) ->
            println("Input file not found")
        Result.Err(_) ->
            println("Failed to open input file")

    return Result.Ok(0)
```

#### Using error propagation

```sushi
use <io/files>
use <io/fs>

fn copy_file(string src, string dst) ~ | IoError:
    let File input = open(src, FileMode.Read())??
    let string content = input.read_all().realise('')
    input.close()

    let File output = open(dst, FileMode.Write())??
    output.write(content)
    output.close()

    return Result.Ok(~)

fn main() i32:
    match copy_file("input.txt", "output.txt"):
        Result.Ok(_) ->
            println("Copy successful")
        Result.Err(_) ->
            println("Copy failed")

    return Result.Ok(0)
```

## File Utility Functions

### remove

Delete a file from the filesystem.

```sushi
fn remove(string path) -> Result@(i32)
```

**Parameters:**
- `path` - Path to the file to delete

**Returns:**
- `Result.Ok(0)` - File successfully deleted
- `Result.Err()` - Failed to delete file (doesn't exist, permission denied, etc.)

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32:
    match remove("/tmp/old_file.txt"):
        Result.Ok(_) ->
            println("File deleted")
        Result.Err(_) ->
            println("Failed to delete file")

    return Result.Ok(0)
```

**Important:** Only works on files, not directories. Use `rmdir()` for directories.

### rename

Rename or move a file or directory.

```sushi
fn rename(string old_path, string new_path) -> Result@(i32)
```

**Parameters:**
- `old_path` - Current path
- `new_path` - New path

**Returns:**
- `Result.Ok(0)` - Successfully renamed/moved
- `Result.Err()` - Failed (source doesn't exist, permission denied, etc.)

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32:
    match rename("/tmp/old.txt", "/tmp/new.txt"):
        Result.Ok(_) ->
            println("File renamed")
        Result.Err(_) ->
            println("Failed to rename")

    return Result.Ok(0)
```

**Note:** Atomically replaces destination if it exists.

### mtime / ctime

The modification and status-change times of a path, as unix seconds.

```sushi
fn mtime(string path) -> Result@(i64)
fn ctime(string path) -> Result@(i64)
```

**Example:**
```sushi
use <io/files>

fn main() i32:
    match mtime("build/output"):
        Result.Ok(t) -> println("last built at {t}")
        Result.Err(_) -> println("never built")

    return Result.Ok(0)
```

**Note:** ctime is the inode status-change time, not the creation time. A `chmod` moves it; a content write moves both.

### mode

The raw `st_mode` of a path: the file-type bits plus the permission bits.

```sushi
fn mode(string path) -> Result@(i32)
```

**Example:**
```sushi
use <io/files>

fn main() i32:
    match mode("script.sh"):
        Result.Ok(m) ->
            println("permissions: {m & 0o777}")
        Result.Err(_) -> println("no such file")

    return Result.Ok(0)
```

**Note:** mask with `0o777` for the permission bits, with `0o170000` for the file-type bits. `is_file`/`is_dir`/`is_symlink` answer the type question directly.

### is_symlink

Ask whether the path itself is a symbolic link. This is the one query that does
NOT follow the link (`lstat`); `is_file` and `is_dir` answer for the target.

```sushi
fn is_symlink(string path) -> Result@(bool)
```

**Example:**
```sushi
use <io/files>

fn main() i32:
    match is_symlink("/usr/local/bin/tool"):
        Result.Ok(link) ->
            if (link):
                println("a link")
            else:
                println("the real thing")
        Result.Err(_) -> println("no such path")

    return Result.Ok(0)
```

### read_dir

List the entries of a directory.

```sushi
fn read_dir(string path) -> Result@(string[])
```

**Parameters:**
- `path` - Directory path to list

**Returns:**
- `Result.Ok(string[])` - The entry names. `.` and `..` are not included.
- `Result.Err(FileError)` - Failed (not found, permission denied, not a directory)

**Example:**
```sushi
use <io/files>

fn main() i32:
    match read_dir("/tmp"):
        Result.Ok(entries) ->
            foreach(name in entries.iter()):
                println(name)
        Result.Err(_) -> println("Cannot list /tmp")

    return Result.Ok(0)
```

**Notes:**
- The result holds entry NAMES, not paths. Join with the directory yourself.
- The order is the order the OS returns; it is unspecified. Do not depend on it.
- Every kind of entry is listed: files, directories, symlinks.

### mkdir

Create a new directory with specified permissions.

```sushi
fn mkdir(string path, i32 mode) -> Result@(i32)
```

**Parameters:**
- `path` - Directory path to create
- `mode` - Permissions in octal format (e.g., 0o755, 0o700)

**Returns:**
- `Result.Ok(0)` - Directory created successfully
- `Result.Err()` - Failed (already exists, permission denied, parent doesn't exist)

**Example:**

```sushi
use <io/files>

fn main() i32:
    match mkdir("/tmp/mydir", 0o755):
        Result.Ok(_) ->
            println("Directory created")
        Result.Err(_) ->
            println("Failed to create directory")

    return Result.Ok(0)
```

**Common permissions:**
- `0o755` - Owner: rwx, Group: r-x, Others: r-x
- `0o700` - Owner: rwx, Group: ---, Others: ---
- `0o775` - Owner: rwx, Group: rwx, Others: r-x

**Note:** Does not create parent directories. All parents must already exist.

### rmdir

Remove an empty directory.

```sushi
fn rmdir(string path) -> Result@(i32)
```

**Parameters:**
- `path` - Directory path to remove

**Returns:**
- `Result.Ok(0)` - Directory removed successfully
- `Result.Err()` - Failed (doesn't exist, not empty, permission denied)

**Example:**

```sushi
use <io/files>

fn main() i32:
    match rmdir("/tmp/mydir"):
        Result.Ok(_) ->
            println("Directory removed")
        Result.Err(_) ->
            println("Failed to remove directory")

    return Result.Ok(0)
```

**Important:** Directory must be empty. Use `remove()` to delete files inside first.

### copy

Copy a file's contents to a new location.

```sushi
fn copy(string src, string dst) -> Result@(i32)
```

**Parameters:**
- `src` - Source file path
- `dst` - Destination file path

**Returns:**
- `Result.Ok(0)` - File copied successfully
- `Result.Err()` - Failed (source doesn't exist, permission denied, I/O error)

**Example:**

```sushi
use <io/files>
use <io/fs>

fn main() i32:
    match copy("/tmp/source.txt", "/tmp/backup.txt"):
        Result.Ok(_) ->
            println("File copied")
        Result.Err(_) ->
            println("Failed to copy file")

    return Result.Ok(0)
```

**Note:** Overwrites destination if it exists. Uses efficient 4KB buffering internally.

### File Utility Pattern Matching Example

```sushi
use <io/files>

fn backup_and_cleanup(string path) ~:
    let string backup = "{path}.bak"

    # path is used twice below; clone it for the first call so it stays
    # usable for the second (a by-value string argument moves)
    match copy(path.clone(), backup):
        Result.Ok(_) ->
            println("Backup created")
        Result.Err(_) ->
            return Result.Err(StdError.Error)

    match remove(path):
        Result.Ok(_) ->
            println("Original removed")
        Result.Err(_) ->
            println("Cleanup failed")
            return Result.Err(StdError.Error)

    return Result.Ok(~)

fn main() i32:
    match backup_and_cleanup("/tmp/data.txt"):
        Result.Ok(_) ->
            println("Operation complete")
        Result.Err(_) ->
            println("Operation failed")

    return Result.Ok(0)
```

## The descriptor layer

Underneath the `file` handle sits a thin layer over the raw descriptor. It is the same
shape `<net/socket>` gives `<net/tcp>` — the primitives a handle type is written on top
of — and it comes in two halves.

**Positional**: `fd_pread` and `fd_pwrite` take the offset as an argument and never move
the descriptor's own file position, which is what makes them safe to share.

**Sequential**: `fd_read`, `fd_write`, `fd_write_str`, `fd_readln` and `fd_seek` move it,
which is what makes them the ones a handle is written on.

`fd_open`, `fd_dup`, `fd_close` and `fd_isatty` belong to neither half.

### `fd_pread(i32 fd, i64 offset, i32 max) -> Result@(u8[], FileError)`
### `fd_pwrite(i32 fd, i64 offset, u8[] data) -> Result@(i32, FileError)`

Read or write at an offset **without moving the descriptor's file position**. That is
what makes them the answer for concurrent reads of one file: the offset is an argument,
so nothing is shared and nothing can race. Every language that supports concurrent file
I/O converged on this primitive — C and POSIX have `pread(2)` and `pwrite(2)`, Go has
`File.ReadAt`, Rust has `FileExt::read_at`, Java takes a position argument on
`FileChannel.read` — and none of them needed a new kind of type.

`fd_pread` answers what ARRIVED, which may be fewer bytes than asked for and is empty at
end of file. `fd_pwrite` answers the count it took; looping until the whole buffer is
gone is the caller's job.

### `fd_open(string path, i32 intent, i32 mode) -> Result@(i32, FileError)`

`intent` says what the caller WANTS, not what the platform calls it:

| intent | means |
|---|---|
| `0` | read only |
| `1` | write: create, truncate |
| `2` | append: create, append |
| `3` | read and write: create, keep |

The `O_*` flag values differ between macOS and Linux, and Sushi has no conditional
compilation, so a number spelled in portable source would be wrong on one of them. The
intent crosses the boundary and the platform layer maps it. An unrecognised number opens
read-only, which is the safe reading of a value this function does not know.

`mode` is the permission bits a newly created file gets, as an integer — `420` is `0644`.

### `fd_read(i32 fd, i32 max) -> Result@(u8[], FileError)`

ONE `read(2)` from the descriptor's current position, which it advances. The answer
carries what ARRIVED and may be shorter than asked for; an **empty array is end of file**
and not an error, so a caller loops until the answer is empty.

### `fd_write(i32 fd, u8[] data) -> Result@(i32, FileError)`
### `fd_write_str(i32 fd, string s) -> Result@(i32, FileError)`

Write every byte, looping past a short write, and answer the count. `fd_write_str` takes
the string's own bytes with no `to_bytes()` copy in front of them — a fat pointer already
carries a pointer and a length, which is what `write(2)` wants.

Unlike `sock_send`, these do not hand a partial write back to the caller. A socket's
partial write is information — how much the peer's window took — and a file's is not.

### `fd_readln(i32 fd) -> Result@(string, FileError)`

One line, the newline **stripped**, and an empty string at end of file — so a caller
loops until the answer is empty, exactly as with `fd_read`.

It has two paths, and which one runs depends on whether the descriptor can seek:

| descriptor | how it reads | why |
|---|---|---|
| a file, or anything seekable | `pread` a chunk, then one `lseek` past the newline | fast: 0.60s for 200 000 lines, against 0.47s for the buffered `fgets` it replaces |
| a pipe, a socket, a terminal — including `stdin` | one byte at a time | correct: a pipe cannot give back an over-read, so a chunked read would swallow bytes the next reader owns |

The byte-at-a-time path costs 4.49s over the same 200 000 lines, nearly all of it in the
kernel. That is the price of not losing data, and it is only paid where it must be. For
bulk line reading over any descriptor, the buffered reader is the general answer.

### `fd_seek(i32 fd, i64 offset, i32 whence) -> Result@(i64, FileError)`

Move the descriptor's file position, and answer the NEW position. `whence` is an intent
like `fd_open`'s: `0` from the start, `1` from the current position, `2` from the end.

There is no `fd_tell`, because it would say nothing new: the current position is
`fd_seek(fd, 0, 1)`.

### `fd_isatty(i32 fd) -> bool`

Whether the descriptor is a terminal. A **bare bool**, not a Result: a descriptor that is
not a terminal and a descriptor that is not open both answer false, so there is no
failure a caller could act on.

### `fd_dup(i32 fd) -> Result@(i32, FileError)`

A **second descriptor over the same open file description**. The offset is shared, so
this is for the shared-listener pattern and not for concurrent reads of one file —
`fd_pread` and `fd_pwrite` are that.

### `fd_close(i32 fd) -> Result@(i32, FileError)`

Close one descriptor.

## Common Patterns

### Reading entire file

```sushi
use <io/files>
use <io/fs>

fn read_file(string path) string | IoError:
    let File f = open(path, FileMode.Read())??
    let string content = f.read_all().realise('')
    f.close()
    return Result.Ok(content)

fn main() i32:
    let string content = read_file("data.txt").realise("")
    println(content)

    return Result.Ok(0)
```

### Writing entire file

```sushi
use <io/files>
use <io/fs>

fn write_file(string path, string data) ~ | IoError:
    let File f = open(path, FileMode.Write())??
    f.write(data)
    f.close()
    return Result.Ok(~)

fn main() i32 | IoError:
    write_file("output.txt", "Hello, World!")??
    println("File written")

    return Result.Ok(0)
```

### Processing CSV file

```sushi
use <io/files>
use <io/fs>
use <collections/strings>

fn main() i32 | IoError:
    let File f = open("data.csv", FileMode.Read())??
    let string content = f.read_all().realise('')
    f.close()

    let string[] lines = content.split("\n")

    foreach(line in lines.iter()):
        if (not line.is_empty()):
            let string[] fields = line.split(",")

            foreach(field in fields.iter()):
                print("{field}\t")

            println("")

    return Result.Ok(0)
```

### Creating log file

```sushi
use <io/files>
use <io/fs>

fn log_message(string message) ~ | IoError:
    let File f = open("app.log", FileMode.Append())??
    f.write("{message}\n")
    f.close()
    return Result.Ok(~)

fn main() i32 | IoError:
    log_message("Application started")??
    log_message("Processing data")??
    log_message("Application finished")??

    return Result.Ok(0)
```

### Checking file existence

```sushi
use <io/files>
use <io/fs>

fn file_exists(string path) bool:
    match open(path, FileMode.Read()):
        Result.Ok(poke f) ->
            f.close()
            return Result.Ok(true)
        Result.Err(_) ->
            return Result.Ok(false)

fn main() i32:
    if (file_exists("config.txt").realise(false)):
        println("Config file found")
    else:
        println("Config file not found")

    return Result.Ok(0)
```

## Platform Behavior

### Path Separators

- **Unix/Linux/macOS:** Forward slash `/`
- **Recommended:** Use forward slashes for cross-platform compatibility

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    # Unix-style paths work on all platforms
    let File f = open("data/input.txt", FileMode.Read())??
    f.close()

    return Result.Ok(0)
```

### Line Endings

Different platforms use different line endings:
- **Unix/Linux/macOS:** `\n` (LF)
- **Windows:** `\r\n` (CRLF)

Sushi uses `\n` internally. When reading files, line endings are preserved.

### File Permissions

File permissions are platform-specific:
- **Unix/Linux/macOS:** Standard POSIX permissions (user/group/other)
- **Windows:** ACLs

`PermissionDenied` error occurs when the process lacks required permissions.

## Performance Considerations

### Buffering

File operations are buffered by the operating system. For large files:

```sushi
use <io/files>
use <io/fs>

fn main() i32 | IoError:
    let File f = open("large.txt", FileMode.Read())??

    # Reading line-by-line is more memory-efficient than .read_all().realise('')
    let bool done = false
    while (not done):
        let string line = f.readln().realise('')

        if (line.is_empty()):
            done := true
        else:
            # Process line
            println(line)

    f.close()

    return Result.Ok(0)
```

### Memory Usage

- `.read_all().realise('')` loads entire file into memory
- `.readln().realise('')` reads one line at a time (more memory-efficient)

Choose based on file size and use case.

## Security Considerations

### Path Traversal

Always validate file paths from user input:

```sushi
use <io/files>
use <io/fs>
use <collections/strings>

fn is_safe_path(string path) bool:
    # Reject paths with ..
    if (path.contains("..")):
        return Result.Ok(false)

    # Reject absolute paths if needed
    if (path.starts_with("/")):
        return Result.Ok(false)

    return Result.Ok(true)

fn main() i32 | IoError:
    let string user_path = "data.txt"

    if (not is_safe_path(user_path).realise(false)):
        println("Invalid path")
        return Result.Ok(1)

    let File f = open(user_path, FileMode.Read())??
    let string content = f.read_all().realise('')
    f.close()

    println(content)

    return Result.Ok(0)
```

### File Permissions

Be cautious with write operations:

```sushi
use <io/files>
use <io/fs>

fn main() i32:
    # Always check if overwriting is intended
    match open("important.txt", FileMode.Write()):
        Result.Ok(poke f) ->
            # This TRUNCATES the existing file!
            f.write("New content")
            f.close()
        Result.Err(_) ->
            println("Failed to open file")

    return Result.Ok(0)
```

Use `FileMode.Append()` to preserve existing content.

## See Also

- [Console I/O](console.md) - Standard input/output/error operations
- [String Methods](../../standard-library.md) - String operations for file content
- [Standard Library Reference](../../standard-library.md) - Complete stdlib reference
- [Error Handling](../../error-handling.md) - Result and Maybe types
