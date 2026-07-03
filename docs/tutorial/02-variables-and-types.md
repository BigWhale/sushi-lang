# 2. Variables & Types

A program is mostly about moving data around, so the first thing to learn is how to
*name* data. In Python you write `answer = 42` and the language figures out the type. In
Java you write `int answer = 42;`. Sushi is closer to Java: every variable has a type, and
you say so when you declare it. The compiler then holds you to it.

## Declaring variables

You introduce a new variable with `let`, followed by its **type**, its **name**, and an
initial value:

```sushi
let i32 answer = 42
```

Read it as "let the `i32` named `answer` be `42`". The type comes *before* the name, which
trips up people coming from C or Java for about five minutes and then feels natural.

Local variables are **mutable** — you can change a variable's value after declaring it.
There's no `mut` keyword to write, because a `let` has no immutable variety to distinguish
it from. (Compile-time immutability is a separate construct, `const`, not a flavour of
`let`.)

Changing a variable uses a different operator from declaring one: you **reassign** with
`:=`, not `=`. The single `=` belongs only to the initial `let`; `:=` updates an
already-declared variable *in place*. Keeping the two separate means you can always tell at
a glance whether a line introduces a new variable or updates an existing one — and a typo
can't silently create one.

```sushi
--8<-- "docs/tutorial/examples/02-variables-and-types/declaring.sushi"
```

Output:

```
Name: Arthur Dent
The answer: 42
Has a towel: 1
Recalculated (wrongly): 54
That is better: 42
```

Two things to notice. We used `{name}` inside the string to splice a variable's value
into the text — that's **string interpolation**, and it only works in double-quoted
strings. And `has_towel`, a `bool`, printed as `1` rather than `true`: booleans display as
`1` (true) and `0` (false).

!!! note "`:=` only reassigns existing variables"
    If you write `score := 10` without ever having declared `score` with `let`, the
    compiler stops you with "assignment to undeclared variable". And reassignment must keep
    the same type — you can't `:=` a `string` into an `i32`, because `:=` writes into the
    existing variable rather than making a new one.

!!! note "Reassigning vs. shadowing"
    `:=` is **reassignment**: the same variable, a new value, the same type. Declaring the
    name again with `let` is something different — **shadowing** — which introduces a
    *separate* variable that reuses the name and leaves the original untouched. The compiler
    warns when a `let` shadows a name from an outer scope (`CW1002`), so when you mean
    "update this variable," reach for `:=`. This is the mirror image of languages like Rust,
    which make variables immutable by default and lean on shadowing instead.

## The primitive types

Sushi's built-in scalar types are explicit about size and signedness:

- **Signed integers**: `i8`, `i16`, `i32`, `i64`
- **Unsigned integers**: `u8`, `u16`, `u32`, `u64`
- **Floating point**: `f32`, `f64`
- **Boolean**: `bool` (`true` / `false`)
- **Text**: `string` (UTF-8, covered properly in [Chapter 5](05-strings.md))

The number after `i` or `u` is the width in bits, so an `i32` holds roughly plus or minus
two billion, and a `u8` holds `0` to `255`. When you write a plain integer literal with no
other hint, its default type is `i32`.

## Numeric literals

Integers can be written in four bases. Hexadecimal uses a `0x` prefix, binary `0b`, and
octal `0o` (the C-style bare leading zero, like `0755`, is deliberately rejected to avoid
confusion). The prefixes are case-insensitive. In the prefixed forms you may group digits
with underscores for readability:

```sushi
--8<-- "docs/tutorial/examples/02-variables-and-types/literals.sushi"
```

Output:

```
decimal: 42
hex 0x2A: 42
binary 0b101010: 42
octal 0o52: 42
grouped hex 0xDEAD_BEEF: 3735928559
grouped binary 0b1010_1010: 170
```

The first four lines are the same value, `42`, written four ways. The underscores in
`0xDEAD_BEEF` and `0b1010_1010` are purely cosmetic — the compiler ignores them. (We cast
`0xDEAD_BEEF` to `u32` because it's larger than a signed `i32` can hold without wrapping
to a negative number.)

!!! note "Underscores need a radix prefix"
    Digit grouping with `_` works in the `0x`, `0b`, and `0o` forms. A plain decimal like
    `1_000_000` is *not* accepted, so write large decimals without underscores.

## Casting with `as`

Sushi will not silently mix numeric types for you. If you have an `i32` and you want true
fractional division, or you need to widen a value to a larger type, you convert explicitly
with the `as` operator:

```sushi
--8<-- "docs/tutorial/examples/02-variables-and-types/casts.sushi"
```

Output:

```
integer division 42 / 5: 8
float division 42.0 / 5.0: 8.4
widened to i64: 42
u8 max: 255
```

Dividing two `i32` values does **integer** division (`42 / 5` is `8`, the remainder is
dropped). Cast both operands to `f64` first and you get `8.4`. Notice that whole floats
like `42.0` print without a trailing `.0`.

## The blank type `~`

Some functions exist only for their side effects — they print something and have no
meaningful value to hand back. Their return type is the **blank type**, written `~`, which
is Sushi's equivalent of `void` in C or Java, or returning `None` in Python. Even a blank
function ends with `return Result.Ok(~)`: it still reports success, it just has nothing
useful to carry.

## Block scope

A variable declared inside a block — the indented body of an `if`, a loop, or any
function — lives only until that block ends. Variables from an enclosing block are still
visible inside the nested one.

```sushi
--8<-- "docs/tutorial/examples/02-variables-and-types/blank-and-scope.sushi"
```

Output:

```
outer is 1
inner is 2
outer is still visible: 1
Now boarding: Ford Prefect
```

Here `inner` exists only inside the `if`, while `outer` is reachable both inside and after
it. The `announce` function uses the blank type: it prints a boarding call and returns
nothing.

## What you learned

- Declare variables with `let Type name = value`; reassign them in place with `:=` (a
  second `let` with the same name *shadows* rather than updates).
- Primitive types are explicit about size and signedness: `i8`..`i64`, `u8`..`u64`, `f32`,
  `f64`, `bool`, `string`. A bare integer literal defaults to `i32`.
- Integer literals come in decimal, `0x` hex, `0b` binary, and `0o` octal; the prefixed
  forms allow `_` digit grouping.
- Convert between numeric types explicitly with `as` — nothing happens implicitly.
- `~` is the blank (void-style) type for functions that return nothing.
- Variables are scoped to the block they're declared in.

Next we put these values to work making decisions and repeating ourselves. On to
[Control Flow](03-control-flow.md).
