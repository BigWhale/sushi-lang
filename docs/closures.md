# Closures

[← Back to Documentation](index.md)

Guide to closures and lambda literals in Sushi: anonymous function values that **capture** their
enclosing scope. This is the Tier 1 slice — the minimal but real capability, built on the
[First-Class Functions](first-class-functions.md) floor. See the
[design note](design/closures.md) for the full tiered plan and what remains.

## Table of Contents

- [Overview](#overview)
- [Lambda syntax](#lambda-syntax)
- [Capture](#capture)
- [Escaping closures](#escaping-closures)
- [How it compiles](#how-it-compiles)
- [Error codes](#error-codes)
- [Limitations](#limitations)

## Overview

A **lambda literal** is an anonymous function value written inline, with access to the locals of
the function it's written in:

```sushi
fn make_adder(i32 n) fn(i32) -> i32:
    return Result.Ok(|i32 x| x + n)     # captures n by value

fn use_adder() i32:
    let fn(i32) -> i32 add5 = make_adder(5)??
    return Result.Ok(add5(10)??)        # 15

fn main() i32:
    println(use_adder().realise(0))
    return Result.Ok(0)
```

A closure is a `fn(...)`-typed value exactly like a bare function reference (see
[First-Class Functions](first-class-functions.md)) — same type, same call syntax, same `Result`
semantics. The difference is that a lambda can read (by captured copy) the variables around it,
and the resulting value can be returned or stored, outliving the scope it was written in.

## Lambda syntax

Two body forms:

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

The block form ends in a dedent with no trailing token, so the grammar admits it only where that's
unambiguous — the RHS of a `let`. Passing a block-body lambda as a call argument is a parse error;
use the expression form, or bind it to a `let` first.

Parameters use Sushi's `type name` form (`|i32 x, string s|`). A **bare-name** parameter (`|x|`,
no type) is legal only where an expected `fn(...)` type supplies it — an annotated `let` binding,
or a call argument to a `fn(...)`-typed parameter:

```sushi
fn apply(fn(i32) -> i32 f, i32 v) i32:
    return Result.Ok(f(v)??)

fn main() i32:
    let i32 scale = 3
    println(apply(|x| x * scale, 7).realise(0))   # x : i32 inferred from apply's signature -> 21
    return Result.Ok(0)
```

Result semantics are identical to `fn`: an expression body `|x| e` desugars to `return
Result.Ok(e)`, so calling through a closure yields `Result<T, E>` and `??`/`.realise()`/`if
(result)`/matching all work unchanged. The block form optionally takes a `-> T [| E]` annotation
after the closing pipe, exactly like a `fn` declaration.

Because the body is auto-wrapped in `Ok`, a fallible call inside a lambda body needs its own `??`
at the point of use — you can't let an inner `Result` flow straight out, since the desugar would
wrap it again (`Result<Result<T, E>, E>`) and the types won't match. That is why `compose` is
written `|x| f(g(x)??)??` and not `|x| f(g(x)??)`.

## Capture

Tier 1 supports **copy capture** only: primitives, strings, and copyable structs/fixed arrays are
captured by value into a heap-allocated environment.

```sushi
fn main() i32:
    let i32 a = 3
    let i32 b = 4
    let fn(i32) -> i32 f = |x| x + a + b
    println(f(10).realise(0))    # 17 -- both a and b captured
    return Result.Ok(0)
```

Two capture shapes are rejected in Tier 1, both as **CE2094**:

- **Capturing a `&peek`/`&poke` borrow.** Threading a borrow's exclusivity through an escaping
  closure is deferred to Tier 2.
- **Capturing an owned value** (a dynamic array, `List<T>`, `Own<T>`). Move-capture (and the
  environment RAII it needs) isn't implemented yet, so it's rejected rather than silently aliasing
  the outer buffer:

```sushi
fn main() i32:
    let i32[] nums = from([1, 2, 3])
    let fn(i32) -> i32 f = |i32 x| x + nums.len()   # CE2094: owned value 'nums' cannot be captured
    return Result.Ok(0)
```

A lambda **parameter** whose type is owning is also rejected (CE2094) — see
[Limitations](#limitations).

## Escaping closures

Because the captured environment is heap-allocated (not stack-allocated), a closure can be
returned from the function that created it, or stored in a struct/`List`, and called later — the
`make_adder` example above is exactly this. This is the core new capability over v1's bare
function pointers, which had nothing to capture and therefore nothing to escape.

## How it compiles

A function value is now a **three-word fat pointer** `{fn_ptr, env_ptr, drop_ptr}` (24 bytes),
replacing v1's bare pointer:

- A non-capturing value (a plain `fn` reference, or a lambda that captures nothing) carries null
  `env_ptr`/`drop_ptr` — it behaves like v1, just wrapped in the wider struct.
- A capturing lambda heap-allocates an environment struct holding the captured copies; `fn_ptr`
  points at the compiler-synthesized lifted function, and calling through the value passes
  `env_ptr` as a hidden leading argument.

Function types stay **invariant and capture-agnostic**: `fn(i32) -> i32` names both a plain `fn`
and any closure of that shape — capture is not part of the type. A mismatch is still **CE2002**
(assignment) or **CE2092** (call-through), exactly as in v1.

## Error codes

| Code | Meaning |
| --- | --- |
| **CE2094** | illegal closure capture — a `&peek`/`&poke` borrow, an owned value (dynamic array / `List<T>` / `Own<T>`), or an owning lambda-parameter type |
| **CE2092** | function value type mismatch at call-through (reused, unchanged from v1) |
| **CE2002** | function value assigned to an incompatible function-typed variable (reused, unchanged from v1) |

## Limitations

Tier 1 is deliberately the minimal real slice. Known gaps, in order of how much they matter:

- **The captured environment leaks.** A capturing closure's heap environment is never freed (RAII
  wiring for it is the main remaining Tier 1 item) — this is safe (no double-free, no
  use-after-free) but not memory-clean. Avoid creating capturing closures in a hot loop until this
  lands.
- **No move-capture of owned types.** Capturing a dynamic array, `List<T>`, or `Own<T>` is
  rejected (CE2094) rather than silently aliased.
- **No stdlib combinators yet.** `List.map`/`.filter`/`.fold` and a `compose` helper are not
  authored — the foundation (passing a capturing closure to a function and calling it) works, but
  there's no library code built on it yet.
- **Nested lambdas** (a lambda written inside another lambda's body) are lifted best-effort; deep
  nested capture chains are not guaranteed to work.
- **Deferred to Tier 2** (unchanged from the design note): `&peek`/`&poke` borrow capture, bound
  method values (`obj.method` as a callable), generic-function references (still **CE2093**),
  widening `Call.callee` so `arr[0]()`/`obj.handler()` work directly (still need a local binding),
  and first-class C callbacks.

The full tiered plan, the fat-pointer ABI rationale, and file:line implementation anchors live in
the [design note](design/closures.md).
