# First-Class Functions

[← Back to Documentation](index.md)

Complete guide to first-class functions in Sushi: **function types** (`fn(i32) -> i32`) and
**function values** — referencing a named function, storing it, passing it, and calling through
it. This is the v1 feature: zero-cost bare function pointers. Sushi now also has
[closures](closures.md) (capturing lambda literals); this guide covers the non-capturing floor
both share.

## Table of Contents

- [Overview](#overview)
- [Function types](#function-types)
- [Function values](#function-values)
- [Calling through a function value](#calling-through-a-function-value)
- [Functions in data structures](#functions-in-data-structures)
- [Custom error types](#custom-error-types)
- [How it compiles](#how-it-compiles)
- [Type compatibility](#type-compatibility)
- [Error codes](#error-codes)
- [Limitations and deferred features](#limitations-and-deferred-features)

## Overview

A **function value** lets you treat a function like any other value: bind it to a variable, put
it in a struct field or a `List`, pass it to another function, and call it indirectly. This
replaces hand-rolled `match`-based dispatch with ordinary data — the building block for callback
APIs, dispatch tables, and visitor-style code.

```sushi
fn add_one(i32 x) i32:
    return Result.Ok(x + 1)

fn apply(fn(i32) -> i32 f, i32 v) i32:
    return Result.Ok(f(v)??)

fn main() i32:
    let fn(i32) -> i32 g = add_one     # reference a function by name
    let i32 out = apply(g, 41).realise(0)   # pass it, call through it -> 42
    println(out)
    return Result.Ok(0)
```

A plain function reference like `add_one` above carries no captured state — it is a **bare
function pointer**, the raw address of an already-compiled function. Sushi also has
[closures](closures.md): a lambda literal (`|x| ...`) that *does* capture its enclosing scope. The
two share the same function type and call syntax (see [below](#limitations-and-deferred-features)).

## Function types

A function type names the shape of a callable. It mirrors the function-declaration syntax for
return and error types:

| Syntax | Meaning |
| --- | --- |
| `fn(i32) -> i32` | takes an `i32`, returns `i32`, error type implicitly `StdError` |
| `fn(i32, string) -> bool` | two parameters, returns `bool` |
| `fn() -> ~` | no parameters, blank (`~`) return |
| `fn(i32) -> i32 \| MathError` | explicit custom error type (the `\| E` mirrors `fn f() i32 \| MathError`) |

The arrow `->` is required, and the return type is mandatory. Function types nest and compose
like any other type — they work as parameter types, struct fields, and generic type arguments
(`List<fn(i32) -> i32>`).

## Function values

A function value is produced by writing a **plain top-level function's name** in value position
(no call parentheses):

```sushi
fn double(i32 x) i32:
    return Result.Ok(x * 2)

fn main() i32:
    let fn(i32) -> i32 f = double    # `double` here is a value, not a call
    return Result.Ok(0)
```

Only **plain top-level functions** can be referenced this way. Extension methods, perk methods,
and FFI externals have different calling conventions and are not bare-referenceable; **generic**
functions are deferred (see [error codes](#error-codes)).

## Calling through a function value

Call a function value exactly like a named function — `f(args)`. Because every Sushi function
returns `Result<T, E>`, an indirect call yields the same `Result` a direct call would, so `??`,
`if (result)`, and pattern matching all work unchanged:

```sushi
fn run_twice(fn(i32) -> i32 f, i32 v) i32:
    let i32 once = f(v)??
    let i32 twice = f(once)??
    return Result.Ok(twice)
```

## Functions in data structures

### Struct fields

A function value can be a struct field. To call it, bind the field to a local first — `obj.op()`
parses as a *method* call on `obj`, not as a call of the function-valued field `op`:

```sushi
struct Handler:
    fn(i32) -> i32 op

fn run(Handler h, i32 v) i32:
    let fn(i32) -> i32 f = h.op    # bind the field to a local
    return Result.Ok(f(v)??)       # then call through it
```

### Lists (dispatch tables)

`List<fn(...)>` is the idiomatic way to hold a collection of functions — a dispatch table you can
iterate:

```sushi
fn dispatch(List<fn(i32) -> i32> ops, i32 v) i32:
    let i32 acc = v
    foreach(f in ops.iter()):
        acc := f(acc)??
    return Result.Ok(acc)
```

`.get(i)` returns `Maybe<fn(...)>` just like any element type; unwrap it (`??` / `.realise`) and
bind to a local before calling. (Raw arrays of function pointers are not expressible — the `[]`
in `fn() -> T[]` binds to the return type `T[]` — so use `List<fn(...)>` for collections.)

## Custom error types

The error type is part of the function type, so it threads through an indirect call correctly:

```sushi
enum DivError:
    DivByZero

fn safe_div(i32 a, i32 b) i32 | DivError:
    if (b == 0):
        return Result.Err(DivError.DivByZero)
    return Result.Ok(a / b)

fn run(fn(i32, i32) -> i32 | DivError op, i32 x, i32 y) i32 | DivError:
    return Result.Ok(op(x, y)??)    # propagates DivError out of the indirect call
```

A function whose type omits `| E` has the implicit `StdError` error type, exactly like an
ordinary `fn f() T` declaration.

## How it compiles

A function value lowers to a **three-word fat pointer** `{fn_ptr, env_ptr, drop_ptr}` (this
widened from a bare one-word pointer when closures were added; see the
[closures guide](closures.md#how-it-compiles) for the full picture):

- A Sushi `fn add(i32) i32` lowers to an LLVM function with signature
  `Result<i32, StdError>(i32)`. Referencing it as a value builds `{f__thunk, null, null}` — a small
  adapter thunk address, with `env_ptr`/`drop_ptr` null.
- Calling through a function value is a single indirect `call`, with `env_ptr` passed as a hidden
  leading argument (ignored by the thunk for a plain reference).
- For a **non-capturing** value — everything on this page — there is no environment allocation and
  no cleanup: the null `env_ptr`/`drop_ptr` make storage and destruction a no-op, so this stays
  effectively zero-cost. A **capturing** lambda instead heap-allocates an environment; that's the
  closures feature, not covered here.

## Type compatibility

Function types are **invariant**: two are compatible only when the arity, every parameter type,
the return type, and the error type match exactly. There is no implicit conversion between
function types (no variance, no coercion). A mismatch is a clean diagnostic — see below.

## Error codes

| Code | Meaning |
| --- | --- |
| **CE2092** | function value type mismatch at a call-through — wrong arity, parameter type, return type, or error type |
| **CE2093** | illegal function reference — a **generic** function (deferred in v1) |
| **CE2002** | a function value assigned to a variable/parameter of an incompatible function type (the general assignment-mismatch error) |

Extension methods, perk methods, and FFI externals are not bare-referenceable at all: a bare name
that resolves to none of constant/variable/top-level-function is an undeclared identifier
(**CE1001**), and externals are reached only through their namespace.

## Limitations and deferred features

v1 is intentionally the smallest useful slice, designed so each deferred piece is **additive**:

- **Closures / lambda literals now exist** — see the [Closures guide](closures.md). Tier 1 covers
  copy-capture and escaping closures; borrow capture, move-capture of owned types, and stdlib
  combinators (`List.map`/`.filter`/`.fold`) remain outstanding there.
- **No generic-function references** (`identity<i32>`) — **CE2093**. Deferred until the
  instantiation can be forced and its mangled address taken.
- **Extension/perk-method and FFI-extern values** are not referenceable (different ABIs).
- **Call-through only via a `Name`.** Calling through an arbitrary expression — `(expr)()`,
  `arr[0]()`, `get_fn()()`, or a struct field `obj.handler()` directly — is deferred. Bind to a
  local first: `let f = arr.get(0)??` then `f(x)`.

The deeper design rationale, the options considered, and the migration path live in the
[First-Class Functions design note](design/first-class-functions.md) and the
[Closures design note](design/closures.md).
