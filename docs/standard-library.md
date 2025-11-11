# Standard Library Reference

[← Back to Documentation](README.md)

Complete reference for Sushi's built-in types and functions.

## Table of Contents

- [Result<T>](#resultt)
- [Maybe<T>](#maybet)
- [List<T>](#listt)
- [HashMap<K, V>](#hashmapk-v)
- [Array Methods](#array-methods)
- [String Methods](#string-methods)
- [I/O Functions](#io-functions)
- [File Operations](#file-operations)
- [Math Operations](#math-operations)
- [Environment Variables](#environment-variables)
- [Time Functions](#time-functions)

## Result<T>

All functions implicitly return `Result<T>` for explicit error handling.

### Variants

- `Result.Ok(value)` - Success with value
- `Result.Err()` - Failure with no value

### Methods

#### `.realise(default)`

Extract value or return default on error.

```sushi
fn get_value() i32:
    return Result.Ok(42)

fn main() i32:
    let i32 x = get_value().realise(0)  # x = 42

    let i32 y = might_fail().realise(-1)  # y = -1 if error

    return Result.Ok(0)
```

**Parameters:**
- `default` - Value to return if `Result.Err()`

**Returns:** `T` (the unwrapped value or default)

### Usage in Conditionals

```sushi
let Result<i32> result = divide(10, 2)

if (result):
    # Success - result is Ok
    let i32 value = result.realise(0)
else:
    # Failure - result is Err
    println("Operation failed")
```

## Maybe<T>

Type-safe optional values.

### Variants

- `Maybe.Some(value)` - Contains a value
- `Maybe.None()` - No value

### Methods

#### `.is_some() -> bool`

Check if value is present.

```sushi
let Maybe<i32> m = Maybe.Some(42)
if (m.is_some()):
    println("Has value")
```

#### `.is_none() -> bool`

Check if value is absent.

```sushi
let Maybe<i32> m = Maybe.None()
if (m.is_none()):
    println("No value")
```

#### `.realise(default) -> T`

Extract value or return default.

```sushi
let Maybe<i32> m = Maybe.Some(42)
let i32 x = m.realise(0)  # x = 42

let Maybe<i32> empty = Maybe.None()
let i32 y = empty.realise(0)  # y = 0
```

#### `.expect(message) -> T`

Extract value or panic with message.

```sushi
let Maybe<i32> m = Maybe.Some(42)
let i32 x = m.expect("Expected a value")  # x = 42

let Maybe<i32> empty = Maybe.None()
# let i32 y = empty.expect("Value required")  # Runtime panic!
```

### Pattern Matching

```sushi
match result:
    Maybe.Some(value) ->
        println("Got: {value}")
    Maybe.None() ->
        println("No value")
```

## List<T>

Generic growable array with automatic memory management.

**Import:** `use <collections>`

### Construction

#### `List.new() -> List<T>`

Create empty list (zero capacity, lazy allocation).

```sushi
let List<i32> nums = List.new()
```

#### `List.with_capacity(i32 n) -> List<T>`

Create list with pre-allocated capacity.

```sushi
let List<string> names = List.with_capacity(100)
```

### Query Methods

#### `.len() -> i32`

Get number of elements.

```sushi
println("Size: {list.len()}")
```

#### `.capacity() -> i32`

Get allocated capacity.

```sushi
println("Capacity: {list.capacity()}")
```

#### `.is_empty() -> bool`

Check if list is empty.

```sushi
if (list.is_empty()):
    println("Empty list")
```

### Access Methods

#### `.get(i32 index) -> Maybe<T>`

Get element at index (bounds-checked).

```sushi
match list.get(0):
    Maybe.Some(value) ->
        println("First: {value}")
    Maybe.None() ->
        println("Index out of bounds")
```

#### `.pop() -> Maybe<T>`

Remove and return last element.

```sushi
match list.pop():
    Maybe.Some(value) ->
        println("Popped: {value}")
    Maybe.None() ->
        println("Empty list")
```

### Modification Methods

#### `.push(T element) -> ~`

Append element (auto-grows capacity).

```sushi
list.push(42)
list.push(100)
```

#### `.insert(i32 index, T element) -> Result<~>`

Insert element at index (shifts elements right).

```sushi
# Insert at beginning
list.insert(0, 1)

# Insert in middle
list.insert(5, 42)

# Insert at end (equivalent to push)
list.insert(list.len(), 99)
```

**Bounds:** `0 <= index <= len`

#### `.remove(i32 index) -> Maybe<T>`

Remove and return element at index (shifts elements left).

```sushi
match list.remove(2):
    Maybe.Some(value) ->
        println("Removed: {value}")
    Maybe.None() ->
        println("Index out of bounds")
```

**Bounds:** `0 <= index < len`

#### `.clear() -> ~`

Remove all elements (keeps capacity).

```sushi
list.clear()
println("Length: {list.len()}")  # 0
println("Capacity: {list.capacity()}")  # Unchanged
```

### Capacity Management

#### `.reserve(i32 n) -> ~`

Ensure capacity is at least `n`.

```sushi
list.reserve(100)  # Ensure space for 100 elements
```

#### `.shrink_to_fit() -> ~`

Reduce capacity to match length.

```sushi
list.shrink_to_fit()  # Capacity = len
```

### Iteration

#### `.iter() -> Iterator<T>`

Create iterator for foreach loops.

```sushi
foreach(value in list.iter()):
    println(value)
```

### Memory Management

#### `.free() -> ~`

Free memory and reset to empty (still usable).

```sushi
list.free()
list.push(1)  # OK: Can still use
```

#### `.destroy() -> ~`

Free memory and invalidate (unusable).

```sushi
list.destroy()
# list.len()  # ERROR CE2406: use of destroyed variable
```

### Debugging

#### `.debug() -> ~`

Print internal state (length, capacity, elements).

```sushi
list.debug()  # Output: List<i32> { len: 3, capacity: 4, [1, 2, 3] }
```

### Performance

- `push()`: Amortized O(1)
- `pop()`: O(1)
- `get()`: O(1)
- `insert()`: O(n)
- `remove()`: O(n)
- `clear()`: O(n)

## HashMap<K, V>

Generic hash table with open addressing (linear probing).

**Import:** `use <collections>`

### Construction

#### `HashMap.new() -> HashMap<K, V>`

Create empty hash map (initial capacity 16).

```sushi
let HashMap<string, i32> ages = HashMap.new()
```

### Methods

#### `.insert(K key, V value) -> ~`

Insert or update key-value pair.

```sushi
ages.insert("Arthur", 42)
ages.insert("Ford", 200)
```

**Note:** Automatically resizes at 0.75 load factor.

#### `.get(K key) -> Maybe<V>`

Get value for key.

```sushi
match ages.get("Arthur"):
    Maybe.Some(age) ->
        println("Arthur is {age}")
    Maybe.None() ->
        println("Not found")
```

#### `.remove(K key) -> Maybe<V>`

Remove and return value for key.

```sushi
match ages.remove("Arthur"):
    Maybe.Some(age) ->
        println("Removed age {age}")
    Maybe.None() ->
        println("Key not found")
```

#### `.contains(K key) -> bool`

Check if key exists.

```sushi
if (ages.contains("Arthur")):
    println("Arthur exists")
```

#### `.len() -> i32`

Get number of entries.

```sushi
println("Entries: {ages.len()}")
```

#### `.free() -> ~`

Clear all entries and reset to capacity 16 (still usable).

```sushi
ages.free()
ages.insert("Zaphod", 150)  # OK
```

#### `.rehash(i32 new_capacity) -> ~`

Manually rehash with new capacity (must be power of 2).

```sushi
ages.rehash(64)  # Resize to capacity 64
```

#### `.debug() -> ~`

Print internal state.

```sushi
ages.debug()
```

### Key Requirements

Keys must implement `.hash() -> u64` method. Supported types:

- Primitives: `i8`, `i16`, `i32`, `i64`, `u8`, `u16`, `u32`, `u64`, `f32`, `f64`, `bool`
- `string`
- Structs (with hashable fields)
- Enums (with hashable variant data)

**Not supported:** Nested arrays (cannot be hashed)

## Array Methods

### Common (Fixed and Dynamic)

#### `.len() -> i32`

```sushi
let i32[5] arr = [1, 2, 3, 4, 5]
println(arr.len())  # 5
```

#### `.get(i32 index) -> T`

Bounds-checked access.

```sushi
let i32 value = arr.get(2)  # Runtime bounds check
```

#### `.iter() -> Iterator<T>`

```sushi
foreach(n in arr.iter()):
    println(n)
```

#### `.hash() -> u64`

```sushi
let u64 h = arr.hash()
```

#### `.fill(T value) -> ~`

Fill all elements with value (in-place).

```sushi
arr.fill(0)  # All elements become 0
```

#### `.reverse() -> ~`

Reverse array elements (in-place).

```sushi
arr.reverse()  # [5, 4, 3, 2, 1]
```

### Dynamic Array Only

#### `.push(T element) -> ~`

```sushi
arr.push(42)
```

#### `.pop() -> T`

```sushi
let i32 last = arr.pop()
```

#### `.capacity() -> i32`

```sushi
println(arr.capacity())
```

#### `.clone() -> T[]`

Deep copy.

```sushi
let i32[] copy = arr.clone()
```

#### `.free() -> ~`

Clear and reset (still usable).

```sushi
arr.free()
arr.push(1)  # OK
```

#### `.destroy() -> ~`

Free and invalidate.

```sushi
arr.destroy()
# arr.len()  # ERROR CE2406
```

### Byte Array Only (u8[])

#### `.to_string() -> string`

Zero-cost UTF-8 conversion.

```sushi
let u8[] bytes = from([72 as u8, 105 as u8])
let string text = bytes.to_string()  # "Hi"
```

## String Methods

#### `.len() -> i32`

Character count (UTF-8 aware).

```sushi
let string s = "Hello 🌍"
println(s.len())  # 7 characters
```

#### `.size() -> i32`

Byte count.

```sushi
println(s.size())  # 10 bytes
```

#### `.is_empty() -> bool`

```sushi
if (s.is_empty()):
    println("Empty string")
```

#### `.find(string needle) -> Maybe<i32>`

Find substring position.

```sushi
match text.find("world"):
    Maybe.Some(pos) ->
        println("Found at {pos}")
    Maybe.None() ->
        println("Not found")
```

#### `.split(string delimiter) -> string[]`

Split into array.

```sushi
let string[] parts = "a,b,c".split(",")
# parts = ["a", "b", "c"]
```

#### `.trim() -> string`

Remove leading/trailing whitespace.

```sushi
let string clean = "  hello  ".trim()  # "hello"
```

#### `.to_upper() -> string`

Convert to uppercase (ASCII only).

```sushi
let string loud = "hello".to_upper()  # "HELLO"
```

#### `.to_lower() -> string`

Convert to lowercase (ASCII only).

```sushi
let string quiet = "HELLO".to_lower()  # "hello"
```

## I/O Functions

### Console Output

#### `println(string message) -> ~`

Print with newline.

```sushi
println("Hello, World!")
```

#### `print(string message) -> ~`

Print without newline.

```sushi
print("Loading")
print("...")
```

### Standard Streams

#### `stdin.read_line() -> string`

Read line from stdin.

```sushi
println("Enter name:")
let string name = stdin.read_line()
```

#### `stdin.read_bytes(i32 n) -> u8[]`

Read exactly n bytes.

```sushi
let u8[] data = stdin.read_bytes(100)
```

#### `stdout.write_bytes(u8[] data) -> ~`

Write bytes to stdout.

```sushi
stdout.write_bytes(data)
```

#### `stderr.write_bytes(u8[] data) -> ~`

Write bytes to stderr.

```sushi
stderr.write_bytes(data)
```

## File Operations

### Opening Files

#### `open(string path, FileMode mode) -> FileResult`

```sushi
match open("data.txt", FileMode.Read()):
    FileResult.Ok(f) ->
        # Use file
        f.close()
    FileResult.Err(e) ->
        # Handle error
        println("Failed to open file")
```

**File modes:**
- `FileMode.Read()` - Read only
- `FileMode.Write()` - Write only (create/truncate)
- `FileMode.Append()` - Append only

### File Methods

#### `.read() -> string`

Read entire file as string.

```sushi
let string content = file.read()
```

#### `.read_line() -> string`

Read single line.

```sushi
let string line = file.read_line()
```

#### `.write(string data) -> ~`

Write string to file.

```sushi
file.write("Hello, file!")
```

#### `.close() -> ~`

Close file.

```sushi
file.close()
```

### Error Handling

```sushi
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
```

## Math Operations

Mathematical functions for all numeric types.

**Import:** `use <math>`

**Documentation:** See [Math Module](stdlib/math.md) for complete reference

### Quick Reference

**Absolute Value:**
```sushi
use <math>

let i32 x = abs_i32(-42)  # 42
let f64 y = abs_f64(-3.14)  # 3.14
```

Available for: `i8`, `i16`, `i32`, `i64`, `f32`, `f64`

**Min/Max:**
```sushi
use <math>

let i32 smaller = min_i32(10, 20)  # 10
let i32 larger = max_i32(10, 20)  # 20
```

Available for all integer and floating-point types.

**Floating-Point Operations:**
```sushi
use <math>

let f64 root = sqrt_f64(16.0)  # 4.0
let f64 power = pow_f64(2.0, 3.0)  # 8.0
let f64 floored = floor_f64(3.7)  # 3.0
let f64 ceiled = ceil_f64(3.2)  # 4.0
let f64 rounded = round_f64(3.5)  # 4.0
let f64 truncated = trunc_f64(3.9)  # 3.0
```

Available for: `f32`, `f64`

## Environment Variables

Read and modify system environment variables.

**Import:** `use <env>`

**Documentation:** See [Environment Module](stdlib/env.md) for complete reference

### getenv

Get environment variable value.

```sushi
use <env>

fn main() i32:
    match getenv("PATH"):
        Maybe.Some(path) ->
            println("PATH: {path}")
        Maybe.None() ->
            println("PATH not set")

    return Result.Ok(0)
```

**Returns:** `Maybe<string>` - Value if exists, `Maybe.None()` otherwise

### setenv

Set environment variable value.

```sushi
use <env>

fn main() i32:
    # Set variable (overwrite if exists)
    setenv("MY_VAR", "my_value", 1)??

    # Set only if not already defined
    setenv("MY_VAR", "new_value", 0)??

    return Result.Ok(0)
```

**Parameters:**
- `key` - Variable name
- `value` - Variable value
- `overwrite` - 1 to replace existing, 0 to preserve existing

**Returns:** `Result<i32>` - Success or error

## Time Functions

High-precision sleep functions.

**Import:** `use <time>`

### sleep

Sleep for N seconds.

```sushi
use <time>

fn main() i32:
    println("Waiting 1 second...")
    let i32 result = sleep(1 as i64)??
    println("Done!")

    return Result.Ok(0)
```

**Parameters:** `i64 seconds`

**Returns:** `Result<i32>` - 0 on success, remaining microseconds if interrupted

### msleep

Sleep for N milliseconds.

```sushi
use <time>

fn main() i32:
    println("Waiting 500ms...")
    let i32 result = msleep(500 as i64)??
    println("Done!")

    return Result.Ok(0)
```

**Parameters:** `i64 milliseconds`

**Returns:** `Result<i32>` - 0 on success, remaining microseconds if interrupted

### usleep

Sleep for N microseconds.

```sushi
use <time>

fn main() i32:
    let i32 result = usleep(1000 as i64)??  # 1ms
    return Result.Ok(0)
```

**Parameters:** `i64 microseconds`

**Returns:** `Result<i32>` - 0 on success, remaining microseconds if interrupted

### nanosleep

Sleep with nanosecond precision.

```sushi
use <time>

fn main() i32:
    # Sleep for 1.5 seconds
    let i32 result = nanosleep(1 as i64, 500000000 as i64)??
    return Result.Ok(0)
```

**Parameters:**
- `i64 seconds`
- `i64 nanoseconds`

**Returns:** `Result<i32>` - 0 on success, remaining microseconds if interrupted

**Note:** Actual precision limited by OS scheduler (typically ~1ms minimum on macOS).

---

**See also:**
- [Math Module](stdlib/math.md) - Complete math function reference
- [Environment Module](stdlib/env.md) - Environment variable details
- [Platform System](stdlib/platform.md) - Platform-specific implementations
- [Error Handling](error-handling.md) - Deep dive into Result and Maybe
- [Language Reference](language-reference.md) - Complete syntax reference
- [Examples](examples/) - Hands-on examples
