# Time Module

[← Back to Standard Library](../standard-library.md)

High-precision sleep functions using POSIX `nanosleep()`.

## Import

```sushi
use <time>
```

## Overview

The time module provides sleep functions with various granularities. All functions use POSIX `nanosleep()` internally for precise timing across Unix-like platforms.

**Available functions:**
- `sleep()` - Sleep for N seconds
- `msleep()` - Sleep for N milliseconds
- `usleep()` - Sleep for N microseconds
- `nanosleep()` - Sleep with nanosecond precision

All functions return `Result<i32>` with 0 on success, or remaining microseconds if interrupted by a signal.

## Functions

### `sleep(i64 seconds) -> Result<i32>`

Sleep for N seconds.

```sushi
use <time>

fn main() i32:
    println("Waiting 1 second...")
    let i32 result = sleep(1 as i64)??
    println("Done!")

    return Result.Ok(0)
```

**Parameters:**
- `seconds` - Number of seconds to sleep

**Returns:** `Result<i32>`
- `0` on success
- Remaining microseconds if interrupted by signal

### `msleep(i64 milliseconds) -> Result<i32>`

Sleep for N milliseconds.

```sushi
use <time>

fn main() i32:
    println("Waiting 500ms...")
    let i32 result = msleep(500 as i64)??
    println("Done!")

    return Result.Ok(0)
```

**Parameters:**
- `milliseconds` - Number of milliseconds to sleep

**Returns:** `Result<i32>`
- `0` on success
- Remaining microseconds if interrupted by signal

### `usleep(i64 microseconds) -> Result<i32>`

Sleep for N microseconds.

```sushi
use <time>

fn main() i32:
    println("Waiting 1000μs...")
    let i32 result = usleep(1000 as i64)??
    println("Done!")

    return Result.Ok(0)
```

**Parameters:**
- `microseconds` - Number of microseconds to sleep

**Returns:** `Result<i32>`
- `0` on success
- Remaining microseconds if interrupted by signal

### `nanosleep(i64 seconds, i64 nanoseconds) -> Result<i32>`

Sleep with nanosecond precision.

```sushi
use <time>

fn main() i32:
    # Sleep for 1.5 seconds
    let i32 result = nanosleep(1 as i64, 500000000 as i64)??
    println("Done!")

    return Result.Ok(0)
```

**Parameters:**
- `seconds` - Number of seconds to sleep
- `nanoseconds` - Additional nanoseconds (0-999,999,999)

**Returns:** `Result<i32>`
- `0` on success
- Remaining microseconds if interrupted by signal

## Platform Notes

### Precision

The actual sleep precision is limited by the OS scheduler:
- **macOS:** Typically ~1ms minimum precision
- **Linux:** Typically ~1ms minimum precision (depends on kernel configuration)
- Requesting shorter sleep times may result in longer actual sleep

### Signal Interruption

All sleep functions can be interrupted by signals (e.g., SIGINT from Ctrl+C). When interrupted:
- The function returns early
- Return value indicates remaining sleep time in microseconds
- Use pattern matching or `??` operator to handle interruption

```sushi
match msleep(1000 as i64):
    Result.Ok(remaining) ->
        if (remaining == 0):
            println("Completed full sleep")
        else:
            println("Interrupted with {remaining}μs remaining")
    Result.Err() ->
        println("Sleep failed")
```

## Implementation

Uses POSIX `nanosleep()` system call:
- Portable across Unix-like systems (macOS, Linux, BSD)
- More precise than `sleep()` or `usleep()` from libc
- Handles signal interruption correctly
- 48-bit precision (sufficient for most use cases)

## Best Practices

- Use `sleep()` for coarse-grained delays (seconds)
- Use `msleep()` for UI delays and polling intervals
- Use `usleep()` for fine-grained timing
- Use `nanosleep()` when you need explicit control
- Always handle Result with `??` or pattern matching
- Be aware of scheduler limitations for sub-millisecond sleep
- Avoid busy-waiting loops - use sleep functions instead

## Common Use Cases

**Rate limiting:**
```sushi
foreach(i in 0..100):
    process_item(i)
    msleep(10 as i64)??  # 10ms delay between items
```

**Retry with backoff:**
```sushi
fn retry_operation() Result<i32>:
    foreach(attempt in 0..5):
        match try_operation():
            Result.Ok(value) ->
                return Result.Ok(value)
            Result.Err() ->
                println("Attempt {attempt} failed, retrying...")
                msleep(1000 as i64)??  # 1 second backoff
    return Result.Err()
```

**Animation timing:**
```sushi
foreach(frame in 0..60):
    render_frame(frame)
    msleep(16 as i64)??  # ~60 FPS
```

## See Also

- [Random Module](random.md) - For random delays
- [Environment Module](env.md) - For environment-based configuration
- [I/O Console](io/console.md) - For progress indicators
