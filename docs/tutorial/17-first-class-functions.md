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

!!! note "A plain reference vs. a closure"
    A plain function reference like `add_one` above carries no captured variables — it's the bare
    address of the compiled function, with no allocation and no cleanup. Sushi also has
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

You can call the field **directly** — `op.run(7)`. When a struct has a fn-typed field and no
method of the same name, `op.run(7)` routes to the function stored in the field. (If a method
`run` also existed, the method would win; bind the field to a local first — `let f = op.run` — to
call the field in that case.)

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

## Calling through any expression

A function value doesn't have to sit in a plain variable to be called. You can call through any
expression that produces one — a `List` element or a parenthesized expression:

```sushi
let List<fn(i32) -> i32> table = List.new()
table.push(add_one)
let i32 a = table.get(0)??(41)??      # call the retrieved function value
let i32 b = (table.get(0)??)(41)??    # same, parenthesized
```

## Referencing a generic function

A **generic** function can be referenced as a value when you give the binding an explicit function
type — the annotation fixes which instantiation you mean:

```sushi
fn identity<T>(T x) T:
    return Result.Ok(x)

let fn(i32) -> i32 g = identity      # identity<i32>, chosen by the annotation
let i32 n = g(41)??                  # 41
```

Without an expected function type — for instance passing `identity` straight into a call argument
with no typed binding — the reference is still **CE2093**; bind it to a typed local first.

## What else the compiler checks

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
- A plain function reference is a **bare pointer** — zero-cost, no captured state. Sushi also has
  **closures** (capturing lambda literals) — see the next chapter.
- Call a function-valued **struct field** directly (`obj.field(x)`); a same-named method would win.
  You can also call through any expression that yields a function value (`table.get(0)??(x)`).
- Reference a **generic function** as a value when an explicit function type is present
  (`let fn(i32) -> i32 g = identity`); a bare reference with no expected type is **CE2093**.
- The **error type is part of the function type** and propagates through `??`.
- A call-through mismatch is **CE2092**, and an assignment mismatch is **CE2002**.

That's functions-as-data. Next, [Chapter 18 (Closures)](18-closures.md) adds the capturing lambda
literal. For the complete reference on this chapter's material, see the
[First-Class Functions guide](../first-class-functions.md) and the
[design note](../design/closures.md#1-the-v1-floor-function-types-and-values-non-capturing).
