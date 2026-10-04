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
Declare an error type with the keyword `error`, and name it in the `T | MyError` syntax.
Now callers can `match` on the specific variant. The body of an `error` declaration is the
body of an `enum`: variants and payloads. The `E` of a `Result` must be an error type, in
both spellings (`T | E` and `Result@(T, E)`): a plain `enum`, a struct or `i32` there is
the error `CE2084`, and for a plain `enum` the help says to write `error` in place of
`enum`.

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/custom-errors.sushi"
```

Output:

```
42 / 6 = 7
cannot divide by zero
```

`fn safe_divide(i32 a, i32 b) i32 | JumpError` reads as "returns an `i32`, or fails with a
`JumpError`" — that is, `Result@(i32, JumpError)`. Because an error type is an enum, the `match`
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
error types must match, or the program declares a conversion between them (see
[Converting between error types](#converting-between-error-types)). `??` converts nothing
on its own.

`??` takes a `Result` and nothing else. On any other value it is **CE2507**.

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

### From a `Maybe` to an error: `or_err`

A `Maybe` says that a value is absent, but not why. It holds no error value, so `??` on a
`Maybe` is **CE2507**. `.or_err(nom e)` writes the error value: it turns `Maybe.Some(value)`
into `Result.Ok(value)` and `Maybe.None()` into `Result.Err(e)`. Then `??` propagates it.
An array's `.get(i)` returns a `Maybe`, so `.get(i).or_err(nom e)??` is a short way to say
"stop with this error if there is no element here":

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/maybe-propagation.sushi"
```

Output:

```
second of three, doubled: 42
second of one, doubled:   -1
```

The argument carries `nom`, because the error value moves into the `Result`.

## Converting between error types

A program that calls two modules gets two error types. A conversion joins them. Declare it
once, beside the target type, with `extend <Source> as <Target>:`, and every `??` that
meets the pair calls it:

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/conversion.sushi"
```

Output:

```
warp 2
refused: AppError.Parse(ParseError.NotADigit)
refused: AppError.TooFast
```

`parse_digit` fails with a `ParseError`, and `warp_factor` answers `AppError`. Without the
`extend ParseError as AppError:` block, the `??` in `warp_factor` is **CE2511**, and the
help names the declaration to write. The body is an ordinary function body: `self` is the
`ParseError`, and it returns the `AppError`.

- `e as AppError` calls the same conversion on one value.
- A conversion is one step. `A as B` and `B as C` do not give `A` to `C`.
- Only the unit that declares the target type may declare a conversion into it
  (**CE2519**). The stdlib declares `FileError as IoError` and `NetError as IoError`, so a
  function that answers `IoError` can `??` a call that answers `FileError`.
- For one call, `.map_err(f)` converts with no declaration:
  `parse_digit(s).map_err(|nom ParseError e| AppError.Parse(e))??`.

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

## `assert`: for a bug, not for an error

Some failures are not errors at all: they are bugs. `average()` below has no answer for an
empty array, and a caller that passes one has made a mistake. A channel would make every
caller handle a case that a correct program never reaches. `assert` states the rule
instead, and stops the program when it is broken:

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/assert.sushi"
```

Output, when you build and run it in `docs/tutorial/examples/06-error-handling/`:

```
42
Runtime Error RE2026: assertion failed at ./assert.sushi:2:5: average() needs at least one score
```

The program exits with code 1. The message is optional (`assert(cond)` alone prints the
position), and it can be any `string`, an interpolation included. The program builds the
message only when the condition is false.

Choose with one question: **can the caller do something about it?** If yes, it is an
error: write the channel and return `Result.Err(...)`. If no, it is a bug: `assert`.

## What you learned

- A function that can fail writes an error channel, `fn f() T | E:`. The call gives
  `Result@(T, E)`, and the body returns `Result.Ok(value)` or `Result.Err(error)`. The error
  type is declared with `error` (`CE2084` for any other type).
- A function without `| E` is bare: it returns the value, and the call gives the value.
  There is no default error type. Use the bare form only for a total function.
- `??` unwraps `Ok` or propagates `Err` from the enclosing function — RAII-safe, zero-cost,
  and legal only in a body with a channel. It takes a `Result` only: on a `Maybe`, write the
  error value with `.or_err(nom e)??`.
- `extend A as B:` declares a conversion, and `??` and `as` call it. `.map_err(f)`
  converts at one call.
- `.realise(default)` unwraps with a fallback; `if (result.is_ok()):` splits Ok from Err.
- `Maybe@(T)` (`Maybe.Some` / `Maybe.None`) models presence vs. absence — Sushi's `null`
  replacement — with `.is_some()`, `.is_none()`, `.realise()`, and `.expect()`.
- `main` is bare and returns the exit code. `??` in `main` is **CE0131**; handle errors
  there with `match`, `.realise()`, or `if (result.is_ok()):` instead.
- `assert(cond, message)` stops the program with **RE2026** when an invariant is false.
  It is for a bug; the channel is for an error that a caller can handle.

Next we put values in bulk. On to [Arrays](07-arrays.md).
