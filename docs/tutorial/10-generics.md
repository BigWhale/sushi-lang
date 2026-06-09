# 10. Generics

So far every type we've written has been concrete: a `Point` holds two `i32`s, a `string`
holds text. But plenty of code doesn't care *what* it's holding. A container that stashes
one value is the same code whether that value is an integer or a starship. Writing it twice
would be a waste, and copy-paste is where bugs breed.

Generics let you write the code once and parameterise it over the type. If you've used
Java's `List<T>` or Python's type hints like `list[int]`, the idea will feel familiar — but
Sushi's generics are closer to Rust or C++ templates in one important way: there is **no
runtime cost**. We'll get to why at the end.

## Generic structs

A generic struct names one or more **type parameters** in angle brackets after its name,
then uses those names where a concrete type would normally go.

```sushi
--8<-- "docs/tutorial/examples/10-generics/box-and-pair.sushi"
```

`Box<T>` has a single type parameter `T`. When you write `Box(value: 42)` and annotate the
variable as `Box<i32>`, the compiler fills `T` in with `i32`. A `Box<string>` is a
different, separately-checked type — you can't accidentally cross the streams.

`Pair<T, U>` takes two parameters. Note that, just like ordinary structs, generic structs
support **named-argument construction**, and the names can come in any order — `Pair(first:
"Arthur", second: 42)` and `Pair(second: 42, first: "Arthur")` build the same value.

Output:

```
Box of i32 holds 42
Box of string holds Mostly Harmless
Arthur is 42
Arthur is still 42
```

!!! note "Why the type annotation?"
    The `let Box<i32> answer = ...` annotation isn't decoration. It's how the compiler knows
    which concrete type to use. Sushi infers a great deal (we'll see that in a moment), but
    for a `let` binding the declared type is the anchor that pins `T` down.

## Generic enums

Enums can be generic too. The variants carry values whose types refer to the enum's
parameters. Here is a tiny tree node that is either a `Leaf` holding some `T`, or `Empty`.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-enum.sushi"
```

Two things worth highlighting. First, a generic enum variant lists the **bare type** of its
payload — `Leaf(T)`, not `Leaf(T value)`. The pattern `Tree.Leaf(v)` is where you give that
payload a name to use. Second, `Tree.Empty()` carries no value, so when we build
`Tree<string> nothing = Tree.Empty()` the annotation is what tells the compiler this is a
tree of strings.

This is exactly the shape of the built-in `Maybe<T>` and `Result<T, E>` you've already been
using — they're generic enums, nothing more.

Output:

```
A leaf holding 42
An empty tree
```

## Generic functions and type inference

Functions can be generic as well, and here Sushi's inference really earns its keep. A
generic function names its type parameters after the function name, then uses them in the
parameter list and return type.

```sushi
--8<-- "docs/tutorial/examples/10-generics/inference.sushi"
```

`identity<T>(T x) T` is the simplest possible generic function: hand it a value, get the
same value back. The crucial part is the call site — `identity(7)` and `identity("towel")`.
You do **not** write `identity<i32>(7)`. The compiler looks at the argument `7`, sees it's
an `i32`, and infers `T = i32` for you. `make_pair` does the same trick with two parameters
at once.

Output:

```
identity gives 7 and towel
make_pair built (answer, 42)
```

!!! note "Inference has limits — be honest about them"
    Sushi infers type parameters **from the function's value parameters**, and only from
    simple ones. That means:

    - There is **no explicit type-argument syntax**: you cannot write `identity<i32>(7)`.
    - A type parameter must appear directly as a parameter type. The compiler currently
      can't dig `T` out of a *compound* parameter like `Pair<T, U>` or an array `T[]`.
    - Nested generic-function calls (a generic function whose body calls another generic
      function) aren't fully supported yet.

    Within those bounds — a `T` that maps straight onto an argument — inference is reliable,
    and that covers the great majority of everyday generic code.

## Nested generics

Generic types compose. The payload of one generic can itself be a generic, to any depth.
The most common case in real programs is a function that can fail *and* might legitimately
produce "nothing": `Result<Maybe<T>, E>`.

```sushi
--8<-- "docs/tutorial/examples/10-generics/nested.sushi"
```

`parse_count` returns `Result<Maybe<i32>, StdError>` — written **explicitly**, because we
want the literal nested type and not the implicit `Result`-wrapping that `fn f() T` would
apply. Unpacking it is just two nested `match` expressions: peel off the `Result`, then peel
off the `Maybe`. The compiler resolves the type all the way down, propagating `i32` into the
innermost `Maybe.Some`.

Output:

```
Counted 42
No count available
```

## Zero cost: monomorphization

Here's the payoff. When you use `Box<i32>` and `Box<string>`, the compiler doesn't keep one
fuzzy `Box` around that figures out types while the program runs (that's what Java does with
type erasure, and it costs you boxing and casts). Instead, at compile time it **stamps out a
separate, fully-concrete copy** of the code for each combination of type arguments you
actually use — a `Box` specialised for `i32`, another specialised for `string`, and so on.
This process is called **monomorphization**.

The consequence is that generic code runs exactly as fast as if you'd hand-written each
specialised version yourself. There is no runtime type information, no dynamic dispatch, no
hidden allocation. You get the convenience of writing the code once and the performance of
writing it many times.

The only thing to keep half an eye on is binary size: each distinct instantiation is real
code. In practice this is rarely a problem, and the optimiser deduplicates a lot, but it's
the trade-off that buys you the speed.

## What you learned

- Generic **structs** and **enums** take type parameters in `<...>` and use them where a
  concrete type would go; named-argument construction still works.
- Generic enum variants list **bare** payload types (`Leaf(T)`); you name the payload in the
  matching pattern.
- Generic **functions** infer their type parameters from the arguments at the call site —
  there's no explicit `f<T>(...)` syntax, and inference only reaches simple parameter types.
- Generic types **nest** freely, e.g. `Result<Maybe<T>, E>`.
- Generics are compiled by **monomorphization**, so they cost nothing at runtime.

Next we'll add behaviour to types — both our own and the built-in ones — with extension
methods and perks. On to [Perks & Extensions](11-perks-and-extensions.md).
