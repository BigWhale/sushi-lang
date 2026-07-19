# 6. Error Handling

This is the chapter where Sushi's personality really shows. In Python a function that
fails throws an exception that may sail silently past three callers before someone catches
it (or no one does). In Java you juggle checked exceptions and `null`. Sushi takes a
different oath: **failure is a value, and the type system makes you deal with it.** A
function doesn't return "an `i32`, or maybe an explosion." It returns a `Result@(i32, E)` —
success *or* error, right there in the type — and the compiler won't let you forget which
one you're holding.

By the end of this chapter you'll understand `Result@(T, E)`, the `??` propagation operator,
the `Maybe@(T)` optional type, and the small set of patterns that keep `main` warning-free.

## Result&lt;T, E&gt;, and why it exists

You already met `Result` in Chapter 1: `main` ends with `return Result.Ok(0)`. That wasn't
ceremony. **Every** function in Sushi returns a `Result@(T, E)` — a value that is either:

- `Result.Ok(value)` — success, carrying a `T`, or
- `Result.Err(error)` — failure, carrying an `E`.

Here's a function that can fail, and two ways to handle the outcome at the call site:
`if (result):` (Ok runs the `if`, Err runs the `else`) and `.realise(default)` (covered
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

- The function's declared return type is `i32`, but the body returns `Result.Ok(...)` /
  `Result.Err(...)`. That's **implicit wrapping**: writing `fn halve(i32 n) i32` actually
  means `Result@(i32, StdError)`. You write the *value* type; Sushi wraps it in `Result` for
  you. (Recap from Chapter 4.)
- `StdError` is the built-in default error type. `StdError.Error` is its catch-all variant —
  fine for "something went wrong" when you don't need detail.

!!! note "Why not just exceptions?"
    Exceptions are invisible in a function's signature — you can't tell by looking whether a
    call might blow up. `Result@(T, E)` puts the failure mode in the type, so the compiler
    can *prove* you handled it. The cost is a little more typing; the payoff is whole
    categories of "I forgot that could fail" bugs that simply cannot compile.

## Custom error types

`StdError.Error` is fine for quick programs, but real code wants to say *what* went wrong.
Define an `enum` and name it as the error type with the `T | ErrorEnum` syntax. Now callers
can `match` on the specific variant.

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
    Use *either* the implicit form `fn f() T | MyError` *or* the fully explicit
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

## Maybe&lt;T&gt;: "a value, or nothing"

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

Because `find_index` is a normal function it returns `Result@(Maybe@(i32), StdError)` — two
layers. We peel the `Result` with `match`, then inspect the `Maybe` inside. (`Result.Err(_)`
uses `_` to ignore the bound error: a `match` arm for `Err` must bind something, and `_`
says "I don't care about it" without tripping an unused-variable warning.)

## Don't use `??` in `main()`

Here's the one rule that trips up newcomers. The `??` operator is wonderful in *helper*
functions, but using it in `main` triggers a compiler warning, **CW2511**:

> CW2511: `??` operator used in `main()` (consider explicit error handling)

Why discourage it? `main` is the top of the call stack — there's nowhere left to propagate
*to*. If `main` propagated an error, your program would exit with an opaque failure and no
explanation. Sushi nudges you to handle errors deliberately at the boundary instead. (In
this tutorial, treat any warning as a failure to fix — a clean build has exit code `0`.)

The fix is to consume results explicitly. There are three main-safe patterns:

- **`match`** — when you want to handle Ok and Err differently.
- **`.realise(default)`** — when a fallback value is enough.
- **`if (result):`** — for a quick Ok/else split.

```sushi
--8<-- "docs/tutorial/examples/06-error-handling/main-safe.sushi"
```

Output:

```
matched ok: 7
realise fallback: 0
if ok: 7
```

None of those use `??`, so the program builds cleanly. Save `??` for the helpers that
`main` calls — that's exactly where its early-return magic belongs.

!!! note "The shape of a tidy program"
    A common, comfortable structure: small helper functions that lean on `??` to chain
    fallible steps, and a `main` that calls them and resolves the final `Result` with
    `match` or `.realise()`. Errors propagate cleanly through the middle and get handled
    once, at the edge.

## What you learned

- Every Sushi function returns `Result@(T, E)`: `Result.Ok(value)` or `Result.Err(error)`.
- Writing `fn f() T` implicitly wraps to `Result@(T, StdError)`; `fn f() T | MyError` lets
  you supply a custom error enum.
- `??` unwraps `Ok` or propagates `Err` from the enclosing function — RAII-safe, zero-cost,
  and meant for helper functions (not `main`).
- `.realise(default)` unwraps with a fallback; `if (result):` splits Ok from Err.
- `Maybe@(T)` (`Maybe.Some` / `Maybe.None`) models presence vs. absence — Sushi's `null`
  replacement — with `.is_some()`, `.is_none()`, `.realise()`, and `.expect()`.
- Using `??` in `main` warns with **CW2511**; handle errors there with `match`,
  `.realise()`, or `if (result):` instead.

Next we put values in bulk. On to [Arrays](07-arrays.md).
