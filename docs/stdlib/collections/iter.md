# Iter combinators

[← Back to Standard Library](../../standard-library.md)

Higher-order combinators over `List<T>`: `map`, `filter`, `fold` (and
[`compose`](#compose)).

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

Because they are free functions, you call them as `map(xs, f)`, not `xs.map(f)` (the UFCS
method form needs method-level type parameters, which is a separate feature).

**Element types** are copy/primitive for now (`filter` re-pushes each kept element, `map`
reads each one); owned-element combinators are deferred.

**Function arguments**: pass a **typed-param lambda** (`|i32 x| ...`) or a plain
**function reference**. A bare-param lambda (`|x| ...`) cannot be inferred against a
generic parameter — annotate the parameter or use a function reference instead.

## Functions

### `map<T, U>(List<T> xs, fn(T) -> U f) -> List<U>`

Apply `f` to every element, collecting the results into a new list.

```sushi
use <collections/iter>

fn main() i32:
    let i32 factor = 10
    let List<i32> xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    let List<i32> ys = map(xs, |i32 x| x * factor).realise(List.new())
    println(ys.get(2).realise(-1))    # 30
    return Result.Ok(0)
```

### `filter<T>(List<T> xs, fn(T) -> bool pred) -> List<T>`

Keep the elements for which `pred` returns `true`.

```sushi
use <collections/iter>

fn main() i32:
    let i32 threshold = 2
    let List<i32> xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    xs.push(4)
    let List<i32> big = filter(xs, |i32 x| x > threshold).realise(List.new())
    println(big.len())    # 2
    return Result.Ok(0)
```

### `fold<T, U>(List<T> xs, U init, fn(U, T) -> U f) -> U`

Reduce the list left-to-right, threading `acc` through `f`.

```sushi
use <collections/iter>

fn main() i32:
    let List<i32> xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    let i32 total = fold(xs, 100, |i32 acc, i32 x| acc + x).realise(-1)
    println(total)    # 106
    return Result.Ok(0)
```

### `compose<T, U, V>(fn(T) -> U g, fn(U) -> V f) -> fn(T) -> V`

Build a new function that applies `g` first, then `f` (`f` after `g`). The returned
closure captures `f` and `g`.

```sushi
use <collections/iter>

fn inc(i32 x) i32:
    return Result.Ok(x + 1)

fn dbl(i32 x) i32:
    return Result.Ok(x * 2)

fn main() i32:
    let fn(i32) -> i32 incthendouble = compose(inc, dbl).realise(dbl)
    println(incthendouble(10).realise(-1))    # dbl(inc(10)) = 22
    return Result.Ok(0)
```

## See also

- [List<T>](list.md) — the underlying collection
- [First-Class Functions & Closures](../../design/closures.md) — how lambdas and function
  values work
