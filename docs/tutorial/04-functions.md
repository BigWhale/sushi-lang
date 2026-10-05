# 4. Functions

We've been leaning on `main`, plus a few built-ins like `println`. Now we write our own
functions. The mechanics are familiar from any language — name, parameters, body — but
Sushi adds one rule that touches every function you write: a function that can fail
says so in its signature, with an **error channel**. This chapter explains the channel and
shows how to *use* the values that functions give back.

## Declaring and calling

A function starts with `fn`, a name, a parenthesised parameter list, and a return type.
Each parameter is written type-first, exactly like a variable declaration: `i32 a`. The
body is indented under a colon:

```sushi
--8<-- "docs/tutorial/examples/04-functions/basics.sushi"
```

Output:

```
40 + 2 = 42
Hello, Arthur! Welcome aboard the Heart of Gold.
Hello, Ford! Welcome aboard the Heart of Gold.
```

`add` takes two `i32`s and returns an `i32`. `greet` returns the blank type `~` from
[Chapter 2](02-variables-and-types.md) because it only prints. Both functions are
**bare**: they cannot fail, so they return their value directly (`return a + b`), and the
call gives the value itself. A bare `~` function can reach the end of its body with no
`return`.

## Functions that can fail: the error channel

Most real functions can fail. A file is missing, a number does not parse, a division has
a zero divisor. Such a function writes an **error channel** after its return type: a `|`
and an error type.

```sushi
fn safe_divide(i32 a, i32 b) i32 | StdError:
```

This is the normal way to write a function that can fail. The call gives a
`Result@(i32, StdError)`: a value that is either a success (`Result.Ok(value)`) or a
failure (`Result.Err(error)`). The body spells both constructors: `return Result.Ok(a / b)`
on success, `return Result.Err(StdError.Error)` on failure. `StdError` is a built-in error
type with one catch-all variant, `StdError.Error`. There is no default error type: a
function without `| E` has no channel and cannot fail.

!!! note "Why an explicit channel?"
    The channel is part of the signature, so the compiler knows where an error can
    appear and makes each caller handle it. You cannot forget a failure by accident.

!!! warning "A bare function is the exception"
    Use the bare form seldom. Use it only when the function is total over its inputs and
    will stay so: a checksum, a pure arithmetic or string helper, a path join. Write a
    channel for everything else: I/O, parsing, allocation on a size that you get, and a
    function that can gain a failure later. Keep a channel on a public function when you
    are not sure, because a channel that you add later changes the signature and breaks
    every caller. The compiler does not enforce this rule.
    [The error channel](../design/error-channel.md) records the decision.

## Consuming a `Result` in `main`

`main` is bare: it returns the exit code directly (`return 0`), and it cannot have an
error channel. So `main` cannot use `??`, the operator that passes an error on to the
caller (`??` in a bare body is **[CE0131](../error-catalog.md#ce0131)**). `main` handles each `Result` itself. (`??` is
the normal tool in a function with a channel, as you will see in
[Chapter 6](06-error-handling.md).)

Two everyday techniques work well in `main`:

- `if (result.is_ok()):` asks whether the call succeeded — the `if` branch runs on
  success, the `else` branch on failure. Inside the success branch,
  `result.realise(default)` pulls out the value. A condition is a bool, so the bare
  `if (result):` is refused with **[CE2516](../error-catalog.md#ce2516)**.
- `.realise(default)` unwraps a success directly, substituting `default` if it was an
  error.

```sushi
--8<-- "docs/tutorial/examples/04-functions/consuming-result.sushi"
```

Output:

```
42 / 6 = 7
Division by zero refused, as it should be.
With a default: -1
```

`safe_divide` returns `Result.Err(StdError.Error)` when asked to divide by zero. The first
call succeeds, so `if (good.is_ok()):` runs its success branch. The second fails, so its
`else` branch runs. The last line shows `.realise(-1)` standing in `-1` because the
division failed. At no point could we have forgotten the failure case.

The call of a bare function is not a `Result`. `add(40, 2)` gives an `i32`, so `??` on it
is **[CE2507](../error-catalog.md#ce2507)** and `.realise(0)` on it is **[CE2008](../error-catalog.md#ce2008)**.

## Parameter modes

A parameter can have a **mode**. The mode tells who owns the argument during and after the
call. There are four:

| Declaration | Call | What the function gets |
|---|---|---|
| `string s` | `f(s)` | A **borrow** (the default). The function can read the value. The caller keeps it. |
| `peek string s` | `f(peek s)` | A read-only borrow through a pointer. |
| `poke i32 n` | `f(poke n)` | A read-write borrow through a pointer. A change is visible to the caller. |
| `nom string s` | `f(nom s)` | The value itself. The function owns it now, and the caller cannot use it again. |

A mode is written at both ends: in the declaration and at the call. If the two do not
agree, the compiler refuses the call and tells you which marker to add.

```sushi
--8<-- "docs/tutorial/examples/04-functions/parameter-modes.sushi"
```

Output:

```
Crew size: 2
Still ours: 2
Jumps: 2
Boarding: Arthur
Boarding: Ford
```

`count` borrows `crew`, so `main` can use `crew` after the call. `add_one` changes the
caller's `jumps` through `poke`. `board` takes `crew` with `nom`: after `board(nom crew)`,
a use of `crew` in `main` is the error [`CE2405`](../error-catalog.md#ce2405) ("cannot borrow moved variable"). For most
parameters, the default borrow is correct. [Chapter 12](12-memory-management.md) explains
ownership in full.

## Custom error types

`StdError` says only that something failed. A function can declare its own error type in
the channel: the part before the `|` is the success type, the part after is the error
type. That makes failures self-documenting.

```sushi
--8<-- "docs/tutorial/examples/04-functions/custom-error.sushi"
```

Output:

```
Jumping to Magrathea
The jump failed: out of fuel.
```

Here `jump` returns `string | NavError`, i.e. `Result@(string, NavError)`, and can fail in
two named ways. `NavError` is declared with the keyword `error`: it is an enum that may be
the error type of a `Result`. We consume it in `main` with the same `if (result.is_ok()):`
pattern as before. This is only a taste — declaring error types, propagating and
converting them with `??`, and pattern matching on the specific failure is the subject of
[Chapter 6](06-error-handling.md).

## Public and private declarations

By default a function is private to its **unit**. A unit is one source file. Marking the
function `public` makes it part of the unit's exported surface, so other units in a
multi-file project can call it. The syntax is the keyword `public` in front of `fn`:

```sushi
--8<-- "docs/tutorial/examples/04-functions/public-fn.sushi"
```

Output:

```
area: 42
perimeter: 26
```

In a single-file program like this, `public` makes no practical difference — but it's the
habit you'll want once your programs grow past one file.

The same marker, and the same default, apply to a `const`, a `var`, a `struct`, an `enum`
and a `perk`. These six declarations are all private until they say otherwise.

One rule to know before you get there: a **public thing may not hand out a private one**.
If `public fn area()` returns a `Rect`, then `Rect` has to be `public` too, or the compiler
refuses the signature — a caller in another unit would receive a type it cannot even name.

## What you learned

- Declare functions with `fn name(Type param, ...) ReturnType:` and call them by name.
- A function that can fail writes an error channel, `fn f() T | E:`. The call gives a
  `Result@(T, E)`, and the body returns `Result.Ok(...)` or `Result.Err(...)`.
- A function without `| E` is bare: it returns its value directly, and the call gives the
  value. Use the bare form only for a function that is total and will stay so.
- `main` is bare and returns the exit code (`return 0`).
- A parameter is a borrow by default. `peek` and `poke` borrow through a pointer, and `nom`
  gives the value to the function. The mode is written at both ends.
- In `main`, consume a `Result` without `??`: use `if (result.is_ok()):` / `else:` and
  `.realise(default)`.
- Name your own error type in the channel, `fn foo() T | ErrorType` — explored fully in
  [Chapter 6](06-error-handling.md).
- `public fn` exports a function for use by other units, and `public` does the same for a
  `const`, a `var`, a `struct`, an `enum` and a `perk`. A public signature may only name
  public types.

Functions give us reusable building blocks. Next we look closely at the type we've been
printing all along: text. On to [Strings](05-strings.md).
