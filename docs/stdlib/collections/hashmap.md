# HashMap&lt;K, V&gt;

[← Back to Standard Library](../../standard-library.md)

Generic hash table with open addressing (linear probing).

## Import

```sushi
use <collections/hashmap>
```

## Overview

`HashMap<K, V>` is a hash table that provides fast key-value lookups. It features:
- **Open addressing**: Linear probing collision resolution
- **Auto-resize**: Grows at 0.75 load factor
- **Power-of-two capacity**: Fast indexing via bitwise AND
- **Type-safe access**: `.get()` returns `Maybe<V>` for safe lookups
- **RAII cleanup**: Automatic recursive cleanup of entries

## Construction

### `HashMap.new() -> HashMap<K, V>`

Create empty hash map (initial capacity 16).

```sushi
let HashMap<string, i32> ages = HashMap.new()
```

## Methods

### `.insert(K key, V value) -> ~`

Insert or update key-value pair.

```sushi
ages.insert("Arthur", 42)
ages.insert("Ford", 200)
```

**Note:** Automatically resizes at 0.75 load factor.

### `.get(K key) -> Maybe<V>`

Get value for key.

```sushi
match ages.get("Arthur"):
    Maybe.Some(age) ->
        println("Arthur is {age}")
    Maybe.None() ->
        println("Not found")
```

### `.remove(K key) -> Maybe<V>`

Remove and return value for key.

```sushi
match ages.remove("Arthur"):
    Maybe.Some(age) ->
        println("Removed age {age}")
    Maybe.None() ->
        println("Key not found")
```

### `.contains_key(K key) -> bool`

Check if key exists.

```sushi
if (ages.contains_key("Arthur")):
    println("Arthur exists")
```

### `.len() -> i32`

Get number of entries.

```sushi
println("Entries: {ages.len()}")
```

## Iteration

A `HashMap` can be iterated three ways. Each returns an iterator suitable for a `foreach`
loop. Iteration order is unspecified.

### `.keys() -> Iterator<K>`

Iterate over the keys.

```sushi
foreach(name in ages.keys()):
    println(name)
```

### `.values() -> Iterator<V>`

Iterate over the values.

```sushi
foreach(age in ages.values()):
    println(age)
```

### `.entries() -> Iterator<Entry<K, V>>`

Iterate over key-value pairs. Each `Entry<K, V>` exposes `.key` and `.value` fields.

```sushi
foreach(entry in ages.entries()):
    println("{entry.key} is {entry.value}")
```

!!! note
    `.keys()`, `.values()`, and `.entries()` require the receiver to be a plain variable
    name — chained calls such as `get_map().keys()` are not currently supported.

### `.free() -> ~`

Clear all entries and reset to capacity 16 (still usable).

```sushi
ages.free()
ages.insert("Zaphod", 150)  # OK
```

### `.rehash() -> ~`

Rebuild the map at its current capacity, clearing out tombstones left by removals.

```sushi
ages.rehash()  # Rebuild, removing tombstones
```

### `.debug() -> ~`

Print internal state.

```sushi
ages.debug()
```

## Key Requirements

Keys must implement `.hash() -> u64` method. Supported types:

- **Primitives**: `i8`, `i16`, `i32`, `i64`, `u8`, `u16`, `u32`, `u64`, `f32`, `f64`, `bool`
- **string**
- **Structs** (with hashable fields)
- **Enums** (with hashable variant data)

**Not supported:** Nested arrays (cannot be hashed)

## Hash Function

The hash function is auto-derived for all types:

- **Primitives**: FxHash for integers, FNV-1a for strings, normalized floats
- **Composites**: FNV-1a combining field/element hashes
- **Limitation**: Nested arrays cannot be hashed

## Performance

- `insert()`: Amortized O(1)
- `get()`: O(1) average case
- `remove()`: O(1) average case
- `contains_key()`: O(1) average case

## Implementation Details

- Open addressing with linear probing for collision resolution
- Power-of-two capacities for fast indexing (uses bitwise AND instead of modulo)
- Automatic resize at 0.75 load factor (triggers on insertion)
- `.free()` recursively destroys all entries and resets to capacity 16
- Supports enum values with primitive/struct fields (automatic variant data cleanup)

## Known Limitations

- Storing an owning value (a struct/enum with a dynamic-array field, `List<T>`, or `Own<T>`) as a
  map value currently crashes at runtime on `get`/`free` (issue #140)
- Keys must be hashable (implement `.hash() -> u64`)
- `.rehash()` takes no arguments; it rebuilds at the current capacity (cannot resize to a chosen capacity)
- `.keys()`/`.values()`/`.entries()` require the receiver to be a plain variable (no chaining)

## Best Practices

- Use `.contains_key()` before `.get()` if you only need an existence check
- Call `.free()` to reclaim memory when clearing large maps
- Use `.rehash()` to clear tombstones after many removals
- Prefer string keys over complex types for best performance
- Pattern match on `.get()` results to handle missing keys gracefully

## Example Usage

```sushi
use <collections/hashmap>

fn main() i32:
    let HashMap<string, i32> scores = HashMap.new()

    # Insert entries
    scores.insert("Alice", 100)
    scores.insert("Bob", 85)
    scores.insert("Charlie", 92)

    # Lookup with pattern matching
    match scores.get("Alice"):
        Maybe.Some(score) ->
            println("Alice scored {score}")
        Maybe.None() ->
            println("Alice not found")

    # Check existence
    if (scores.contains_key("Bob")):
        println("Bob exists in map")

    # Remove entry
    match scores.remove("Charlie"):
        Maybe.Some(score) ->
            println("Removed Charlie with score {score}")
        Maybe.None() ->
            println("Charlie not in map")

    # Debug output
    println("Total entries: {scores.len()}")
    scores.debug()

    return Result.Ok(0)
```
