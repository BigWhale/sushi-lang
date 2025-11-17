# List&lt;T&gt;

[← Back to Standard Library](../../standard-library.md)

Generic growable array with automatic memory management.

## Import

```sushi
use <collections>
```

## Overview

`List<T>` is a dynamically-sized array that grows automatically as elements are added. It provides:
- **Zero-capacity start**: Lazy allocation until first push
- **Exponential growth**: Doubles capacity for amortized O(1) push
- **Type-safe access**: `.get()` returns `Maybe<T>` for safe bounds checking
- **Iterator support**: Works with foreach loops
- **RAII cleanup**: Automatic recursive element destruction

## Construction

### `List.new() -> List<T>`

Create empty list (zero capacity, lazy allocation).

```sushi
let List<i32> nums = List.new()
```

### `List.with_capacity(i32 n) -> List<T>`

Create list with pre-allocated capacity.

```sushi
let List<string> names = List.with_capacity(100)
```

## Query Methods

### `.len() -> i32`

Get number of elements.

```sushi
println("Size: {list.len()}")
```

### `.capacity() -> i32`

Get allocated capacity.

```sushi
println("Capacity: {list.capacity()}")
```

### `.is_empty() -> bool`

Check if list is empty.

```sushi
if (list.is_empty()):
    println("Empty list")
```

## Access Methods

### `.get(i32 index) -> Maybe<T>`

Get element at index (bounds-checked).

```sushi
match list.get(0):
    Maybe.Some(value) ->
        println("First: {value}")
    Maybe.None() ->
        println("Index out of bounds")
```

### `.pop() -> Maybe<T>`

Remove and return last element.

```sushi
match list.pop():
    Maybe.Some(value) ->
        println("Popped: {value}")
    Maybe.None() ->
        println("Empty list")
```

## Modification Methods

### `.push(T element) -> ~`

Append element (auto-grows capacity).

```sushi
list.push(42)
list.push(100)
```

### `.insert(i32 index, T element) -> Result<~>`

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

### `.remove(i32 index) -> Maybe<T>`

Remove and return element at index (shifts elements left).

```sushi
match list.remove(2):
    Maybe.Some(value) ->
        println("Removed: {value}")
    Maybe.None() ->
        println("Index out of bounds")
```

**Bounds:** `0 <= index < len`

### `.clear() -> ~`

Remove all elements (keeps capacity).

```sushi
list.clear()
println("Length: {list.len()}")  # 0
println("Capacity: {list.capacity()}")  # Unchanged
```

## Capacity Management

### `.reserve(i32 n) -> ~`

Ensure capacity is at least `n`.

```sushi
list.reserve(100)  # Ensure space for 100 elements
```

### `.shrink_to_fit() -> ~`

Reduce capacity to match length.

```sushi
list.shrink_to_fit()  # Capacity = len
```

## Iteration

### `.iter() -> Iterator<T>`

Create iterator for foreach loops.

```sushi
foreach(value in list.iter()):
    println(value)
```

## Memory Management

### `.free() -> ~`

Free memory and reset to empty (still usable).

```sushi
list.free()
list.push(1)  # OK: Can still use
```

### `.destroy() -> ~`

Free memory and invalidate (unusable).

```sushi
list.destroy()
# list.len()  # ERROR CE2406: use of destroyed variable
```

## Debugging

### `.debug() -> ~`

Print internal state (length, capacity, elements).

```sushi
list.debug()  # Output: List<i32> { len: 3, capacity: 4, [1, 2, 3] }
```

## Performance

- `push()`: Amortized O(1)
- `pop()`: O(1)
- `get()`: O(1)
- `insert()`: O(n)
- `remove()`: O(n)
- `clear()`: O(n)

## Implementation Details

- Uses `llvm.memmove` for safe overlapping memory operations
- Exponential growth strategy: doubles capacity on each reallocation
- Recursive element destruction for nested structures
- Iterator support for foreach loops via `.iter()`

## Best Practices

- Use `.with_capacity()` when final size is known to avoid reallocations
- Use `.get()` for safe access, returns `Maybe<T>` instead of panicking
- Call `.free()` to reclaim memory early if list is no longer needed
- Use `.shrink_to_fit()` after batch operations to reduce memory footprint
- Prefer `.pop()` over `.remove(len-1)` for last element
