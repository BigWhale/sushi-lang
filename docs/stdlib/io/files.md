# File Operations

[← Back to Standard Library](../../standard-library.md)

File system operations for reading, writing, and managing files.

## Import

```sushi
use <io/files>
```

## Overview

The file operations module provides safe file I/O with explicit error handling. All file operations use POSIX file descriptors under the hood and return `FileResult` for proper error handling.

## Opening Files

### open

Open a file with a specific mode.

```sushi
fn open(string path, FileMode mode) -> FileResult
```

**Parameters:**
- `path` - File path (relative or absolute)
- `mode` - File access mode

**Returns:**
- `FileResult.Ok(file)` - Successfully opened file
- `FileResult.Err(error)` - Error occurred (see Error Handling section)

**File Modes:**

- `FileMode.Read()` - Open for reading only (file must exist)
- `FileMode.Write()` - Open for writing only (creates file or truncates existing)
- `FileMode.Append()` - Open for appending (creates file if doesn't exist)

**Example:**

```sushi
use <io/files>

fn main() i32:
    match open("data.txt", FileMode.Read()):
        FileResult.Ok(f) ->
            println("File opened successfully")
            f.close()
        FileResult.Err(e) ->
            println("Failed to open file")

    return Result.Ok(0)
```

**With error propagation:**

```sushi
use <io/files>

fn read_config() string:
    let file f = open("config.txt", FileMode.Read())??
    let string content = f.read()
    f.close()
    return Result.Ok(content)

fn main() i32:
    match read_config():
        Result.Ok(config) ->
            println("Config: {config}")
        Result.Err() ->
            println("Failed to read config")

    return Result.Ok(0)
```

## File Methods

### read

Read entire file contents as a string.

```sushi
fn file.read() -> string
```

**Returns:**
- String containing entire file contents

**Example:**

```sushi
use <io/files>

fn main() i32:
    match open("data.txt", FileMode.Read()):
        FileResult.Ok(f) ->
            let string content = f.read()
            f.close()
            println("Content: {content}")
        FileResult.Err(_) ->
            println("Failed to read file")

    return Result.Ok(0)
```

**Processing file content:**

```sushi
use <io/files>

fn main() i32:
    let file f = open("numbers.txt", FileMode.Read())??
    let string content = f.read()
    f.close()

    let string[] lines = content.split("\n")

    foreach(line in lines.iter()):
        if (not line.is_empty()):
            println("Line: {line}")

    return Result.Ok(0)
```

### read_line

Read a single line from the file.

```sushi
fn file.read_line() -> string
```

**Returns:**
- String containing one line (without newline character)

**Example:**

```sushi
use <io/files>

fn main() i32:
    let file f = open("data.txt", FileMode.Read())??

    let string first_line = f.read_line()
    println("First: {first_line}")

    let string second_line = f.read_line()
    println("Second: {second_line}")

    f.close()

    return Result.Ok(0)
```

**Line-by-line processing:**

```sushi
use <io/files>

fn main() i32:
    let file f = open("log.txt", FileMode.Read())??
    let i32 line_count = 0

    # Read until empty line (EOF)
    let bool done = false
    while (not done):
        let string line = f.read_line()

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
fn file.write(string data) -> ~
```

**Parameters:**
- `data` - String to write

**Example:**

```sushi
use <io/files>

fn main() i32:
    let file f = open("output.txt", FileMode.Write())??

    f.write("Hello, World!")
    f.write("\n")
    f.write("Second line")

    f.close()

    return Result.Ok(0)
```

**Writing formatted data:**

```sushi
use <io/files>

fn main() i32:
    let file f = open("report.txt", FileMode.Write())??

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

fn main() i32:
    let file f = open("log.txt", FileMode.Append())??

    f.write("New log entry\n")

    f.close()

    return Result.Ok(0)
```

### close

Close the file and release resources.

```sushi
fn file.close() -> ~
```

**Example:**

```sushi
use <io/files>

fn main() i32:
    let file f = open("data.txt", FileMode.Read())??
    let string content = f.read()
    f.close()  # Always close files

    println(content)

    return Result.Ok(0)
```

**Important:** Always close files after use to release system resources. File handles are limited by the operating system.

## Error Handling

### FileResult Enum

Result type for file operations:

```sushi
enum FileResult:
    Ok(file)
    Err(FileError)
```

### FileError Enum

Error types for file operations:

```sushi
enum FileError:
    NotFound()
    PermissionDenied()
    AlreadyExists()
    Other()
```

### Error Patterns

#### Pattern matching all errors

```sushi
use <io/files>

fn main() i32:
    match open("config.txt", FileMode.Read()):
        FileResult.Ok(f) ->
            let string data = f.read()
            f.close()
            println(data)
        FileResult.Err(FileError.NotFound()) ->
            println("File not found")
        FileResult.Err(FileError.PermissionDenied()) ->
            println("Permission denied")
        FileResult.Err(_) ->
            println("Other error")

    return Result.Ok(0)
```

#### Nested error handling

```sushi
use <io/files>

fn main() i32:
    match open("data.txt", FileMode.Read()):
        FileResult.Ok(f) ->
            match open("output.txt", FileMode.Write()):
                FileResult.Ok(out) ->
                    let string data = f.read()
                    out.write(data)
                    f.close()
                    out.close()
                    println("File copied")
                FileResult.Err(_) ->
                    println("Failed to open output file")
                    f.close()
        FileResult.Err(FileError.NotFound()) ->
            println("Input file not found")
        FileResult.Err(_) ->
            println("Failed to open input file")

    return Result.Ok(0)
```

#### Using error propagation

```sushi
use <io/files>

fn copy_file(string src, string dst) ~:
    let file input = open(src, FileMode.Read())??
    let string content = input.read()
    input.close()

    let file output = open(dst, FileMode.Write())??
    output.write(content)
    output.close()

    return Result.Ok(~)

fn main() i32:
    match copy_file("input.txt", "output.txt"):
        Result.Ok(_) ->
            println("Copy successful")
        Result.Err() ->
            println("Copy failed")

    return Result.Ok(0)
```

## File Utility Functions

### remove

Delete a file from the filesystem.

```sushi
fn remove(string path) -> Result<i32>
```

**Parameters:**
- `path` - Path to the file to delete

**Returns:**
- `Result.Ok(0)` - File successfully deleted
- `Result.Err()` - Failed to delete file (doesn't exist, permission denied, etc.)

**Example:**

```sushi
use <io/files>

fn main() i32:
    match remove("/tmp/old_file.txt"):
        Result.Ok(_) ->
            println("File deleted")
        Result.Err() ->
            println("Failed to delete file")

    return Result.Ok(0)
```

**Important:** Only works on files, not directories. Use `rmdir()` for directories.

### rename

Rename or move a file or directory.

```sushi
fn rename(string old_path, string new_path) -> Result<i32>
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

fn main() i32:
    match rename("/tmp/old.txt", "/tmp/new.txt"):
        Result.Ok(_) ->
            println("File renamed")
        Result.Err() ->
            println("Failed to rename")

    return Result.Ok(0)
```

**Note:** Atomically replaces destination if it exists.

### mkdir

Create a new directory with specified permissions.

```sushi
fn mkdir(string path, i32 mode) -> Result<i32>
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
        Result.Err() ->
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
fn rmdir(string path) -> Result<i32>
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
        Result.Err() ->
            println("Failed to remove directory")

    return Result.Ok(0)
```

**Important:** Directory must be empty. Use `remove()` to delete files inside first.

### copy

Copy a file's contents to a new location.

```sushi
fn copy(string src, string dst) -> Result<i32>
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

fn main() i32:
    match copy("/tmp/source.txt", "/tmp/backup.txt"):
        Result.Ok(_) ->
            println("File copied")
        Result.Err() ->
            println("Failed to copy file")

    return Result.Ok(0)
```

**Note:** Overwrites destination if it exists. Uses efficient 4KB buffering internally.

### File Utility Pattern Matching Example

```sushi
use <io/files>

fn backup_and_cleanup(string path) ~:
    let string backup = "{path}.bak"

    match copy(path, backup):
        Result.Ok(_) ->
            println("Backup created")
        Result.Err() ->
            return Result.Err()

    match remove(path):
        Result.Ok(_) ->
            println("Original removed")
        Result.Err() ->
            println("Cleanup failed")
            return Result.Err()

    return Result.Ok(~)

fn main() i32:
    match backup_and_cleanup("/tmp/data.txt"):
        Result.Ok(_) ->
            println("Operation complete")
        Result.Err() ->
            println("Operation failed")

    return Result.Ok(0)
```

## Common Patterns

### Reading entire file

```sushi
use <io/files>

fn read_file(string path) string:
    let file f = open(path, FileMode.Read())??
    let string content = f.read()
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

fn write_file(string path, string data) ~:
    let file f = open(path, FileMode.Write())??
    f.write(data)
    f.close()
    return Result.Ok(~)

fn main() i32:
    write_file("output.txt", "Hello, World!")??
    println("File written")

    return Result.Ok(0)
```

### Processing CSV file

```sushi
use <io/files>

fn main() i32:
    let file f = open("data.csv", FileMode.Read())??
    let string content = f.read()
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

fn log_message(string message) ~:
    let file f = open("app.log", FileMode.Append())??
    f.write("{message}\n")
    f.close()
    return Result.Ok(~)

fn main() i32:
    log_message("Application started")??
    log_message("Processing data")??
    log_message("Application finished")??

    return Result.Ok(0)
```

### Checking file existence

```sushi
use <io/files>

fn file_exists(string path) bool:
    match open(path, FileMode.Read()):
        FileResult.Ok(f) ->
            f.close()
            return Result.Ok(true)
        FileResult.Err(_) ->
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

fn main() i32:
    # Unix-style paths work on all platforms
    let file f = open("data/input.txt", FileMode.Read())??
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

fn main() i32:
    let file f = open("large.txt", FileMode.Read())??

    # Reading line-by-line is more memory-efficient than .read()
    let bool done = false
    while (not done):
        let string line = f.read_line()

        if (line.is_empty()):
            done := true
        else:
            # Process line
            println(line)

    f.close()

    return Result.Ok(0)
```

### Memory Usage

- `.read()` loads entire file into memory
- `.read_line()` reads one line at a time (more memory-efficient)

Choose based on file size and use case.

## Security Considerations

### Path Traversal

Always validate file paths from user input:

```sushi
use <io/files>

fn is_safe_path(string path) bool:
    # Reject paths with ..
    if (path.contains("..")):
        return Result.Ok(false)

    # Reject absolute paths if needed
    if (path.starts_with("/")):
        return Result.Ok(false)

    return Result.Ok(true)

fn main() i32:
    let string user_path = "data.txt"

    if (not is_safe_path(user_path).realise(false)):
        println("Invalid path")
        return Result.Ok(1)

    let file f = open(user_path, FileMode.Read())??
    let string content = f.read()
    f.close()

    println(content)

    return Result.Ok(0)
```

### File Permissions

Be cautious with write operations:

```sushi
use <io/files>

fn main() i32:
    # Always check if overwriting is intended
    match open("important.txt", FileMode.Write()):
        FileResult.Ok(f) ->
            # This TRUNCATES the existing file!
            f.write("New content")
            f.close()
        FileResult.Err(_) ->
            println("Failed to open file")

    return Result.Ok(0)
```

Use `FileMode.Append()` to preserve existing content.

## See Also

- [Console I/O](console.md) - Standard input/output/error operations
- [String Methods](../../standard-library.md#string-methods) - String operations for file content
- [Standard Library Reference](../../standard-library.md) - Complete stdlib reference
- [Error Handling](../../error-handling.md) - Result and Maybe types
