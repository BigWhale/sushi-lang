# 6. Error Handling

This is the chapter where Sushi's personality really shows. In Python a function that
fails throws an exception that may sail silently past three callers before someone catches
it (or no one does). In Java you juggle checked exceptions and `null`. Sushi takes a
different oath: **failure is a value, and the type system makes you deal with it.** A
function doesn't return "an `i32`, or maybe an explosion." It returns a `Result@(i32, E)` —
success *or* error, right there in the type — and the compiler won't let you forget which
one you're holding.

By the end of this chapter you'll understand the error channel, `Result@(T, E)`, the `??`
propagation operator, the `Maybe@(T)` optional type, and the small set of patterns that
handle errors in `main`.

## `Result@(T, E)`, and why it exists

A function that can fail writes an **error channel** in its signature: `fn f() T | E:`.
This is the normal way to write such a function. The call gives a `Result@(T, E)` — a value
that is either:

- `Result.Ok(value)` — success, carrying a `T`, or
- `Result.Err(error)` — failure, carrying an `E`.

Here's a function that can fail, and two ways to handle the outcome at the call site:
`if (result.is_ok()):` (Ok runs the `if`, Err runs the `else`) and `.realise(default)` (covered
later).

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/result-basics.sushi"
```

Output:

```
84 halved is 42
7 is odd, cannot halve cleanly
```

Two details to absorb:

- The signature `fn halve(i32 n) i32 | StdError` means "returns an `i32`, or fails with a
  `StdError`". The call gives `Result@(i32, StdError)`. The body spells both constructors:
  `return Result.Ok(...)` and `return Result.Err(...)`. A plain `return n / 2` in this body
  is **CE2030**. (Recap from Chapter 4.)
- `StdError` is a built-in error type. `StdError.Error` is its catch-all variant — fine for
  "something went wrong" when you don't need detail. It is not a default: a function that
  writes no `| E` has no channel at all.

### The bare form is the exception

A function without `| E` is **bare**. It cannot fail: the body returns the value
(`return x`), and the call gives the value itself. In a bare body, `return Result.Ok(...)`
is **CE2091** and `??` is **CE0131**. On the call of a bare function, `??` is **CE2507** and
`.realise(...)` is **CE2008**, because the value is not a `Result`.

Use the bare form seldom. Use it only when the function is total over its inputs and will
stay so (a checksum, a pure arithmetic or string helper, a path join), and a channel would
only force a dead `??` or `.realise` on every caller. Write a channel for everything else:
I/O, parsing, allocation on a size that you get, and a function that can gain a failure
later. Keep a channel on a public function when you are not sure: a channel that you add
later changes the signature and breaks every caller and every binary library. The compiler
does not enforce this rule. [The error channel](../design/error-channel.md) records the
decision, and [Error handling](../error-handling.md) has the full text.

!!! note "Why not just exceptions?"
    Exceptions are invisible in a function's signature — you can't tell by looking whether a
    call might blow up. `Result@(T, E)` puts the failure mode in the type, so the compiler
    can *prove* you handled it. The cost is a little more typing; the payoff is whole
    categories of "I forgot that could fail" bugs that simply cannot compile.

## Custom error types

`StdError.Error` is fine for quick programs, but real code wants to say *what* went wrong.
Define an `enum` and name it as the error type with the `T | ErrorEnum` syntax. Now callers
can `match` on the specific variant. The error type must be an enum, in both spellings
(`T | E` and `Result@(T, E)`); `fn f() i32 | i32` is the error `CE2084`.

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/custom-errors.sushi"
```

Output:

```
42 / 6 = 7
cannot divide by zero
```

`fn safe_divide(i32 a, i32 b) i32 | JumpError` reads as "returns an `i32`, or fails with a
`JumpError`" — that is, `Result@(i32, JumpError)`. Because the error is an enum, the `match`
can name each failure mode (`DivisionByZero`, `NegativeInput`) and the compiler checks that
you covered them all.

!!! note "Don't mix `|` with an explicit `Result`"
    Use *either* the short form `fn f() T | MyError` *or* the fully explicit
    `fn f() Result@(T, MyError)` — never both at once. Writing
    `fn f() Result@(T, E1) | E2` is a contradiction and the compiler rejects it (CE2085).

## The `??` propagation operator

Matching on every single call would get tedious fast. When a helper function just wants to
say "if this failed, fail too, with the same error," that's what `??` is for. Applied to a
`Result`, `??` either **unwraps the `Ok` value** or **immediately returns the `Err`** from
the enclosing function.

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/propagation.sushi"
```

Output:

```
with tank: 20
no tank:   -1
```

Look at `plan_jump`. Each `??` collapses a whole match into one character:

```sushi
let i32 fuel = fetch_fuel(tank_present)??
```

If `fetch_fuel` returned `Result.Err`, `plan_jump` returns that same error right there, and
the lines below never run. If it returned `Result.Ok(50)`, `fuel` is plainly `50`. The
error types must match exactly — `??` does not silently convert one error enum into another.

!!! note "`??` is RAII-safe and zero-cost"
    When `??` bails out early, Sushi still runs the cleanup for anything you'd allocated so
    far (its RAII destructors fire on the error path too) — no leaks, even on the unhappy
    path. And it compiles down to a plain branch-and-return: there's no hidden exception
    machinery or runtime tax. It's syntax sugar over the `match` you'd otherwise write by
    hand.

## `.realise(default)` for safe unwrapping

Sometimes you don't want to propagate an error — you just want a sensible fallback.
`.realise(default)` unwraps the `Ok` value, or hands back `default` if it's an `Err`. We've
been using it already; here it is spelled out:

```sushi
let i32 ok = plan_jump(true).realise(-1)      # Ok(20)  -> 20
let i32 failed = plan_jump(false).realise(-1)  # Err     -> -1
```

This is the workhorse for turning a `Result` into a plain value without branching, and —
as we'll see in a moment — it's one of the main-safe ways to consume results.

## `Maybe@(T)`: a value, or nothing

`Result@(T, E)` answers "did it succeed, and if not, *why*?" Sometimes you don't have a why —
there's simply a value present or absent. A lookup that finds nothing isn't an *error*; it's
just empty. For that, Sushi has `Maybe@(T)`:

- `Maybe.Some(value)` — there's a value,
- `Maybe.None()` — there isn't.

This is Sushi's replacement for `null` and for sentinel values like `-1`. You met it in
Chapter 5: `string.find()` returns `Maybe@(i32)`. Its handy methods are `.is_some()`,
`.is_none()`, `.realise(default)` (same idea as on `Result`), and `.expect(msg)` (unwrap or
crash with a message — use only when absence would be a genuine bug).

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/maybe.sushi"
```

Output:

```
Crew of 3 aboard
Ford is aboard
Ford index (or -1): 1
No Vogons aboard, thankfully
Vogon index (or -1): -1
```

`find_index` writes the channel `| StdError`, so the call gives
`Result@(Maybe@(i32), StdError)` — two layers. We peel the `Result` with `match`, then inspect the `Maybe` inside. (`Result.Err(_)`
uses `_` to ignore the bound error: a `match` arm for `Err` must bind something, and `_`
says "I don't care about it" without tripping an unused-variable warning.)

`crew` is an ordinary parameter, so `find_index` only borrows it. `main` keeps the array
and can search it again.

### `??` on a `Maybe`

`??` also works on a `Maybe@(T)`, in a function that returns a `Result`. `Maybe.Some(value)`
unwraps to `value`. `Maybe.None()` makes the function return an error at once. An array's
`.get(i)` returns a `Maybe`, so `??` is a short way to say "stop if there is no element
here":

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/maybe-propagation.sushi"
```

Output:

```
second of three, doubled: 42
second of one, doubled:   -1
```

## `main` is bare: no `??` there

Here's the one rule that trips up newcomers. `main` is a bare function: it returns the
program's exit code directly (`return 0`), and it cannot have an error channel (`| E` on
`main` is **CE0106**). So `??` in `main` has nowhere to propagate *to*, and the compiler
refuses it with **CE0131**. `main` handles each error at the boundary instead, and chooses
the exit code.

There are three patterns for `main`:

- **`match`** — when you want to handle Ok and Err differently.
- **`.realise(default)`** — when a fallback value is enough.
- **`if (result.is_ok()):`** — for a quick Ok/else split.

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/main-safe.sushi"
```

Output:

```
matched ok: 7
realise fallback: 0
if ok: 7
```

None of those use `??`, so the program compiles. Save `??` for the helpers with a channel
that `main` calls — that's exactly where its early-return magic belongs.

!!! note "The shape of a tidy program"
    A common, comfortable structure: small helper functions that lean on `??` to chain
    fallible steps, and a `main` that calls them and resolves the final `Result` with
    `match` or `.realise()`. Errors propagate cleanly through the middle and get handled
    once, at the edge.

## What you learned

- A function that can fail writes an error channel, `fn f() T | E:`. The call gives
  `Result@(T, E)`, and the body returns `Result.Ok(value)` or `Result.Err(error)`. The error
  type must be an enum (`CE2084`).
- A function without `| E` is bare: it returns the value, and the call gives the value.
  There is no default error type. Use the bare form only for a total function.
- `??` unwraps `Ok` or propagates `Err` from the enclosing function — RAII-safe, zero-cost,
  and legal only in a body with a channel. On a `Maybe`, `??` unwraps `Some` and
  returns an error for `None`.
- `.realise(default)` unwraps with a fallback; `if (result.is_ok()):` splits Ok from Err.
- `Maybe@(T)` (`Maybe.Some` / `Maybe.None`) models presence vs. absence — Sushi's `null`
  replacement — with `.is_some()`, `.is_none()`, `.realise()`, and `.expect()`.
- `main` is bare and returns the exit code. `??` in `main` is **CE0131**; handle errors
  there with `match`, `.realise()`, or `if (result.is_ok()):` instead.

Next we put values in bulk. On to [Arrays](07-arrays.md).
