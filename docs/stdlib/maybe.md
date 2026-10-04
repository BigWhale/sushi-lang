# Maybe@(T)

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
let Maybe@(i32) opt = Maybe.Some(42)
if (opt.is_some()):
    println("Has value")
```

### `.is_none() -> bool`

Check if no value present.

```sushi
let Maybe@(string) opt = Maybe.None()
if (opt.is_none()):
    println("No value")
```

### `.realise(default) -> T`

Extract value or return default.

```sushi
let Maybe@(i32) opt = Maybe.None()
let i32 value = opt.realise(0)  # Returns 0
```

### `.expect(string message) -> T`

Extract value or panic with message.

```sushi
let Maybe@(i32) opt = Maybe.Some(42)
let i32 value = opt.expect("Expected a value!")
```

## Error Propagation

Use `??` to unwrap a `Some`. On a `None`, `??` returns early from the enclosing function
with an `Err`:

```sushi
fn double_first(i32[] arr) i32 | StdError:
    let i32 first = arr.get(0)??
    return Result.Ok(first * 2)
```

For an empty `arr`, `double_first` returns `Result.Err(StdError.Error)`.

Use `??` on a `Maybe` in a function whose error type is `StdError`. The compiler also
accepts it in a function with a different error enum, but then the value inside the
`Err` is not defined. In that function, match the `Maybe` and return the error variant
that you want:

```sushi
error LookupError:
    Missing

fn first_or_missing(i32[] arr) i32 | LookupError:
    match arr.get(0):
        Maybe.Some(v) -> return Result.Ok(v)
        Maybe.None() -> return Result.Err(LookupError.Missing)
```

## Pattern Matching

```sushi
use <collections/strings>

let string text = "a needle in a haystack"
match text.find("needle"):
    Maybe.Some(pos) ->
        println("Found at {pos}")
    Maybe.None() ->
        println("Not found")
```

## Use Cases

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
