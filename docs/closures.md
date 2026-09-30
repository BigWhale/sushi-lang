# Closures

[← Back to Documentation](index.md)

This guide is about closures and lambda literals in Sushi. A lambda is an anonymous function
value that can **capture** values from the scope that encloses it. A closure has the same type
and the same call syntax as a bare function value (see
[First-Class Functions](first-class-functions.md)). The [design note](design/closures.md) gives
the representation and the work that is not done.

## Table of Contents

- [Overview](#overview)
- [Lambda syntax](#lambda-syntax)
- [Capture](#capture)
- [Escaping closures](#escaping-closures)
- [How it compiles](#how-it-compiles)
- [Error codes](#error-codes)
- [Limitations](#limitations)

## Overview

A **lambda literal** is an anonymous function value that you write inline. It can read the
locals of the function that holds it:

```sushi
fn make_adder(i32 n) fn(i32) -> i32:
    return |i32 x| x + n     # captures n by value

fn use_adder() i32:
    let fn(i32) -> i32 add5 = make_adder(5)
    return add5(10)        # 15

fn main() i32:
    println(use_adder())
    return 0
```

A closure is a `fn(...)`-typed value, the same as a bare function reference. It has the same
type, the same call syntax and the same `Result` semantics. The difference: a lambda can capture
the variables around it, and you can return or store the value, so it can live longer than the
scope that made it.

## Lambda syntax

There are two body forms:

```sushi
# expression body: a general expression, usable as a let RHS or a call argument
let fn(i32) -> i32 f = |i32 x| x + n

# block body: a full fn body -- allowed ONLY as a `let` RHS
let fn(i32) -> i32 g = |i32 x|:
    let i32 y = x * 2
    return Result.Ok(y + n)

# zero parameters: |~|, not ||  (the lexer reads `||` as the `or` operator)
let fn() -> i32 h = |~| n + 1
```

The block form ends with a dedent and no token after it. Thus the grammar accepts it only where
this is not ambiguous: the right side of a `let`. A block-body lambda as a call argument is a
parse error (CE6001). Use the expression form, or bind the lambda to a `let` first.

Parameters use the Sushi `type name` form (`|i32 x, string s|`). A **bare-name** parameter
(`|x|`, with no type) is legal only where an expected `fn(...)` type gives the type: an annotated
`let`, or a call argument to a `fn(...)`-typed parameter:

```sushi
fn apply(fn(i32) -> i32 f, i32 v) i32:
    return f(v)

fn main() i32:
    let i32 scale = 3
    println(apply(|x| x * scale, 7))   # x : i32 inferred from apply's signature -> 21
    return 0
```

A call through a closure gives a `Result@(T, E)`, the same as a `fn`. Use `??`, `.realise()` or a
`match` on it. A `Result` is not a condition: `if (f(1))` is CE2516. Use `.is_ok()` to test it.

- **Expression body.** `|x| e` becomes `return Result.Ok(e)`. The compiler adds the `Ok`.
- **Block body.** It is a full function body. Every path must end with a `return`: a block body
  that can reach its end is CE0107. A body that returns `~` ends with `return Result.Ok(~)`.
  After the closing pipe, the block form can have a `-> T [| E]` annotation, the same as a `fn`
  declaration.

The expression body wraps its value in `Ok`. Thus a fallible call in an expression body needs its
own `??`. If the inner `Result` goes out as it is, the wrap makes a
`Result@(Result@(T, E), E)`, and the types do not agree. For this reason, `compose` is written
`|x| f(g(x)??)??` and not `|x| f(g(x)??)`.

## Capture

A value that does not own a resource is captured **by copy** into an environment on the heap.
This includes the primitives, a string bound directly from a literal, and a struct or a fixed
array that holds no owning field:

```sushi
fn main() i32:
    let i32 a = 3
    let i32 b = 4
    let fn(i32) -> i32 f = |x| x + a + b
    println(f(10))    # 17 -- both a and b captured
    return 0
```

A value that owns a resource is captured **by move**. This includes a `string` that owns heap
memory (for example, an interpolated string), a dynamic array, a `List@(T)`, an `Own@(T)` and
every other owning type. The outer binding is consumed: a later use of it is CE2405. The
environment becomes the only owner, and it frees the value when the closure is freed:

```sushi
fn main() i32:
    let i32[] nums = from([1, 2, 3])
    let fn() -> i32 f = |~| nums.len()   # moves nums into f's environment
    println(f())              # 3
    return 0
```

The compiler refuses two shapes with **CE2094**:

- **The capture of a `peek`/`poke` borrow.**
- **A lambda parameter of an owning container type**: a dynamic array, a `List@(T)` or an
  `Own@(T)`. See [Limitations](#limitations). A `string` parameter is legal
  (`|string s| s.len()`).

## Escaping closures

The captured environment is on the heap, not on the stack. Thus you can return a closure from
the function that made it, or store it in a struct or a `List`, and call it later. The
`make_adder` example above shows this.

## How it compiles

A function value is a **four-word fat pointer** `{fn_ptr, env_ptr, drop_ptr, clone_ptr}`
(32 bytes):

- A value that captures nothing (a plain `fn` reference, or a lambda that reads nothing from its
  scope) has a null `env_ptr`, `drop_ptr` and `clone_ptr`.
- A capturing lambda allocates an environment struct on the heap that holds the captured values.
  `fn_ptr` points to the lifted function that the compiler makes. A call through the value passes
  `env_ptr` as a hidden first argument. `drop_ptr` frees the environment, and `clone_ptr` copies
  it for `.clone()`.

Function types are **invariant**, and the capture is not part of the type: `fn(i32) -> i32` names
a plain `fn` and every closure of that shape. A mismatch is **CE2002** (assignment) or **CE2092**
(call-through).

A function type DOES carry the **mode** of each parameter, and it is invariant on the mode:
`fn(nom string) -> i32` and `fn(string) -> i32` are different types in the two directions, and so
are `fn(peek T)` and `fn(poke T)`. Otherwise one indirection could go around the mode rule. See
[docs/design/borrow-model.md](design/borrow-model.md).

**A closure VALUE is an owning value.** When you pass one to an unmarked parameter, the callee
borrows it, and the caller keeps and frees the environment. When you pass it to a `nom`
parameter, the callee gets the environment. `compose(nom g, nom f)` in `<collections/iter>` needs
`nom`: the closure that it returns captures the two arguments, so it becomes their owner.

## Error codes

| Code | Meaning |
| --- | --- |
| **CE2094** | illegal closure capture: a `peek`/`poke` borrow, or an owning container or variadic lambda-parameter type |
| **CE2093** | a generic function value that no position type solves |
| **CE0107** | a block-body lambda that can reach its end with no `return` |
| **CE2427** | a `nom` marker on a function-value argument that does not agree with the declared mode of the callee |
| **CE2092** | function value type mismatch at call-through |
| **CE2002** | function value assigned to an incompatible function-typed variable |

## Limitations

- **A lambda parameter of type `List@(T)`, `Own@(T)` or a dynamic array** is refused (CE2094).
  The indirect-call path has no deep copy for it. This is not the same as a *capture*, which
  moves owned values (see [Capture](#capture)).
- **Nested lambdas** (a lambda in the body of another lambda) are lifted, but a deep chain of
  nested captures is not guaranteed to work.
- **Not available**: the capture of a `peek`/`poke` borrow, bound method values (`obj.method` as
  a bare callable), and C callbacks.

These work:

- A generic function as a value, in every position where the type is solved: an annotated `let`,
  an argument, a rebind, a `return`, a field, a payload, a `.realise()` default, behind an alias
  and to a generic callee. Only a value that no position type solves is **CE2093**.
- A call through a fn-typed struct field, a container get-out, a call result, a parenthesized
  expression and a captured closure value.
- The method form of the combinators: `use <collections/iter>` gives `.map`, `.filter` and
  `.fold` as extension methods on `List@(T)` and `T[]`, beside the free functions. You chain them
  with `??` (`xs.map(f)??.filter(p)??`).

The [design note](design/closures.md) gives the fat-pointer ABI and the implementation anchors.
