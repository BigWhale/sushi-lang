# HashMap@(K, V)

[← Back to Standard Library](../../standard-library.md)

Generic hash table with open addressing (linear probing).

## Import

```sushi
use <collections/hashmap>
```

## Overview

`HashMap@(K, V)` is a hash table that provides fast key-value lookups. It features:
- **Open addressing**: Linear probing collision resolution
- **Auto-resize**: Grows at 0.75 load factor
- **Power-of-two capacity**: Fast indexing via bitwise AND
- **Type-safe access**: `.get()` returns `Maybe@(V)` for safe lookups
- **RAII cleanup**: Automatic recursive cleanup of entries

## Construction

### `HashMap.new() -> HashMap@(K, V)`

Create empty hash map (initial capacity 16).

```sushi
let HashMap@(string, i32) ages = HashMap.new()
```

## Methods

### `.insert(K key, V value) -> ~`

Insert or update key-value pair.

```sushi
ages.insert("Arthur", 42)
ages.insert("Ford", 200)
```

**Note:** Automatically resizes at 0.75 load factor.

### `.get(K key) -> Maybe@(V)`

Get value for key.

```sushi
match ages.get("Arthur"):
    Maybe.Some(age) ->
        println("Arthur is {age}")
    Maybe.None() ->
        println("Not found")
```

### `.remove(K key) -> Maybe@(V)`

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

### `.is_empty() -> bool`

`true` when the map holds no entries.

```sushi
if (ages.is_empty()):
    println("no entries")
```

### `.tombstone_count() -> i32`

The number of slots that a `.remove()` marked as deleted. `.rehash()` clears them.

```sushi
ages.remove("Ford")
println(ages.tombstone_count())   # 1
ages.rehash()
println(ages.tombstone_count())   # 0
```

### `.clone() -> HashMap@(K, V)`

A deep copy: the new map owns its own entries.

```sushi
let HashMap@(string, i32) copy = ages.clone()
```

## Iteration

A `HashMap` can be iterated three ways. Each returns an iterator suitable for a `foreach`
loop. Iteration order is unspecified.

### `.keys() -> Iterator@(K)`

Iterate over the keys.

```sushi
foreach(name in ages.keys()):
    println(name)
```

### `.values() -> Iterator@(V)`

Iterate over the values.

```sushi
foreach(age in ages.values()):
    println(age)
```

### `.entries() -> Iterator@(Entry@(K, V))`

Iterate over key-value pairs. Each `Entry@(K, V)` exposes `.key` and `.value` fields.

```sushi
foreach(entry in ages.entries()):
    println("{entry.key} is {entry.value}")
```

!!! note
    `.keys()`, `.values()`, and `.entries()` accept any receiver whose type resolves,
    including a fallible getter: `foreach(k in get_map()??.keys())` works, and the map
    it produces is freed at scope exit.

### `.free() -> ~`

Clear all entries and reset to capacity 16 (still usable).

### `.destroy() -> ~`

Free the map and its entries. A later use of the map is a compile error.

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

A key or a value of a primitive type or a string prints as `println` writes it, and a
string is quoted. Anything else prints as `<value>`.

## Key Requirements

A key needs two things: a hash (`.hash() -> u64`) and an equality test. The map uses
the hash to find a slot, and the equality test to tell two keys in one slot apart.
Supported types:

- **Primitives**: `i8`, `i16`, `i32`, `i64`, `u8`, `u16`, `u32`, `u64`, `f32`, `f64`, `bool`
- **string**
- **Structs** (each field has a hash and an equality test)
- **Enums** (each payload has a hash and an equality test)
- **`List@(T)`** of a key type, and a struct that holds one.

The equality test is the predefined perk `Eq`: an `extend K with Eq` implementation, or else
the equality that the compiler derives from what the key holds. A `Hashable` override and
an `Eq` override are two separate contracts. They are read in every position a key is used.
A float that a key holds uses the total rule: `0.0` and `-0.0` are one key, and a NaN key
can be found again. The hash of every NaN is the same.

**Not supported:**

- A dynamic array (`i32[]`, `string[]`): **CE2058**. A dynamic array has no equality test
  at the top level. Use a fixed array, or a `List@(T)`.
- A type with no equality test: **CE2055**. That is a function value, a `ptr`, a
  `HashMap@(K, V)`, and a type that holds one, unless the type implements `Eq`.
  A note names the field.
- A `HashMap@(K, V)` itself. A map has no hash of its own: its buckets carry a state for
  each slot and the slot order is not the entry order, so a hash over them would answer
  two values for one set of entries.

The compiler checks the key type at every written `HashMap@(K, V)` type.

### A `Hashable` override gives a hash, not equality

Hashing and equality are two contracts. `extend T with Hashable` REPLACES the hash of
`T` everywhere, and it makes a type hashable that the compiler cannot hash (for example,
a struct with a function-typed field). It does NOT give `T` an equality test. A function
value and a `ptr` have no equality. Thus a type that holds one of them is not a key with
a `Hashable` override alone (**CE2055**, and #936):

<!-- docs-sweep: error CE2055 -->
```sushi
use <collections/hashmap>

struct Handler:
    fn(i32) -> i32 run
    i32 id

extend Handler with Hashable:
    fn hash() u64:
        return self.id as u64

fn main() i32:
    let HashMap@(Handler, i32) m = HashMap.new()    # CE2055: no equality test
    m.free()
    return 0
```

An `Eq` implementation beside the `Hashable` one makes the type a key:

```sushi
use <collections/hashmap>

struct Handler:
    fn(i32) -> i32 run
    i32 id

extend Handler with Hashable:
    fn hash() u64:
        return self.id as u64

extend Handler with Eq:
    fn eq(Handler other) bool:
        return self.id == other.id

fn main() i32:
    let HashMap@(Handler, i32) m = HashMap.new()
    m.insert(Handler(|i32 x| x + 1, 7), 1)
    println(m.contains_key(Handler(|i32 x| x, 7)))
    m.free()
    return 0
```

Another way is to use a field that has equality as the key (here the
`i32 id`), and keep the full value in the map as the value:

```sushi
use <collections/hashmap>

struct Handler:
    fn(i32) -> i32 run
    i32 id

fn main() i32:
    let HashMap@(i32, Handler) m = HashMap.new()
    let Handler h = Handler(|i32 x| x + 1, 7)
    m.insert(h.id, h)
    match m.get(7):
        Maybe.Some(found) -> println("{found.run(41)}")
        Maybe.None -> println("none")
    m.free()
    return 0
```

## Hash Function

The hash function is auto-derived for all types:

- **Primitives**: FxHash for integers, FNV-1a for strings, normalized floats (every NaN hashes alike, and `0.0` and `-0.0` hash alike)
- **Composites**: FNV-1a combining field/element hashes
- **Limitation**: An element type with no hash (today, `ptr`) has no derived hash

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

- Keys must have a hash and an equality test (see Key Requirements)
- `.rehash()` takes no arguments; it rebuilds at the current capacity (cannot resize to a chosen capacity)
- The iteration order is not specified

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
    let HashMap@(string, i32) scores = HashMap.new()

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

    return 0
```
