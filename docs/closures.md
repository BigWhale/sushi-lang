# Closures

[← Back to Documentation](index.md)

Guide to closures and lambda literals in Sushi: anonymous function values that **capture** their
enclosing scope. Tier 1 is complete, and two Tier 2 items (generic-function references with an
explicit expected type, and widened call-through) have landed on top of it, built on the
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
Result.Ok(e)`, so calling through a closure yields `Result@(T, E)` and `??`/`.realise()`/`if
(result)`/matching all work unchanged. The block form optionally takes a `-> T [| E]` annotation
after the closing pipe, exactly like a `fn` declaration.

Because the body is auto-wrapped in `Ok`, a fallible call inside a lambda body needs its own `??`
at the point of use — you can't let an inner `Result` flow straight out, since the desugar would
wrap it again (`Result@(Result@(T, E), E)`) and the types won't match. That is why `compose` is
written `|x| f(g(x)??)??` and not `|x| f(g(x)??)`.

## Capture

Primitives, strings, and copyable structs/fixed arrays are captured **by copy** into a
heap-allocated environment:

```sushi
fn main() i32:
    let i32 a = 3
    let i32 b = 4
    let fn(i32) -> i32 f = |x| x + a + b
    println(f(10).realise(0))    # 17 -- both a and b captured
    return Result.Ok(0)
```

An owned dynamic array, `List@(T)`, or `Own@(T)` is captured **by move**: the outer binding is
consumed (a later use of it is CE2405, use-after-move) and the heap environment becomes the sole
owner, freeing the value when the closure's environment is freed:

```sushi
fn main() i32:
    let i32[] nums = from([1, 2, 3])
    let fn() -> i32 f = |~| nums.len()   # moves nums into f's environment
    println(f().realise(0))              # 3
    return Result.Ok(0)
```

Two capture shapes are still rejected, both as **CE2094**:

- **Capturing a `&peek`/`&poke` borrow.** Threading a borrow's exclusivity through an escaping
  closure is deferred to Tier 2.
- **A lambda parameter whose type is owning** (the indirect-call path has no deep-copy for an
  owning parameter yet) — see [Limitations](#limitations).

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
| **CE2094** | illegal closure capture — a `&peek`/`&poke` borrow, or an owning lambda-parameter type |
| **CE2092** | function value type mismatch at call-through (reused, unchanged from v1) |
| **CE2002** | function value assigned to an incompatible function-typed variable (reused, unchanged from v1) |

## Limitations

Tier 1 is complete, plus two Tier 2 items (T2.3/T2.4) have landed. Known gaps that remain:

- **`List@(T)`/`Own@(T)`/dynamic-array-typed lambda *parameters* have no deep-copy** in the
  indirect-call path, so they're rejected (CE2094) — this is distinct from *capture*, which does
  move owned values (see [Capture](#capture)).
- **No UFCS method form** (`xs.map(f)`) for the stdlib combinators — `use
  <collections/iter>` ships `map`/`filter`/`fold`/`compose` as free generic functions
  (`map(xs, f)`, not `xs.map(f)`); owned-element combinators are not authored yet.
- **Nested lambdas** (a lambda written inside another lambda's body) are lifted best-effort; deep
  nested capture chains are not guaranteed to work.
- **Deferred to Tier 2**: `&peek`/`&poke` borrow capture, bound method values (`obj.method` as a
  bare callable), and first-class C callbacks. Generic-function references now work when an
  explicit expected `fn` type is present (`let fn(i32) -> i32 g = identity`); a bare reference with
  no expected type is still **CE2093**. Calling through a fn-typed struct field, a container
  get-out, a parenthesized expression, or a captured closure *value* all work now (`Call.callee`
  widening, T2.4).

The full tiered plan, the fat-pointer ABI rationale, and file:line implementation anchors live in
the [design note](design/closures.md).
