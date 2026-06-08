# 4. Functions

We've been leaning on `main`, plus a few built-ins like `println`. Now we write our own
functions. The mechanics are familiar from any language — name, parameters, body — but
Sushi adds one twist that touches every function you'll ever write: the return value is
quietly wrapped in a `Result`. This chapter explains that wrapping and shows how to *use*
the values functions hand back.

## Declaring and calling

A function starts with `fn`, a name, a parenthesised parameter list, and a return type.
Each parameter is written type-first, exactly like a variable declaration: `i32 a`. The
body is indented under a colon:

```
--8<-- "docs/tutorial/examples/04-functions/basics.sushi"
```

Output:

```
40 + 2 = 42
Hello, Arthur! Welcome aboard the Heart of Gold.
Hello, Ford! Welcome aboard the Heart of Gold.
```

`add` takes two `i32`s and reports an `i32`. `greet` returns the blank type `~` from
[Chapter 2](02-variables-and-types.md) because it only prints. Notice both functions end
with `return Result.Ok(...)`, and `add`'s result is unwrapped with `.realise(0)` before we
print it. That's the twist worth slowing down for.

## Everything returns a `Result`

In [Chapter 1](01-getting-started.md) we saw `main` end with `return Result.Ok(0)`. That
wasn't special to `main`. **Every** Sushi function returns a `Result` — a value that is
either a success (`Result.Ok(value)`) or a failure (`Result.Err(error)`).

When you write a function whose return type looks like a plain `i32`:

```
fn add(i32 a, i32 b) i32:
```

the compiler reads that `i32` as shorthand and actually gives you back a
`Result<i32, StdError>`. The `i32` is the success type; `StdError` is a built-in default
error type. That's why even `add`, which never fails, still has to say `Result.Ok(a + b)`.
There is no way to "just return an int" — success is always announced explicitly.

This sounds heavy, but it's the foundation of Sushi's promise that you can't accidentally
ignore an error. [Chapter 6](06-error-handling.md) is devoted to making it ergonomic; for
now we just need to *consume* the results.

!!! note "Why force `Result.Ok`?"
    Making success explicit means the compiler always knows where errors can appear, and
    can insist you handle them. The cost is one `Result.Ok(...)` per return; the payoff is
    a whole class of bugs that simply can't compile.

## Consuming a `Result` in `main`

A function returns a `Result`, so the caller has to open the box. There's a tempting
operator, `??`, that unwraps it in one character — but using `??` inside `main` triggers a
warning (CW2511), so in `main` we use safer, explicit tools instead. (`??` is perfectly
fine in *other* functions, as you'll see in the next chapter.)

Two everyday techniques work well in `main`:

- `if (result):` treats the `Result` as a condition — the `if` branch runs on success, the
  `else` branch on failure. Inside the success branch, `result.realise(default)` pulls out
  the value.
- `.realise(default)` unwraps a success directly, substituting `default` if it was an
  error.

```
--8<-- "docs/tutorial/examples/04-functions/consuming-result.sushi"
```

Output:

```
42 / 6 = 7
Division by zero refused, as it should be.
With a default: -1
```

`safe_divide` returns `Result.Err(StdError.Error)` when asked to divide by zero. The first
call succeeds, so `if (good):` runs its success branch. The second fails, so its `else`
branch runs. The last line shows `.realise(-1)` standing in `-1` because the division
failed. At no point did we touch `??`, and at no point could we have forgotten the failure
case.

## Custom error types

`StdError` is the default, but a function can declare its own error type with the
`T | ErrorType` syntax: the part before the `|` is the success type, the part after is the
error type. That makes failures self-documenting.

```
--8<-- "docs/tutorial/examples/04-functions/custom-error.sushi"
```

Output:

```
Jumping to Magrathea
The jump failed: out of fuel.
```

Here `jump` returns `string | NavError`, i.e. `Result<string, NavError>`, and can fail in
two named ways. We consume it in `main` with the same `if (result):` pattern as before.
This is only a taste — designing error types, propagating them with `??`, and pattern
matching on the specific failure is the subject of
[Chapter 6](06-error-handling.md).

## `public` functions

By default a function is private to its file. Marking it `public` makes it part of the
file's exported surface, so other units in a multi-file project can call it. The syntax is
just the keyword `public` in front of `fn`:

```
--8<-- "docs/tutorial/examples/04-functions/public-fn.sushi"
```

Output:

```
area: 42
perimeter: 26
```

In a single-file program like this, `public` makes no practical difference — but it's the
habit you'll want once your programs grow past one file.

## What you learned

- Declare functions with `fn name(Type param, ...) ReturnType:` and call them by name.
- **Every** function returns a `Result`. A bare return type like `i32` is shorthand for
  `Result<i32, StdError>`, so every path must end in `Result.Ok(...)` or `Result.Err(...)`.
- In `main`, consume a `Result` without `??`: use `if (result):` / `else:` and
  `.realise(default)`.
- Declare a custom error type with `fn foo() T | ErrorType` — explored fully in
  [Chapter 6](06-error-handling.md).
- `public fn` exports a function for use by other units.

Functions give us reusable building blocks. Next we look closely at the type we've been
printing all along: text. On to [Strings](05-strings.md).
