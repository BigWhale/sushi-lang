# Array Methods

[← Back to Standard Library](../../standard-library.md)

Built-in methods for fixed-size and dynamic arrays.

## Import

Arrays are built-in types and require no import. For dynamic array construction from literals, arrays are available by default.

```sushi
let i32[5] fixed = [1, 2, 3, 4, 5]  # Fixed-size array
let i32[] dynamic = from([1, 2, 3])  # Dynamic array
```

## Overview

Sushi provides two array types:
- **Fixed arrays** (`T[N]`): Stack-allocated, compile-time size
- **Dynamic arrays** (`T[]`): Heap-allocated, runtime size

Both types share common methods, while dynamic arrays have additional memory management methods.

## Common Methods (Fixed and Dynamic)

### `.len() -> i32`

Get number of elements.

```sushi
let i32[5] arr = [1, 2, 3, 4, 5]
println(arr.len())  # 5
```

### `.get(i32 index) -> Maybe<T>`

Bounds-checked access (returns `Maybe<T>`).

```sushi
match arr.get(2):
    Maybe.Some(value) ->
        println("Value: {value}")
    Maybe.None() ->
        println("Index out of bounds")

# Or use error propagation
let i32 value = arr.get(2)??
```

**Note:** Direct indexing `arr[index]` is also available but throws RE2020 runtime error on out-of-bounds.

### `.iter() -> Iterator<T>`

Create iterator for foreach loops.

```sushi
foreach(n in arr.iter()):
    println(n)
```

### `.hash() -> u64`

Compute hash of array contents.

```sushi
let u64 h = arr.hash()
```

**Limitation:** Nested arrays cannot be hashed.

### `.fill(T value) -> ~`

Fill all elements with value (in-place).

```sushi
arr.fill(0)  # All elements become 0
```

### `.reverse() -> ~`

Reverse array elements (in-place).

```sushi
let i32[5] arr = [1, 2, 3, 4, 5]
arr.reverse()  # [5, 4, 3, 2, 1]
```

## Dynamic Array Only

### `.push(T element) -> ~`

Append element to end (grows array).

```sushi
let i32[] arr = from([1, 2, 3])
arr.push(42)
# arr is now [1, 2, 3, 42]
```

### `.pop() -> Maybe<T>`

Remove and return last element.

```sushi
match arr.pop():
    Maybe.Some(last) ->
        println("Popped: {last}")
    Maybe.None() ->
        println("Array is empty")
```

### `.capacity() -> i32`

Get allocated capacity.

```sushi
println("Capacity: {arr.capacity()}")
```

### `.clone() -> T[]`

Deep copy of array.

```sushi
let i32[] copy = arr.clone()
```

### `.free() -> ~`

Clear and reset to zero capacity (still usable).

```sushi
arr.free()
arr.push(1)  # OK: Can still use
```

### `.destroy() -> ~`

Free memory and invalidate (unusable).

```sushi
arr.destroy()
# arr.len()  # ERROR CE2406: use of destroyed variable
```

## Byte Array Only (u8[])

### `.to_string() -> string`

Zero-cost UTF-8 conversion.

```sushi
let u8[] bytes = from([72 as u8, 105 as u8])
let string text = bytes.to_string()  # "Hi"
```

## Memory Management

### Fixed Arrays
- Stack-allocated
- Size known at compile-time
- Automatic cleanup when out of scope
- Cannot grow or shrink

### Dynamic Arrays
- Heap-allocated
- Size determined at runtime
- RAII cleanup with recursive element destruction
- Move semantics (ownership transfer)
- Can grow with `.push()`

## Safe vs Unsafe Access

```sushi
let i32[] arr = from([1, 2, 3])

# Safe: Returns Maybe<T>
let Maybe<i32> safe = arr.get(0)
let i32 value = arr.get(0)??  # Error propagation

# Unsafe: Direct indexing (throws RE2020 if out of bounds)
let i32 direct = arr[0]
```

**Best practice:** Use `.get()` for safety, use `[index]` for idiomatic access when bounds are known.

## Performance

- **Access** (`.get()`, `[index]`): O(1)
- **Push** (`.push()`): Amortized O(1)
- **Pop** (`.pop()`): O(1)
- **Fill** (`.fill()`): O(n)
- **Reverse** (`.reverse()`): O(n)
- **Hash** (`.hash()`): O(n)
- **Clone** (`.clone()`): O(n)

## Implementation Details

- Dynamic arrays use exponential growth strategy
- Runtime bounds checking for all access methods
- RAII cleanup recursively destroys nested structures
- Move semantics prevent use-after-move errors
- `.destroy()` marks array as invalid at compile-time

## Best Practices

- Use fixed arrays when size is known at compile-time
- Use dynamic arrays for runtime-sized collections
- Prefer `.get()` over direct indexing for safety
- Use `.clone()` sparingly (deep copy overhead)
- Call `.free()` to reclaim memory early if array is no longer needed
- Use `.iter()` for idiomatic iteration in foreach loops
- Prefer `List<T>` over dynamic arrays for complex operations

## Example Usage

```sushi
fn main() i32:
    # Fixed array
    let i32[3] fixed = [1, 2, 3]
    println("Fixed length: {fixed.len()}")

    # Dynamic array
    let i32[] dynamic = from([1, 2, 3])
    dynamic.push(4)
    dynamic.push(5)

    # Safe access
    match dynamic.get(2):
        Maybe.Some(value) ->
            println("Element 2: {value}")
        Maybe.None() ->
            println("Out of bounds")

    # Iteration
    foreach(n in dynamic.iter()):
        println(n)

    # In-place operations
    dynamic.reverse()
    dynamic.fill(0)

    # Cleanup
    dynamic.free()

    return Result.Ok(0)
```
