# Iter combinators

[← Back to Standard Library](../../standard-library.md)

Higher-order combinators over `List@(T)` and `T[]`: `map`, `filter`, `fold` — as
methods and as free functions — and `compose`.

## Import

```sushi
use <collections/iter>
```

## Overview

`collections/iter` is the first **Sushi-source** standard-library module: it ships as
bundled `.sushi` source and is merged as a compilation unit when you import it. The
combinators are ordinary generic free functions, so they monomorphize through the normal
generic pipeline — there is no bitcode, and nothing is emitted unless your program
actually instantiates a combinator.

The combinators exist in TWO forms. The **method form** declares the `| StdError`
channel, so each call yields a `Result` and chains with `??`:

<!-- docs-sweep: skip (fragment; the full program is under Methods below) -->
```sushi
let i32 total = xs.map(|i32 x| x * 2)??.filter(|i32 x| x > 2)??.fold(0, |i32 acc, i32 x| acc + x)??
```

The **free functions** stay, and are called as `map(xs, f)`. One unit's
`use <collections/iter>` makes the methods callable in every unit — extensions are
program-wide (see `docs/design/ufcs-combinators.md`).

**Element types**: the method-form `filter` is fully general — it clones each kept
element, so an owning element type works. `map` and `fold` (both forms) stay
copy/primitive-element.

**Function arguments**: pass a **typed-param lambda** (`|i32 x| ...`) or a plain
**function reference**. A bare-param lambda (`|x| ...`) cannot be inferred against a
generic parameter (CE2063) — annotate the parameter or use a function reference instead.

## Methods

Each method is an extension with the `| StdError` error channel. On a `T[]` receiver
the collecting methods return a `List` — a dynamic array has no empty generic
constructor to fill.

### `xs.map@(U)(fn(T) -> U f) -> List@(U) | StdError`

On `List@(T)` and on `T[]`. Applies `f` to every element, collecting the results into
a new list. `f`'s error propagates out of the call.

```sushi
use <collections/iter>

fn doubled_sum() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    let i32 total = xs.map(|i32 x| x * 2)??.fold(0, |i32 acc, i32 x| acc + x)??
    return Result.Ok(total)

fn main() i32:
    println("{doubled_sum().realise(-1)}")
    return Result.Ok(0)
```

### `xs.filter(fn(T) -> bool pred) -> List@(T) | StdError`

On `List@(T)` and on `T[]`. Keeps the elements for which `pred` answers true, cloning
each kept element — so an owning element type works.

### `xs.fold@(U)(U init, fn(U, T) -> U f) -> U | StdError`

On `List@(T)` and on `T[]`. Reduces left to right, threading the accumulator through
`f`.

### Chaining and the unhandled channel

A channel method stops the chain until it is handled: `xs.map(f).filter(p)` is CE2515,
and the diagnostic spells the fix (`xs.map(f)??.filter(p)`). Handle a link with `??`,
with `match`, or with `.realise(default)`.

## Free functions

### `map@(T, U)(List@(T) xs, fn(T) -> U f) -> List@(U)`

Apply `f` to every element, collecting the results into a new list.

```sushi
use <collections/iter>

fn main() i32:
    let i32 factor = 10
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    let List@(i32) ys = map(xs, |i32 x| x * factor).realise(List.new())
    println(ys.get(2).realise(-1))    # 30
    return Result.Ok(0)
```

### `filter@(T)(List@(T) xs, fn(T) -> bool pred) -> List@(T)`

Keep the elements for which `pred` returns `true`.

```sushi
use <collections/iter>

fn main() i32:
    let i32 threshold = 2
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    xs.push(4)
    let List@(i32) big = filter(xs, |i32 x| x > threshold).realise(List.new())
    println(big.len())    # 2
    return Result.Ok(0)
```

### `fold@(T, U)(List@(T) xs, U init, fn(U, T) -> U f) -> U`

Reduce the list left-to-right, threading `acc` through `f`.

```sushi
use <collections/iter>

fn main() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    let i32 total = fold(xs, 100, |i32 acc, i32 x| acc + x).realise(-1)
    println(total)    # 106
    return Result.Ok(0)
```

### `compose@(T, U, V)(nom fn(T) -> U g, nom fn(U) -> V f) -> fn(T) -> V`

Build a new function that applies `g` first, then `f` (`f` after `g`). The returned
closure captures `f` and `g`, so it becomes their owner -- which is why both parameters
declare `nom` and both call-site arguments carry the marker. `map`, `filter` and `fold`
only CALL their function argument, so they borrow it and need no marker.

`map(xs, f)` also borrows `xs`: the list is still yours after the call, so mapping twice
over one list works.

```sushi
use <collections/iter>

fn inc(i32 x) i32:
    return Result.Ok(x + 1)

fn dbl(i32 x) i32:
    return Result.Ok(x * 2)

fn main() i32:
    let fn(i32) -> i32 incthendouble = compose(nom inc, nom dbl).realise(dbl)
    println(incthendouble(10).realise(-1))    # dbl(inc(10)) = 22
    return Result.Ok(0)
```

## See also

- [List@(T)](list.md) — the underlying collection
- [First-Class Functions & Closures](../../design/closures.md) — how lambdas and function
  values work
