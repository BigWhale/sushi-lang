# 15. Variadic Functions

Most functions take a fixed number of arguments. But some — a logger, a formatter, a
`sum` — want to accept *however many* you give them. [Chapter 14](14-stdlib-ffi-libraries.md)
already showed the simplest case: a `...T` parameter that gathers trailing arguments of the
**same** type into an array. This chapter picks up where that left off and adds the powerful
part: **parameter packs**, which accept arguments of *different* types and stay completely
type-checked and zero-cost.

If you've met C++ variadic templates or Rust's macros-that-pretend-to-be-variadic, packs are
Sushi's take on the idea — but ordinary, typed, and (as always) compiled away to nothing.

## A quick recap: homogeneous `...T`

A trailing `...T name` collects same-typed trailing arguments into an owned `T[]` you iterate
like any other array:

```sushi
fn sum(...i32 nums) i32:
    let i32 total = 0
    foreach(n in nums.iter()):
        total := total + n
    return Result.Ok(total)
```

That's perfect when every argument is an `i32`. But what if you want to accept an `i32`, a
`string`, and a `bool` in the *same* call? An array can't hold three different types. That's
where packs come in.

## Forwarding an array: bloom

Sometimes you already *have* the arguments in an array and want to forward the whole thing,
rather than pulling elements out one at a time. The postfix `...` operator does exactly that —
Sushi calls it a **bloom**, since the array "opens" and its elements fan out to fill the trailing
call arguments, mirroring Go's `f(arr...)`:

```sushi
--8<-- "docs/tutorial/examples/15-variadic-functions/bloom-basics.sushi"
```

Output:

```
sum = 10
```

A bloom is a **move**, not a copy: `xs` is consumed by the call to `sum`, so don't read or pass it
again afterward. It also has to be a bare variable — you can't bloom a call result, a struct
field, or an inline array literal (`sum(from([1, 2])...)` doesn't work; bind it to a `let` first).
And a bloom must be the *only* trailing argument: you can't mix it with individual trailing
arguments in the same call. Anything else (blooming into a non-variadic parameter, blooming
alongside other trailing args) is a compile error (**CE0120**).

## Parameter packs: arguments of different types

A **parameter pack** binds a variable-length list of *concrete, possibly-different* types. It
has two pieces that share a name: a **type pack** `...Ts` in the angle brackets, and a
**value pack** `...Ts args` in the parameter list. You walk the values with `expand`:

```sushi
--8<-- "docs/tutorial/examples/15-variadic-functions/pack-basics.sushi"
```

Output:

```
int 42
text 'Mostly Harmless'
bool true
int 7
```

Three things are happening here:

- **`...Ts: Describe`** says "a pack of types, each of which implements `Describe`." That
  constraint is what lets the body call `.describe()` — without it the compiler couldn't know
  the elements share *any* method.
- **`expand(a in args):`** runs its body once per argument, with `a` bound to that argument's
  real value and type. The first `a` is an `i32`, the second a `string`, the third a `bool` —
  each `a.describe()` dispatches to the right `extend` block.
- **`show_all()` with no arguments is fine** — the `expand` body simply runs zero times (notice
  there's no fourth line of output).

!!! note "`expand` is not a loop"
    `foreach` runs at *runtime*, iterating one container. `expand` runs at *compile time*: the
    compiler stamps out a separate, fully-typed copy of the body for each argument and throws the
    "pack" away before codegen. There is no runtime list of mixed types, no boxing, and no type
    tag — just straight-line code. A `foreach` couldn't do this anyway, because there's no single
    element type to iterate.

## A fixed parameter, then a pack

Like `...T`, a pack comes last and can follow ordinary fixed parameters:

```sushi
--8<-- "docs/tutorial/examples/15-variadic-functions/pack-with-fixed.sushi"
```

Output:

```
mixed:
  - 42
  - towel
  - 7
empty:
```

`label` is an ordinary `string` argument; everything after it is swept into the pack. The
second call passes nothing for the pack, so only the label prints.

## `expand` can build values, not just print

The body of an `expand` is normal code — it can read and update the surrounding locals, so you
can **accumulate** across the elements instead of just printing them:

```sushi
--8<-- "docs/tutorial/examples/15-variadic-functions/pack-accumulate.sushi"
```

Output:

```
[1][two][true]
[42]

```

Each unrolled copy appends to `line`. (The third call, `print_row()`, accumulates nothing and
prints an empty line.) Early `return` and the `??` operator also work inside `expand`, and any
owned temporaries you create per element are cleaned up exactly once — even on an early exit.

## Why the constraint matters

You might wonder why `...Ts: Describe` needs the `: Describe` at all. Because the elements have
*different* types, the only operations the body can perform are ones **guaranteed for every
possible element**. The perk bound is that guarantee, checked once where the function is
defined. Try to call the pack with a type that doesn't implement the perk and you get a clear
**CE2090** at the call site, naming the type and the missing perk — not a wall of errors buried
inside the expanded body.

## Packs travel across libraries

Because a pack function is generic, it ships across a `.slib` boundary just like any generic.
A library can export a `printf`-style helper:

```sushi
public fn show_all@(...Ts: Display)(...Ts args) ~:
    expand(a in args):
        println(a.display())
    return Result.Ok(~)
```

and a program that `use`s the library monomorphizes it at *its own* call sites, supplying
`Display` implementations for whatever types it passes. The [Libraries guide](../libraries.md)
covers how templates cross the boundary; the [Variadics guide](../variadics.md) has the full
reference.

## What you learned

- **`...T`** gathers *same-typed* trailing arguments into an owned `T[]` (Chapter 14).
- **Bloom** (`arr...`) forwards an existing array into a `...T` slot by moving it, instead of
  passing elements one at a time — the array must be a bare variable, and the sole trailing
  argument (CE0120 otherwise).
- **Parameter packs** `...Ts` accept *different-typed* arguments; the type pack `...Ts` and the
  value pack `...Ts args` share a name.
- **`expand(x in pack):`** is compile-time-unrolled — once per element, each typed concretely —
  not a runtime loop. Zero arguments runs it zero times.
- A **perk constraint** (`...Ts: Perk`) makes the body callable and is checked upfront (CE2090
  on a bad element type).
- `expand` bodies are ordinary code: they can accumulate, early-`return`, and use `??`.
- Packs are **monomorphized** like generics, so they cost nothing at runtime, and they **cross
  `.slib` boundaries** (unlike native `...T`).

That's the tour. For the complete reference — every form, error code, and the cross-library
mechanics — see the [Variadics guide](../variadics.md) and the
[Variadics design note](../design/variadics.md). Now go pass an improbable number of arguments
to something.
