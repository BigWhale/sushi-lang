# 19. Higher-Order Combinators

Chapters 17 and 18 gave you function values and closures. This chapter puts them to work with
three classic list combinators — `map`, `filter`, and `fold` — plus `compose`, all from the
opt-in `collections/iter` module.

Unlike `List` and `HashMap`, these are not always in scope: you bring them in with a `use`.

```sushi
use <collections/iter>
```

`collections/iter` is the first Sushi module written **in Sushi itself** — the combinators are
ordinary generic functions that compile alongside your program. Nothing is generated unless you
actually call one, so an unused `use` costs nothing.

## map — transform every element

`map(xs, f)` applies `f` to each element of a `List@(T)` and collects the results into a new
`List@(U)`:

```sushi
--8<-- "docs/tutorial/examples/19-higher-order-combinators/map.sushi"
```

Output:

```
46
```

The lambda `|i32 a| a + crew` captures the outer local `crew` (Chapter 18) — combinators and
closures work together with no special ceremony. Here the call is the free-function form,
`map(ages, ...)`, so the list is the first argument; the method form `ages.map(...)` exists
too, and gets its own section below.

## filter — keep the ones that match

`filter(xs, pred)` keeps exactly the elements for which `pred` returns `true`:

```sushi
--8<-- "docs/tutorial/examples/19-higher-order-combinators/filter.sushi"
```

Output:

```
2
```

Two of the four readings (`42` and `99`) clear the threshold, so the filtered list has length 2.

## fold — collapse a list to a single value

`fold(xs, init, f)` threads an accumulator through the list left to right, starting from `init`:

```sushi
--8<-- "docs/tutorial/examples/19-higher-order-combinators/fold.sushi"
```

Output:

```
42
```

Each step computes `acc + item`; starting from `0`, the tab at Milliways comes to `6 + 6 + 30`.
`fold` is the general shape behind sum, product, min/max, and many other one-value reductions.

## A plain function instead of a lambda

Any argument that expects a `fn(...)` value accepts a **function reference** — just name a
top-level function:

```sushi
--8<-- "docs/tutorial/examples/19-higher-order-combinators/fn-reference.sushi"
```

Output:

```
-5
```

## compose — glue two functions together

`compose(nom g, nom f)` builds a new function that runs `g` first, then feeds the result to `f`:

```sushi
--8<-- "docs/tutorial/examples/19-higher-order-combinators/compose.sushi"
```

Output:

```
42
```

`compose(nom babel, nom improbability)` returns a `fn(i32) -> i32` that computes
`improbability(babel(20))` = `(20 + 1) * 2`. The returned function is a closure that captures both
`babel` and `improbability` — exactly the capture-and-call machinery from Chapter 18, now packaged
for you.

The two `nom` markers are why: the closure captures both functions, so it becomes their owner,
and `compose` declares that. `map`, `filter` and `fold` only *call* their function argument, so
they borrow it and take no marker.

## The method form — chain with `??`

Each combinator also exists as an **extension method** on `List@(T)` and on `T[]`. A
method declares the error channel `| StdError`, so a call yields a `Result` — handle
each link with `??` and the chain reads left to right:

```sushi
--8<-- "docs/tutorial/examples/19-higher-order-combinators/method-chain.sushi"
```

Output:

```
84
```

Leave a `??` out and the compiler stops you with CE2515, spelling the fix: an unhandled
`Result` has no `.filter()`, its payload does. On a `T[]` receiver the collecting
methods return a `List` — a dynamic array has no empty generic constructor to fill.

## Two things to know

!!! note "Element ownership"
    `map` and `fold` work on copyable element types — integers, floats, `bool`, strings,
    and copyable structs. The **method-form `filter` is fully general**: it clones each
    kept element, so an owning element type works there. The free-function `filter`
    stays copy-only.

!!! warning "Annotate bare-parameter lambdas passed to a combinator"
    A bare-parameter lambda (`|x| ...`) cannot infer its type *against a generic parameter*, since
    the combinator's own type parameters are still being solved. Give the parameter a type
    (`|i32 x| ...`) or pass a function reference. To hand a **generic** function to a combinator,
    bind it to a typed local first:

    ```sushi
    let fn(i32) -> i32 id = identity   # fixes the instantiation
    let List@(i32) same = map(xs, id).realise(List.new())
    ```

## What you learned

- `use <collections/iter>` brings in `map`, `filter`, `fold` — as methods on `List@(T)`
  and `T[]` AND as free functions — plus `compose`.
- The method form declares the `| StdError` channel: chain with `??`
  (`xs.map(f)??.filter(p)??.fold(0, g)??`), and CE2515 catches a link you forgot to handle.
- `collections/iter` is the first Sushi-source standard-library module; the combinators
  monomorphize like any generic and cost nothing when unused.
- Each combinator takes a `fn(...)` value: a lambda (capturing or not) or a plain function
  reference.
- `compose` returns a closure that captures and calls the two functions you give it.
- `map` and `fold` are copy-element; the method-form `filter` clones and is fully general.
  Annotate bare-parameter lambdas, or bind a generic function to a typed local, when
  passing them to a combinator.
