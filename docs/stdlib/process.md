# Process Control Module

[← Back to Standard Library](../standard-library.md)

System process control and information functions.

## Import

```sushi
use <sys/process>
```

## Overview

The process module provides functions for controlling and querying process state. It uses POSIX functions for Unix portability across macOS, Linux, and other Unix-like systems.

## Functions

### getcwd

Get the current working directory.

```sushi
fn getcwd() -> Result<string>
```

**Returns:**
- `Result.Ok(path)` containing the absolute path to the current working directory
- `Result.Err()` if the directory cannot be determined (e.g., directory was deleted)

**Example:**

```sushi
use <sys/process>

fn main() i32:
    match getcwd():
        Result.Ok(path) ->
            println("Current directory: {path}")
        Result.Err(_) ->
            println("Failed to get current directory")

    return Result.Ok(0)
```

**Error propagation:**

```sushi
use <sys/process>

fn show_cwd() i32 | ProcessError:
    let string cwd = getcwd()??
    println("Working in: {cwd}")
    return Result.Ok(0)

fn main() i32:
    return Result.Ok(show_cwd().realise(1))
```

### chdir

Change the current working directory.

```sushi
fn chdir(string path) -> Result<i32>
```

**Parameters:**
- `path` - Absolute or relative path to the new directory

**Returns:**
- `Result.Ok(0)` on success
- `Result.Ok(-1)` on failure (e.g., directory does not exist, permission denied)

**Example:**

```sushi
use <sys/process>

fn main() i32:
    match chdir("/tmp"):
        Result.Ok(code) ->
            if (code == 0):
                println("Changed to /tmp")
            else:
                println("Failed to change directory")
        Result.Err(_) ->
            println("chdir returned error")

    return Result.Ok(0)
```

**Navigating directories:**

```sushi
use <sys/process>

fn navigate() i32 | ProcessError:
    # Save current directory
    let string original = getcwd()??

    # Change to a different directory
    let i32 result = chdir("/var/log")??

    if (result == 0):
        println("Now in /var/log")
        let string current = getcwd()??
        println("Current: {current}")

        # Restore original directory
        chdir(original)??
        println("Restored to {original}")

    return Result.Ok(0)

fn main() i32:
    return Result.Ok(navigate().realise(1))
```

### exit

Terminate the current process with an exit code.

```sushi
fn exit(i32 code) -> ~
```

**Parameters:**
- `code` - Exit code to return to the parent process (0 for success, non-zero for error)

**Returns:**
- Never returns (terminates the process immediately)

**Example:**

```sushi
use <sys/process>

fn main() i32:
    let bool critical_error = true

    if (critical_error):
        println("Critical error occurred")
        exit(1)

    println("This line is never reached")
    return Result.Ok(0)
```

**Exit codes convention:**

```sushi
use <sys/process>

const i32 EXIT_SUCCESS = 0
const i32 EXIT_FAILURE = 1
const i32 EXIT_INVALID_ARGS = 2
const i32 EXIT_CONFIG_ERROR = 3

fn main() i32:
    let bool config_loaded = false

    if (not config_loaded):
        println("ERROR: Configuration file not found")
        exit(EXIT_CONFIG_ERROR)

    # Normal execution continues...
    return Result.Ok(EXIT_SUCCESS)
```

### getpid

Get the process ID of the current process.

```sushi
fn getpid() -> i32
```

**Returns:**
- The process ID (always succeeds, never negative)

**Example:**

```sushi
use <sys/process>

fn main() i32:
    let i32 pid = getpid()
    println("Process ID: {pid}")
    return Result.Ok(0)
```

**Logging with PID:**

```sushi
use <sys/process>

fn log_message(string message) ~:
    let i32 pid = getpid()
    println("[PID {pid}] {message}")
    return Result.Ok(~)

fn main() i32:
    log_message("Application starting")
    log_message("Processing data")
    log_message("Application finished")
    return Result.Ok(0)
```

### getuid

Get the user ID of the current process.

```sushi
fn getuid() -> i32
```

**Returns:**
- The real user ID of the calling process (always succeeds, never negative)

**Example:**

```sushi
use <sys/process>

fn main() i32:
    let i32 uid = getuid()
    println("User ID: {uid}")

    if (uid == 0):
        println("Running as root")
    else:
        println("Running as unprivileged user")

    return Result.Ok(0)
```

**Permission checking:**

```sushi
use <sys/process>

fn require_root() Result<i32, StdError>:
    let i32 uid = getuid()

    if (uid != 0):
        println("ERROR: This program must be run as root")
        exit(1)

    return Result.Ok(0)

fn main() i32:
    require_root().realise(1)

    println("Running with root privileges")
    # Perform privileged operations...

    return Result.Ok(0)
```

### run

Spawn an external program by argv vector (PATH-searched, **no shell**), capturing its
standard output and standard error and returning its exit code.

```sushi
fn run(string cmd, ...string args) -> Result<ProcessOutput, ProcessError>
```

`args` is variadic: pass individual arguments directly (`run("echo", "hello")`), or forward an
already-built `string[]` with a [bloom](../variadics.md#spread-forwarding-bloom) (`run("sh",
argv...)`) when the argument list is computed at runtime. A bloom moves the source array into the
call, so don't reuse it afterward.

**Parameters:**
- `cmd` - The program to run (searched on `PATH`, e.g. `"clang"`, `"echo"`)
- `args` - The argument vector passed to the program (not including `cmd` itself)

**Returns:**
- `Result.Ok(ProcessOutput)` if the program was spawned and ran to completion. A **non-zero
  exit code is still `Ok`** - it is a normal outcome the caller inspects, not an error. The
  `ProcessOutput` struct carries:
  - `exit_code` (`i32`) - the program's exit status
  - `stdout_text` (`string`) - everything the program wrote to standard output
  - `stderr_text` (`string`) - everything the program wrote to standard error
- `Result.Err(ProcessError.SpawnFailed)` if the program could not be started (e.g. command
  not found)
- `Result.Err(ProcessError.SignalReceived)` if the program was killed by a signal

Because arguments are passed as a real argv vector rather than through a shell, there is no
shell-quoting or injection surface. Field names are `stdout_text` / `stderr_text` (not
`stdout` / `stderr`, which are reserved stream keywords).

**Example:**

```sushi
use <sys/process>
use <io/stdio>

fn main() i32:
    match run("echo", "Mostly Harmless"):
        Result.Ok(out) ->
            println("exit={out.exit_code}")
            println(out.stdout_text)
        Result.Err(ProcessError.SpawnFailed) ->
            println("command not found")
        Result.Err(_) ->
            println("spawn error")

    return Result.Ok(0)
```

**Driving a toolchain (reading a child's diagnostics):**

```sushi
use <sys/process>
use <io/stdio>

fn compile_ir(string path) i32 | ProcessError:
    let string[] argv = from([path, "-o", "out"])
    let ProcessOutput out = run("clang", argv...)??   # bloom: argv is moved into the call
    if (out.exit_code != 0):
        println("{out.stderr_text}")
    return Result.Ok(out.exit_code)
```

## Error Handling

Functions integrate with Sushi's error handling system:

### Result-returning functions

`getcwd()` and `chdir()` return `Result` types:

```sushi
use <sys/process>

# 1. Error propagation (??): the enclosing function carries the error type
fn enter_tmp() string | ProcessError:
    let string dir = getcwd()??
    chdir("/tmp")??
    return Result.Ok(dir)

fn main() i32:
    # 1. Propagation, handled at the boundary with .realise()
    let string dir = enter_tmp().realise("/unknown")
    println("Was in: {dir}")

    # 2. Pattern matching
    match getcwd():
        Result.Ok(path) ->
            println("CWD: {path}")
        Result.Err(_) ->
            println("Failed to get CWD")

    # 3. .realise() for a default value
    let string safe_cwd = getcwd().realise("/unknown")
    println("Safe CWD: {safe_cwd}")

    return Result.Ok(0)
```

### Never-failing functions

`getpid()` and `getuid()` always succeed:

```sushi
use <sys/process>

fn main() i32:
    # No error handling needed
    let i32 pid = getpid()
    let i32 uid = getuid()

    println("PID: {pid}, UID: {uid}")
    return Result.Ok(0)
```

### Never-returning functions

`exit()` never returns, so code after it is unreachable:

```sushi
use <sys/process>

fn main() i32:
    if (false):
        exit(1)
        # Compiler knows this is unreachable

    return Result.Ok(0)
```

## Platform-Specific Behavior

### macOS (darwin)

Platform-specific implementation in `stdlib/src/_platform/darwin/process.py`:
- Uses standard POSIX `getcwd()`, `chdir()`, `getpid()`, `getuid()`
- PATH_MAX is typically 1024 bytes
- Follows BSD semantics

### Linux

Platform-specific implementation in `stdlib/src/_platform/linux/process.py`:
- Uses standard POSIX `getcwd()`, `chdir()`, `getpid()`, `getuid()`
- PATH_MAX is typically 4096 bytes
- Follows GNU/Linux semantics

### Windows (not supported)

Windows support is not yet implemented. The module requires POSIX compatibility.

## Common Patterns

### Directory Navigation

Run an operation in another directory and always restore the original. `with_directory` takes
the operation as a **function value** (`fn() -> i32`) and calls through it — see
[First-Class Functions](../first-class-functions.md).

```sushi
use <sys/process>

fn with_directory(string path, fn() -> i32 operation) i32 | ProcessError:
    # Save the current directory, then switch to the target
    let string original = getcwd()??
    chdir(path)??

    # Call through the operation function value, unwrapping its result
    let i32 result = operation().realise(-1)

    # Restore the original directory
    chdir(original)??

    return Result.Ok(result)

fn process_files() i32:
    println("Processing files in current directory")
    return Result.Ok(0)

fn main() i32:
    match with_directory("/tmp", process_files):
        Result.Ok(code) ->
            println("Operation completed: {code}")
        Result.Err(_) ->
            println("Operation failed")

    return Result.Ok(0)
```

### Process Information

```sushi
use <sys/process>

fn print_process_info() ~:
    let i32 pid = getpid()
    let i32 uid = getuid()
    let string cwd = getcwd().realise("/unknown")

    println("Process Information:")
    println("  PID: {pid}")
    println("  UID: {uid}")
    println("  CWD: {cwd}")

    return Result.Ok(~)

fn main() i32:
    print_process_info()
    return Result.Ok(0)
```

### Graceful Exit

```sushi
use <sys/process>

fn cleanup() ~:
    println("Cleaning up resources...")
    return Result.Ok(~)

fn graceful_exit(i32 code) ~:
    cleanup()??
    println("Exiting with code {code}")
    exit(code)
    return Result.Ok(~)  # Never reached

fn main() i32:
    let bool error = false

    if (error):
        graceful_exit(1)

    println("Normal execution")
    return Result.Ok(0)
```

## Security Considerations

### Directory Traversal

Always validate paths to prevent directory traversal attacks:

```sushi
use <sys/process>

fn safe_chdir(string path) i32 | ProcessError:
    # Validate path doesn't contain ../ components
    # Add your validation logic here

    let i32 code = chdir(path)??
    return Result.Ok(code)

fn main() i32:
    # Bad: User-controlled path without validation
    # chdir(user_input)

    # Good: Validated path
    match safe_chdir("/tmp"):
        Result.Ok(code) ->
            if (code == 0):
                println("Changed directory safely")
        Result.Err(_) ->
            println("Invalid directory")

    return Result.Ok(0)
```

### Privilege Checks

Always check privileges before performing sensitive operations:

```sushi
use <sys/process>

fn require_non_root() Result<i32, StdError>:
    let i32 uid = getuid()

    if (uid == 0):
        println("ERROR: Do not run this as root")
        exit(1)

    return Result.Ok(0)

fn main() i32:
    require_non_root().realise(1)
    println("Running as unprivileged user")
    return Result.Ok(0)
```

### Exit Code Convention

Use standard exit codes for better shell integration:

```sushi
use <sys/process>

const i32 EXIT_SUCCESS = 0
const i32 EXIT_FAILURE = 1
const i32 EXIT_USAGE = 2       # Command line usage error
const i32 EXIT_DATAERR = 65    # Data format error
const i32 EXIT_NOINPUT = 66    # Cannot open input
const i32 EXIT_UNAVAILABLE = 69 # Service unavailable
const i32 EXIT_SOFTWARE = 70    # Internal software error
const i32 EXIT_IOERR = 74       # I/O error
const i32 EXIT_CONFIG = 78      # Configuration error

fn main() i32:
    # Use appropriate exit codes
    exit(EXIT_CONFIG)
    return Result.Ok(0)  # Never reached
```

## Example: Simple File Processor

```sushi
use <sys/process>
use <io/stdio>

fn process_directory(string dir_path) i32 | ProcessError:
    println("Processing directory: {dir_path}")

    # Save current location
    let string original_dir = getcwd()??

    # Change to target directory
    let i32 change_result = chdir(dir_path)??
    if (change_result != 0):
        println("ERROR: Cannot access directory: {dir_path}")
        return Result.Ok(1)

    # Verify we're in the right place
    let string current = getcwd()??
    println("Working in: {current}")

    # Process files here...
    println("Processing files...")

    # Return to original directory
    let i32 restore = chdir(original_dir)??
    if (restore != 0):
        println("WARNING: Could not restore directory")

    return Result.Ok(0)

fn main() i32:
    let i32 pid = getpid()
    let i32 uid = getuid()

    println("File Processor [PID: {pid}, UID: {uid}]")

    # Don't run as root
    if (uid == 0):
        println("ERROR: Do not run as root")
        exit(1)

    # Process each directory (.realise maps a ProcessError to the failure code 1)
    let i32 result1 = process_directory("/tmp").realise(1)
    let i32 result2 = process_directory("/var/log").realise(1)

    if (result1 != 0 or result2 != 0):
        println("Some directories failed to process")
        exit(1)

    println("All directories processed successfully")
    return Result.Ok(0)
```

## See Also

- [Standard Library Reference](../standard-library.md) - Complete stdlib reference
- [Environment Variables](env.md) - Working with environment variables
- [Error Handling](../error-handling.md) - Result and Maybe types
- [I/O Operations](io/files.md) - File system operations
