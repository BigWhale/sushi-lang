# 18. Closures

Chapter 17 gave you function values that are bare pointers — no memory of where they came from.
This chapter adds the piece that was missing: a lambda literal that **captures** the variables
around it, so a function value can carry a little state of its own.

This builds directly on [Chapter 17 (First-Class Functions)](17-first-class-functions.md). If
you've used closures in Python, JavaScript, or Rust, the shape will feel familiar; Sushi's version
is typed, and — for now — captures by value only.

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
would have — a closure and a bare function pointer are interchangeable wherever a `fn(...)` value
is expected.

!!! note "Captured by copy"
    `n` is captured by **value** — the lambda gets its own copy at the moment it's created, stored
    in a small heap-allocated environment. Mutating `n` afterward in `main` would not change what
    `f` sees (and vice versa).

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
alive. This is the core new capability closures add over v1's bare function pointers, which had
nothing to capture and therefore nothing that could outlive its scope.

## What you can't capture (yet)

Two things are rejected at compile time, both as **CE2094**:

- **A `&peek`/`&poke` borrow.** Threading a borrow's exclusivity guarantee through an escaping
  closure is a harder problem, deferred to a later Tier.
- **An owned value** — a dynamic array, `List<T>`, or `Own<T>`. Capturing one safely needs
  move-capture (the outer binding gets consumed) plus environment cleanup on drop; neither is
  wired up yet, so the compiler rejects it rather than risk a use-after-free.

```sushi
fn main() i32:
    let i32[] nums = from([1, 2, 3])
    let fn(i32) -> i32 f = |i32 x| x + nums.len()   # CE2094
    return Result.Ok(0)
```

Copy-capture — primitives, strings, copyable structs — is unaffected by either restriction.

One more honest gap: a capturing closure's heap environment is not freed yet (the cleanup pass
hasn't landed). It's safe — no double-free — just not memory-clean, so avoid building closures in
a hot loop for now.

## What you learned

- A **lambda literal** (`|params| expr`, or `|params|: <block>` as a `let` RHS) is an anonymous
  function value that can **capture** locals from its enclosing scope, by value copy.
- `|~|` is the zero-parameter form (`||` isn't usable — the lexer reads it as `or`).
- A capturing closure's environment is **heap-allocated**, so the closure can **escape** — be
  returned or stored — and still work correctly.
- Capturing a **borrow** or an **owned value** is rejected today (**CE2094**); only copyable data
  captures.
- A closure and a plain function value share the exact same type (`fn(...) -> T [| E]`) and call
  semantics — everything from Chapter 17 about parameters, struct fields, `List<fn(...)>`, and
  error types applies unchanged.

For the full picture — the fat-pointer representation, every capture rule, and what's still
deferred — see the [Closures guide](../closures.md) and the [design note](../design/closures.md).
