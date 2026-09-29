# 18. Closures

Chapter 17 gave you plain function values, with no memory of where they came from.
This chapter adds the piece that was missing: a lambda literal that **captures** the variables
around it, so a function value can carry a little state of its own.

This builds directly on [Chapter 17 (First-Class Functions)](17-first-class-functions.md). If
you've used closures in Python, JavaScript, or Rust, the shape will feel familiar; Sushi's version
is typed, and captures plain values by copy and owning values by move.

## A capturing lambda

A **lambda literal** is written between pipes: `|params| expr`. Unlike a plain function value, it
can read a local from its enclosing scope:

```sushi
--8<-- "docs/tutorial/examples/18-closures/capture-basic.sushi"
```

Output:

```
15
```

`|i32 x| x + n` is an anonymous function that takes one `i32` parameter and reads the outer local
`n`. Assigning it to `fn(i32) -> i32 f` gives it exactly the same type a plain function reference
would have. A closure and a plain function reference are interchangeable wherever a `fn(...)`
value is expected.

!!! note "Captured by copy"
    `n` is a primitive, so it is captured by **value** — the lambda gets its own copy at the moment
    it's created, stored in a small heap-allocated environment. Mutating `n` afterward in `main`
    would not change what `f` sees (and vice versa). Owned values are captured by *move* instead —
    see [Capturing owned values](#capturing-owned-values) below.

## Bare parameters and multiple captures

A lambda parameter can omit its type (`|x|` instead of `|i32 x|`) when the surrounding context
already pins it down — here, the `let fn(i32) -> i32` annotation. A lambda body can also capture
more than one outer local:

```sushi
--8<-- "docs/tutorial/examples/18-closures/bare-param.sushi"
```

Output:

```
17
```

The same bare-parameter inference works when a lambda is passed directly as a call argument to a
`fn(...)`-typed parameter — no annotation needed on the lambda itself, since the callee's
parameter type supplies it.

## Escaping closures

Because a capturing lambda's environment lives on the heap (not on the stack frame that created
it), the closure can be **returned** and called long after its creating function has returned:

```sushi
--8<-- "docs/tutorial/examples/18-closures/escaping.sushi"
```

Output:

```
15
```

`make_adder` returns a closure that captured its parameter `n`; by the time `add5(10)` runs,
`make_adder`'s own stack frame is long gone, but the heap environment holding `n = 5` is still
alive. A plain function reference has nothing to capture, so this is the new capability that
closures add.

## Capturing owned values

A **plain** value is captured by **copy**: a primitive, a string bound directly from a literal,
and a struct or enum that owns nothing. An **owning** value is captured by **move**: a dynamic
array, `List@(T)`, `HashMap@(K, V)`, `Own@(T)`, a string that owns heap (for example, one built
by interpolation), a type that implements `Drop`, and a struct or enum that holds one of these.
The environment takes ownership, so the outer binding is consumed (a later use is **CE2405**),
and the value is freed with the closure's environment:

```sushi
fn main() i32:
    let i32[] nums = from([1, 2, 3])
    let fn(i32) -> i32 f = |i32 x| x + nums.len()   # moves nums into the closure
    println(f(10).realise(-1))                      # 13
    return Result.Ok(0)
```

A closure can even capture and call **another closure**, so you can build one function out of
another (this is exactly what `compose` in [Chapter 19](19-higher-order-combinators.md) does).

The environment is **freed automatically** on every exit path — scope exit, an early `return`, or
`??` — including when a closure is returned or stored in a `List`. No leaks, no double-frees.

## What you cannot capture

Capturing a **`peek`/`poke` borrow** is refused at compile time with **CE2094**. Pass the
borrowed data as a parameter to the closure; do not capture the borrow.

A **lambda parameter** cannot have an owning type either: `|i32[] a| a.len()` is also
**CE2094**. To pass a function over owning values, write a named function and use a
function reference (chapter 17).

## What you learned

- A **lambda literal** (`|params| expr`, or `|params|: <block>` as a `let` RHS) is an anonymous
  function value that can **capture** locals from its enclosing scope.
- `|~|` is the zero-parameter form (`||` isn't usable — the lexer reads it as `or`).
- A capturing closure's environment is **heap-allocated**, so the closure can **escape** — be
  returned or stored — and still work correctly, and it is **freed automatically** on every exit.
- Plain values (primitives, literal-bound strings, structs that own nothing) are captured by
  **copy**; owning values (a heap string, a dynamic array, `List@(T)`, `Own@(T)`, and more) by
  **move** (the outer binding is consumed). Capturing a **borrow** is **CE2094**, and so is a
  lambda parameter of an owning type.
- A closure and a plain function value share the exact same type (`fn(...) -> T [| E]`) and call
  semantics — everything from Chapter 17 about parameters, struct fields, `List@(fn(...))`, and
  error types applies unchanged.

For the full picture — the fat-pointer representation, every capture rule, and what's still
deferred — see the [Closures guide](../closures.md) and the [design note](../design/closures.md).
