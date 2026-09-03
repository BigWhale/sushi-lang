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

fn log_it() ~ | IoError:
    let File f = open("out.log", FileMode.Append())??
    f.writeln("Mostly Harmless")??
    return Result.Ok(~)
    # f drops here. The descriptor closes.
```

Three things follow from that, and each surprises somebody:

- **A `File` cannot be copied.** `.clone()` is **CE2431**: a field-by-field copy would
  duplicate the descriptor number and leave two owners that both close it.
- **`close()` CONSUMES the handle.** A read after a close is **CE2435** while compiling,
  not EBADF at run time, and the scope exit that follows has nothing left to close. A
  bare `match` binding is a read-only view, so `Result.Ok(f) -> f.close()` is CE2411:
  write `Result.Ok(nom f)` over a scrutinee the match owns, or -- better -- delete the
  `close()` and let the drop do it. A handle held in a struct FIELD cannot be closed
  explicitly at all, for the same reason.
- **Reads and writes are UNBUFFERED.** Each one is a system call. A handle that
  buffered would have to flush at a point nobody wrote, and a destructor cannot report
  the failure of that flush -- so buffering is a separate type that a caller opts into,
  [`BufReader` and `BufWriter`](buf.md).

The contract methods -- `read`, `write`, `flush`, `seek`, and the conveniences over
them (`read_all`, `readch`, `writeln`, `tell`) -- take `poke self`: the receiver every
`Reader` and `Writer` declares, so that a BUFFERED handle can implement them. A file's
own position lives in the KERNEL, so the mode costs it nothing, but it does mean the
handle you write through must be writable storage: a `let` local, a `poke` parameter or
a `nom`/`poke` match binding -- never a bare borrow. `close()` takes the handle.

`stdin`, `stdout` and `stderr` are `File` values too -- see [Console I/O](console.md).

## Opening Files

### open

Open a file with a specific mode.

```sushi
use <io/fs>

fn open(string path, FileMode mode) File | IoError
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
        Result.Ok(nom f) ->
            println("open: {f.is_open()}")
            # f drops at the end of the arm, and the descriptor closes.
        Result.Err(_) ->
            println("Failed to open file")

    return Result.Ok(0)
```

**With error propagation:**

```sushi
use <io/files>
use <io/fs>

fn read_config() string | IoError:
    let File f = open("config.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)
    # f drops at the return, and the descriptor closes.

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
| `read` | `(poke self, i32 max) string \| IoError` -- one read, as text | `Reader` |
| `read_bytes` | `(poke self, i32 max) u8[] \| IoError` -- one read, as bytes | `Reader` |
| `write` | `(poke self, string data) ~ \| IoError` -- every byte, or an error | `Writer` |
| `write_bytes` | `(poke self, u8[] data) ~ \| IoError` | `Writer` |
| `flush` | `(poke self) ~ \| IoError` -- a successful no-op on a descriptor | `Writer` |
| `seek` | `(poke self, i64 offset, SeekFrom origin) i64 \| IoError` -- answers the NEW position | `Seek` |
| `read_all` | `(poke self) string \| IoError` -- the whole file, from the current position | `File` |
| `readln` | `() Maybe@(string) \| IoError` -- one line, newline stripped; `None` at the end | `File` |
| `readch` | `() string \| IoError` -- one byte, as text | `File` |
| `writeln` | `(string data) ~ \| IoError` | `File` |
| `tell` | `() i64 \| IoError` | `File` |
| `read_at` | `(i64 offset, i32 count) u8[] \| IoError` -- one read at an offset; the position does not move | `File` |
| `write_at` | `(i64 offset, u8[] data) i32 \| IoError` -- one write at an offset, answers the count; the position does not move | `File` |
| `share` | `() File \| IoError` -- `dup(2)`: a second OWNER over the SAME open file description, so the offset is SHARED | `File` |
| `is_open` | `() bool` -- false once `close()` has run | `File` |
| `is_terminal` | `() bool` -- true only for a terminal | `File` |
| `close` | `(nom self) ~ \| IoError` -- CONSUMES the handle | `File` |

A `File` closes itself when its owner leaves scope, so `close()` is only needed where the
failure has to be SEEN. Every method is a plain borrow except `close()`, which CONSUMES
the handle: a file's position lives in the kernel, not in the struct, so a read and a
write need no mutable receiver.

**A `File` keeps no line loop.** There is no `File.lines()`: an unbuffered handle
yielding lines is one system call per line, which is the cost the buffer exists to
remove. `readln()` is the one-line unbuffered read and can serve in a pinch;
[`BufReader.lines()`](buf.md) is what a loop wants.

### read and read_all

`read(max)` is ONE read and answers what arrived; `read_all()` loops until the end and
answers the rest of the file from the current position.

```sushi
fn File.read(i32 max) string | IoError
fn File.read_all() string | IoError
```

`read()`'s bound counts BYTES, so a multi-byte character can be split across two calls --
a caller that must not cut one reads bytes with `read_bytes()`, accumulates them, and
converts once. An EMPTY answer is the end of input -- a SHORT answer is not, because a
pipe hands over whatever has arrived so far. `read_all()` holds the whole answer in memory
at once; a large file wants `read()` in a loop, or [`BufReader`](buf.md).

**Example:** the whole file, in a helper that carries the channel.

```sushi
use <io/fs>

fn contents(string path) string | IoError:
    let File f = open(path, FileMode.Read())??
    return Result.Ok(f.read_all()??)

fn main() i32:
    match contents("data.txt"):
        Result.Ok(text) -> println("Content: {text}")
        Result.Err(_) -> println("Failed to read file")
    return Result.Ok(0)
```

**Processing file content:**

```sushi
use <io/fs>
use <collections/strings>

fn report(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let string content = f.read_all()??
    let string[] lines = content.split("\n")

    foreach(line in lines.iter()):
        if (not line.is_empty()):
            println("Line: {line}")

    return Result.Ok(~)

fn main() i32:
    match report("numbers.txt"):
        Result.Ok(_) -> println("read")
        Result.Err(_) -> println("failed")
    return Result.Ok(0)
```

### readln

Read one line from the file, without its newline.

```sushi
fn File.readln() Maybe@(string) | IoError
```

**Returns:** the line, or `Maybe.None` at end of file. A blank line is `Maybe.Some("")`,
so a caller can tell a blank line from the end of the file. This is the UNBUFFERED read:
one system call per line at best. Reading a whole file line by line wants `BufReader`
from `<io/buf>`.

**Example:**

<!-- docs-sweep: skip (reads a file the sweep does not create) -->
```sushi
use <io/fs>

fn first_two(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    match f.readln()??:
        Maybe.Some(line) -> println("First: {line}")
        Maybe.None -> println("empty file")
    match f.readln()??:
        Maybe.Some(line) -> println("Second: {line}")
        Maybe.None -> println("one line only")
    return Result.Ok(~)
```

**Line-by-line processing:** the loop ends on `Maybe.None` and on nothing else, so a
blank line in the middle of the file does not truncate it. Note that this is the
UNBUFFERED loop, at one system call per line: `foreach(line?? in r.lines())` over a
[`BufReader`](buf.md) is what a whole file wants, and it is shorter besides.

<!-- docs-sweep: skip (reads a file the sweep does not create) -->
```sushi
use <io/fs>

fn count_lines(string path) i32 | IoError:
    let File f = open(path, FileMode.Read())??
    let i32 lines = 0
    let bool done = false
    while (not done):
        match f.readln()??:
            Maybe.Some(line) ->
                lines := lines + 1
                println("{lines}: {line}")
            Maybe.None ->
                done := true
    return Result.Ok(lines)
```

### write and writeln

```sushi
fn File.write(string data) ~ | IoError
fn File.writeln(string data) ~ | IoError
fn File.write_bytes(u8[] data) ~ | IoError
```

A write answers `~` and never a count: the primitive underneath LOOPS past a short write,
so every byte has gone or the call is an error. That is why there is no partial-write case
to handle, and why a discarded write Result is `CW2001` at every bare-statement call site.

**Example:**

```sushi
use <io/fs>

fn write_report(string path, i32 count) ~ | IoError:
    let File f = open(path, FileMode.Write())??
    f.writeln("Report")??
    f.writeln("======")??
    f.writeln("Items processed: {count}")??
    return Result.Ok(~)
    # f drops here, and the descriptor closes.

fn main() i32:
    match write_report("report.txt", 42):
        Result.Ok(_) -> println("written")
        Result.Err(_) -> println("failed")
    return Result.Ok(0)
```

**Appending:** the mode is the only difference. `FileMode.Append()` puts every write at
the end of the file, so two processes appending do not overwrite each other's lines.

```sushi
use <io/fs>

fn log_line(string message) ~ | IoError:
    let File f = open("log.txt", FileMode.Append())??
    f.writeln(message)??
    return Result.Ok(~)

fn main() i32:
    match log_line("Mostly Harmless"):
        Result.Ok(_) -> println("logged")
        Result.Err(_) -> println("failed")
    return Result.Ok(0)
```

### flush

```sushi
fn File.flush() ~ | IoError
```

**It does nothing, successfully.** A descriptor is not buffered: by the time `write()`
returns, the bytes are the kernel's. The method is on the `Writer` contract so that a
function written against `Writer` keeps compiling when the handle it is given is swapped
for a [`BufWriter`](buf.md), where the call does real work.

Getting the bytes onto the DISK is `fsync()`, a much stronger promise, and not what
`flush()` has ever meant.

### close

Close the file, and CONSUME the handle.

```sushi
fn File.close(nom self) ~ | IoError
```

The call takes the handle, so the binding is spent: a read after a close is **CE2435**
while compiling, and the scope exit that follows has nothing to close. Call it only where
the failure has to be SEEN -- an owned handle closes itself on drop, and a destructor
cannot answer a `Result`. The close itself can fail: a write the file system had not
finished is reported here and nowhere else.

**Example:**

<!-- docs-sweep: skip (reads a file the sweep does not create) -->
```sushi
use <io/fs>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let string content = f.read_all()??
    f.close()??
    println(content)
    return Result.Ok(~)
```

**A handle in a struct field cannot be closed this way.** A field read is a borrow, and
consuming one is **CE2411**. Let the struct's own drop close it, or take the handle out
of the wrapper that holds it with `into_inner()`.

### read_at and write_at

```sushi
fn File.read_at(i64 offset, i32 count) u8[] | IoError
fn File.write_at(i64 offset, u8[] data) i32 | IoError
```

The offset is an ARGUMENT, so neither call moves the file position: a `read()` or a
`tell()` afterwards finds it where it was. That is what makes the pair the answer for
concurrent reads of one file -- nothing is shared between two callers, so nothing can
race. Every language that supports concurrent file I/O converged on it, and none needed
a new kind of type: the offset stops being shared state the moment it becomes an
argument.

`read_at` is one `pread(2)` and answers what was there, which may be fewer bytes than
asked for and is empty past the end of the file. `write_at` is one `pwrite(2)`: the bytes
it covers are replaced, the rest of the file is left alone, and it answers the count it
took, which may be fewer than offered -- the loop is the caller's, exactly as it is for a
socket's `send()`. A pipe has no offset, and both answer an error on one.

```sushi
use <io/fs>

fn magic(string path) string | IoError:
    let File f = open(path, FileMode.Read())??
    let u8[] head = f.read_at(0 as i64, 4)??
    return Result.Ok(head.to_string())

fn main() i32:
    match magic("archive.bin"):
        Result.Ok(m) -> println("starts with {m}")
        Result.Err(_) -> println("no archive")
    return Result.Ok(0)
```

### share

```sushi
fn File.share() File | IoError
```

A second handle over the SAME open file description: `dup(2)`. The answer is an
independent descriptor the program OWNS, so it closes on drop -- even when the receiver
is `stdout`, which does not -- and closing either handle leaves the other open. The
receiver is a plain borrow, so `let File twin = f.share()??` leaves `f` usable, and
`twin.close()??` spends only `twin`.

**The offset is shared.** It is part of the open file description, so a read through one
handle moves the other. That makes `share()` the shared-listener pattern -- several
workers taking turns on one descriptor -- and NOT the answer for concurrent reads of one
file. `read_at()` and `write_at()` are that answer, because their offset is an argument.

A handle has no `.clone()` (**CE2431**): a copy verb would hide the second descriptor.
`share()` is the operation that means one, and its name says so.

```sushi
use <io/fs>

fn main() i32:
    match stdout.share():
        Result.Ok(nom out) -> out.writeln("Mostly Harmless")
        Result.Err(_) -> println("no second handle")
    return Result.Ok(0)
```

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

fn config() string | IoError:
    let File f = open("config.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)

fn main() i32:
    match config():
        Result.Ok(text) -> println(text)
        Result.Err(IoError.NotFound) -> println("no such file")
        Result.Err(IoError.Os(code)) -> println("errno {code}")
        Result.Err(_) -> println("could not read it")
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

fn read_config() string | IoError:
    let File f = open("config.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)

fn main() i32:
    match read_config():
        Result.Ok(data) ->
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

fn duplicate(string src, string dst) ~ | IoError:
    let File input = open(src, FileMode.Read())??
    let File output = open(dst, FileMode.Write())??
    output.write(input.read_all()??)??
    return Result.Ok(~)
    # Both handles drop here, in reverse declaration order: output, then input.

fn main() i32:
    match duplicate("data.txt", "output.txt"):
        Result.Ok(_) ->
            println("File copied")
        Result.Err(IoError.NotFound()) ->
            println("Input file not found")
        Result.Err(_) ->
            println("Failed to copy")

    return Result.Ok(0)
```

#### Using error propagation

```sushi
use <io/files>
use <io/fs>

fn copy_file(string src, string dst) ~ | IoError:
    let File input = open(src, FileMode.Read())??
    let string content = input.read_all()??

    let File output = open(dst, FileMode.Write())??
    output.write(content)??
    output.close()??          # the close is checked, so a failed flush to disk is seen

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
fn remove(string path) -> Result@(i32, FileError)
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
fn rename(string old_path, string new_path) -> Result@(i32, FileError)
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
fn mtime(string path) -> Result@(i64, FileError)
fn ctime(string path) -> Result@(i64, FileError)
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
fn mode(string path) -> Result@(i32, FileError)
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
fn is_symlink(string path) -> Result@(bool, FileError)
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
fn read_dir(string path) -> Result@(string[], FileError)
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
fn mkdir(string path, i32 mode) -> Result@(i32, FileError)
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
fn rmdir(string path) -> Result@(i32, FileError)
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
fn copy(string src, string dst) -> Result@(i32, FileError)
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

### `fd_readln(i32 fd) -> Result@(Maybe@(string), FileError)`

One line, the newline **stripped**, in a `Maybe`. A blank line is `Maybe.Some("")` and
the end of the file is `Maybe.None`, so the two are never the same answer. It used to
answer an empty string for both, which truncated a file at its first blank line and made
a failed read look like a clean end.

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
`fd_pread` and `fd_pwrite` are that. `File.share()` is written on it.

### `fd_close(i32 fd) -> Result@(i32, FileError)`

Close one descriptor.

## Common Patterns

### Reading entire file

```sushi
use <io/files>
use <io/fs>

fn read_file(string path) string | IoError:
    let File f = open(path, FileMode.Read())??
    return Result.Ok(f.read_all()??)

fn main() i32:
    match read_file("data.txt"):
        Result.Ok(content) -> println(content)
        Result.Err(_) -> println("could not read it")

    return Result.Ok(0)
```

### Writing entire file

```sushi
use <io/files>
use <io/fs>

fn write_file(string path, string data) ~ | IoError:
    let File f = open(path, FileMode.Write())??
    f.write(data)??
    return Result.Ok(~)

fn main() i32:
    match write_file("output.txt", "Mostly Harmless"):
        Result.Ok(_) -> println("File written")
        Result.Err(_) -> println("could not write it")

    return Result.Ok(0)
```

### Processing CSV file

```sushi
use <io/files>
use <io/fs>
use <collections/strings>

fn show_csv(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let string content = f.read_all()??
    let string[] lines = content.split("\n")

    foreach(line in lines.iter()):
        if (not line.is_empty()):
            let string[] fields = line.split(",")

            foreach(field in fields.iter()):
                print("{field}\t")

            println("")

    return Result.Ok(~)

fn main() i32:
    match show_csv("data.csv"):
        Result.Ok(_) -> println("read")
        Result.Err(_) -> println("failed")

    return Result.Ok(0)
```

### Creating log file

```sushi
use <io/files>
use <io/fs>

fn log_message(string message) ~ | IoError:
    let File f = open("app.log", FileMode.Append())??
    f.writeln(message)??
    return Result.Ok(~)

fn run() ~ | IoError:
    log_message("Application started")??
    log_message("Processing data")??
    log_message("Application finished")??
    return Result.Ok(~)

fn main() i32:
    match run():
        Result.Ok(_) -> println("logged")
        Result.Err(_) -> println("could not log")

    return Result.Ok(0)
```

### Checking file existence

`exists()` answers a bare `bool` and opens nothing, so it costs one `stat` and cannot
leave a descriptor behind. Note that an answer is only ever a statement about the past:
between the check and the open, another process can create or remove the path, so a
program that must not race should open the file and read the error instead.

```sushi
use <io/files>

fn main() i32:
    if (exists("config.txt")):
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
use <io/fs>

fn read_input() string | IoError:
    # Unix-style paths work on all platforms
    let File f = open("data/input.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)

fn main() i32:
    match read_input():
        Result.Ok(text) -> println(text)
        Result.Err(_) -> println("no input")

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

**A `File` does not buffer.** Every read and every write on a handle is one system call,
so a loop of small reads pays for one call each. That is the cost
[`BufReader` and `BufWriter`](buf.md) exist to remove: they read a window and hand out
of it, so a line-at-a-time loop costs one call per window instead of one per line.

```sushi
use <io/fs>
use <io/buf>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = buf_reader(nom f, 8192)??

    foreach(line?? in r.lines()):
        println(line)

    return Result.Ok(~)

fn main() i32:
    match show("large.txt"):
        Result.Ok(_) -> println("read")
        Result.Err(_) -> println("failed")

    return Result.Ok(0)
```

### Memory Usage

- `read_all()` loads the whole file into memory, however large it is
- `readln()` holds one line at a time, at the cost of one system call per line
- `BufReader.read_line()`, and `foreach` over `r.lines()`, hold one line at a time and
  pay one system call per WINDOW

Choose by file size: `read_all()` for a configuration file, the buffered loop for
anything a user might grow without asking.

## Security Considerations

### Path Traversal

Always validate file paths from user input:

```sushi
use <io/files>
use <io/fs>
use <collections/strings>

extend string is_safe_path() bool:
    # Reject paths with ..
    if (self.contains("..")):
        return false

    # Reject absolute paths if needed
    if (self.starts_with("/")):
        return false

    return true

fn read_checked(string path) string | IoError:
    if (not path.is_safe_path()):
        return Result.Err(IoError.InvalidInput)

    let File f = open(path, FileMode.Read())??
    return Result.Ok(f.read_all()??)

fn main() i32:
    match read_checked("data.txt"):
        Result.Ok(content) -> println(content)
        Result.Err(IoError.InvalidInput) -> println("Invalid path")
        Result.Err(_) -> println("could not read it")

    return Result.Ok(0)
```

### File Permissions

Be cautious with write operations:

```sushi
use <io/files>
use <io/fs>

fn overwrite() ~ | IoError:
    # FileMode.Write() TRUNCATES the existing file.
    let File f = open("important.txt", FileMode.Write())??
    f.write("New content")??
    return Result.Ok(~)

fn main() i32:
    match overwrite():
        Result.Ok(_) -> println("written")
        Result.Err(_) -> println("Failed to open file")

    return Result.Ok(0)
```

Use `FileMode.Append()` to preserve existing content.

## See Also

- [Buffered I/O](buf.md) - `BufReader` and `BufWriter` over any handle
- [I/O Contracts](contracts.md) - `Reader`, `Writer` and `Seek`
- [Console I/O](console.md) - Standard input/output/error operations
- [String Methods](../../standard-library.md) - String operations for file content
- [Standard Library Reference](../../standard-library.md) - Complete stdlib reference
- [Error Handling](../../error-handling.md) - Result and Maybe types
