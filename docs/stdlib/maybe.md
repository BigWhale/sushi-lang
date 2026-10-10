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

### `.or_err(nom e)`

Turn the `Maybe@(T)` into a `Result@(T, E)`: `Some(v)` becomes `Ok(v)`, and `None` becomes
`Err(e)`. The signature, written as an extension, is
`extend Maybe@(T) or_err@(E)(nom self, nom E e) Result@(T, E)`. It is a built-in method
and needs no import.

```sushi
error LookupError:
    Missing

fn main() i32:
    let Maybe@(i32) found = Maybe.Some(7)
    let Maybe@(i32) empty = Maybe.None()
    println(found.or_err(nom LookupError.Missing).realise(-1))  # 7
    match empty.or_err(nom LookupError.Missing):
        Result.Ok(v) -> println(v)
        Result.Err(e) -> println(e)                             # LookupError.Missing
    return 0
```

- The argument is `nom`, because the value moves into the `Err`. Write the marker at the
  call: `or_err(nom LookupError.Missing)`. A call without it is [CE2427](../error-catalog.md#ce2427).
- `E` is solved from the argument, and it must be an error type ([CE2084](../error-catalog.md#ce2084)).
- The receiver is `nom self`, because the `Some` payload moves into the `Ok`. A named
  `Maybe` that owns something (a `Maybe@(string)`) is spent by the call, and a later use is
  [CE2435](../error-catalog.md#ce2435). A `Maybe` that owns nothing (a `Maybe@(i32)`) is copied and stays usable.
- A BORROWED `Maybe` is read through, as `??` reads a borrowed `Result`: a get-out
  (`xs.get(0)`, also from a temporary owner), a parameter or a pattern binding. Under
  `??` the value then binds a borrow, as the get-out does, and a consuming use of it is
  [CE2411](../error-catalog.md#ce2411). No clone is needed.
- **The read-through rule ([CE2522](../error-catalog.md#ce2522)).** When the borrowed `Maybe` owns its payload (a
  `string`, an array, a handle), its `Result` holds a borrowed `Ok` and an owned `Err`.
  Only `??` takes the two apart, so the call is legal only as the operand of `??`. In a
  `let`, a method call such as `.is_ok()`, a `match`, an argument or a `return`, it is
  [CE2522](../error-catalog.md#ce2522). The help names both forms: put the call under `??`, or take an owned copy first
  with `xs.get(0).clone().or_err(nom e)`.
- Exactly one argument ([CE2009](../error-catalog.md#ce2009)).

## Error Propagation

`??` takes a `Result@(T, E)` and nothing else. A `Maybe` holds no error value: a `None`
says that a value is absent, and not why. So `??` on a `Maybe` is [CE2507](../error-catalog.md#ce2507), and the program
writes the error value with `or_err`:

```sushi
error LookupError:
    Missing

fn double_first(i32[] arr) i32 | LookupError:
    let i32 first = arr.get(0).or_err(nom LookupError.Missing)??
    return Result.Ok(first * 2)

fn main() i32:
    let i32[] full = from([21])
    let i32[] empty = new()
    println(double_first(full).realise(-1))     # 42
    println(double_first(empty).realise(-1))    # -1
    return 0
```

For an empty `arr`, `double_first` returns `Result.Err(LookupError.Missing)`. The error
type is any error type: the value is written at the site, so the compiler never makes one
up. In a get-out of an owned payload, `??` binds a borrow:

```sushi
error LookupError:
    Missing

fn first_name(List@(string) names) i32 | LookupError:
    let string s = names.get(0).or_err(nom LookupError.Missing)??   # a borrow of the element
    return Result.Ok(s.len())

fn main() i32:
    let List@(string) names = List.new()
    names.push("Ford")
    println(first_name(names).realise(-1))      # 4
    return 0
```

## Pattern Matching

```sushi
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
- Use `.or_err(nom e)??` to propagate a `None` as an error, with an error value you choose
- Use pattern matching for explicit handling of both cases
