# 8. Structs & Enums

Arrays hold many values of the *same* type. But often you want to bundle together values
of *different* types that belong to one another — a person's name and age, a config's host
and port. And sometimes you want a value that is one of several distinct shapes — a status
that's either "idle" or "travelling somewhere". Sushi gives you **structs** for the first
and **enums** for the second. If you've used Rust, these will feel like home; if you come
from Python, think dataclasses and (much stronger) enums; from Java, classes-without-methods
and sealed types.

## Structs: grouping related fields

A `struct` defines a new type made of named, typed fields.

```
--8<-- "docs/tutorial/examples/08-structs-and-enums/struct-basics.sushi"
```

Output:

```
Arthur Dent is 30 years old.
Arthur Dent knows where his towel is.
```

The definition lists each field with its type first, then its name (`string name`, just
like a variable declaration). To build an instance, call the struct's name like a function
and pass values **in field-declaration order**: `Person("Arthur Dent", 30, true)`. Once you
have a value, you read its fields with dot notation: `arthur.name`, `arthur.age`.

!!! note "Booleans print as `0` and `1`"
    If you interpolate a `bool` directly — `{arthur.has_towel}` — it prints as `0` or `1`,
    not `false` or `true`. That's why the example uses `if (arthur.has_towel):` and prints
    a sentence instead. Keep it in mind when you format output.

## Named arguments

Positional construction is concise, but when a struct has several fields — especially
several `bool` fields — it's easy to get the order wrong and hard to read. Sushi lets you
pass arguments **by name** instead:

```
--8<-- "docs/tutorial/examples/08-structs-and-enums/named-args.sushi"
```

Output:

```
A connects to localhost on port 8080.
B connects to magrathea.net on port 443.
B uses SSL.
```

With named arguments the order no longer matters — `Config(use_ssl: true, host: ...,
port: ...)` works even though `use_ssl` is the last field in the definition. Each value
labels itself, which makes `use_ssl: true` far clearer than a bare `true` buried among
other arguments.

!!! note "All-or-nothing"
    You can construct positionally *or* by name, but you cannot mix the two in a single
    constructor call. Pick one style per call. (Named arguments cost nothing at runtime —
    the compiler resolves them to positions while building your program.)

## Enums: one of several shapes

An `enum` defines a type whose value is exactly one of a fixed set of **variants**. Each
variant can optionally carry data of its own.

```
--8<-- "docs/tutorial/examples/08-structs-and-enums/enums.sushi"
```

Output:

```
A point has no size.
A circle of radius 2.
A rectangle 3 by 4.
```

`Shape` has three variants. `Point()` carries no data. `Circle(f64)` carries one
floating-point number (a radius). `Rectangle(f64, f64)` carries two (width and height).
Notice the variants list *types*, not field names — when you destructure them you name the
pieces yourself (`Circle(r)`, `Rectangle(w, h)`).

You construct a value by naming the enum, the variant, and any data:
`Shape.Circle(2.0)`. To read the data back out, you use `match`, which inspects which
variant you have and binds its data to names. We're leaning on `match` here just enough to
print something; the next chapter is devoted entirely to it.

!!! note "Floats print without trailing zeros"
    `Shape.Circle(2.0)` carries the `f64` value `2.0`, but interpolating it prints `2`, not
    `2.0`. Sushi trims insignificant trailing zeros when formatting floats.

## A note on generics

Both structs and enums can be **generic** — parameterised by a type. You've already used
generic enums without thinking about it: `Maybe<T>` and `Result<T, E>` are exactly this.
You can define your own, like a `Pair<T, U>` that holds two values of any types. We'll get
to defining your own generics in [Chapter 10](10-generics.md); for now, just know the
toolbox extends that far.

## What you learned

- A `struct` groups named, typed fields into one type; build it positionally
  (`Point(10, 20)`) and read fields with dot notation (`p.x`).
- Named arguments (`Point(y: 20, x: 10)`) are order-independent and self-documenting, but
  you can't mix named and positional in one call.
- An `enum` is a value that's exactly one of several variants; variants can carry data
  (`Shape.Circle(f64)`), and you construct them as `Shape.Circle(2.0)`.
- `match` is how you read an enum's data back out.
- Structs and enums can both be generic — more on that in Chapter 10.

Those `match` expressions deserve a proper introduction. On to
[Pattern Matching](09-pattern-matching.md).
