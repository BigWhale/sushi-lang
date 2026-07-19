# List&lt;T&gt;

[← Back to Standard Library](../../standard-library.md)

Generic growable array with automatic memory management.

## Import

```sushi
# List@(T) is built-in - no import required
```

## Overview

`List@(T)` is a dynamically-sized array that grows automatically as elements are added. It provides:
- **Zero-capacity start**: Lazy allocation until first push
- **Exponential growth**: Doubles capacity for amortized O(1) push
- **Type-safe access**: `.get()` returns `Maybe@(T)` for safe bounds checking
- **Iterator support**: Works with foreach loops
- **RAII cleanup**: Automatic recursive element destruction

`List@(T)` is an owning type: assigning it, or passing it by value to a function, **moves**
it (the source binding can no longer be used; the destination now owns and frees it). Borrow
it with `&peek List@(T)` / `&poke List@(T)` to use it without transferring ownership. There is no
direct `list[i]` indexing operator (unlike `T[]` arrays) — use `.get(i)` for safe access.

## Construction

### `List.new() -> List@(T)`

Create empty list (zero capacity, lazy allocation).

```sushi
let List@(i32) nums = List.new()
```

### `List.with_capacity(i32 n) -> List@(T)`

Create list with pre-allocated capacity.

```sushi
let List@(string) names = List.with_capacity(100)
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

### `.get(i32 index) -> Maybe@(T)`

Get element at index (bounds-checked). The list keeps the element — `.get()` does not remove
it. If `T` is an owning type (e.g. `string`, a struct/enum holding heap data), the returned
value is a deep copy, so the list and the returned `Maybe` each own independent memory.

```sushi
match list.get(0):
    Maybe.Some(value) ->
        println("First: {value}")
    Maybe.None() ->
        println("Index out of bounds")
```

### `.pop() -> Maybe@(T)`

Remove and return last element. Unlike `.get()`, this moves the element out — the list no
longer owns it.

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

### `.insert(i32 index, T element) -> Result@(~)`

Insert element at index (shifts elements right). Returns `Result.Err` if `index` is out of
bounds — unlike `.push()`/`.get()`/`.pop()`/`.remove()`, this is the one `List@(T)` method that
can fail, so it returns a `Result` instead of `~` or `Maybe@(T)`.

```sushi
let List@(i32) nums = List.new()
nums.push(2)
nums.push(3)
nums.push(4)

# Insert at beginning
match nums.insert(0, 1):
    Result.Ok(_) -> println("inserted")
    Result.Err(_) -> println("index out of bounds")

# Insert in middle
nums.insert(2, 99)

# Insert at end (equivalent to push)
nums.insert(nums.len(), 100)
```

**Bounds:** `0 <= index <= len`

### `.remove(i32 index) -> Maybe@(T)`

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

### `.reserve(i32 additional) -> ~`

Ensure capacity is at least `len() + additional` — i.e. reserve room for `additional` more
elements on top of what the list already holds. Only grows, never shrinks; a no-op if the
current capacity already covers `len() + additional`.

```sushi
list.reserve(100)  # Ensure space for 100 more elements beyond the current length
```

### `.shrink_to_fit() -> ~`

Reduce capacity to match length.

```sushi
list.shrink_to_fit()  # Capacity = len
```

## Iteration

### `.iter() -> Iterator@(T)`

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
list.debug()
```

Output (one element per line):

```
List@(i32) {
  len: 3, capacity: 4
  [0] 1
  [1] 2
  [2] 3
}
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
- Use `.get()` for safe access, returns `Maybe@(T)` instead of panicking
- Call `.free()` to reclaim memory early if list is no longer needed
- Use `.shrink_to_fit()` after batch operations to reduce memory footprint
- Prefer `.pop()` over `.remove(len-1)` for last element
