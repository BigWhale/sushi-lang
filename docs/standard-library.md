# Sushi Standard Library

Complete reference for Sushi's standard library modules and types.

## Table of Contents

### Core Types
- [Result<T>](stdlib/result.md) - Error handling for fallible operations
- [Maybe<T>](stdlib/maybe.md) - Optional values

### Collections
- [List<T>](stdlib/collections/list.md) - Dynamic growable array
- [HashMap<K, V>](stdlib/collections/hashmap.md) - Hash table with open addressing
- [Arrays](stdlib/collections/arrays.md) - Fixed and dynamic array methods
- [Strings](stdlib/collections/strings.md) - 33 string manipulation methods

### I/O Operations
- [Console I/O](stdlib/io/console.md) - println, print, stdin/stdout/stderr
- [File I/O](stdlib/io/files.md) - File operations with error handling

### System Modules
- [Math](stdlib/math.md) - Mathematical operations (abs, min, max, sqrt, pow, trig)
- [Time](stdlib/time.md) - High-precision sleep functions
- [Environment](stdlib/env.md) - Environment variables and system information
- [Process Control](stdlib/process.md) - Process management (getcwd, chdir, exit, getpid, getuid)
- [Platform](stdlib/platform.md) - Platform detection and OS-specific utilities

## Quick Reference

### Importing Modules

```sushi
use <collections/strings>  # String methods
use <collections>          # List<T>, HashMap<K, V>
use <io/stdio>             # Console I/O
use <io/files>             # File operations
use <math>                 # Math functions
use <time>                 # Sleep functions
use <sys/env>              # Environment variables
use <sys/process>          # Process control
```

### Common Patterns

#### Error Handling

```sushi
# Using ?? operator for propagation
fn read_config() string:
    let file f = open("config.txt", FileMode.Read())??
    let string content = f.read()
    f.close()
    return Result.Ok(content)

# Using pattern matching
match parse_number("42"):
    Result.Ok(n) -> println("Got: {n}")
    Result.Err() -> println("Parse failed")

# Using .realise() for defaults
let i32 port = config.get("port").realise(8080)
```

#### Optional Values

```sushi
# Safe array access
match arr.get(0):
    Maybe.Some(first) -> println("First: {first}")
    Maybe.None() -> println("Array empty")

# String searching
let string text = "hello world"
let Maybe<i32> pos = text.find("world")
```

#### Collections

```sushi
# List<T>
let List<i32> numbers = List.new()
numbers.push(1)
numbers.push(2)
numbers.push(3)

# HashMap<K, V>
let HashMap<string, i32> ages = HashMap.new()
ages.insert("Alice", 30)
match ages.get("Alice"):
    Maybe.Some(age) -> println("Age: {age}")
    Maybe.None() -> println("Not found")

# Arrays
let i32[] arr = from([1, 2, 3])
arr.push(4)
foreach(n in arr.iter()):
    println(n)
```

#### String Processing

```sushi
let string text = "  Hello World  "
let string clean = text.trim().lower()  # "hello world"

let string[] parts = "a,b,c".split(",")
let string joined = ",".join(parts)  # "a,b,c"

let string path = "/home/user/file.txt"
let string filename = path.strip_prefix("/home/user/")  # "file.txt"
```

#### File I/O

```sushi
# Reading files
match open("data.txt", FileMode.Read()):
    FileResult.Ok(f) ->
        let string content = f.read()
        f.close()
        println(content)
    FileResult.Err(FileError.NotFound()) ->
        println("File not found")
    FileResult.Err(_) ->
        println("Other error")

# Writing files
match open("output.txt", FileMode.Write()):
    FileResult.Ok(f) ->
        f.write("Hello, file!")
        f.close()
    FileResult.Err(_) ->
        println("Failed to write")
```

## Module Overview

### Collections (`use <collections>`)

**List<T>** - Generic dynamic array with:
- Construction: `new()`, `with_capacity()`
- Access: `get()`, `len()`, `is_empty()`
- Modification: `push()`, `pop()`, `insert()`, `remove()`, `clear()`
- Iteration: `iter()` for foreach loops
- Memory: `free()`, `destroy()`

**HashMap<K, V>** - Generic hash table with:
- Construction: `new()`
- Operations: `insert()`, `get()`, `remove()`, `contains_key()`
- Iteration: `keys()`, `values()`
- Automatic resizing at 0.75 load factor
- Memory: `free()`, `destroy()`

**Arrays** - Built-in array support:
- Fixed arrays: `i32[10]`
- Dynamic arrays: `i32[]` with `from([...])`
- Methods: `len()`, `get()`, `push()`, `pop()`, `iter()`, `clone()`
- Safe access with `get()` returns `Maybe<T>`
- Unsafe direct indexing: `arr[i]`

**Strings** - 33 methods covering:
- Inspection, slicing, transformation, padding, stripping
- Splitting/joining, case conversion, parsing
- UTF-8 aware where needed

### I/O (`use <io/stdio>`, `use <io/files>`)

**Console I/O:**
- `println()`, `print()` - Output with/without newline
- `stdin.read_line()` - Read user input
- `stdout`, `stderr` - Direct stream access

**File I/O:**
- `open()` - Open files with Read/Write/Append modes
- File methods: `read()`, `read_line()`, `write()`, `close()`
- Error handling with `FileResult` and `FileError` enums

### Math (`use <math>`)

Functions for all numeric types:
- Absolute value: `abs_i32()`, `abs_f64()`, etc.
- Min/Max: `min_i32()`, `max_f64()`, etc.
- Floating-point: `sqrt()`, `pow()`, `floor()`, `ceil()`, `round()`
- Trigonometry: `sin()`, `cos()`, `tan()`, `asin()`, `acos()`, `atan()`
- Exponential: `exp()`, `ln()`, `log10()`, `log2()`
- Constants: `PI`, `E`, `SQRT_2`, `LN_2`, `LN_10`

### Time (`use <time>`)

High-precision sleep functions:
- `sleep(i64)` - Sleep for N seconds
- `msleep(i64)` - Sleep for N milliseconds
- `usleep(i64)` - Sleep for N microseconds
- `nanosleep(i64, i64)` - Nanosecond precision

### Environment (`use <sys/env>`)

Environment and system:
- `getenv()` - Get environment variable
- `setenv()` - Set environment variable
- `unsetenv()` - Remove environment variable
- Process control: `exit()`, `getcwd()`, `chdir()`

## Design Principles

1. **Explicit error handling** - All fallible operations return `Result<T>` or `Maybe<T>`
2. **Memory safety** - RAII cleanup, no manual memory management
3. **Zero-cost abstractions** - Generics compile to concrete types
4. **UTF-8 by default** - Strings are UTF-8, methods are aware where needed
5. **Immutability** - String methods return new strings, arrays use RAII
6. **Type safety** - No null, no undefined behavior, exhaustive pattern matching

## Performance Notes

- **List<T>**: Amortized O(1) push, O(n) insert/remove
- **HashMap<K, V>**: O(1) average insert/get/remove, O(n) worst case
- **String methods**: All allocate new strings, O(n) for most operations
- **Arrays**: Direct memory access, bounds checked at runtime
- **Generics**: Monomorphized at compile-time (no runtime overhead)

## See Also

- [Language Reference](language-reference.md) - Core language features
- [Memory Management](memory-management.md) - RAII, borrowing, ownership
- [Generics](generics.md) - Generic types and functions
- [Getting Started](getting-started.md) - Installation and first program
