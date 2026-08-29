# Time Module

[← Back to Standard Library](../standard-library.md)

High-precision sleep functions using POSIX `nanosleep()`.

## Import

```sushi
use <time>
```

## Overview

The time module provides sleep functions with various granularities and two clock reads. The sleep functions use POSIX `nanosleep()` internally; the clocks use POSIX `clock_gettime()`.

**Available functions:**
- `sleep()` - Sleep for N seconds
- `msleep()` - Sleep for N milliseconds
- `usleep()` - Sleep for N microseconds
- `nanosleep()` - Sleep with nanosecond precision
- `now()` - Read the unix clock, in seconds
- `monotonic_ns()` - Read the monotonic clock, in nanoseconds

The sleep functions return `Result@(i32)` with 0 on success, or remaining microseconds if interrupted by a signal. The clock functions return `Result@(i64)`.

## Functions

### `sleep(i64 seconds) -> Result@(i32)`

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

**Returns:** `Result@(i32)`
- `0` on success
- Remaining microseconds if interrupted by signal

### `msleep(i64 milliseconds) -> Result@(i32)`

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

**Returns:** `Result@(i32)`
- `0` on success
- Remaining microseconds if interrupted by signal

### `usleep(i64 microseconds) -> Result@(i32)`

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

**Returns:** `Result@(i32)`
- `0` on success
- Remaining microseconds if interrupted by signal

### `nanosleep(i64 seconds, i64 nanoseconds) -> Result@(i32)`

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

**Returns:** `Result@(i32)`
- `0` on success
- Remaining microseconds if interrupted by signal

### `now() -> Result@(i64)`

Read the wall clock as unix time: whole seconds since 1970-01-01 00:00:00 UTC.

```sushi
use <time>

fn main() i32:
    let i64 t = now().realise(0)
    println("unix time: {t}")
    return Result.Ok(0)
```

**Notes:**
- The wall clock can jump (NTP adjustment, manual change). Do not measure durations with it; use `monotonic_ns()`.
- The value is UTC. Civil date conversion is a separate concern.

### `monotonic_ns() -> Result@(i64)`

Read the monotonic clock, in nanoseconds. The clock never goes backward and is independent of the wall clock. Only the difference between two reads has meaning; the zero point is unspecified (boot time on most systems).

```sushi
use <time>

fn main() i32:
    let i64 start = monotonic_ns().realise(0)
    msleep(50 as i64).realise(0)
    let i64 elapsed_ms = (monotonic_ns().realise(0) - start) / 1_000_000
    println("slept for about {elapsed_ms} ms")
    return Result.Ok(0)
```

**Notes:**
- An i64 nanosecond count covers about 292 years; overflow is not a practical concern.
- The resolution is platform dependent; expect at least microsecond granularity.

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
    Result.Err(_) ->
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
fn retry_operation() Result@(i32, StdError):
    foreach(attempt in 0..5):
        match try_operation():
            Result.Ok(value) ->
                return Result.Ok(value)
            Result.Err(_) ->
                println("Attempt {attempt} failed, retrying...")
                msleep(1000 as i64)??  # 1 second backoff
    return Result.Err(StdError.Error)
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
