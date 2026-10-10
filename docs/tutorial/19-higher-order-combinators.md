# 19. Higher-Order Combinators

Chapters 17 and 18 gave you function values and closures. This chapter puts them to work with
three classic list combinators — `map`, `filter`, and `fold` — plus `compose`, all from the
opt-in `collections/iter` module.

The METHODS (`xs.map(f)`) are always available, as every method of a built-in type is. The
free FUNCTIONS (`map(xs, f)`, `compose`) are names, and you bring them in with a `use`.

```sushi
use <collections/iter>
```

`collections/iter` is written **in Sushi itself**, as are several other standard modules (for
example `<io/fs>` and `<io/buf>`). The combinators are ordinary generic functions that compile
with your program. Nothing is generated unless you
actually call one, so an unused `use` costs nothing.

The combinators are **bare**: a call gives the value itself, not a `Result`, so there is no
`??` after `map`, `filter` or `fold`. They take bare function types (`fn(T) -> U`), so the
function that you give them is bare too. A function with a channel, `fn(T) -> U | E`, is a
different type, and the call is refused (**[CE2006](../error-catalog.md#ce2006)**). A bare function is the exception in
Sushi, and the combinators are a correct use of it: they are total over their inputs.
[The error channel](../design/error-channel.md) gives the rule.

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

## The method form — chain the calls

Each combinator also exists as an **extension method** on `List@(T)` and on `T[]`. A
method is bare, so a call gives the value, and the next link calls directly on it. The
chain reads left to right:

```sushi
--8<-- "docs/tutorial/examples/19-higher-order-combinators/method-chain.sushi"
```

Output:

```
84
```

`tab_total` is bare too, so it returns `total` directly. On a `T[]` receiver the collecting
methods return a `List` — a dynamic array has no empty generic constructor to fill.

## Two things to know

!!! note "Element ownership"
    `map` and `fold` borrow each element and give it to your function, so they work on
    every element type. `filter`, in both forms, clones each kept element, so its element
    type needs `Clone`: an owning type such as `string` works, and a type that holds a
    resource, such as a `File`, gets no `filter` ([`CE4006`](../error-catalog.md#ce4006) at the call). `fold` clones
    its `init` once, so the type of `init` needs `Clone` too. For a container element (`T[]`, `List@(T)`, `Own@(T)`), give it a function
    reference, because a lambda parameter cannot be an owning container ([`CE2094`](../error-catalog.md#ce2094),
    chapter 18). A `string` lambda parameter is legal.

!!! warning "Annotate bare-parameter lambdas passed to a combinator"
    A bare-parameter lambda (`|x| ...`) cannot infer its type *against a generic parameter*, since
    the combinator's own type parameters are still being solved. Give the parameter a type
    (`|i32 x| ...`) or pass a function reference.

    A **generic** function can go to a combinator directly when the other arguments solve
    every type parameter: with `fn keep@(T)(T x) bool`, both `filter(xs, keep)` and
    `xs.filter(keep)` compile, because `xs` gives `T`. For `map`, nothing but the function
    gives `U`, so `map(xs, identity)` is refused ([`CE2060`](../error-catalog.md#ce2060), [`CE2093`](../error-catalog.md#ce2093)). Bind the generic
    function to a typed local first:

    ```sushi
    let fn(i32) -> i32 id = identity   # fixes the instantiation
    let List@(i32) same = map(xs, id)
    ```

## What you learned

- `<collections/iter>` gives `map`, `filter`, `fold` as methods on `List@(T)` and `T[]`,
  which need no import, AND as free functions, plus `compose`, which need
  `use <collections/iter>`.
- The combinators are bare, in both forms: chain the method calls directly
  (`xs.map(f).filter(p).fold(0, g)`), with no `??`.
- `collections/iter` is a Sushi-source standard-library module; the combinators
  monomorphize like any generic and cost nothing when unused.
- Each combinator takes a bare `fn(...)` value: a lambda (capturing or not) or a plain
  function reference. A function with a channel does not fit (**[CE2006](../error-catalog.md#ce2006)**).
- `compose` returns a closure that captures and calls the two functions you give it.
- `map` and `fold` borrow each element, `filter` clones the kept elements, and `fold`
  clones `init` once. So an owning element or accumulator type (`string`) works in both
  forms.
- Annotate bare-parameter lambdas. A generic function goes in directly when the other
  arguments solve its type parameters; otherwise bind it to a typed local first.
