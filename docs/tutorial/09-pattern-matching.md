# 9. Pattern Matching

The previous chapter built enums but could only peek inside them with a `match` we hadn't
properly explained. Time to fix that. **Pattern matching** is how you inspect a value,
figure out which shape it has, and pull out the data it carries — all at once, and in a way
the compiler can check for completeness. If you've used Rust's `match` or a `switch`
expression in modern Java, you'll recognise the idea; if you've only had Python's
`if/elif` ladders or `match` statements, this is the same idea with teeth: the compiler
won't let you forget a case.

## Matching and destructuring

A `match` expression takes a value and lists patterns, each with an arrow `->` and a body.
Sushi tries each pattern in turn and runs the body of the first that fits. When a variant
carries data, the pattern can **destructure** it: name the data, and it becomes a variable
inside that branch.

```
--8<-- "docs/tutorial/examples/09-pattern-matching/match-basics.sushi"
```

Output:

```
Circle with radius 2
Rectangle 3 by 4
```

In `Shape.Circle(r) ->`, the `r` isn't a value to compare against — it's a *name* that
captures whatever radius this circle carries. Inside that branch, `r` is just a `f64` you
can use. Likewise `Rectangle(w, h)` binds both pieces at once. This is the everyday rhythm
of working with enums: match to find the variant, destructure to get the data.

!!! note "`match` is the way in"
    Destructuring through `match` is the *only* way to read an enum variant's associated
    data. There's no `shape.radius` field to reach for — the data lives inside a particular
    variant, and matching is how you prove you're looking at the right one before you touch
    it.

## The wildcard `_`

Sometimes you only care about one or two variants and want a single catch-all for the
rest. The wildcard pattern `_` matches anything.

```
--8<-- "docs/tutorial/examples/09-pattern-matching/wildcard.sushi"
```

Output:

```
On the way to Magrathea
Not currently traveling
Not currently traveling
Not currently traveling
```

Here only `Traveling` gets special treatment; `Idle`, `Panicking`, and `Lost` all fall
through to `_`. The wildcard also works *inside* a variant to ignore data you don't need:
`Status.Panicking(_)` matches any panic level without binding it. Use `_` to keep a match
focused on the cases that actually matter.

## Nested patterns

Patterns can reach more than one level deep. Because `Result` and the enums it wraps are
themselves enums, you can match a `Result.Err(...)` *and* the specific error variant inside
it in a single pattern.

```
--8<-- "docs/tutorial/examples/09-pattern-matching/nested.sushi"
```

Output:

```
Failed: the drive is not configured.
Failed: improbability is too low.
Success: We have arrived... somewhere.
```

Look at `Result.Err(DriveError.NotConfigured()) ->`. That single pattern says "this is an
`Err`, *and* the error inside it is specifically `NotConfigured`". You handle each failure
mode distinctly without unwrapping the `Result` first and matching again. Nested patterns
keep error handling flat and readable, even when the data is several layers deep.

## Exhaustiveness: the compiler has your back

Here's the feature that makes pattern matching more than a fancy `switch`. When you match
on an enum, Sushi **requires you to handle every variant** (or cover the leftovers with a
`_`). Forget one, and the program won't compile.

That's not a nuisance — it's a safety net. Suppose you later add a fourth variant to an
enum. Every `match` that doesn't account for it suddenly fails to compile, pointing you at
exactly the code that needs updating. Whole categories of "oops, I forgot the new case"
bugs simply can't reach a running program. It's the same instinct behind `Result` itself:
make the compiler force you to deal with every possibility, so your users never trip over
the one you missed.

!!! note "Two ways to be exhaustive"
    You can list every variant explicitly, or list the ones you care about and finish with
    `_` as a catch-all. Both satisfy the exhaustiveness check. Prefer listing variants
    explicitly when you genuinely want different behaviour for each — that way, adding a new
    variant later *forces* you to revisit the match instead of silently sliding into the
    `_` branch.

## What you learned

- `match` selects a branch by the shape of a value and destructures variant data into
  named variables (`Shape.Circle(r) -> ...`).
- The wildcard `_` is a catch-all, both as a whole-pattern fallback and inside a variant to
  ignore data.
- Patterns nest: `Result.Err(DriveError.NotConfigured())` matches the outer and inner
  variants together.
- Matching on an enum is **exhaustive** — the compiler insists every variant is handled,
  turning forgotten cases into compile errors instead of runtime bugs.

Next we'll write our own generic types and functions. On to [Generics](10-generics.md).
