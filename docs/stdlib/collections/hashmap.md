# HashMap&lt;K, V&gt;

[← Back to Standard Library](../../standard-library.md)

Generic hash table with open addressing (linear probing).

## Import

```sushi
use <collections>
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

### `.contains(K key) -> bool`

Check if key exists.

```sushi
if (ages.contains("Arthur")):
    println("Arthur exists")
```

### `.len() -> i32`

Get number of entries.

```sushi
println("Entries: {ages.len()}")
```

### `.free() -> ~`

Clear all entries and reset to capacity 16 (still usable).

```sushi
ages.free()
ages.insert("Zaphod", 150)  # OK
```

### `.rehash(i32 new_capacity) -> ~`

Manually rehash with new capacity (must be power of 2).

```sushi
ages.rehash(64)  # Resize to capacity 64
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
- `contains()`: O(1) average case

## Implementation Details

- Open addressing with linear probing for collision resolution
- Power-of-two capacities for fast indexing (uses bitwise AND instead of modulo)
- Automatic resize at 0.75 load factor (triggers on insertion)
- `.free()` recursively destroys all entries and resets to capacity 16
- Supports enum values with primitive/struct fields (automatic variant data cleanup)

## Known Limitations

- Enum variants with dynamic array fields cause type system errors
- No iterator support yet (cannot use in foreach loops)
- Keys must be hashable (implement `.hash() -> u64`)
- Manual rehash requires power-of-two capacity

## Best Practices

- Use `.contains()` before `.get()` if you only need existence check
- Call `.free()` to reclaim memory when clearing large maps
- Use `.rehash()` to pre-allocate capacity if final size is known
- Prefer string keys over complex types for best performance
- Pattern match on `.get()` results to handle missing keys gracefully

## Example Usage

```sushi
use <collections>

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
    if (scores.contains("Bob")):
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
