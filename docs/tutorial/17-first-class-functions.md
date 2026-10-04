# 17. First-Class Functions

So far a function has been something you *call*. In this chapter a function becomes something you
can *hold*: store it in a variable, pass it to another function, keep a whole `List` of them, and
call through any of those. That one idea — functions as values — is what turns a pile of `if`/
`match` dispatch into clean, data-driven code.

This builds on [Chapter 4 (Functions)](04-functions.md), [Chapter 6 (Error Handling)](06-error-handling.md),
and [Chapter 13 (Collections)](13-collections.md). If you know Python's "functions are objects" or
C's function pointers, you already have the intuition. Sushi's version is typed, and a plain
function reference carries no captured state.

## A function as a value

Write a function's **name without parentheses** and you get a *function value* — a reference to
that function. Its type is a **function type**, written `fn(params) -> return`:

```sushi
--8<-- "docs/tutorial/examples/17-first-class-functions/pass-and-call.sushi"
```

Output:

```
42
42
```

Three things to notice:

- **`let fn(i32) -> i32 f = add_one`** binds the function value. The type reads "takes an `i32`,
  returns an `i32`" — the same shape as `add_one`'s signature.
- **`apply(fn(i32) -> i32 op, i32 v)`** takes a function *as a parameter*. Inside, `op(v)` calls
  through it.
- Calling through a function value gives what a direct call gives. `fn(i32) -> i32` is a
  **bare** function type, so `op(v)` gives an `i32`, and `apply` returns it with
  `return op(v)`. A function type has an error channel only when it writes `| E` (see
  below).

!!! note "A plain reference vs. a closure"
    A plain function reference like `add_one` above carries no captured variables. It points to
    the compiled function, with no allocation and no cleanup. Sushi also has
    [closures](../closures.md): a lambda literal that *does* capture surrounding variables, covered
    in the next chapter. Both are `fn(...)`-typed values with identical call syntax.

## A dispatch table with `List`

Because a function value is an ordinary value, you can put a bunch of them in a `List` and iterate
— a *dispatch table* or *pipeline*:

```sushi
--8<-- "docs/tutorial/examples/17-first-class-functions/dispatch-table.sushi"
```

Output:

```
14
```

`List@(fn(i32) -> i32)` is a list whose element type is a function type. `foreach` hands you each
stored function in turn, and `step(acc)` calls through it. An array of functions puts the
function type in parentheses: `(fn(i32) -> i32)[]`, or `(fn(i32) -> i32)[3]` for a fixed array.
Without the parentheses, the `[]` belongs to the return type: `fn(i32) -> i32[]` is a function
that returns `i32[]`.

## Functions in a struct

A function value can be a struct field — handy for bundling a name, some config, and the behavior
to run:

```sushi
--8<-- "docs/tutorial/examples/17-first-class-functions/struct-field.sushi"
```

Output:

```
square
49
```

You can call the field **directly** — `op.run(7)`. When a struct has a fn-typed field and no
method of the same name, `op.run(7)` routes to the function stored in the field. (If a method
`run` also existed, the method would win. To call the field in that case, bind it to a typed
local first: `let fn(i32) -> i32 f = op.run`.)

## The error type travels with the function

A function type can write an error channel, just like a declaration does with `| E`. The
channel is part of the type, so it propagates correctly through an indirect call:

```sushi
--8<-- "docs/tutorial/examples/17-first-class-functions/custom-error.sushi"
```

Output:

```
42
-1
```

`fn(i32, i32) -> i32 | DivError` says the function can fail with a `DivError`. Inside `run`, the
`op(x, y)??` propagates that error out, and the caller turns it into a default with `.realise(-1)`.
Omit the `| E` and the function type is bare: the call gives the value, and `??` on it is
**CE2507**, exactly as for a normal `fn`. A bare type and a type with a channel do not
convert: `fn(i32) -> i32` and `fn(i32) -> i32 | E` are different types (**CE2002**).

The functions in this chapter are bare because they are total: `add_one` cannot fail. A
bare function is the exception. Write a channel for a function that can fail, now or later.
[The error channel](../design/error-channel.md) gives the rule.

A function with a channel that returns a function type writes the explicit form,
`fn make() Result@(fn(i32) -> i32, StdError):`. A `| E` written after a function type
belongs to that function type, so `fn make() fn(i32) -> i32 | StdError:` is a bare
function that returns a function type with a channel.

## Calling through any expression

A function value doesn't have to sit in a plain variable to be called. You can call through any
expression that produces one — a `List` element or a parenthesized expression. (This
fragment is the body of a function with a channel: `.or_err(nom StdError.Error)` turns the
`Maybe` that `.get(0)` gives into a `Result`, and `??` unwraps it.)

```sushi
let List@(fn(i32) -> i32) table = List.new()
table.push(add_one)
let i32 a = table.get(0).or_err(nom StdError.Error)??(41)      # call the retrieved function value
let i32 b = (table.get(0).or_err(nom StdError.Error)??)(41)    # same, parenthesized
```

## Referencing a generic function

A **generic** function can be a value when the position states a function type. The function
type chooses the instantiation:

```sushi
fn identity@(T)(T x) T:
    return x

fn apply(fn(i32) -> i32 op, i32 v) i32:
    return op(v)

let fn(i32) -> i32 g = identity      # identity@(i32), chosen by the annotation
let i32 n = g(41)                    # 41
let i32 m = apply(identity, 42)      # the parameter type chooses identity@(i32)
```

This works in every position that states the function type: a typed `let`, a rebind, an
argument to a `fn(...)` parameter, a `return` from a function that returns a function type, a
struct field and an enum payload. A position with no function type (for example, an argument
to a generic parameter `T`) cannot choose the instantiation, and the reference is
**CE2093**.

## What else the compiler checks

- A wrong-shaped call through a function value (wrong arity or argument type) → **CE2092**.
- Assigning a function value to an incompatible function-typed variable → **CE2002**. Function
  types are *invariant*: arity, every parameter and its mode, the return type, and the error type
  must match exactly. `fn(nom string) -> i32` and `fn(string) -> i32` are different types.

Extension methods, perk methods, and C externals aren't bare-referenceable at all — they have
different calling conventions, so a bare name that isn't a plain function is just an undeclared
identifier.

## What you learned

- A **function value** is a function's name used without `()` — its type is a **function type**
  `fn(params) -> return [| Error]`. Without `| Error` the type is bare.
- You can **store** function values (variables, struct fields, `List@(fn(...))`), **pass** them as
  arguments, and **call through** them; an indirect call gives what a direct one gives.
- A plain function reference has **no captured state**: no allocation and no cleanup. Sushi
  also has **closures** (capturing lambda literals) — see the next chapter.
- Call a function-valued **struct field** directly (`obj.field(x)`); a same-named method would win.
  You can also call through any expression that yields a function value (`table.get(0).or_err(nom StdError.Error)??(x)`).
- Reference a **generic function** as a value in any position that states the function type
  (`let fn(i32) -> i32 g = identity`, `apply(identity, 42)`); a position with no function
  type is **CE2093**.
- The **error channel is part of the function type** and propagates through `??`.
- A call-through mismatch is **CE2092**, and an assignment mismatch is **CE2002**.

That's functions-as-data. Next, [Chapter 18 (Closures)](18-closures.md) adds the capturing lambda
literal. For the complete reference on this chapter's material, see the
[First-Class Functions guide](../first-class-functions.md) and the
[design note](../design/closures.md#1-function-types-and-values-non-capturing).
