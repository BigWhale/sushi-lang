# Maybe<T>

[← Back to Standard Library](../standard-library.md)

Optional value type for nullable data.

## Variants

### `Maybe.Some(value)`

Contains a value of type T.

### `Maybe.None()`

No value present.

## Methods

### `.is_some() -> bool`

Check if value is present.

```sushi
let Maybe<i32> opt = Maybe.Some(42)
if (opt.is_some()):
    println("Has value")
```

### `.is_none() -> bool`

Check if no value present.

```sushi
let Maybe<string> opt = Maybe.None()
if (opt.is_none()):
    println("No value")
```

### `.realise(default) -> T`

Extract value or return default.

```sushi
let Maybe<i32> opt = Maybe.None()
let i32 value = opt.realise(0)  # Returns 0
```

### `.expect(string message) -> T`

Extract value or panic with message.

```sushi
let Maybe<i32> opt = Maybe.Some(42)
let i32 value = opt.expect("Expected a value!")
```

## Error Propagation

Use `??` to unwrap or propagate None:

```sushi
fn get_first(i32[] arr) Maybe<i32>:
    let Maybe<i32> first = arr.get(0)??
    return Maybe.Some(first * 2)
```

## Pattern Matching

```sushi
match text.find("needle"):
    Maybe.Some(pos) ->
        println("Found at {pos}")
    Maybe.None() ->
        println("Not found")
```

## Use Cases

- Optional function parameters (future feature)
- Search operations (find, get)
- Parsing operations that may fail
- Database lookups
- Dictionary/map access

## Best Practices

- Prefer Maybe over sentinel values (-1, null, etc.)
- Use `.is_some()` / `.is_none()` for conditional checks
- Use `.realise(default)` when a fallback makes sense
- Use `.expect(msg)` only when you're certain a value exists
- Use pattern matching for explicit handling of both cases
