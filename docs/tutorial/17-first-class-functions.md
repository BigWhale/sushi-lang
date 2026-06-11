# 17. First-Class Functions

So far a function has been something you *call*. In this chapter a function becomes something you
can *hold*: store it in a variable, pass it to another function, keep a whole `List` of them, and
call through any of those. That one idea — functions as values — is what turns a pile of `if`/
`match` dispatch into clean, data-driven code.

This builds on [Chapter 4 (Functions)](04-functions.md), [Chapter 6 (Error Handling)](06-error-handling.md),
and [Chapter 13 (Collections)](13-collections.md). If you know Python's "functions are objects" or
C's function pointers, you already have the intuition — Sushi's version is typed and compiles to a
bare pointer with zero overhead.

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
- Calling through a function value returns a `Result` just like calling the function directly, so
  the familiar `??` works on `op(v)`.

!!! note "It's just a pointer"
    A function value carries no captured variables — it is the bare address of the compiled
    function. There are **no closures** yet (a closure remembers surrounding variables; this
    doesn't). That keeps function values free: one pointer, no allocation, no cleanup.

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

`List<fn(i32) -> i32>` is a list whose element type is a function type. `foreach` hands you each
stored function in turn, and `step(acc)` calls through it. (Use `List<fn(...)>` rather than a raw
array for a collection of functions — in `fn() -> T[]` the `[]` belongs to the return type `T[]`,
so there is no "array of functions" syntax.)

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

One wrinkle worth remembering: you **bind the field to a local before calling it**. Writing
`op.run(7)` directly would parse as *"call the method `run` on `op`"*, not *"call the function
stored in the field `run`"*. So `let fn(i32) -> i32 f = op.run` first, then `f(7)`.

## The error type travels with the function

A function type can spell out a custom error type, just like a declaration does with `| E`. That
error type is part of the type, so it propagates correctly through an indirect call:

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
Omit the `| E` and the error type is the implicit `StdError`, exactly as for a normal `fn`.

## What you can and can't reference (yet)

Only **plain top-level functions** are referenceable as values in v1. The compiler will stop you,
clearly, in the other cases:

- A **generic** function (`identity<i32>`) → **CE2093** (deferred to a later release).
- A wrong-shaped call through a function value (wrong arity or argument type) → **CE2092**.
- Assigning a function value to an incompatible function-typed variable → **CE2002**. Function
  types are *invariant*: arity, every parameter, the return type, and the error type must match
  exactly.

Extension methods, perk methods, and C externals aren't bare-referenceable at all — they have
different calling conventions, so a bare name that isn't a plain function is just an undeclared
identifier.

## What you learned

- A **function value** is a function's name used without `()` — its type is a **function type**
  `fn(params) -> return [| Error]`.
- You can **store** function values (variables, struct fields, `List<fn(...)>`), **pass** them as
  arguments, and **call through** them; an indirect call returns a `Result` just like a direct one.
- Function values are **bare pointers** — zero-cost, no captured state, **no closures** in v1.
- Call a function-valued **struct field** by binding it to a local first (`obj.field()` is a method
  call).
- The **error type is part of the function type** and propagates through `??`.
- Only **plain top-level functions** qualify; generic-function references are **CE2093**, a
  call-through mismatch is **CE2092**, and an assignment mismatch is **CE2002**.

That's functions-as-data. For the complete reference — every form, the compilation model, and the
roadmap toward closures — see the [First-Class Functions guide](../first-class-functions.md) and
the [design note](../design/first-class-functions.md).
