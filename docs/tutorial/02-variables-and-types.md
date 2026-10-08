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
it from. For a value that never changes, use a `const` (see
[Constants and unit variables](#constants-and-unit-variables) below).

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
Has a towel: true
Recalculated (wrongly): 54
That is better: 42
```

Two things to notice. We used `{name}` inside the string to splice a variable's value
into the text — that's **string interpolation**, and it only works in double-quoted
strings. And `has_towel`, a `bool`, printed as the word `true`: a boolean displays as
`true` or `false` in every position.

!!! note "`:=` only reassigns existing variables"
    If you write `score := 10` without ever having declared `score` with `let`, the
    compiler stops you with "assignment to undeclared variable". And reassignment must keep
    the same type — you can't `:=` a `string` into an `i32`, because `:=` writes into the
    existing variable rather than making a new one.

!!! note "Reassigning vs. shadowing"
    `:=` is **reassignment**: the same variable, a new value, the same type. A `let` in a
    *nested* block can use a name that an outer block already declares. This is
    **shadowing**: the inner `let` makes a *separate* variable, and the outer variable does
    not change. The compiler gives the warning [`CW1002`](../error-catalog.md#cw1002) for each shadow. When you want to
    change a variable, use `:=`. A second `let` of one name in the *same* block is the
    error [`CE1006`](../error-catalog.md#ce1006).

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
confusion). The prefixes are case-insensitive. In every base you may group digits
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
grouped decimal 1_000_000: 1000000
grouped float 3.141_592: 3.14159
```

The first four lines are the same value, `42`, written four ways. The underscores in the
last four are purely cosmetic — the compiler ignores them.

A literal gets its type from its context. In `let u32 flags = 0xDEAD_BEEF`, the annotation
`u32` gives the literal the type `u32`, so no cast is necessary. A parameter, a struct field
and the other operand of an operator also give a literal its type. When there is no
context, an integer literal is an `i32` and a float literal is an `f64`. A literal that the
type cannot hold is an error: `let u8 b = 256` gives [`CE2073`](../error-catalog.md#ce2073) ("literal 256 out of range for
u8").

!!! note "One underscore, between two digits"
    Digit grouping with `_` works in every base — `1_000_000` and `3.141_592` as much as
    `0xDEAD_BEEF` — and in all three parts of a float, so `1_0.2_5e1_0` is legal. The rule
    is that an underscore must have a digit on each side. So `1__0`, `1_`, `0x_FF` and
    `3._14` are all rejected, each with a message naming the problem and the fix.

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
whole float: 42
```

Dividing two `i32` values does **integer** division (`42 / 5` is `8`, the remainder is
dropped). Cast both operands to `f64` first and you get `8.4`. A whole float such as
`42.0` prints without a trailing `.0`.

The `u8` line has no cast: the annotation gives the literal `255` the type `u8`. A cast is
for a *value* that already has a type, such as the `i32` variable `distance`. Literal
typing does not convert a value.

## The blank type `~`

Some functions exist only for their side effects — they print something and have no
meaningful value to hand back. Their return type is the **blank type**, written `~`, which
is Sushi's equivalent of `void` in C or Java, or returning `None` in Python. A bare blank
function can reach the end of its body with no `return`. A blank function with an error
channel (`fn f() ~ | E:`) ends with `return Result.Ok(~)`: it reports success, with no
useful value to carry.

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

## Constants and unit variables

A `const` is a named value that the compiler calculates at compile time. You declare it at
the top level of a file, outside every function. A `const` never changes; an assignment to
one is an error.

A `var` at the top level is **unit-level storage**: one variable for the whole program.
The program sets its value before `main` starts. Every function in the file can read it and
change it with `:=`.

```sushi
--8<-- "docs/tutorial/examples/02-variables-and-types/constants.sushi"
```

Output:

```
Heart of Gold carries 4 crew
Jumps so far: 2
```

The initializer of a `const` or a `var` must be a constant expression: literals, other
constants, operators and `as`. It cannot call a function. By default, a `const` and a `var`
are private to their file; [Chapter 4](04-functions.md#public-and-private-declarations)
shows how to make them `public`.

## What you learned

- Declare variables with `let Type name = value`; reassign them in place with `:=`. A
  `let` in a nested block that reuses an outer name *shadows* it ([`CW1002`](../error-catalog.md#cw1002)).
- Primitive types are explicit about size and signedness: `i8`..`i64`, `u8`..`u64`, `f32`,
  `f64`, `bool`, `string`. A bare integer literal defaults to `i32`.
- Integer literals come in decimal, `0x` hex, `0b` binary, and `0o` octal; every base
  allows `_` digit grouping.
- A literal gets its type from its context (`let u8 b = 255`). Convert a *value* between
  numeric types explicitly with `as` — nothing happens implicitly.
- `~` is the blank (void-style) type for functions that return nothing.
- Variables are scoped to the block they're declared in.
- `const` is a compile-time value; a top-level `var` is one variable for the whole program.

Next we put these values to work making decisions and repeating ourselves. On to
[Control Flow](03-control-flow.md).
