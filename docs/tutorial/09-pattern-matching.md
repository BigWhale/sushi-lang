# 9. Pattern Matching

The previous chapter built enums but could only peek inside them with a `match` we hadn't
properly explained. Time to fix that. **Pattern matching** is how you inspect a value,
figure out which shape it has, and pull out the data it carries — all at once, and in a way
the compiler can check for completeness. If you've used Rust's `match` or a `switch`
expression in modern Java, you'll recognise the idea; if you've only had Python's
`if/elif` ladders or `match` statements, this is the same idea with teeth: the compiler
won't let you forget a case.

## Matching and destructuring

A `match` statement takes a value and lists patterns, each with an arrow `->` and a body.
Sushi tries each pattern in turn and runs the body of the first that fits. When a variant
carries data, the pattern can **destructure** it: name the data, and it becomes a variable
inside that branch.

```sushi
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

A `match` is a statement, not an expression: it does not give a value. `let string w =
match n:` is a syntax error. To get a value out of a `match`, assign it or return it in
each arm.

!!! note "`match` is the way in"
    Destructuring through `match` is the *only* way to read an enum variant's associated
    data. There's no `shape.radius` field to reach for — the data lives inside a particular
    variant, and matching is how you prove you're looking at the right one before you touch
    it.

## The wildcard `_`

Sometimes you only care about one or two variants and want a single catch-all for the
rest. The wildcard pattern `_` matches anything.

```sushi
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

```sushi
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
bugs simply can't reach a running program. A missing variant is the error [`CE2040`](../error-catalog.md#ce2040), and
the message names the variants that you did not handle. It's the same instinct behind
`Result` itself: make the compiler force you to deal with every possibility, so your users
never trip over the one you missed.

!!! note "Two ways to be exhaustive"
    You can list every variant explicitly, or list the ones you care about and finish with
    `_` as a catch-all. Both satisfy the exhaustiveness check. Prefer listing variants
    explicitly when you genuinely want different behaviour for each — that way, adding a new
    variant later *forces* you to revisit the match instead of silently sliding into the
    `_` branch.

## Matching integers

A `match` also works on an integer. Each arm is an integer literal, in any base. An integer
has too many values to list, so a trailing `_` arm is required ([`CE2074`](../error-catalog.md#ce2074)).

```sushi
--8<-- "docs/tutorial/examples/09-pattern-matching/integer-arms.sushi"
```

Output:

```
0: nothing at all
42: the answer
10752: a much larger answer
7: just a number
```

Two arms with the same value are an error ([`CE2075`](../error-catalog.md#ce2075)), also when they use different bases,
as `42` and `0x2A` do. Every arm must fit the type of the matched value: an enum arm in a
`match` on an integer is the error [`CE2076`](../error-catalog.md#ce2076).

## Matching strings

A `match` also works on a `string`. Each arm is a string literal, in double quotes or in
single quotes. A string also has too many values to list, so a trailing `_` arm is
required ([`CE2074`](../error-catalog.md#ce2074)).

```sushi
fn reply(string word) string:
    match word:
        "hello" -> return "Mostly Harmless"
        "bye" -> return "So long, and thanks for all the fish"
        _ -> return "Don't Panic"

fn main() i32:
    println(reply("hello"))
    println(reply("Hello"))
    return 0
```

Output:

```
Mostly Harmless
Don't Panic
```

The match compares bytes, so `"Hello"` does not match `"hello"`. The arms are tested in
order, from the top. A string literal is also legal inside a pattern, for example
`Maybe.Some("--help")` or `("go", direction)`. Two arms with the same value are the error
[`CE2075`](../error-catalog.md#ce2075), also when one uses double quotes and the other single quotes.

## Alternatives

One arm can hold several patterns, with `|` between them. The arm runs when one of the
patterns matches. Each pattern is an **alternative**.

```sushi
--8<-- "docs/tutorial/examples/09-pattern-matching/alternatives.sushi"
```

Output:

```
agree
agree
refuse
unclear
2
3
0
```

The arm `Drink.Tea(n) | Drink.Coffee(n)` binds `n` in both alternatives, so the arm body
can read `n` whatever drink matched. Each alternative must bind the same names, with the
same types and the same mode; a difference is the error [`CE2126`](../error-catalog.md#ce2126). An
alternative can also stand inside a pattern: `Maybe.Some(1 | 2)` matches a `Some` that holds
1 or 2. Put a space on each side of `|`: the compiler reads `||` as the logical `or`.

## Binding modes

A name in a pattern, such as `count` in `Cargo.Crates(count)`, has a **mode**, like a
parameter does ([Chapter 4](04-functions.md#parameter-modes)):

- A bare name **borrows** the data. You can read it; the matched value keeps it.
- `poke name` points into the data. A write through the name changes the matched value.
- `nom name` **takes** the data out of the matched value. The arm owns it and can give it
  away with `nom`.

A `nom` binding needs a value that the `match` owns. A temporary value, such as the
result of the call `load()`, is owned by the `match`. To give a local variable to a
`match`, write `match nom manifest:`; after this, `manifest` is not usable. A `nom`
binding in a plain `match manifest:` is the error [`CE2432`](../error-catalog.md#ce2432).

```sushi
--8<-- "docs/tutorial/examples/09-pattern-matching/binding-modes.sushi"
```

Output:

```
Crates: 3
Crates now: 4
Names kept: 2
Names kept: 1
```

For a plain value such as an `i32`, the bare borrow is almost always what you want. `nom`
is for data that owns memory, such as the `string[]` here.
[Chapter 12](12-memory-management.md) explains ownership in full.

## What you learned

- `match` selects a branch by the shape of a value and destructures variant data into
  named variables (`Shape.Circle(r) -> ...`).
- The wildcard `_` is a catch-all, both as a whole-pattern fallback and inside a variant to
  ignore data.
- Patterns nest: `Result.Err(DriveError.NotConfigured())` matches the outer and inner
  variants together.
- Matching on an enum is **exhaustive** — the compiler insists every variant is handled
  ([`CE2040`](../error-catalog.md#ce2040)), turning forgotten cases into compile errors instead of runtime bugs.
- A `match` on an integer or on a `string` uses literal arms and needs a trailing `_`.
- `|` puts several alternatives in one arm (`"yes" | "y" ->`); each alternative binds the
  same names.
- A pattern binding borrows by default; `poke` writes into the data, and `nom` takes it
  from a value that the `match` owns.
- `match` is a statement; it does not give a value.

Next we'll write our own generic types and functions. On to [Generics](10-generics.md).
