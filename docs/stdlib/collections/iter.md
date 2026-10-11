# Iter combinators

[← Back to Standard Library](../../standard-library.md)

Higher-order combinators over `List@(T)` and `T[]`: `map`, `filter`, `fold` — as
methods and as free functions — `compose`, and the tuple combinators `enumerate`, `zip`,
`partition` and `unzip`. The module also gives the name `Iterator`, whose lazy adapters
are built into the compiler ([Lazy adapters](#lazy-adapters-on-iteratort)).

## Import

```sushi
use <collections/iter>
```

The import brings the free functions. The methods need no import: they are on
`List@(T)` and `T[]`, built-in types, so they are available in every unit
([Extension visibility](../../design/extension-visibility.md), R1).

## Overview

`collections/iter` is the first **Sushi-source** standard-library module: it ships as
bundled `.sushi` source and is merged as a compilation unit when you import it, or when a
unit calls one of its methods. The
combinators are ordinary generic free functions, so they monomorphize through the normal
generic pipeline — there is no bitcode, and nothing is emitted unless your program
actually instantiates a combinator.

The combinators are **bare**, in both forms: they have no error channel, a call gives the
value itself, and they take bare function types (`fn(T) -> U`). So the calls chain
directly, with no `??` after `map`, `filter` or `fold`. A function with a channel,
`fn(T) -> U | E`, is a different type, and the call is refused ([CE2006](../../error-catalog.md#ce2006)). A bare function
is the exception in Sushi; the combinators are bare because each one is total over its
inputs. [The error channel](../../design/error-channel.md) gives the rule.

The combinators exist in TWO forms. The **method form** chains left to right:

<!-- docs-sweep: skip (fragment; the full program is under Methods below) -->
```sushi
let i32 total = xs.map(|i32 x| x * 2).filter(|i32 x| x > 2).fold(0, |i32 acc, i32 x| acc + x)
```

The **free functions** stay, and are called as `map(xs, f)`. They need
`use <collections/iter>` in the unit that calls them; the methods need no import.

**Element types**: every combinator, in both forms, takes an owning element type
(`List@(string)`). `filter` clones each kept element, and `fold` clones `init` once, so
those two state the bound `Clone` on the type they clone (#1070): a `string` is `Clone`,
a handle such as a `File` is not.

**Function arguments**: pass a **typed-param lambda** (`|i32 x| ...`) or a plain
**function reference**. A bare-param lambda (`|x| ...`) cannot be inferred against a
generic parameter ([CE2063](../../error-catalog.md#ce2063)) — annotate the parameter or use a function reference instead.

## Lazy adapters on `Iterator@(T)`

An iterator value (`xs.iter()`, `m.keys()`, a range in a `let`) has eight built-in
adapters. They are compiler code, so they need no import: `use <collections/iter>` is
necessary only to WRITE the name `Iterator`. An import that a unit uses only for an
adapter gets [CW3006](../../error-catalog.md#cw3006) under `--warn-unused`.

| Adapter | Answer |
|---|---|
| `it.map(nom f)` | `Iterator@(U)`: the answer of `f` for each item. `f` reads the item as a borrow |
| `it.filter(nom pred)` | The same iterator type: the items for which `pred` answers `true` |
| `it.enumerate()` | `Iterator@((i32, T))`: each item with its count, from 0 |
| `it.zip(nom other)` | `Iterator@((T, U))`: the items of the two iterators side by side, to the end of the shorter one |
| `it.clone_each()` | `Iterator@(T)`: one `.clone()` for each borrowed item |
| `it.fold(nom init, nom f)` | The last accumulator. `f` reads each item as a borrow |
| `it.partition(nom pred)` | `(List@(T), List@(T))`: the items that `pred` keeps, then the others |
| `it.collect()` | `List@(T)`: each item, in order |

The first five are **lazy**: they answer a new iterator and do no work until a `foreach`
or a consumer asks for an item. A chain walks its source one time, and only the consumer
at its end allocates a result. Each adapter takes its receiver and its function with
`nom`.

**Item modes.** `xs.iter()` gives `Iterator@(peek T)`: each item is a borrow, and nothing
is copied. `filter` keeps the mode. `map` answers the owned result of its function.
`enumerate` and `zip` keep the mode of each side in its tuple element, so
`names.iter().enumerate()` is `Iterator@((i32, peek string))`. `clone_each` makes each
item owned. For a plain item (an `i32`) the two modes are one type.

`collect` and `partition` keep each item in a `List`, and there is no `List@(peek T)`. So
over a `peek` item of an owning type (a `string`) they are
[CE2129](../../error-catalog.md#ce2129). Clone the items first:
`.clone_each().collect()`.

**Borrow.** An adapter over a borrowed iterator borrows the same owners: the container is
frozen until the last use of the chain, and the chain cannot leave its function
([CE2411](../../error-catalog.md#ce2411)). `zip` of two borrowed iterators borrows both
owners. A user function with a `nom Iterator@(...)` parameter refuses a borrowed iterator;
only a built-in adapter takes one.

```sushi
fn main() i32:
    let i32[] xs = from([1, 2, 3, 4])
    let List@(i32) squares = xs.iter().filter(nom |i32 x| x % 2 == 0).map(nom |i32 x| x * x).collect()
    println(squares.len())                  # 2

    let string[] names = from(["a", "bb", "ccc"])
    foreach(p in names.iter().enumerate()):
        println("{p.0}: {p.1}")             # the names are not copied
    let List@(string) long = names.iter().filter(nom |string s| s.len() > 1).clone_each().collect()
    println(long.len())                     # 2
    println(xs.iter().fold(nom 0, nom |i32 acc, i32 x| acc + x))    # 10
    return 0
```

## Methods

Each method is a bare extension method, and a one-line forwarder over the built-in
adapters: `xs.map(f)` is `xs.iter().map(nom f.clone()).collect()`. On a `T[]` receiver
the collecting methods return a `List` — a dynamic array has no empty generic
constructor to fill.

### `xs.map@(U)(fn(T) -> U f) -> List@(U)`

On `List@(T)` and on `T[]`. Applies `f` to every element, collecting the results into
a new list.

```sushi
fn doubled_sum() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    let i32 total = xs.map(|i32 x| x * 2).fold(0, |i32 acc, i32 x| acc + x)
    return total

fn main() i32:
    println("{doubled_sum()}")
    return 0
```

### `xs.filter(fn(T) -> bool pred) -> List@(T)`

On `List@(T: Clone)` and on `(T: Clone)[]`. Keeps the elements for which `pred` answers
true, cloning each kept element. The element type must be `Clone`: a value that holds no
resource. A list of handles has no `filter`, and a call on one is CE4006.

### `xs.fold@(U: Clone)(U init, fn(U, T) -> U f) -> U`

On `List@(T)` and on `T[]`. Reduces left to right, threading the accumulator through
`f`. `init` is cloned once, so the accumulator type must be `Clone`.

### Chaining

A method call gives the value, so the next link calls on it directly:
`xs.map(f).filter(p).fold(0, g)`. A `??` after a link is [CE2507](../../error-catalog.md#ce2507), because the value is not
a `Result`.

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
    let List@(i32) ys = map(xs, |i32 x| x * factor)
    println(ys.get(2).realise(-1))    # 30
    return 0
```

### `filter@(T: Clone)(List@(T) xs, fn(T) -> bool pred) -> List@(T)`

Keep the elements for which `pred` returns `true`. Each kept element is a clone.

```sushi
use <collections/iter>

fn main() i32:
    let i32 threshold = 2
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    xs.push(4)
    let List@(i32) big = filter(xs, |i32 x| x > threshold)
    println(big.len())    # 2
    return 0
```

### `fold@(T, U: Clone)(List@(T) xs, U init, fn(U, T) -> U f) -> U`

Reduce the list left-to-right, threading `acc` through `f`.

```sushi
use <collections/iter>

fn main() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    let i32 total = fold(xs, 100, |i32 acc, i32 x| acc + x)
    println(total)    # 106
    return 0
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
    return x + 1

fn dbl(i32 x) i32:
    return x * 2

fn main() i32:
    let fn(i32) -> i32 incthendouble = compose(nom inc, nom dbl)
    println(incthendouble(10))    # dbl(inc(10)) = 22
    return 0
```

## Tuple combinators

Four combinators answer [tuples](../../language-reference.md#tuples). They follow `map`
and `filter`: each one is a free function and a bare method on `List@(T)` and on `T[]`,
except `unzip`, which is a free function only (an extension on a tuple type is refused). Each
one answers a new `List`, reads its receiver as a borrow, and clones each element as
`filter` does, so each element type must be `Clone`. A `List` is not an iterator itself: walk
the answer with `.iter()`, and destructure the item in the `foreach`.

| Free function | Answer |
|---|---|
| `enumerate@(T: Clone)(List@(T) xs)` | `List@((i32, T))`: each element with its index, from 0 |
| `zip@(T: Clone, U: Clone)(List@(T) xs, List@(U) ys)` | `List@((T, U))`: the elements side by side; it stops at the shorter list |
| `partition@(T: Clone)(List@(T) xs, fn(T) -> bool pred)` | `(List@(T), List@(T))`: the elements that `pred` keeps, then the others, each in order |
| `unzip@(T: Clone, U: Clone)(List@((T, U)) xs)` | `(List@(T), List@(U))`: the first elements, then the second elements |

The methods are `xs.enumerate()`, `xs.zip(ys)` and `xs.partition(pred)`; on a `T[]`
receiver, `zip` takes a `U[]`.

```sushi
use <collections/iter>

fn main() i32:
    let List@(string) names = List.new()
    names.push("Arthur")
    names.push("Ford")
    let List@(i32) ages = List.new()
    ages.push(42)
    ages.push(200)
    ages.push(7)

    foreach((i, name) in enumerate(names).iter()):
        println("{i}: {name}")              # 0: Arthur, then 1: Ford
    foreach((name, age) in names.zip(ages).iter()):
        println("{name} is {age}")          # two lines: 7 has no partner
    let (even, odd) = ages.partition(|i32 n| n % 2 == 0)
    println("{even.len()} even, {odd.len()} odd")    # 2 even, 1 odd
    let (back, _) = unzip(zip(names, ages))
    println(back.len())                     # 2
    return 0
```

## See also

- [List@(T)](list.md) — the underlying collection
- [First-Class Functions & Closures](../../design/closures.md) — how lambdas and function
  values work
