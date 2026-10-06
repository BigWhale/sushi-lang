# List@(T)

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

`List@(T)` is an owning type: assigning it, or handing it to a `nom` parameter, **moves**
it (the source binding can no longer be used; the destination now owns and frees it). An
ordinary parameter borrows, so `f(list)` leaves the list yours; `peek List@(T)` /
`poke List@(T)` borrow by pointer, which is what lets a callee mutate it in place. There is
no direct `list[i]` indexing operator (unlike `T[]` arrays) — use `.get(i)` for safe access.

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
it. The value in the `Some` is a BORROW of the element, not a copy. If `T` is an owning
type (e.g. `string`, a struct/enum holding heap data), you can read the value, but you
cannot consume it ([CE2411](../../error-catalog.md#ce2411)), and `match nom` on it is refused ([CE2432](../../error-catalog.md#ce2432)). Take an independent
value with `.clone()`:

```sushi
match names.get(0):
    Maybe.Some(s) -> keep(nom s.clone())   # the list still owns its element
    Maybe.None() -> println("empty")
```

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

### `.first() -> Maybe@(T)` and `.last() -> Maybe@(T)`

TODO(worker)

### `.contains(T value) -> bool` and `.index_of(T value) -> Maybe@(i32)`

The search methods of the [arrays](arrays.md), with the same rules. `.contains()`
answers `true` when an element is equal to `value`. `.index_of()` answers the index of
the FIRST element that is equal to `value`, or `Maybe.None()`. Each search goes from
left to right. An empty list contains nothing.

The element type must have equality: the numeric types, `bool`, `string`, or a struct or
an enum with a derived or implemented `Eq`. An element type with no equality is
[CE2100](../../error-catalog.md#ce2100). The needle is a borrow, and the list keeps all of
its elements.

```sushi
fn main() i32:
    let List@(string) words = List.new()
    words.push("alpha")
    words.push("beta")
    words.push("alpha")
    println(words.contains("beta"))               # true
    println(words.contains("delta"))              # false
    println(words.index_of("alpha").realise(-1))  # 0
    println(words.index_of("delta").realise(-1))  # -1
    return 0
```

### `.index_of_from(T value, i32 start) -> Maybe@(i32)`

Finds the first index `i` with `i >= start` where the element is equal to `value`.
The answer is `Maybe.Some(i)`, or `Maybe.None()`. The index is from the start of the
list, not from `start`. The rule is the rule of the [array](arrays.md) method of that
name, and the equality rule is the rule of `.index_of()`.

`start` is an `i32` position. The method clamps `start` into `0..len()`, and it does not
trap:

- A negative `start` searches from index 0.
- A `start` that is equal to `len()` or more than `len()` gives `Maybe.None()`.

```sushi
fn main() i32:
    let List@(i32) l = List.new()
    l.push(7)
    l.push(3)
    l.push(7)
    println(l.index_of_from(7, 1).realise(-1))    # 2
    println(l.index_of_from(7, -1).realise(-1))   # 0
    println(l.index_of_from(3, 2).realise(-1))    # -1
    println(l.index_of_from(7, 3).realise(-1))    # -1
    return 0
```

## Modification Methods

### `.push(T element) -> ~`

Append element (auto-grows capacity).

```sushi
list.push(42)
list.push(100)
```

### `.insert(i32 index, T element) -> Result@(~, StdError)`

Insert element at index (shifts elements right). Returns `Result.Err(StdError.Error)` if
`index` is out of bounds — unlike `.push()`/`.get()`/`.pop()`/`.remove()`, this is the one
`List@(T)` method that can fail, so it returns a `Result` instead of `~` or `Maybe@(T)`.

The element is evaluated and consumed before the bounds check. On the `Err` path the list
destroys the element, so an owning element does not leak, and it is gone after the call.

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

## Hashing

### `.hash() -> u64`

The hash of what the list HOLDS: each element in turn, then the length. It is derived
automatically, and it exists only when the element type has a hash of its own.

```sushi
let List@(i32) a = List.new()
a.push(1)
a.push(2)

let List@(i32) b = List.new()
b.push(1)
b.push(2)

println("{a.hash() == b.hash()}")   # true -- two lists, the same elements
```

Two lists that hold equal elements answer one hash, although their buffers are two. The
length is part of the hash, so a list of one zero and a list of two zeros do not collide.

A list is not a `HashMap@(K, V)` key: a key also needs an equality test, and a list has
none.

## Copying

### `.clone() -> List@(T)`

A deep copy: the new list owns its own copy of each element.

```sushi
let List@(string) copy = names.clone()
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

### Printing

A `List@(T)` goes into an interpolation hole and into `print` / `println` when its element
has a string form, in the form an array prints: `[1, 2, 3]`, a string element in quotes,
and `[]` when it is empty. An element with no string form is [CE2035](../../error-catalog.md#ce2035) in a hole and [CE2115](../../error-catalog.md#ce2115) in
`println`.

```sushi
fn main() i32:
    let List@(string) names = List.new()
    names.push("Ford")
    names.push("Arthur")
    println("{names}")                  # ["Ford", "Arthur"]
    return 0
```

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

An element of a primitive type or a string prints as `println` writes it, and a
string is quoted. Any other element -- a struct, an enum, a nested container --
prints as `<value>`.

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
