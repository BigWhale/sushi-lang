# Result<T>

[← Back to Standard Library](../standard-library.md)

Error handling type for fallible operations.

## Variants

### `Result.Ok(value)`

Success case containing a value.

### `Result.Err()`

Failure case (no additional data).

## Methods

### `.realise(default)`

Extract value or return default on error.

```sushi
let Result<i32> result = some_operation()
let i32 value = result.realise(0)  # Returns 0 if Err
```

## Usage in Conditionals

Result can be used directly in `if` statements:

```sushi
if (risky_operation()):
    println("Success!")
else:
    println("Failed")
```

## Error Propagation

Use the `??` operator to unwrap or propagate errors:

```sushi
fn might_fail() i32:
    let Result<i32> result = risky_operation()
    let i32 value = result??  # Returns early with Err if operation failed
    return Result.Ok(value * 2)
```

## Pattern Matching

```sushi
match parse_number("42"):
    Result.Ok(n) ->
        println("Got: {n}")
    Result.Err() ->
        println("Parse failed")
```

## Best Practices

- Always handle errors explicitly (don't ignore Result values)
- Use `??` for error propagation in function chains
- Use `.realise(default)` when a fallback value makes sense
- Use pattern matching when you need different behavior for Ok/Err
