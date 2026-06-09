# 3. Control Flow

So far our programs have run straight from top to bottom. Real programs need to *decide*
and *repeat*. If you've written `if`/`else` and loops in Python or Java, everything here
will feel familiar — the main surprises are small syntactic ones, like the parentheses
around conditions and the colon-and-indent block style.

## Making decisions with `if`

An `if` tests a boolean condition. The condition goes in parentheses, the line ends in a
colon, and the body is indented. You can chain alternatives with `elif` (one word, like
Python) and finish with an `else`:

```sushi
--8<-- "docs/tutorial/examples/03-control-flow/conditionals.sushi"
```

Output:

```
That is the answer.
You are a hoopy frood.
```

The condition must be a `bool`. A `bool` variable like `has_towel` can stand on its own —
you don't need to write `if (has_towel == true)`. Comparisons (`==`, `!=`, `<`, `<=`, `>`,
`>=`) and the logical operators `and`, `or`, and `not` all produce booleans you can test.

!!! note "Conditions are strict booleans"
    Unlike Python or C, Sushi won't treat `0`, an empty string, or any non-boolean as a
    truth value. `if (count)` is an error; write `if (count > 0)`.

## Repeating with `while`

A `while` loop runs its body over and over as long as its condition stays true. Remember
that rebinding a variable uses `:=`, which is how we make the loop eventually stop:

```sushi
--8<-- "docs/tutorial/examples/03-control-flow/while-loop.sushi"
```

Output:

```
Hyperspace countdown:
T-minus 5
T-minus 4
T-minus 3
T-minus 2
T-minus 1
Jump!
```

## Iterating with `foreach` and ranges

When you want to walk over a sequence of values, reach for `foreach`. The cleanest source
of values is a **range**. Ranges come in two flavours:

- `start..end` is **exclusive** — it stops just before `end` (so `0..5` yields `0,1,2,3,4`).
- `start..=end` is **inclusive** — it includes `end` (so `1..=3` yields `1,2,3`).

If `start` is greater than `end`, the range counts **down** automatically. You can also
`foreach` over an array by calling `.iter()` on it:

```sushi
--8<-- "docs/tutorial/examples/03-control-flow/ranges.sushi"
```

Output:

```
Exclusive 0..5:
  0
  1
  2
  3
  4
Inclusive 1..=3:
  1
  2
  3
Descending 3..0:
  3
  2
  1
Over a list:
  Arthur
  Ford
  Trillian
```

Ranges compile down to plain counting loops — there's no iterator object allocated behind
the scenes, so they're free. (`from([...])` builds an array literal; arrays get their own
[chapter](07-arrays.md) later.)

## `break` and `continue`

Inside any loop, `break` exits the loop immediately, and `continue` skips to the next
iteration without running the rest of the body. They work the same as in Python, Java, and
C:

```sushi
--8<-- "docs/tutorial/examples/03-control-flow/break-continue.sushi"
```

Output:

```
Numbers 1..10, skipping 5, stopping at 8:
  1
  2
  3
  4
  6
  7
```

`5` is missing because `continue` skipped its `println`, and the loop halts before `8`
because `break` fired. Using one of these outside a loop is a compile error — the compiler
won't let a stray `break` slip through.

## What you learned

- `if` / `elif` / `else` choose between branches; conditions go in parentheses and must be
  real `bool` values.
- `while (condition):` repeats while the condition holds; rebind with `:=` to make
  progress.
- `foreach(x in source):` iterates; ranges give you `start..end` (exclusive), `start..=end`
  (inclusive), and automatic descending order.
- `.iter()` lets `foreach` walk an array.
- `break` leaves a loop early; `continue` jumps to the next iteration.

We've been calling `println` and `from` without thinking about it. Time to write our own
functions. On to [Functions](04-functions.md).
