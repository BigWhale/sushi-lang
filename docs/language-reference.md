# Language Reference

[← Back to Documentation](index.md)

Complete syntax and semantics reference for Sushi Lang. For a gentler introduction, see the [Language Guide](language-guide.md).

## Table of Contents

- [Program Structure](#program-structure)
- [Types](#types)
- [Variables](#variables)
- [Functions](#functions)
- [Operators](#operators)
- [Control Flow](#control-flow)
- [Arrays](#arrays)
- [Structs](#structs)
- [Tuples](#tuples)
- [Enums](#enums)
- [Error Types and Conversions](#error-types-and-conversions)
- [Pattern Matching](#pattern-matching)
- [Generics](#generics)
- [Extension Methods](#extension-methods)
- [Perks](#perks)
- [Ownership Operations](#ownership-operations)
- [Closures](#closures)
- [Variadic Functions](#variadic-functions)
- [Foreign Functions](#foreign-functions)
- [Module System](#module-system)
- [Comments](#comments)
- [Documentation Blocks](#documentation-blocks)
- [Keywords](#keywords)
- [Constants](#constants)
- [Unit Variables](#unit-variables)

## Program Structure

Every Sushi program must have a `main` function that returns `i32`:

```sushi
fn main() i32:
    # Program entry point
    return 0
```

`main` returns an integer type ([`CE0106`](error-catalog.md#ce0106)) and takes one parameter, `string[] args`, or no
parameter at all. The type and the name are both part of the rule: `fn main(string[] argv)`,
`fn main(i32 x)` and `fn main(string[] args, i32 x)` are [`CE0138`](error-catalog.md#ce0138). `args` holds the program
name and then each command-line argument, and it is a borrowed view ([`CE2410`](error-catalog.md#ce2410) refuses a move).

### Lines and Continuation

A statement ends at the end of its line. An expression continues onto the next line only
inside `(` or `[`, where layout is free. Parentheses are the continuation mechanism, by
design: there is no continuation character, and a trailing operator does not absorb the
newline. A long expression takes one outer pair:

```sushi
const i32 B_UPPER_A = 65
const i32 B_UPPER_Z = 90
const i32 B_LOWER_A = 97
const i32 B_LOWER_Z = 122

extend u8 is_alpha() bool:
    return ((self as i32 >= B_UPPER_A and self as i32 <= B_UPPER_Z)
        or (self as i32 >= B_LOWER_A and self as i32 <= B_LOWER_Z))

fn main() i32:
    let u8 letter = 66
    let bool a = letter.is_alpha()
    println("{a}")
    return 0
```

Without the outer parentheses the second line starts a new statement, and the parse
fails with [CE6001](error-catalog.md#ce6001).

## Types

### Primitive Types

**Integers (signed):**
- `i8` - 8-bit signed integer (-128 to 127)
- `i16` - 16-bit signed integer (-32,768 to 32,767)
- `i32` - 32-bit signed integer (-2,147,483,648 to 2,147,483,647)
- `i64` - 64-bit signed integer (-9,223,372,036,854,775,808 to 9,223,372,036,854,775,807)

**Integers (unsigned):**
- `u8` - 8-bit unsigned integer (0 to 255)
- `u16` - 16-bit unsigned integer (0 to 65,535)
- `u32` - 32-bit unsigned integer (0 to 4,294,967,295)
- `u64` - 64-bit unsigned integer (0 to 18,446,744,073,709,551,615)

**Floating-point:**
- `f32` - 32-bit IEEE 754 floating-point
- `f64` - 64-bit IEEE 754 floating-point

**Other:**
- `bool` - Boolean (`true` or `false`)
- `string` - a sized UTF-8 string: a data pointer, a byte count and an ownership flag. It
  has no terminator, so an embedded `\0` is one more byte of the length (`"a\0b".len()` is
  3). The layout is in [String Representation](design/string-representation.md)
- `~` - the blank type and its one value. It is a return type (`fn f() ~`), a function-type
  return (`fn() -> ~`), and a value (`Result.Ok(~)`, a match arm `-> ~`). As a unary
  operator, `~` is the bitwise NOT (see [Bitwise](#bitwise))

### Numeric Literals

**Decimal literals** (default):
```sushi
let i32 dec = 42
let i32 large = 1000000
```

**Hexadecimal literals** (base 16, prefix `0x` or `0X`):
```sushi
let i32 hex = 0xFF           # 255
let i32 addr = 0xDEAD_BEEF   # underscores allowed
let i32 mask = 0xFF00
```

**Binary literals** (base 2, prefix `0b` or `0B`):
```sushi
let i32 bin = 0b1111         # 15
let i32 flags = 0b1010_1010  # underscores allowed
let i32 byte = 0b11111111
```

**Octal literals** (base 8, prefix `0o` or `0O`):
```sushi
let i32 oct = 0o755          # 493 (Unix permissions)
let i32 perm = 0o644         # 420
```

**Note**: C-style octals with leading zeros (e.g., `077`) are **not supported** and will cause a compilation error. Use the explicit `0o` prefix instead.

**Byte literals** (`a'x'`, "the ASCII code of x"):

`a'x'` is one byte, 0 to 255. A character from 0 to 127 is written as itself (`a'/'`,
`a'\n'`) or as an escape. A byte from 128 to 255 is written as an escape (`a'\xe9'`). A
Sushi string is UTF-8, so a character above 127 is more than one byte and cannot be one
`a'...'` literal.

The literal takes single quotes only, and the `a` touches the quote. `a '/'`, with a space,
is a parse error. A variable with the name `a` stays legal.

| Escape | Byte |
|---|---|
| `\n` | 10 (newline) |
| `\t` | 9 (tab) |
| `\r` | 13 (carriage return) |
| `\0` | 0 |
| `\\` | 92 (backslash) |
| `\'` | 39 (single quote) |
| `\"` | 34 (double quote) |
| `\xNN` | the byte `NN`: exactly two hex digits, `00` to `ff`, in either case |

`\xNN` is legal below 128 too: `a'\x2f'` is `a'/'`.

Two codes report a literal that is not one byte:

- An empty literal (`a''`), more than one character (`a'ab'`), an unknown escape
  (`a'\q'`) and a `\x` without two hex digits (`a'\x4'`) are
  **[CE6014](error-catalog.md#ce6014)**. The message names the fault.
- A character above 127, written as itself (`a'é'`), is
  **[CE6015](error-catalog.md#ce6015)**. The help gives the UTF-8 bytes of the character.
  For a byte from 128 to 255, the help also gives the escape. The compiler assumes no
  character set (Latin-1, Windows-1252).

A byte literal takes its type from its position, as a number literal does. With no
context, it is a `u8`. A value that does not fit the type of the position is
**[CE2073](error-catalog.md#ce2073)**. The range rule is the decimal rule: `a'\xc8'` is 200,
and 200 does not fit an `i8`. The hex literal `0xc8` fits an `i8` as a bit pattern, but
the byte literal does not.

When the two operands of one operation are bare literals and one of them is a byte
literal, the number literal takes `u8`. `a'a' + 1` and `1 + a'a'` are both `u8`, and
`a'a' + 300` is CE2073.

```sushi
fn is_digit(u8 b) bool:
    return b >= a'0' and b <= a'9'

const u8 SEPARATOR = a'/'

fn main() i32:
    let string path = "usr/bin"
    let bool slash = path[3] == SEPARATOR
    let i32 code = a'A'                 # 65: the position gives i32
    let u8 next = a'a' + 1              # 98: the pair rule gives u8
    println("{a'/'} {slash} {code} {next} {is_digit(a'7')}")   # 47 true 65 98 true
    return 0
```

<!-- docs-sweep: error CE2073 -->
```sushi
fn main() i32:
    let i8 pattern = 0xc8               # legal: the bit pattern -56
    let i8 byte = a'\xc8'               # CE2073: 200 does not fit i8
    println("{pattern} {byte}")
    return 0
```

A byte literal is not an array size, and `-a'x'` is not a match arm. In an expression,
`-a'/'` follows the number-literal rules: with no context it is CE2073 (-47 does not fit
`u8`), and in an `i32` position it is -47.

A byte literal is not a string. In a string, `\xNN` gives a character: `"\xe9"` is the
character U+00E9, which is two bytes in UTF-8. `a'\xe9'` is the one byte 233. A string
also keeps an unknown escape as text (`"\q"` is the two characters `\q`), but a byte
literal refuses it (CE6014).

**Decimal literals** (base 10, no prefix):
```sushi
let i32 grouped = 1_000_000   # underscores allowed
let f64 pi = 3.141_592        # in a float's fraction too
let f64 big = 1_0.2_5e1_0     # and in all three parts at once
```

**Common features**:
- Every number literal format supports underscore separators for readability, decimal and
  float included. One underscore, and it must have a digit on each side — so `1__0`,
  `1_`, `0x_FF` and `3._14` are rejected (**[CE6006](error-catalog.md#ce6006)**), each naming the fix
- Prefixes are case insensitive (`0xFF` == `0xff`, `0B1111` == `0b1111`)
- A literal is **context-typed**: it takes its type from context (annotation,
  argument, field, operand). With no numeric context it defaults to `i32` (a byte
  literal defaults to `u8`).

**Context typing**: a bare literal is typed by its
expected type and range-checked at compile time, so no cast is needed to write a
literal of a non-`i32` type. A decimal literal uses value ranges (signed/unsigned per
type); a hex/binary/octal literal uses the target's bit-pattern width (so `0xFF` is a
valid `i8` — the pattern `-1`); an `f32` rejects overflow to infinity (precision loss
on `f64`->`f32` is silently rounded). An out-of-range literal is [`CE2073`](error-catalog.md#ce2073), and an
operation whose *result* leaves the type is [`CE2077`](error-catalog.md#ce2077) (see [Overflow](#overflow)). This is
literal *typing*, not value coercion — converting an already-typed value still needs
`as` (see [Type Conversion](#type-conversion)).

The type reaches the literal through every operator whose result is its operand's
type: the arithmetic and bitwise operators, a shift's left operand, unary minus, and
`~`. It stops at an operator that answers something else -- a comparison and `not`
both give a `bool` -- and it never converts an already-typed value.

```sushi
let i64 big   = 40000000000                # context-typed i64, no cast needed
let u64 max   = 18446744073709551615       # context-typed u64
let u32 mask  = 0x01 | 0x02 | 0x04         # operands typed u32
let u8  all   = ~0                         # 255: the complement of a u8 zero
let i8  small = 200                        # CE2073: out of range for i8
```

**No-context default** ([`CE2070`](error-catalog.md#ce2070)): a literal with no numeric context defaults to
`i32`, and a bare decimal above the signed range (or a radix literal above the
32-bit pattern) is a compile error. A literal cast directly with `as` is exempt and
materializes at the target width:

```sushi
println(40000000000)              # CE2070: context-free, defaults to i32 and overflows
let i64 x = 40000000000 as i64    # exempt: materializes at i64 width
```

### Type Conversion

All type conversions must be explicit using the `as` keyword:

```sushi
let i32 x = 42
let f64 y = x as f64        # int to float
let i16 small = y as i16    # float to int (truncates)
let u32 unsigned = x as u32 # signed to unsigned
```

**Rules:**
- A cast is between two numeric types, or between two error types that have a declared
  conversion (`e as AppError`, see [Error Types and Conversions](#error-types-and-conversions))
- Float-to-integer truncates toward zero
- No implicit conversions
- No casting to/from strings or arrays
- Every other pair is [`CE2014`](error-catalog.md#ce2014)

### Array Types

**Fixed arrays:**
```sushi
let i32[5] fixed = [1, 2, 3, 4, 5]
```

**Dynamic arrays:**
```sushi
let i32[] dynamic = from([1, 2, 3])
let string[] empty = new()
let u8[] none = from([])
```

An empty `from([])` and a `new()` spell no element type of their own: each takes the type
of its position -- a `let`, a struct field, a `Result.Ok` payload, a parameter, a
`.realise()` default, an extension's bare `return`. So `make().realise(from([]))` and
`return from([])` in an `extend S empty() u8[]` both mean `u8[]`. A position with no type
-- a method receiver, an index base, a `println` argument -- gives no element type, and
the empty literal there is [`CE2111`](error-catalog.md#ce2111). Declare the array first (`let i32[] xs = from([])`)
and use the name.

**Nested arrays:** an array element can be an array. The suffixes read from LEFT TO RIGHT:
a suffix applies to the type on its left.

| Written | Is | Layout |
|---|---|---|
| `i32[][]` | a dynamic array of `i32[]` | a descriptor of descriptors |
| `i32[3][]` | a dynamic array of `i32[3]` | a descriptor; each slot is 12 bytes inline |
| `i32[][3]` | a fixed array of 3 `i32[]` | 3 descriptors inline |
| `i32[2][3]` | a fixed array of 3 `i32[2]` | `[3 x [2 x i32]]` |

An index removes the LAST suffix. For `i32[2][3] m`, `m[i]` is an `i32[2]`, `i` is in
`0..3`, and `m[i][j]` has `j` in `0..2`. **C reads the other way**: in C, `int m[2][3]` is
2 rows of 3. In Sushi, `i32[2][3]` is 3 rows of 2.

```sushi
fn main() i32:
    let i32[2][3] m = [[1, 2], [3, 4], [5, 6]]
    let i32[][] d = from([from([1]), from([2, 3])])
    let i32[2][] g = from([[1, 2], [3, 4]])
    d[0].push(9)
    m[2][0] := 7
    println("{m.len()} {m[0].len()} {m[2][0]} {d[0][1]} {g[1][1]}")   # 3 2 7 9 4
    return 0
```

### Function Types

A function type describes a first-class function value (a bare function pointer). The return
type is mandatory. The optional `| E` gives the function type an error channel. Without it,
the function type is bare. The channel is part of the type, so the two forms do not convert
([`CE2002`](error-catalog.md#ce2002)).

```sushi
fn(i32) -> i32 | MathError     # takes i32, a call returns Result@(i32, MathError)
fn(i32) -> i32                 # bare: a call returns i32
fn(i32, string) -> bool        # two parameters
fn() -> ~                      # no parameters, blank return
```

An array of function values puts the function type in parentheses: `(fn(i32) -> i32)[]` is a
dynamic array, and `(fn(i32) -> i32)[3]` is a fixed array. Without the parentheses, an array
suffix belongs to the return type, so `fn(i32) -> i32[]` is a function that returns `i32[]`.
Parentheses around a function type are legal in every type position and do not change the
type: `List@((fn(i32) -> i32))` is `List@(fn(i32) -> i32)`. A diagnostic prints the
parentheses around a function element of an array.

A function with a channel that returns a function type writes the explicit form,
`fn make() Result@(fn(i32) -> i32, StdError):`. A `| E` written after a function type
belongs to that function type, so `fn make() fn(i32) -> i32 | StdError:` is a bare function
that returns a function type with a channel.

Reference a plain top-level function by name to get a value of that type, then store, pass, or
call through it:

```sushi
let fn(i32) -> i32 f = add_one     # `add_one` used as a value; it is bare
let i32 r = f(41)                  # call through it, like a direct call
```

Function types are invariant (arity, parameters, return, and error type must match exactly).
A plain top-level function is referenceable as above; a **closure** — a capturing lambda literal
(`|i32 x| x + n`) — is also a `fn(...)`-typed value and shares the same call syntax. A **generic**
function is referenceable when the expected function type is explicit (`let fn(i32) -> i32 g =
identity`); otherwise it is [`CE2093`](error-catalog.md#ce2093).

A function of another unit is a function value too: bare through a flat import (`plain`),
and behind the dot of an aliased one (`l.plain`). A private function of another unit is
[`CE3005`](error-catalog.md#ce3005), and a bare name that two imports offer is [`CE3012`](error-catalog.md#ce3012). A bare name names the unit's
own function before an imported function of the same name.

You can also call through any expression that evaluates to a function value, not just a bare name —
a fn-typed struct field (`obj.handler(x)`, when no method of that name exists), a container get-out
(`fns.get(0).or_err(nom StdError.Error)??(x)`), or a parenthesized expression (`(e)(x)`). See the
[First-Class Functions guide](first-class-functions.md) and the [Closures guide](closures.md).

## Variables

### Declaration

Variables must be declared with `let`:

```sushi
let i32 x = 42
let string name = "Arthur"
let bool flag = true
```

### Rebinding

Use `:=` to rebind variables (must be declared first):

```sushi
let i32 x = 10
x := 20     # OK
x := 30     # OK

# ERROR: Cannot rebind without prior declaration
# y := 5    # CE1002: assignment to undeclared variable 'y'
```

A tuple rebinds more than one place at once: `(a, b) := (b, a)` evaluates the whole right
side first and then assigns from left to right, so it is a swap. See
[Destructuring Rebind](#destructuring-rebind).

A name that is a **view of another value's storage** cannot be rebound. A `match` or
`foreach` binding is **[CE2414](error-catalog.md#ce2414)**, a `let` bound from a field read, an index or a container
get-out is **[CE2426](error-catalog.md#ce2426)**, and a `peek` reference is **[CE2408](error-catalog.md#ce2408)** — in each case the store would
free a value the owner still holds. A name with storage of its own is unaffected: a local,
a parameter (a borrow parameter included) and a unit variable are all rebindable.

```sushi
match b:
    Box.Full(s) -> s := "rebound"        # CE2414
    Box.Empty -> println("empty")

match b:
    Box.Full(poke s) -> s := "rebound"   # write through to the owner
    Box.Empty -> println("empty")

match b:
    Box.Full(s) ->
        let string m = s.clone()         # a value of your own; a plain `let` borrows again
        m := "rebound"
    Box.Empty -> println("empty")
```

### Reference bindings

A `let` may bind a **reference** into storage another variable owns, with the mode on the
declaration: `let poke T x = <place>` writes through, `let peek T x = <place>` reads
through. The place is a local, a field or element of one, a unit variable, or an
`Own@(T)`'s payload (`o.get()`); it is written bare, and a call result or a `??` is a
temporary with no address to bind (**[CE2404](error-catalog.md#ce2404)**). The binding is block-scoped, and while it
lives the owner is frozen: mutating, rebinding or moving the owner and then using the
binding is **[CE2412](error-catalog.md#ce2412)**.

```sushi
struct Holder:
    i32 n
    i32[] items

fn main() i32:
    let Own@(Holder) o = Own.alloc(Holder(1, from([])))
    let poke Holder h = o.get()    # a pointer into the Own's cell, no copy
    h.items.push(9)                # reaches the payload
    h.n := 42
    println("{o.get().n} {o.get().items.len()}")   # 42 1
    return 0
```

One `poke` binding of an owner at a time (**[CE2403](error-catalog.md#ce2403)**); a `peek` beside a live `poke`, or
the reverse, is **[CE2407](error-catalog.md#ce2407)**; a write through a `peek` binding is **[CE2408](error-catalog.md#ce2408)**; a `poke`
binding out of a `peek` parameter is **[CE2408](error-catalog.md#ce2408)** too. Consuming the binding stays
**[CE2411](error-catalog.md#ce2411)** -- it names storage the owner still frees -- and `.clone()` is the escape. A
constant is read-only storage: `let peek` reads it, and `let poke` is **[CE2400](error-catalog.md#ce2400)**. A unit
variable takes both.

### Scope

Variables are block-scoped:

```sushi
fn main() i32:
    let i32 x = 1

    if (true):
        let i32 y = 2  # y scoped to if block
        x := 3         # OK: x from outer scope

    # ERROR: y not in scope
    # println(y)

    return 0
```

A block declares a name one time. A second `let` of a name in the same block is
[CE1006](error-catalog.md#ce1006). The parameters of a function, a `foreach` item, an
`expand` binder and the bindings of a match arm are in the scope of the body that they
start, so a `let` in that body cannot repeat them. To change a variable, use `:=`. To use
a name again, declare it in a nested block: that is a shadow
([CW1002](error-catalog.md#cw1002)).

## Functions

### Declaration

```sushi
fn function_name(param1_type param1_name, param2_type param2_name) return_type:
    # Function body
    return value
```

**Example:**

```sushi
use <collections/strings>

fn parse_age(string text) i32 | StdError:
    if (text.is_empty()):
        return Result.Err(StdError.Error)
    return Result.Ok(text.len())

fn add(i32 a, i32 b) i32:
    return a + b

fn greet(string name) ~:
    println("Mostly Harmless, {name}!")
```

### Return Types

A function has an error channel only when its signature writes one. The declaration has
three forms:

| declared return | the call returns |
|---|---|
| `fn f() T \| E` | `Result@(T, E)` |
| `fn f() Result@(T, E)` | `Result@(T, E)`, not wrapped again |
| `fn f() T` | `T`: the function is BARE |

The explicit form already names the error type, so `fn f() Result@(T, E) | E` is [`CE2085`](error-catalog.md#ce2085).
The error type `E` is an ERROR TYPE: an enum declared with `error`, or a predefined one
such as `StdError`. Any other type is [`CE2084`](error-catalog.md#ce2084) (see
[Error Types and Conversions](#error-types-and-conversions)). There is no default error
type: `fn f() T` is bare and returns `T`.

**A bare function is the exception.** Use it seldom: only when the function is total over
its inputs and will stay so (a checksum, a pure arithmetic or string helper, a path join),
and a channel would only force a dead `.realise` or `??` on every caller. Write a channel
for everything else. A public function keeps a channel when there is any doubt, because a
channel added later changes the signature and breaks every caller and every binary `.slib`.
The compiler does not enforce this. The decision record is
[The error channel is opt-in](design/error-channel.md).

```sushi
use <math>

fn halve(i32 a) i32 | StdError:                        # Result@(i32, StdError)
    if (a % 2 != 0):
        return Result.Err(StdError.Error)
    return Result.Ok(a / 2)

fn divide(i32 a, i32 b) i32 | MathError:    # Result@(i32, MathError)
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok(a / b)

fn main() i32:
    println(halve(8).realise(0))            # 4
    println(divide(7, 0).realise(-1))       # -1
    return 0
```

A body with a channel returns `Result.Ok(value)` or `Result.Err(error)`, and nothing wraps a
bare value: a bare `return value` is [`CE2030`](error-catalog.md#ce2030). A BARE body returns the value itself:
`return Result.Ok(...)` there is [`CE2091`](error-catalog.md#ce2091), and `??` there is [`CE0131`](error-catalog.md#ce0131). `MathError` has its
home in `<math>`, and `StdError` is global.

The caller takes the value out of the `Result` in one of three ways: `??` returns the error
from the calling function at once, `.realise(default)` gives the default for an error, and
`match` reads each arm. `??` needs the same error type in the caller, or a declared
conversion from the callee's error type into it. `??` takes a `Result@(T, E)` and nothing
else: on a `Maybe@(T)` it is [`CE2507`](error-catalog.md#ce2507), and `m.or_err(nom e)??` writes the error value. The
call of a bare function returns the value, so `??` on it is [`CE2507`](error-catalog.md#ce2507) and `.realise` on it
is [`CE2008`](error-catalog.md#ce2008).

`main` is bare. It returns the exit code (`return 0`), and a `| E` on it is [`CE0106`](error-catalog.md#ce0106). A `??`
in `main` is [`CE0131`](error-catalog.md#ce0131); use `match` or `.realise()` there. The full guide is
[Error Handling](error-handling.md).

The body must return on every code path. A `~` function with a channel ends with
`return Result.Ok(~)`; a bare `~` function can reach its end. Any other body that can
reach its end is [`CE0107`](error-catalog.md#ce0107).

A statement that can never run is an error too, [`CE0140`](error-catalog.md#ce0140): a statement after a `return`,
after an `if` with an `else` whose every arm returns, after a `match` whose every arm
returns, or after a `break` or a `continue` in the same block. It is reported once per
block, at the first dead statement, with a note at the statement that ends the path.

### Parameters

A parameter declares one of four **modes**. The mode says who frees the value, and a marked
mode is written at the declaration and at the call site alike:

| declaration | call site | who frees | notes |
|---|---|---|---|
| `string x` | `f(s)` | caller | the default; the argument stays usable |
| `nom string x` | `f(nom s)` | **callee** | a later use of the argument is [`CE2405`](error-catalog.md#ce2405) |
| `peek string x` | `f(peek s)` | caller | by pointer, read only; many at once |
| `poke string x` | `f(poke s)` | caller | by pointer, read/write; one, exclusive |

**Unmarked (a borrow):**
```sushi
fn modify(i32 x) i32:
    x := x + 1          # the callee's own copy
    return x
```

**`nom` (a consume):**
```sushi
fn eat(nom string s) ~:
    println(s)
    return ~  # s is freed here

fn main() i32:
    let string base = "Ford"
    let string s = "{base} Prefect"
    eat(nom s)
    # println(s)          # ERROR CE2405: s was handed over
    return 0
```

**Borrowed by pointer:**
```sushi
fn increment(poke i32 counter) ~:
    counter := counter + 1

fn read_value(peek i32 x) i32:
    return x
```

The rule and its reasoning are [docs/design/borrow-model.md](design/borrow-model.md).

## Operators

### Operand types

Two numeric operands of one operator must have the same type. Sushi converts no
numeric type on its own, so the operands say what the result is: `+ - * / %`, the
comparisons `== != < <= > >=`, and the bitwise `& | ^` all refuse a mixed pair
with **[CE2510](error-catalog.md#ce2510)**.

<!-- docs-sweep: error CE2510 -->
```sushi
fn main() i32:
    let u8 low = 0x34
    let u32 wide = 0x1200
    let u32 both = low | wide          # CE2510: u8 and u32
    return 0
```

`as` makes the widths agree, and then the operation says what it means:

```sushi
fn main() i32:
    let u8 low = 0x34
    let u32 wide = 0x1200
    let u32 both = (low as u32) | wide  # 0x1234
    return 0
```

A shift is the exception. Its right operand is a count, not a second value: it
says how far to move the bits, and the result keeps the type of the left operand.
The count can be of any numeric type.

```sushi
fn main() i32:
    let u64 value = 8
    let u8 places = 8
    let u64 shifted = value << places   # 2048
    return 0
```

A count is also limited by the width of the value it shifts, because a count at or
above that width moves every bit out of the type. A count the compiler can read --
a literal, a constant, an expression of them -- is **[CE2512](error-catalog.md#ce2512)**:

<!-- docs-sweep: error CE2512 -->
```sushi
fn main() i32:
    let u8 high = 0x12
    let u8 shifted = high << 8          # CE2512: a u8 count runs from 0 to 7
    return 0
```

Cast the value to the width the shift is meant to reach:

```sushi
fn main() i32:
    let u8 high = 0x12
    let u32 reached = (high as u32) << 8    # 0x1200
    return 0
```

A computed count -- a loop index, a value read from a file -- cannot be read at
compile time, so it is not an error. It has a defined answer instead: a shift by a
count at or above the width moves every bit out of the type and gives **0**. An
arithmetic right shift fills from the sign bit, so it leaves the sign behind: 0 for
a positive value and -1 for a negative one. A negative count is out of range at the
other end and answers the same way.

```sushi
fn shift(u8 value, u8 places) u8:
    return value << places

fn main() i32:
    println("{shift(0x12, 3)}")     # 144
    println("{shift(0x12, 8)}")     # 0 -- every bit has left the u8
    return 0
```

The count is never masked. Masking is what the hardware does and what Java and Rust
expose, and it would answer `value << 8` on a `u8` with the value itself -- a wrong
answer that reads like a working shift. Sushi follows Go here: shifting by one place
at a time is the rule, so a count that empties the type gives an empty result. It
costs one compare and one conditional move, and nothing at all when the count is a
constant.

### Arithmetic

- `+` - Addition
- `-` - Subtraction
- `*` - Multiplication
- `/` - Division (integer division for int types)
- `%` - Modulo (remainder)

Every operand is a number, unary minus included: an integer or a float and nothing else.
A `bool`, a `string`, a struct, an enum, an array or an unhandled `Result@(T, E)` /
`Maybe@(T)` is **[CE2518](error-catalog.md#ce2518)**, and two numeric types of different widths are [CE2510](error-catalog.md#ce2510). There is
no concatenation operator, so `+` with a `string` operand is [CE2509](error-catalog.md#ce2509) and the escape is
interpolation. Add the fields of a struct one at a time, use `match` to read an enum, and
take the value out of a wrapper with `??`, `.realise(default)` or `match`.

<!-- docs-sweep: error CE2518 -->
```sushi
fn main() i32:
    let bool flag = true
    let i32 x = 1 + flag      # CE2518: '+' takes a numeric operand, and 'bool' is not one
    println("{x}")
    return 0
```

**A divisor the compiler can read must not be zero.** A literal zero, a constant that
holds one and a fold that gives one are each **[CE0112](error-catalog.md#ce0112)**, in a body exactly as in a
constant. A computed divisor is ordinary code and is left alone.

<!-- docs-sweep: error CE0112 -->
```sushi
fn main() i32:
    let i32 x = 10 / 0        # CE0112: division by zero
    println("{x}")
    return 0
```

### Overflow

An expression whose value the compiler reads is computed at the **declared width**, and an
operation whose result the type cannot hold is a compile error (**[CE2077](error-catalog.md#ce2077)**). That covers a
constant and a fold of literals in a body — one expression has one meaning:

<!-- docs-sweep: error CE2077 -->
```sushi
fn main() i32:
    let u8 sum = 200 + 100    # CE2077: '+' gives 300, which is out of range for u8
    println(sum)
    return 0
```

The **overflow-checked** operators are `+`, `-`, `*`, `/`, `%` and unary minus. Division
has one such case, the smallest signed value over `-1`, and unary minus has one, the
smallest signed value: neither has an answer the type can hold.

The **width-defined** operators compute at the width and never report, because the bits
that leave the type are lost by design: `~`, `&`, `|`, `^`, `<<` and `>>`. So `200 << 1`
on a `u8` is 144, and `~0` on a `u32` is 4294967295.

An `as` cast is the escape. It asks for the bit pattern, so it truncates: `300 as u8` is
44. A wider type is the other answer.

**Run time is not checked.** Only an expression the compiler reads is checked, so two
locals wrap:

```sushi
fn main() i32:
    let u8 a = 200
    let u8 b = 100
    let u8 sum = a + b        # 44 at run time, and nothing reports it
    println(sum)
    return 0
```

### Comparison

- `==` - Equal
- `!=` - Not equal
- `<` - Less than
- `<=` - Less than or equal
- `>` - Greater than
- `>=` - Greater than or equal

Both operands must be of one type: a mixed pair is [CE2513](error-catalog.md#ce2513), and two numeric types of
different widths are [CE2510](error-catalog.md#ce2510). A type with no such comparison is [CE2514](error-catalog.md#ce2514).

**At the top level**, equality (`==`, `!=`) accepts the numeric types, `bool`, `string`, and
every struct and enum. An order (`<`, `<=`, `>`, `>=`) accepts the numeric types, `string`,
and every struct and enum. A bare `bool` has no order, because `a < b` on two bools is
almost always a typo for `!=`.

**A struct or an enum compares through a predefined perk.** `==` and `!=` read `Eq`, and the
four order operators read `Ord` (see [Predefined Perks](#predefined-perks-drop-hashable-eq-ord-and-display)).
The compiler derives both from what the type holds, so no declaration is needed:

- **A struct** compares its fields in declaration order. The first difference decides.
- **An enum** compares the variant first, in declaration order, and then the payload from
  left to right. So `Maybe.Some(1) < Maybe.None` and `Result.Ok(1) < Result.Err(e)`.
- **`Maybe` and `Result`** take `==` and `<` like any enum. A bare variant on one side takes
  its type from the other side: `m == Maybe.None`.
- **A bool field** orders `false` before `true`. The top-level refusal does not apply
  inside a type.
- **An array, a `List@(T)` and an `Own@(T)`** compare only when a type holds them. An array
  and a list compare element by element, and a prefix is less. An `Own@(T)` compares its
  payload.
- **A `HashMap@(K, V)`, a `ptr`, a function value, a closure and an iterator** have no
  equality and no order. A type that holds one has none either. The diagnostic adds a note
  that names the field: `no derived Eq: field 'f' -> a function value`.

An `extend T with Eq` or `extend T with Ord` implementation replaces the derived rule. The
replacement wins in every position: the operator, a field of another derived method,
`contains` and `index_of`, and a `HashMap` key.

**Two float equalities.** A bare float `==` is IEEE: `NaN == NaN` is false, and `-0.0 == 0.0`
is true. A float that a type HOLDS uses a total rule instead: `-0.0` equals `0.0`, every
NaN equals every NaN, and a NaN orders after every number. So `compare` is zero exactly when
`==` is true, and a struct with a float field is a usable sort key and map key.

```sushi
struct Version:
    i32 major
    i32 minor

enum Level:
    Low
    High(i32)

fn main() i32:
    let Version a = Version(1, 2)
    let Version b = Version(1, 10)
    println(a == b)                    # false
    println(a < b)                     # true: the first difference decides
    println(Level.Low < Level.High(0)) # true: the variant decides first
    println(Level.High(2) > Level.High(1))
    return 0
```

Use `a.eq(b)` and `a.compare(b)` to call the contracts by name. See
[Methods of the contracts](#methods-of-the-contracts).

**A string comparison reads bytes.** It walks the UTF-8 bytes of the two strings, and the
length breaks the tie when the common bytes agree, so a prefix comes out below the longer
string that starts with it. This matches Rust and Go.

```sushi
let string a = "apple"
let string b = "apples"
if (a < b):                 # true: a prefix is less
    println("shorter first")
if ("Zoo" < "apple"):       # true: 'Z' is 0x5A, 'a' is 0x61
    println("capitals first")
```

Two consequences follow from reading bytes. The order is stable and cheap, which is what a
map key or a binary search needs. It is not a collation: it puts every capital before every
lowercase letter, and it does not normalize, so the two Unicode spellings of `é` are neither
equal nor adjacent. A list that a person reads needs a locale-aware comparison, which Sushi
does not provide yet.

### Logical

- `and` (or `&&`) - Logical AND (short-circuits)
- `or` (or `||`) - Logical OR (short-circuits)
- `xor` (or `^^`) - Logical XOR (evaluates both sides)
- `not` (or `!`) - Logical NOT

**Alternative syntax:** Sushi supports both keyword (`and`, `or`, `xor`, `not`) and symbolic (`&&`, `||`, `^^`, `!`) 
forms for all logical operators.

Every operand of every one of them is a **bool**, because an operand is a condition and
a condition takes nothing else. An integer, a string, a float, a struct, an enum or an
array there is [CE2005](error-catalog.md#ce2005), and a `Result@(T, E)` or a `Maybe@(T)` is [CE2516](error-catalog.md#ce2516). `not 5` does
not answer `0`: there is no truthiness to read, so write the question — `not (n == 0)`,
or `n == 0`.

### Bitwise

- `&` - Bitwise AND
- `|` - Bitwise OR
- `^` - Bitwise XOR
- `~` - Bitwise NOT (complement), at the width of its operand -- so `~0` is every
  bit of the type the `0` was given, and a `u8` reads 255 where an `i8` reads -1
- `<<` - Left shift (zero-fill)
- `>>` - Right shift (type-dependent, see below)

Every operand of every one of them must be an **integer**. A float has no bits to
combine: its bits are reached through `f64.to_bits()` / `f32.to_bits()`, which hand
over a `u64` / `u32`, and `from_bits()` goes back. A float operand is **[CE2004](error-catalog.md#ce2004)**.

<!-- docs-sweep: error CE2004 -->
```sushi
fn main() i32:
    let f64 value = 1.5
    let f64 masked = value & 1.0    # CE2004: a float has no bits
    return 0
```

```sushi
fn main() i32:
    let f64 value = 1.5
    let u64 bits = value.to_bits()
    let u64 sign = (bits >> 63) & 1      # 0
    let f64 back = f64.from_bits(bits)   # 1.5
    return 0
```

**Right shift behavior (matches Go/Rust):**
- **Signed types** (`i8`, `i16`, `i32`, `i64`): Arithmetic shift (sign-extends)
  ```sushi
  let i32 a = -16
  let i32 shifted = a >> 2  # Result: -4 (preserves sign bit)
  ```
- **Unsigned types** (`u8`, `u16`, `u32`, `u64`): Logical shift (zero-fills)
  ```sushi
  let u32 a = 3221225472
  let u32 shifted = a >> 2  # Result: 805306368 (zero-fill from left)
  ```

### Bit Methods

The unsigned integers `u8`, `u16`, `u32` and `u64` have three built-in bit methods. No
method takes an argument, and no method needs an import. `W` is the width of the type:
8, 16, 32 or 64.

| Method | Answer | For `0` |
|---|---|---|
| `reverse_bits() T` | The bits in the reverse order, over the full width `W`. Bit 0 goes to bit `W - 1`. The type is the type of the receiver. | `0` |
| `leading_zeros() i32` | The count of zero bits above the highest one bit. | `W` |
| `trailing_zeros() i32` | The count of zero bits below the lowest one bit. | `W` |

The two counts are `i32`, as every count and index is. A value with the top bit set
has `leading_zeros()` 0, and an odd value has `trailing_zeros()` 0. `W -
x.leading_zeros()` is the count of bits that `x` needs.

The methods are on the unsigned types only. A signed or a float receiver is
[CE2008](error-catalog.md#ce2008), and an argument is [CE2009](error-catalog.md#ce2009).
Cast a signed value to its unsigned twin first: `(x as u32).leading_zeros()`. A bare
integer literal is `i32`, so a literal receiver also needs the cast:
`(0x80 as u8).trailing_zeros()`.

**The low `n` bits, reversed.** Use the shift: `c.reverse_bits() >> (W - n)`. For `n = W`
the count is 0, and the answer is the full reversal. For `n = 0` the count is `W`. A
computed shift count at or above the width gives 0, so the answer is 0. Write the count
as an expression of `n`: a count that the compiler can read must be from 0 to `W - 1`,
so `c.reverse_bits() >> 32` on a `u32` is [CE2512](error-catalog.md#ce2512).

```sushi
fn reverse_low(u32 code, i32 n) u32:
    return code.reverse_bits() >> (32 - n)

fn main() i32:
    let u8 b = 0b0001_0110
    let u8 r = b.reverse_bits()
    let i32 lead = b.leading_zeros()
    let i32 trail = b.trailing_zeros()
    println("{r} {lead} {trail}")                                # 104 3 1

    let u32 zero = 0
    println("{zero.leading_zeros()} {zero.trailing_zeros()}")    # 32 32

    let i32 x = -1
    println("{(x as u32).leading_zeros()}")                      # 0

    println("{reverse_low(0b011, 3)} {reverse_low(0b011, 0)}")   # 6 0
    return 0
```

### String

There is no `+` concatenation operator for strings. Build strings with
interpolation instead:

```sushi
let string a = "foo"
let string b = "bar"
let string combined = "{a}{b}"   # "foobar"
```

Two primitives give a string's bytes in place, with no copy (#1091):

- **`s[i]`** answers the `u8` at byte offset `i`. The index is an `i32`, and it is
  bounds-checked like `arr[i]`: an offset past `size` is [`RE2020`](error-catalog.md#re2020). It is a read and never
  a write: `s[i] := v` is [`CE2113`](error-catalog.md#ce2113), because a string is immutable.
- **`string.from_bytes(nom b)`** is a static that TAKES a `u8[]`: the array's buffer
  becomes the string's data, its `len` becomes the string's size, and no byte is copied.
  The array is spent, so a later use of `b` is [`CE2405`](error-catalog.md#ce2405). The bytes are not checked for
  UTF-8.

```sushi
fn main() i32:
    let string s = "Mostly Harmless"
    let u8 first = s[0]                       # 77
    let u8[] b = from([77, 111, 115, 116, 108, 121])
    let string word = string.from_bytes(nom b)
    println("{first} {word}")                 # 77 Mostly
    return 0
```

An index on anything else than an array or a string is [`CE2114`](error-catalog.md#ce2114). `s.to_bytes()` and
`u8[].to_string()` stay for the cases that want a copy.

### Other

- `as` - Type casting, and a declared conversion between two error types
- `??` - Error propagation. Postfix on an expression, and also on a `foreach` binder
  (`foreach(line?? in it)`), where it unwraps the loop's item

## Control Flow

### If-Elif-Else

Parentheses required around conditions. A condition is a `bool` and nothing else: a
`Result@(T, E)` or a `Maybe@(T)` is [CE2516](error-catalog.md#ce2516) (test one with `.is_ok()` / `.is_some()`),
and every other type is [CE2005](error-catalog.md#ce2005) — an integer carries no truth value, so write the
question (`n != 0`). The same rule covers a `while` condition, an `assert` condition,
both operands of `and`, `or` and `xor`, and the operand of `not`, so `not 5` is refused
exactly as `if (5)` is.

```sushi
if (condition):
    # Block
elif (other_condition):
    # Block
else:
    # Block
```

### While Loops

```sushi
while (condition):
    # Loop body
    if (done):
        break
    if (skip):
        continue
```

### For-Each Loops

```sushi
foreach(element in iterable.iter()):
    # Use element
```

Type annotation optional:

```sushi
foreach(i32 element in array.iter()):
    println(element)
```

The item position takes any written type: a built-in, a struct or an enum this program
declares, a generic instantiation (`Maybe@(i32)`), a qualified name (`geo.Vec`), and a
reference form (`poke Point p`), which is the long spelling of `poke p`. A type that
the iterator's element does not match is **[CE2034](error-catalog.md#ce2034)**.

**`_` discards the item.** A loop that only repeats its body writes `_` as the binder, as
a `match` pattern does. `_` binds nothing and the body cannot name it, so it is never
[CW1001](error-catalog.md#cw1001) (an unused name). A NAMED binder that the body never reads is still [CW1001](error-catalog.md#cw1001).

```sushi
fn main() i32:
    foreach(_ in 0..3):                  # three lines, no binding
        println("Mostly Harmless")
    return 0
```

The discard takes every binder form: a written type (`foreach(i32 _ in ...)`), a borrow
mode (`foreach(poke _ in ...)`), and the `??` marker, where `foreach(_?? in it)` leaves
the function on the first failure and discards the value of each success.

**Two things are walkable.** An ITERATOR -- what `.iter()` answers on an array or a
`List@(T)`, what `.keys()` / `.values()` / `.entries()` answer on a `HashMap`, and what a
range is. Or **any type carrying `next()` answering `Maybe@(T)`**: the loop calls it until
it answers `None`, and that is the whole protocol. There is no type to implement and no
perk to name, so a struct becomes walkable by gaining one method.

**A range is not a value.** It has two positions: a `foreach` iterable and an element of
an array literal (`from([0..n])`). A range anywhere else -- a function, method or
constructor argument, a `return`, a `let` initializer, an operand -- is [`CE2122`](error-catalog.md#ce2122). Spell it
into an array to keep it.

**A range always goes up.** `a..b` is empty when `a >= b`, and `a..=b` is empty when
`a > b`. A countdown is `(a..b).rev()`: the same values, last first. `.rev()` is the one
method a range takes, and the result is a range again, with the same two positions. A
second `.rev()` turns the range back. `(0..n).rev()` is `n - 1` down to 0, and it is empty
when `n` is 0:

```sushi
fn main() i32:
    let i32 n = 3
    foreach(i in (0..n).rev()):          # 2 1 0
        println(i)
    foreach(i in (1..=3).rev()):         # 3 2 1
        println(i)
    foreach(i in n..0):                  # empty: a range always goes up
        println(i)
    return 0
```

When the compiler can read both bounds and the range goes down, the range is
**[CE2125](error-catalog.md#ce2125)**, and the help gives the countdown. A range with a computed bound is empty
when it does not go up, and nothing is reported:

<!-- docs-sweep: error CE2125 -->
```sushi
fn main() i32:
    foreach(i in 10..0):                 # CE2125: write `(1..=10).rev()`
        println(i)
    return 0
```

<!-- docs-sweep: skip (a fragment: the narrative owns the struct) -->
```sushi
extend Countdown next(poke self) Maybe@(i32):    # this makes a Countdown walkable
    ...

foreach(n in c):                                 # calls c.next() until it answers None
    println(n)
```

The protocol carries **no error channel**. A `next()` declaring `| E` answers a `Result`
rather than a `Maybe` and is not walkable; a FALLIBLE iterator puts the failure in its
ITEM instead, answering `Maybe@(Result@(T, E))`. The outer `Maybe` says whether there is
more and the inner `Result` says whether reading it worked, and the two are never the same
answer.

The item is then an ordinary value, so every tool the language already has works on it --
a `match` that skips a failure, `.realise(default)` that substitutes one, a `break`. And
`??` **on the binder** is the short form for the common case, leaving the function on the
first failure exactly as `??` does in any other position:

```sushi
use <io/fs>
use <io/buf>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = BufReader.new(nom f, 8192)
    foreach(line?? in r.lines()):        # the first read failure leaves show()
        println(line)
    return Result.Ok(~)

fn main() i32:
    match show("/etc/hosts"):
        Result.Ok(_) -> return 0
        Result.Err(_) -> return 1
```

A `??` binder over an item that is not a `Result` has nothing to unwrap and is
**[CE2517](error-catalog.md#ce2517)**. An iterable that is neither an iterator nor a type with `next()` is
**[CE2033](error-catalog.md#ce2033)**. A reference binding (`foreach(poke r in ...)`) takes no `??` marker: it points
INTO storage, and there is nothing to unwrap there.

`foreach` CONSUMES its iterable, and a protocol iterator is destroyed when the loop ends --
by `break` and by `return` as well as at the end of the input. A protocol iterator held in a
local is MOVED into the loop: a later mention of the local is **[CE2405](error-catalog.md#ce2405)**, and a borrowed one
that owns a resource (a parameter, a field) is **[CE2411](error-catalog.md#ce2411)** -- walk a `.clone()` of it. A
protocol iterator that owns nothing is copied, and the loop walks the copy.

**A tuple item destructures.** `foreach((k, v) in pairs.iter()):` splits each item into
its elements by the rule of a `let` destructure, and a binder's ownership follows from the
item. See [Destructuring in a For-Each Loop](#destructuring-in-a-for-each-loop).

The argument behind all of this -- why the failure rides in the ITEM rather than on the
loop head, why the protocol is not a perk, and why a line iterator's stop is sticky -- is
[Iteration (design)](design/iteration.md).

### Assertions

`assert(cond)` and `assert(cond, message)` state an invariant: a state that a correct
program never reaches. When the condition is `true`, the program continues. When it is
`false`, the program stops with the runtime error **[RE2026](error-catalog.md#re2026)** and exit code 1.

```sushi
fn mean(i32[] xs) i32:
    assert(xs.len() > 0, "mean() of an empty array")
    let i32 total = 0
    foreach(x in xs.iter()):
        total := total + x
    return total / xs.len()

fn main() i32:
    let i32[] xs = from([3, 4, 5])
    println(mean(xs))
    return 0
```

A call of `mean` with an empty array stops the program, and stderr gets the position of
the `assert` and the message:

```
Runtime Error RE2026: assertion failed at ./stats.sushi:2:5: mean() of an empty array
```

- **The condition is a `bool`**, as in every condition position: a `Result` or a `Maybe`
  is [CE2516](error-catalog.md#ce2516), and every other type is [CE2005](error-catalog.md#ce2005).
- **The message is a `string`** ([CE2116](error-catalog.md#ce2116)): a literal, an interpolation or a call. The
  program builds it ONLY when the condition is false, so a passing assert costs one test
  and no allocation.
- **The position** is the `assert` keyword. The file is named as a compile-time
  diagnostic names it: relative to the directory of the build, or the base name of a file
  outside it. A generic function of a binary library is named by its template label,
  `<template:lib:name>`, with the line in the slice that the library ships.
- **An assert is always on.** No flag turns it off, and its condition always runs, side
  effects included.
- **`assert(false)` does not end the path.** A function that returns a value still needs
  its `return` ([CE0107](error-catalog.md#ce0107)), and a statement after it is not dead code.
- **It is a statement**, so it stands wherever a statement does: a block, a one-line
  `match` arm, a lambda body, a generic body. `assert` is a reserved word.
- **A failure is a defect, not data.** No error channel catches it. A failure that the
  caller can handle belongs in the channel `| E`; see
  [Traps Are Not Errors](error-handling.md#traps-are-not-errors). The design record is
  [The `assert` statement](design/assert.md).

## Arrays

See [Standard Library](standard-library.md) for complete array API.

**An index, a count and a range bound are `i32`.** That covers `arr[i]`, a repeat count, a
range bound, and the index or count argument of a built-in method: `get`, `insert` and
`remove` on `T[]` and `List@(T)`; `truncate`, `s`, `ss` and `extend_range` on an array;
`reserve` and `List.with_capacity` on `List@(T)` only. A bare literal takes `i32`. A typed value of another integer type is [`CE2121`](error-catalog.md#ce2121), which names the position (`an array
index is i32, got i64`; [`CE2006`](error-catalog.md#ce2006) as a method argument), and it needs `as i32`: nothing widens, and a float is refused.

### Fixed Arrays

Stack-allocated, compile-time size:

```sushi
let i32[5] arr = [1, 2, 3, 4, 5]
let i32 first = arr.get(0).or_err(nom StdError.Error)??  # .get returns Maybe@(i32); ?? unwraps it
```

#### The size

A fixed array's size is a positive integer the compiler can read. It may be a literal
in any base:

```sushi
fn main() i32:
    let u8[4] decimal = [1, 2, 3, 4]
    let u8[0x4] hex = [1, 2, 3, 4]
    let u8[0b1_00] binary = [1, 2, 3, 4]
    let u8[0o4] octal = [1, 2, 3, 4]
    return 0
```

It may also name an integer constant, so a size that repeats across declarations can
be written once. The constant may be an expression, and a named size works wherever a
type does -- a local, a struct field, a parameter, a return type:

```sushi
const i32 MAX_BITS = 4

struct Counts:
    i32[MAX_BITS] slots

fn walk(i32[MAX_BITS] counts) i32:
    return counts.len()

fn main() i32:
    let i32[MAX_BITS] counts = [1, 2, 3, 4]
    return walk(counts)
```

The constant must be declared in the **same unit**. A size is read while that unit's
AST is built, before any pass holds a program-wide constant table, so a constant in
another unit is reachable as a value but not as a size.

A size that cannot count elements is **[CE2099](error-catalog.md#ce2099)**: a name that is no integer constant
of this unit, a constant that is not an integer, or a zero. A zero-length array does
not exist in Sushi.

<!-- docs-sweep: error CE2099 -->
```sushi
fn main() i32:
    let i32[0] nothing = [1]        # CE2099: an array holds at least one element
    return 0
```

#### A repeated element

An element may say how many slots it fills. `value; count` puts `count` copies of one
value in the literal, and it stands where a single element stands, so runs and plain
elements mix freely:

```sushi
const i32[19]  ZEROS  = [0; 19]
const i32[4]   PAIRS  = [0;2, 1;2]              # 0 0 1 1
const i32[6]   MIXED  = [1, 0;3, 9, 7]          # 1 0 0 0 9 7
const i32[288] FIXED  = [8;144, 9;112, 7;24, 8;8]

fn main() i32:
    let i32[10] tally = [0; 10]
    let i32[]   head  = from([-1; 32768])
    println(tally[9])
    println(head.len())
    return 0
```

**Where the count must be readable depends on the position, not on the element.** A
fixed array's length is part of its TYPE, and a constant's evaluator needs the values,
so both need a count the compiler can read: a literal in any base, the name of an
integer constant, or an expression of them. Unlike an array size, the count is read late
enough to name a constant of **another** unit.

A `from()` array carries its length at run time, so the count there may be **any i32
expression**:

```sushi
fn zeros(i32 n) i32[]:
    return from([0; n])

fn main() i32:
    let i32[] xs = from([10, 20, 30])
    let i32[] prev = from([-1; xs.len()])
    println("{prev.len()} {prev[0]}")       # 3 -1
    return 0
```

A count the compiler CAN read and that is not a count is **[CE2017](error-catalog.md#ce2017)** -- a zero, a
negative, or, in a fixed array or a constant, a value it cannot read:

<!-- docs-sweep: error CE2017 -->
```sushi
fn main() i32:
    let i32 n = 4
    let i32[4] t = [7; n]           # CE2017: a fixed array needs a readable count
    println(t[0])
    return 0
```

A count you can see that spells nothing is a typo, so `[0; 0]` stays an error. A count
you cannot see is **data**: `from([0; n])` with `n` at zero gives an empty `T[]`, the
same value `new()` gives. A run-time count that is **negative** is clamped to zero and
gives the same empty array: a count of zero is already data rather than an error, so a
negative one reaching the same answer needs no rule of its own.

The value is evaluated once, and every slot takes its own copy. A type that owns heap
memory costs one allocation per slot, so use a long run of one only when you mean that.
The repeated value is a **borrow**, which makes it the one literal element that does not
consume -- a run has one value and N slots, so it has no single position to take
ownership into. The source stays yours:

```sushi
use <collections/strings>

fn main() i32:
    let string towel = "mostly harmless".upper()
    let string[3] t = [towel; 3]
    println("{t[0]} {towel}")       # both usable
    return 0
```

#### A range element

An element may be a **range**, and it fills the slots it spans. `start..end` is exclusive
and `start..=end` is inclusive. A range always goes up, as in `foreach`, and `.rev()` fills
the same values, last first:

```sushi
fn main() i32:
    let i32[]  up      = from([0..5])       # 0 1 2 3 4
    let i32[]  through = from([0..=5])      # 0 1 2 3 4 5
    let i32[]  down    = from([(1..=5).rev()])  # 5 4 3 2 1
    let i32[6] table   = [0..=5]
    let i32[]  mixed   = from([-1, 0..3, 99])   # -1 0 1 2 99
    println("{up.len()} {through.len()} {down.len()} {table[5]} {mixed.len()}")
    return 0
```

A range yields **i32**, exactly as `foreach(i in 0..5)` does, so `let i64[] a =
from([0..5])` is a type mismatch. It obeys the same position rule as a repeat: a bound in
a `from()` literal may be any i32 expression, and a fixed array or a constant needs one
the compiler can read. A bound it cannot read there is **[CE2019](error-catalog.md#ce2019)**, and so is a readable
range that yields nothing. A readable range that goes down is **[CE2125](error-catalog.md#ce2125)**:

<!-- docs-sweep: error CE2019 -->
```sushi
fn main() i32:
    let i32[] a = from([3..3])      # CE2019: this range yields no value
    println(a.len())
    return 0
```

A range cannot carry a repeat count. `value; count` repeats ONE value, and a range is
already a sequence, so `[0..2; 3]` is **[CE2020](error-catalog.md#ce2020)**.

What [CE2011](error-catalog.md#ce2011) compares is the **expanded** count, so a run of 144 is 144 slots. When a
literal has a run, [CE2011](error-catalog.md#ce2011) lists every run with the span it fills, because the compiler
cannot know which of two runs is the short one -- either could be:

```
error CE2011: array literal has 287 elements but declared type expects 288
note: run 1 fills 0..143    (144 elements)
note: run 2 fills 144..254  (111 elements)
note: run 3 fills 255..278   (24 elements)
note: run 4 fills 279..286    (8 elements)
```

### Dynamic Arrays

Heap-allocated, runtime size:

```sushi
let i32[] arr = from([1, 2, 3])
let i32[] empty = new()

arr.push(4)
let i32 last = arr.pop().realise(-1)   # .pop() answers Maybe@(T)
```

A dynamic array changes at any index, too. `.insert(i, v)` answers `Result@(~, StdError)`:
`0 <= i <= len` is `Ok` (`i == len` appends), and any other index is `Err` and changes
nothing. `.remove(i)` answers `Maybe@(T)` and hands the element's ownership to the caller;
an index out of range is `Maybe.None()`. Both are the `List@(T)` contract. The element of an
`insert` is consumed, as a `push` consumes it. On a `T[N]`, `.push()`, `.pop()`, `.insert()`
and `.remove()` are **[CE2023](error-catalog.md#ce2023)**, because a fixed array cannot change its length.

```sushi
let i32[] xs = from([1, 3])
let bool placed = xs.insert(1, 2).is_ok()   # [1, 2, 3]
let i32 gone = xs.remove(0).realise(-1)     # 1; xs is [2, 3]
```

The other methods (`first`, `last`, `contains`, `index_of`, `clear`, `truncate`, `fill`,
`reverse`, `extend`, `extend_range`, `s`, `ss`) and their rules are in
[Arrays](stdlib/collections/arrays.md).

`new()` is a value, not only a declaration form. It takes its element type from the position
it stands in, so it spells the empty array anywhere one is expected -- a call argument, an
enum payload, a rebind, a struct field, and the default of a `.realise()`:

```sushi
fn count(i32[] xs) i32:
    return xs.len()

fn mk(bool good) i32[] | StdError:
    if (not good):
        return Result.Ok(new())
    return Result.Ok(from([1, 2, 3]))

let i32 none = count(new())
let i32[] taken = mk(false).realise(new())
```

### Indexed Assignment

`arr[index] := value` writes one element, on a fixed array and a dynamic array alike:

```sushi
let i32[3] scores = [1, 2, 3]
scores[0] := 42

let i32[] names = from([1, 2])
names[1] := 99
```

The index is bounds-checked like a read (**[RE2020](error-catalog.md#re2020)** at run time; **[CE2012](error-catalog.md#ce2012)** for an index the
compiler can read -- a literal, a named constant, or an expression of them -- past the end of a fixed array, **[CE2056](error-catalog.md#ce2056)** for a negative one). An owning element that the write replaces is freed
first. The assignment takes ownership of the value, so an owned source is moved (later use
is **[CE2405](error-catalog.md#ce2405)**) and a value read out of a container needs `.clone()` (**[CE2411](error-catalog.md#ce2411)**).

On a nested array, a chained index is a place too. `grid[i][j] := v` writes into the inner
array in place, and a method on `grid[i]` (`grid[i].push(v)`) changes that inner array, not
a copy of it. Each index in the chain is bounds-checked.

The write must be able to reach the owner. It is rejected through a `peek` parameter
(**[CE2408](error-catalog.md#ce2408)**), a `match`/`foreach` binding (**[CE2414](error-catalog.md#ce2414)**), a method receiver without
`poke self` (**[CE2421](error-catalog.md#ce2421)**), an unmarked parameter (**[CE2422](error-catalog.md#ce2422)**), a `let` binding that borrows
from an owner (**[CE2426](error-catalog.md#ce2426)**), an unbound chained receiver such as `o.get().items`
(**[CE2429](error-catalog.md#ce2429)**), and a constant (**[CE2096](error-catalog.md#ce2096)**).

### Unchecked Indexes: `dont_panic`

A function header may carry the marker `dont_panic because "<reason>"`. It follows the
return type and the error channel, and it comes before the colon. In the body, `a[i]` on an
array and `s[i]` on a string emit no bounds check, for a read and for a write. Nothing
else changes: `get(i)` still answers a `Maybe`, `insert(i, v)` still checks its position,
`assert` still traps, and every diagnostic stays.

<!-- docs-sweep: error CE0152 -->
```sushi
use <collections/strings>

fn contains_naive(string hay, string needle) bool dont_panic because "i + m <= n and j < m hold i + j < n":
    let i32 n = hay.size()
    let i32 m = needle.size()
    let i32 i = 0
    while (i + m <= n):
        let i32 j = 0
        while (j < m and hay[i + j] == needle[j]):
            j := j + 1
        if (j == m):
            return true
        i := i + 1
    return false

fn main() i32:
    println("Mostly Harmless")
    return 0
```

An index that is out of range in a marked body is undefined behaviour, and not the run-time
error [RE2020](error-catalog.md#re2020). The `because` text is required. It is the proof:
it states the guards that hold every index in range.

- **Where it may stand.** On a free function, a static, an extension method, a perk
  implementation method and a conversion (`extend A as B`). A lambda (both forms), a perk
  contract method and an extern each give [CE6111](error-catalog.md#ce6111). A missing
  `because` is the general parse error.
- **Who may write it.** A bundled stdlib unit needs no flag. A user unit needs the build
  flag `--dont-panic`; without it, the marker is [CE0152](error-catalog.md#ce0152), once per
  function. A source library that uses the marker needs the same flag from each consumer
  (see [Libraries](libraries.md#unchecked-indexes-and-consent)).
- **It does not spread.** A function that a marked function calls is checked. A lambda
  inside a marked body is checked, because it can run after the marked function returned.
  An instance of a marked generic function is marked, because it is the same body.
- **An inert marker.** A marked body with no `[]` to uncheck gets
  [CW0004](error-catalog.md#cw0004). The indexes in a lambda do not count.

Guideline: mark only a hot loop that you measured. Measure the loop before the change
and after the change. Put the numbers in the pull request. Write the guards that keep each
index in range in the `because` text. Keep the function as small as the loop. Do not mark
a function if you did not measure a speed increase. If a library author marks a function,
the author must tell this in the documentation of the library, because each consumer must
pass `--dont-panic`. The design record is [dont_panic](design/dont-panic.md).

## Structs

### Definition

```sushi
struct Name:
    type1 field1
    type2 field2
```

**Example:**

```sushi
struct Person:
    string name
    i32 age
    bool active
```

### Instantiation

Structs support both positional and named parameter construction:

**Positional (traditional):**
```sushi
let Person p = Person("Arthur", 42, true)
```

**Named (order-independent):**
```sushi
let Person p1 = Person(name: "Arthur", age: 42, active: true)
let Person p2 = Person(age: 42, active: true, name: "Arthur")  # Order doesn't matter
```

**Rules:**
- Named parameters provide clarity and prevent argument order mistakes
- All fields must be provided (no partial construction)
- Cannot mix positional and named arguments (all-or-nothing)
- Named parameters are resolved at compile-time (zero-cost abstraction)
- A name in an argument list names a FIELD, so a struct construction is the only place
  that takes one. A function call, a method call and an enum variant construction read
  their arguments by position, and a name written there is [`CE6104`](error-catalog.md#ce6104)

### Field Access

```sushi
println(p.name)
p.age := 43
```

### Nested Structs

```sushi
struct Point:
    i32 x
    i32 y

struct Rectangle:
    Point top_left
    Point bottom_right

let Rectangle rect = Rectangle(
    top_left: Point(x: 0, y: 0),
    bottom_right: Point(x: 10, y: 10)
)

println(rect.top_left.x)
```

## Tuples

A tuple is an anonymous, fixed-size product of two or more values of any types. Its type
and its literal are both written in parentheses. The design record is
[docs/design/tuples.md](design/tuples.md).

### Type and Literal

```sushi
fn divmod(i32 a, i32 b) (i32, i32):
    return (a / b, a % b)

fn main() i32:
    let (i32, string) t = (42, "Arthur")
    let ((i32, i32), bool) nested = ((1, 2), true)
    let (i32, i32) q = divmod(7, 2)
    println("{t} {nested} {q.0}")       # (42, "Arthur") ((1, 2), true) 3
    return 0
```

- A tuple type has two or more elements. `(T)` is grouping, as `(fn(i32) -> i32)[]` is,
  and `~` is the unit type, so there is no one-element and no empty tuple.
- A tuple type stands in every type position: a parameter, a return type (with or without a
  `| E` channel), a struct field, an enum payload, a type argument
  (`List@((string, i32))`, `HashMap@((i32, i32), string)`), an array element
  (`(i32, i32)[]`, `(i32, i32)[4]`), a function type and a lambda parameter.
- A tuple type names no binding: `(i32 quot, i32 rem)` is **[CE6105](error-catalog.md#ce6105)**. A record with names
  is a struct. An element takes no mode either: `(peek i32, i32)` is **[CE6107](error-catalog.md#ce6107)**.
- A literal is two or more expressions in parentheses. Each element is a consuming
  position, as an argument of a struct construction is.
- A statement that has its own parentheses keeps them: `println(1, 2)` is a parse error,
  and `println((1, 2))` prints a tuple.

### Element Access

An element is read with `.N`, a literal decimal index that starts at 0. `t.0.1` reads an
element of a nested tuple. The index is a literal because the element type depends on it,
so `t[i]` is not a tuple access.

```sushi
fn main() i32:
    let (i32, (string, i32)) t = (1, ("deep", 2))
    println(t.1.0)                      # deep
    t.0 := 5                            # an element write, as a field write
    println(t.0 + t.1.1)                # 7
    return 0
```

The element access follows the struct field rules: a read is a borrow (consuming it is
**[CE2411](error-catalog.md#ce2411)**), a write consumes its value and destroys the old element, and `nom t.0` is a
field take that spends all of `t` (see [Ownership Operations](#ownership-operations)). An
index past the last element is **[CE2106](error-catalog.md#ce2106)**, and a number with an underscore, an exponent or
a leading zero (`t.0_1`, `t.1e3`, `t.01`) is **[CE6106](error-catalog.md#ce6106)**.

### Destructuring

A `let` destructure splits a tuple into one binder per element:

```sushi
fn pair() (string, (i32, i32)):
    return ("towel", (4, 2))

fn main() i32:
    let (string name, (a, b)) = pair()  # a typed binder, and a nested destructure
    let (_, n) = (name, a * 10 + b)     # `_` discards an element
    println(n)                          # 42
    return 0
```

- An element is a typed binder (`i32 q`), a bare binder (`q`, which takes the element
  type), a `_`, or a nested destructure. A typed binder of the wrong type is the type
  mismatch of a `let` (**[CE2002](error-catalog.md#ce2002)**).
- The destructure names as many elements as the tuple has (**[CE2120](error-catalog.md#ce2120)**), and only a tuple
  destructures (**[CE2117](error-catalog.md#ce2117)**). An unhandled `Result` is **[CE2505](error-catalog.md#ce2505)**: take the value with
  `??`, `.realise(default)` or `match` first.
- A destructure element takes no mode: `let (peek i32 a, b) = t` is **[CE6107](error-catalog.md#ce6107)**.
- **Ownership.** Each binder OWNS its element when the value is owned: a temporary, or an
  owned local, which the destructure spends whole (a later use of it is **[CE2405](error-catalog.md#ce2405)**). Each
  binder BORROWS its element when the value is a borrow: a parameter, a field, or a
  binding. Consuming such a binder is **[CE2411](error-catalog.md#ce2411)**, and a change of the owner while it lives
  is **[CE2412](error-catalog.md#ce2412)**. A `_` element of an owned value is destroyed at the destructure. This is
  the one rule of [ruling 4](design/borrow-model.md#10e-the-fifth-boundary-a-tuple-destructure-binder): a
  bare binder of a destructure owns, and a bare binding of a `match` pattern borrows.

A `match` reads a tuple with a tuple pattern: see [Tuple Patterns](#tuple-patterns).

### Destructuring in a For-Each Loop

A `foreach` destructures each item with the same element list:

```sushi
fn main() i32:
    let List@((string, i32)) people = List.new()
    people.push(("arthur", 42))
    people.push(("ford", 7))
    foreach((name, age) in people.iter()):
        println("{name} {age}")
    foreach((string who, _) in people.iter()):
        println(who)
    people.free()
    return 0
```

The loop is a `let` destructure of the item at the top of the body, so every rule of
[Destructuring](#destructuring) applies: the elements, the count (**[CE2120](error-catalog.md#ce2120)**), a tuple item
(**[CE2117](error-catalog.md#ce2117)**), a typed binder (**[CE2002](error-catalog.md#ce2002)**), and no mode on an element (**[CE6107](error-catalog.md#ce6107)**). A
binder's ownership follows from the item. The items of `.iter()` are borrowed, so each
binder borrows its element and consuming one is **[CE2411](error-catalog.md#ce2411)**. A `next()` protocol iterator
hands out owned items, so each binder owns its element and the body may hand it away. The
binders of an owned item are destroyed at the end of each iteration, at a `break` and at a
`return`.

### Destructuring Rebind

A destructuring rebind assigns the elements of a tuple to existing places:

```sushi
struct Point:
    i32 x
    i32 y

fn main() i32:
    let i32 a = 1
    let i32 b = 2
    (a, b) := (b, a)                    # a swap
    let Point p = Point(0, 0)
    let i32[] xs = from([0, 0])
    (p.x, xs[1]) := (a * 10, b)         # a field and an element
    println("{a} {b} {p.x} {xs[1]}")    # 2 1 20 1
    return 0
```

- The right side is any tuple value: a literal, a call (`(q, r) := divmod(7, 2)`), a
  local or a parameter. A value that is not a tuple is **[CE2117](error-catalog.md#ce2117)**, an unhandled `Result`
  is **[CE2505](error-catalog.md#ce2505)**, and a count that is not the tuple's count is **[CE2120](error-catalog.md#ce2120)**.
- Each target is a place that a plain `:=` takes: a name, a field, an array element, a
  tuple element (`t.0`) or a unit variable. Each target is a rebind or a field write by
  the rule of `x := v`, so it destroys the old value and gives its type to a literal
  element: `(small, big) := (200, 5000000000)` types `200` as the `u8` of `small`. A value
  of the wrong type is **[CE2002](error-catalog.md#ce2002)**, and a read-only target is the error of a plain `:=`
  (for example **[CE2408](error-catalog.md#ce2408)** for a `peek` parameter).
- Nested targets follow the destructure shape: `((a, b), c) := ((1, 2), 3)`.
- The same place twice in the target, nested targets included, is **[CE6109](error-catalog.md#ce6109)**:
  `(a, a) := (1, 2)` would replace the first value with the second.
- **The order** (ruling 13): the whole right side is evaluated first, then each target is
  assigned from left to right. So `(a, b) := (b, a)` is a swap, and `(i, xs[i]) := (1, 9)`
  assigns `i` first, so `xs[i]` reads the new `i`.
- **Ownership.** The right side is taken as a `let` destructure takes it: a temporary is
  owned, an owned local is spent whole (a later use is **[CE2405](error-catalog.md#ce2405)**), and a borrow cannot
  give up an owning element (**[CE2411](error-catalog.md#ce2411)**, once for each element). So an owning swap
  `(s, t) := (t, s)` moves each value to its new name and destroys nothing.

### Comparison, Hashing and Display

A tuple derives `Eq`, `Ord`, `Display`, `hash()` and `clone()` from its elements, as a
struct does from its fields:

- `==` and `!=` compare element by element, and `<`, `<=`, `>`, `>=` are LEXICOGRAPHIC:
  the first element that differs decides. Two tuples of different element types do not
  compare (**[CE2513](error-catalog.md#ce2513)**).
- `hash()` reads the elements in order, so a tuple of hashable elements is a `HashMap` key.
- A tuple prints as `(1, "a")`: the elements in order, a held string quoted.
- `.clone()` is deep, and it is refused when an element declares a resource.

### Generics

A type parameter infers through a tuple, and a written `(T, U)` substitutes in a template:

```sushi
fn swap@(T, U)(nom (T, U) p) (U, T):
    let (a, b) = p
    return (b, a)

fn main() i32:
    let (i32, string) t = (42, "answer")
    let (string, i32) s = swap(nom t)
    println("{s}")                      # ("answer", 42)
    return 0
```

### Tuples in the Standard Library

A stdlib function with two results answers a tuple:

| Module | Function | Answer |
|---|---|---|
| `<math>` | `divmod(T a, T b)` | `(T, T) \| MathError` |
| `<collections/strings>` | `s.split_once(sep)`, `s.rsplit_once(sep)` | `Maybe@((string, string))` |
| `<collections/iter>` | `enumerate`, `zip`, `partition`, `unzip` | a `List` of tuples, or a tuple of `List`s |
| `<collections/hashmap>` | `m.pairs()` | `Iterator@((K, V))` |
| `<io/path>` | `split(p)`, `split_extension(p)` | `(string, string)` |
| `<io/buf>` | `r.into_parts()` | `(R, u8[])` |
| `<net/tcp>` | `l.accept()`, `s.peer()` | `(TcpStream, IpAddr) \| NetError`, `(IpAddr, i32) \| NetError` |
| `<encoding/msgpack>` | `decode_prefix(buf)` | `(MsgValue, i32) \| MpError` |
| `<compression/zlib>` | `inflate_raw_prefix(src)` | `(u8[], i32) \| ZError` |

A `MsgValue.Map` holds its entries as `(MsgValue, MsgValue)[]`. The answer goes into a
destructure, a `match` with a tuple pattern, or a `foreach`:

```sushi
use <math>
use <collections/strings>
use <collections/hashmap>

fn main() i32:
    match divmod(7, 2):
        Result.Ok((q, 1)) -> println("odd, half is {q}")
        Result.Ok((q, _)) -> println("even, half is {q}")
        Result.Err(_) -> println("no answer")
    match "a/b/c".rsplit_once("/"):
        Maybe.Some((dir, file)) -> println("{dir} {file}")    # a/b c
        Maybe.None -> println("no separator")
    let HashMap@(string, i32) ages = HashMap.new()
    ages.insert("Arthur", 42)
    foreach((name, age) in ages.pairs()):
        println("{name} is {age}")
    return 0
```

### What a Tuple Is Not

- There is no tuple constant and no tuple unit variable (see [Constants](#constants)).
- A tuple has no C layout, so it is not a type of an FFI signature (**[CE5003](error-catalog.md#ce5003)**).
- A tuple type is not an extension target and not a perk-implementation target
  (**[CE2110](error-catalog.md#ce2110)**). Write a struct and extend it, or a free function that takes the tuple.
- A tuple does not bloom into a variadic argument list (`f(t...)` is **[CE2006](error-catalog.md#ce2006)**), and
  `expand` walks a type pack and never a tuple.
- A cast does not build a tuple (`x as (i32, i32)` is **[CE2014](error-catalog.md#ce2014)**).

## Enums

### Definition

```sushi
enum Name:
    Variant1()
    Variant2(type1)
    Variant3(type1, type2)
```

**Example:**

```sushi
enum Status:
    Idle()
    Running(i32)
    Error(string)
```

Enum variant fields are positional (type-only); they are bound by position in
pattern matching, not by field name.

### Construction

```sushi
let Status s1 = Status.Idle()
let Status s2 = Status.Running(42)
let Status s3 = Status.Error("Failed")
```

A variant with no payload may also be written without the parentheses: `Status.Idle` is
`Status.Idle()`. The two spellings are one construction, and the same checks apply to
both -- an undeclared variant is [CE2045](error-catalog.md#ce2045), and the value takes the type of its position.
That holds for a generic enum too: `let Maybe@(string) m = Maybe.None` constructs a
`Maybe@(string)`, and the binding owns it exactly as `Maybe.None()` would.

### Reading a Variant

A `match` is the one way to read a variant's payload (see [Pattern Matching](#pattern-matching)):

```sushi
match s2:
    Status.Idle() ->
        println("Idle")
    Status.Running(task_id) ->
        println("Running task {task_id}")
    Status.Error(msg) ->
        println("Error: {msg}")
```

## Error Types and Conversions

### Error Types

An error type is declared with the keyword `error`. The body is the body of an `enum`:
variants, payloads, type parameters, doc blocks and `public`. Only the keyword is
different.

```sushi
error ParseError:
    Empty
    BadDigit(string)

fn parse_digit(string s) i32 | ParseError:
    if (s == ""):
        return Result.Err(ParseError.Empty)
    if (s == "7"):
        return Result.Ok(7)
    return Result.Err(ParseError.BadDigit(s.clone()))

fn main() i32:
    println(parse_digit("7").realise(-1))   # 7
    println(parse_digit("x").realise(-1))   # -1
    return 0
```

An error type is an enum with a flag, so every enum rule applies to it: `match` and
exhaustiveness, payloads, the generic form (`error DecodeError@(T)`), the derived `Eq`,
`Ord`, `Display`, `hash` and `clone`, extension methods, perks, `Drop`, visibility and
libraries. An error type is ordinary data in every position: a field, a payload, a
parameter, a `let`, an array element and a generic argument.

The seven predefined error types are `StdError`, `IoError`, `FileError`, `NetError`,
`ProcessError`, `EnvError` and `MathError`. `FileMode` and `SeekFrom` are plain enums.

**The `E` of every `Result@(T, E)` is an error type**, in both spellings (`T | E` is
`Result@(T, E)`) and in every position: a signature of a function, a method, a perk, a
lambda and a function type, and also a `let`, a field, a payload, a parameter and a
generic argument. Any other type is [`CE2084`](error-catalog.md#ce2084), and the message says what the type is. For
a plain enum, the help says to declare it with `error`. A type parameter in the `E`
position is judged at each instance, with a note at the template.

<!-- docs-sweep: error CE2084 -->
```sushi
enum Color:
    Red

fn pick() i32 | Color:          # CE2084: 'Color' is a plain enum, not an error type
    return Result.Err(Color.Red)

fn main() i32:
    return 0
```

`error` is a reserved word: it cannot be the name of a variable or a function. The import
paths `<io/error>` and `<net/error>` keep the word.

### What `??` Takes

`??` takes a `Result@(T, E)` and nothing else. Any other operand is [`CE2507`](error-catalog.md#ce2507): a
`Maybe@(T)`, and a user enum with `Ok`/`Err` variants (`??` reads the type, not the names
of its variants). A `Maybe` holds no error value, so the program writes one with
`or_err`:

```sushi
error AppError:
    Empty

fn first(i32[] xs) i32 | AppError:
    let i32 v = xs.get(0).or_err(nom AppError.Empty)??
    return Result.Ok(v)

fn main() i32:
    let i32[] full = from([4, 5])
    let i32[] none = new()
    println(first(full).realise(-1))    # 4
    println(first(none).realise(-1))    # -1
    return 0
```

`m.or_err(nom e)` is a built-in method of `Maybe@(T)` and answers `Result@(T, E)`:
`Some(v)` becomes `Ok(v)`, and `None` becomes `Err(e)`. The argument is `nom`, because the
error value moves into the `Err`. The receiver is `nom self`: a named `Maybe` that owns
something is spent by the call ([`CE2435`](error-catalog.md#ce2435)). A BORROWED `Maybe` (a get-out such as
`xs.get(0)`, a parameter, a pattern binding) is read through, as `??` reads a borrowed
`Result`. When its payload owns something, the call is legal only as the operand of
`??` ([`CE2522`](error-catalog.md#ce2522)); elsewhere, take an owned copy first: `xs.get(0).clone().or_err(nom e)`.

### Conversions

A conversion turns a value of one error type into another. It is declared with
`extend <Source> as <Target>:` and a body that returns the target. `self` is the source
value. The declaration has no name, no parameter list and no return type.

```sushi
error LowError:
    Bad(i32)

error AppError:
    Low(LowError)
    Empty

extend LowError as AppError:
    return AppError.Low(self)

fn low(i32 n) i32 | LowError:
    if (n < 0):
        return Result.Err(LowError.Bad(n))
    return Result.Ok(n)

fn app(i32 n) i32 | AppError:
    let i32 v = low(n)??                    # calls the conversion on an Err
    return Result.Ok(v * 2)

fn main() i32:
    println(app(4).realise(-1))             # 8
    match app(-3):
        Result.Ok(v) -> println(v)
        Result.Err(e) -> println(e)         # AppError.Low(LowError.Bad(-3))
    let AppError a = LowError.Bad(9) as AppError
    println(a)                              # AppError.Low(LowError.Bad(9))
    return 0
```

- **`??`** calls the conversion when the error type of its operand differs from the
  channel of the enclosing body. With no declaration, the `??` is [`CE2511`](error-catalog.md#ce2511), and the help
  names the declaration to write.
- **`e as T`** calls the conversion explicitly. With no declaration, the cast is
  [`CE2014`](error-catalog.md#ce2014). `as` consumes its operand by position, with no marker: for a borrowed error
  that owns something, the form is `e.clone() as T` ([`CE2411`](error-catalog.md#ce2411)).
- **One step.** `A as B` and `B as C` do not give `A` to `C`. The lookup is an exact match
  on the pair.
- **Who may declare it.** Only the unit that declares the TARGET type ([`CE2519`](error-catalog.md#ce2519)). For a
  predefined type that unit is its home module, so `FileError as IoError` lives in
  `<io/error>`, and no unit may declare a conversion into `StdError`. A program declares its
  own error type and converts into it.
- **Which types.** The source and the target are non-generic error types ([`CE2520`](error-catalog.md#ce2520)). A
  conversion from a type into itself is [`CE2521`](error-catalog.md#ce2521). Two declarations of one pair are
  [`CE0101`](error-catalog.md#ce0101).
- **The body is bare.** A `| E` on a conversion is a parse error, and a `??` in the body
  is [`CE0131`](error-catalog.md#ce0131). The body consumes `self`.
- **Visibility.** A conversion is found by its pair of types, not by a name, so an import
  neither brings nor hides it. It is as visible as its target type.
- A conversion is not a constant expression: `as` into an error type in a `const` is
  [`CE0108`](error-catalog.md#ce0108).

### `map_err`

`r.map_err(f)` converts the error of one `Result` at one site, with no declaration. It
answers `Result@(T, F)`: `Ok(v)` stays `Ok(v)`, and `Err(e)` becomes `Err(f(e))`.

```sushi
error LowError:
    Bad

error AppError:
    Wrapped(LowError)

fn low() i32 | LowError:
    return Result.Err(LowError.Bad)

fn app() i32 | AppError:
    let i32 v = low().map_err(|nom LowError e| AppError.Wrapped(e))??
    return Result.Ok(v)

fn main() i32:
    match app():
        Result.Ok(v) -> println(v)
        Result.Err(e) -> println(e)         # AppError.Wrapped(LowError.Bad)
    return 0
```

The function takes the error `nom` (`fn(nom E) -> F`), so a lambda writes `|nom LowError e|`.
A lambda parameter cannot have an owning type, so for an error that owns a `string` the
argument is a named function. The receiver is `nom self`: a named `Result` that owns
something is spent, and a borrowed one is [`CE2411`](error-catalog.md#ce2411) (the form is `r.clone().map_err(f)`).
`F` must be an error type ([`CE2084`](error-catalog.md#ce2084)).

The guide is [Error Handling](error-handling.md), and the design record is
[Error types and error conversion](design/error-conversion.md).

## Pattern Matching

### Basic Match

```sushi
match expression:
    Pattern1 -> statement
    Pattern2 -> statement
```

### Wildcard

```sushi
match value:
    Status.Running(_) -> println("Running")
    _ -> println("Other")
```

### Arm Bodies

An arm body is one statement on the arrow, or an indented block of statements. The
inline form takes the statements a block takes -- a call, a `print` or `println`, a
`return`, a `break`, a `continue`, and a REBIND -- plus a bare expression.

```sushi
let i32 kept = 0
match m:
    Maybe.Some(v) -> kept := v          # a rebind of an outer local
    Maybe.None -> ~
```

A `let` needs the block form: a local declared on the arrow has no line to read it.

### Nested Patterns

A pattern may hold another pattern in a payload position. `open()` answers
`Result@(File, IoError)`, so the inner pattern names an `IoError` variant; a pattern of
another enum there is [`CE2107`](error-catalog.md#ce2107). A payload position takes an enum pattern, an integer
literal (`Maybe.Some(0)`), a string literal (`Maybe.Some("--help")`), a tuple pattern
(`Maybe.Some((a, b))`), an `Own(...)` pattern, a binding or a `_`. A nested pattern over a
payload that is not an enum is **[CE2108](error-catalog.md#ce2108)**. An integer literal over a payload that is not an
integer, and a string literal over a payload that is not a `string`, are **[CE2119](error-catalog.md#ce2119)**. A
tuple pattern over a payload that is not a tuple is **[CE2117](error-catalog.md#ce2117)**. The compiler checks a nested
pattern for exhaustiveness as it checks an outer one (see [Exhaustiveness](#exhaustiveness)).

```sushi
use <io/fs>

fn main() i32:
    match open("missing.txt", FileMode.Read()):
        Result.Err(IoError.NotFound()) ->
            println("File not found")
        Result.Err(_) ->
            println("Other file error")
        Result.Ok(_) ->
            println("File opened")
    return 0
```

### Binding Modes

A payload binding carries a MODE, and the three are the ones a parameter has. The bare
form is the common case.

| pattern | the binding is | write through it | rebind the name | may be given away |
|---|---|---|---|---|
| `Ok(x)` | a read-only view | no ([CE2414](error-catalog.md#ce2414)) | no ([CE2414](error-catalog.md#ce2414)) | no ([CE2411](error-catalog.md#ce2411)) |
| `Ok(poke x)` | a pointer into the scrutinee's payload | yes, and it reaches the owner | yes, and it reaches the owner | no |
| `Ok(nom x)` | the value itself, now the arm's | yes | yes | yes |

`nom` TAKES the payload, so the match has to own its scrutinee. A temporary -- a call
result, a constructor, a `??` -- is owned by construction. A place expression belongs to
its owner until the match says `nom`, and then the local is consumed exactly as
`f(nom r)` consumes it.

| scrutinee | the match |
|---|---|
| `match open("out.log", FileMode.Write()):` | OWNS a temporary; `nom` bindings are legal |
| `match r:` | BORROWS the local; a `nom` binding is [CE2432](error-catalog.md#ce2432) |
| `match nom r:` | CONSUMES the local; `nom` bindings are legal, and a later `r` is [CE2405](error-catalog.md#ce2405) |

<!-- docs-sweep: skip (a fragment: `report` is the narrative's, and neither match returns) -->
```sushi
use <io/fs>
use <io/buf>

match open("out.log", FileMode.Write()):
    Result.Ok(f) -> println("opened")                   # a borrow: read only
    Result.Err(e) -> report(e)

match open("out.log", FileMode.Write()):
    Result.Ok(poke f) -> f.writeln("Mostly Harmless")   # writes through: `writeln` takes `poke self`
    Result.Err(e) -> report(e)

match open("out.log", FileMode.Write()):
    Result.Ok(nom f) -> BufWriter.new(nom f, 4096)      # takes it, and says so
    Result.Err(e) -> report(e)
```

An arm takes the variant WHOLE: if any binding in it is `nom`, every other owning payload
of that variant must be `nom` too ([CE2433](error-catalog.md#ce2433)). `nom` is not valid inside an `Own(...)`
pattern ([CE2434](error-catalog.md#ce2434)), and a `peek`/`poke` binding still needs a scrutinee with storage -- a
read through a live owner has none ([CE2404](error-catalog.md#ce2404)).

A `peek`/`poke` binding is legal at the top of an arm: a payload of the arm's enum
pattern, or an element of the arm's tuple pattern (also in a tuple pattern inside it).
Inside a pattern that is nested in an enum payload, and in the payload of an enum pattern
inside a tuple pattern, it is **[CE2424](error-catalog.md#ce2424)**. A bare binding and a `nom` binding are legal at
every depth.

### Exhaustiveness

One checker reads every match: an enum match, a nested enum match, an integer match, a
string match and a tuple match. It gives two answers.

**Every value must match an arm.** A match that does not cover a value is **[CE2040](error-catalog.md#ce2040)**. For
a plain enum match, the message lists the names of the missing variants. When an arm tests
inside a payload or a tuple, the message lists the missing patterns in source syntax:

```sushi
enum Color:
    Red()
    Green()
    Blue()

# ERROR CE2040: missing variants: Blue
match color:
    Color.Red() -> println("Red")
    Color.Green() -> println("Green")

# ERROR CE2040: missing variants: Maybe.Some(Color.Blue)
match maybe_color:
    Maybe.Some(Color.Red) -> println("red")
    Maybe.Some(Color.Green) -> println("green")
    Maybe.None -> println("none")
```

An integer position holds the values of its type, from the lowest to the highest. A literal
covers one value and a range covers each value from its start to its end, so arms of
literals and ranges can cover the whole type: `0x00..=0x7f` and `0x80..=0xff` cover a
`u8`, and the match needs no `_` arm. A string has no end of values, so a string position
is covered only by a `_` or a binding. An integer match that the arms do not cover, and a
string match with no `_` arm, are **[CE2074](error-catalog.md#ce2074)**; the message names the first integer value
that no arm matches. In a tuple or a payload, the missing pattern shows the values that no
arm matches as a value or a range: `(-2147483648..=-1, _)`, `Maybe.Some(128..=255)`.

**Every arm must match a value.** An arm is unreachable when the arms above it match every
value that it matches. That is **[CE2118](error-catalog.md#ce2118)**, an error, with a note at each arm that covers
it. The arms can cover an arm together:

```sushi
# ERROR CE2118: unreachable match arm '(_, Color.Red)'
match pair:
    (Color.Red, _) -> println("first is red")
    (Color.Green, _) -> println("first is green")
    (Color.Blue, _) -> println("first is blue")
    (_, Color.Red) -> println("second is red")    # the three arms above cover it
```

Remove the arm, or move it above the arms that cover it. A `_` arm after integer arms that
cover the type is this error too, and so is a range arm that the arms above cover. Three
older rules come first, and each is the one diagnostic for its arm: a second arm for the
same enum pattern is **[CE2041](error-catalog.md#ce2041)**; a `_` arm that is not the last arm is **[CE2041](error-catalog.md#ce2041)**, and the
arms after it get no second error; a literal arm for a value that an arm above matches (an
integer or a string), and a range that shares some values with the arms above, are
**[CE2075](error-catalog.md#ce2075)**.

Because the checker reads nested patterns, a match that compiles has an arm for every
value. The run-time check **[RE2023](error-catalog.md#re2023)** stays as a backstop, and no program reaches it.

### Literal Matching

A match on an integer scrutinee or on a `string` scrutinee dispatches on literal arms. The
same rules apply to the two kinds:

- An arm holds one literal, or several literals as `|` alternatives (`0 | 1 ->`, see
  [Alternatives](#alternatives)). An integer arm can also hold a range (see
  [Range Arms](#range-arms)).
- Two arms, or two alternatives, with the same value are a duplicate arm (**[CE2075](error-catalog.md#ce2075)**).
- The arms of an integer match can cover the type, and then the match needs no `_` arm.
  Else, and for a string match always, the match ends with a `_` arm
  (**[CE2074](error-catalog.md#ce2074)**). A `_` arm after a cover of the type can never
  run (**[CE2118](error-catalog.md#ce2118)**).
- One match holds one arm kind. A string arm on an integer scrutinee, an integer arm on a
  `string` scrutinee, a literal arm on an enum scrutinee, and a string arm beside an
  integer arm are **[CE2076](error-catalog.md#ce2076)**.
- A literal is also legal inside a pattern: in an enum payload (`Maybe.Some(0) ->`,
  `Maybe.Some(a'/') ->`, `Maybe.Some("--help") ->`), in a tuple element (`(0, n) ->`, `("go", dir) ->`) and in an
  `Own(...)` pattern. The value at that position must be of the kind of the literal: an
  integer for an integer literal, a `string` for a string literal (**[CE2119](error-catalog.md#ce2119)**).

A named constant (`GET ->`) is not a pattern. It is a parse error: write the literal.

#### Integer Arms

Each literal takes the scrutinee's type under the usual context-typing rule. A non-decimal
literal is a bit pattern, and a literal out of range is **[CE2073](error-catalog.md#ce2073)**. The radix does not
change the value: `0x2a` and `42` are the same arm (**[CE2075](error-catalog.md#ce2075)**), and on an `i8` the
bit pattern `0xff` and `-1` are the same arm too. A nested integer literal takes the type
of its position by the same rule.

```sushi
fn tag_name(u8 t) string:
    match t:
        0xc0 ->
            return "nil"
        0xc2 ->
            return "false"
        0xc3 ->
            return "true"
        _ ->
            return "other"

fn main() i32:
    let u8 tag = 0xc0
    println(tag_name(tag))
    return 0
```

#### Range Arms

An integer arm can hold a RANGE of literals: `a..b` matches each value from a to b-1, and
`a..=b` matches each value from a to b. A bound is an integer literal in every form an
integer arm takes: decimal, hex, binary, octal, a byte literal (`a'0'..=a'9'`) and a
negative literal (`-5..=-1`). A range arm is legal in every position a literal arm is: at
the top of an arm, in an enum payload (`Maybe.Some(0..=9) ->`), in a tuple element
(`(0..=9, _) ->`) and as an alternative (`a'a'..=a'z' | a'A'..=a'Z' | a'_' ->`). The
value at its position must be an integer (**[CE2119](error-catalog.md#ce2119)**, and
**[CE2076](error-catalog.md#ce2076)** for a range arm on a `string` or an enum scrutinee).

```sushi
fn format_kind(u8 b) string:
    match b:
        0x00..=0x7f -> return "positive fixint"
        0x80..=0x8f -> return "fixmap"
        0x90..=0x9f -> return "fixarray"
        0xa0..=0xbf -> return "fixstr"
        0xc0 -> return "nil"
        0xc2 | 0xc3 -> return "bool"
        0xe0..=0xff -> return "negative fixint"
        _ -> return "other"

fn word_char(u8 c) bool:
    match c:
        a'a'..=a'z' | a'A'..=a'Z' | a'0'..=a'9' | a'_' -> return true
        _ -> return false

fn sign(i8 n) string:
    match n:
        -128..=-1 -> return "negative"
        0 -> return "zero"
        1..=127 -> return "positive"

fn main() i32:
    println(format_kind(0x93))      # fixarray
    println(word_char(a'_'))        # true
    let i8 n = -7
    println(sign(n))                # negative
    return 0
```

The arms of `sign` hold every value of an `i8`, so the match needs no `_` arm, and a `_`
arm there would be **[CE2118](error-catalog.md#ce2118)**.

The rules of a range arm:

- Each bound takes the type of the value it reads, as a literal arm does, and a bound out
  of that type is **[CE2073](error-catalog.md#ce2073)**. A string bound (`"a".."z"`) is
  **[CE2072](error-catalog.md#ce2072)**: a range bound is an integer.
- A range always goes up. A range whose start is above its end (`0x8f..=0x80`, `5..3`) is
  **[CE2125](error-catalog.md#ce2125)**, and the help gives the written order to use.
- `5..5` matches no value, so its arm can never run (**[CE2118](error-catalog.md#ce2118)**).
- A range or a literal that shares a value with an arm above, or with an earlier
  alternative of its own arm, is **[CE2075](error-catalog.md#ce2075)**, and the message names
  the first value in both: `0x80..=0x8f` then `0x8f..=0x9f` shares 143 (0x8f). A range
  that the arms above cover completely adds no value: it is **[CE2118](error-catalog.md#ce2118)**.
- A range has two bounds. `..=5` and `5..` are not patterns (a parse error), a named
  constant is not a bound, and a pattern cannot bind the matched value (`n @ 1..=9` is a
  parse error): match the range and read the scrutinee in the arm body.

<!-- docs-sweep: error CE2075 -->
```sushi
fn kind(u8 b) string:
    match b:
        0x80..=0x8f -> return "fixmap"
        0x8f..=0x9f -> return "fixarray"    # ERROR CE2075: value 143 (0x8f)
        _ -> return "other"

fn main() i32:
    println(kind(0x80))
    return 0
```

#### Byte Arms

A byte literal (`a'/'`, see [Numeric Literals](#numeric-literals)) is an integer arm. It
takes the scrutinee's type by the same rule, and a value out of range is
**[CE2073](error-catalog.md#ce2073)**. The value of the arm is its byte, so `a'/'` and `47`
are the same arm (**[CE2075](error-catalog.md#ce2075)**). A byte literal is also legal in a
nested position: `Maybe.Some(a'/') ->`, `(a'a', _) ->`. A negated byte literal (`-a'/'`) is
not a pattern.

```sushi
fn kind(u8 b) i32:
    match b:
        a'/' -> return 1
        a'.' -> return 2
        a'\n' -> return 3
        a'\xff' -> return 4
        _ -> return 0

fn starts_with_slash(u8[] bytes) bool:
    match bytes.first():
        Maybe.Some(a'/') -> return true
        _ -> return false

fn main() i32:
    let string path = "a.b/c"
    println(kind(path[1]))              # 2
    println(kind(path[3]))              # 1
    let u8[] bytes = from([a'/', a'x'])
    println(starts_with_slash(bytes))   # true
    return 0
```

The quotes tell the arm kind. `'/' ->` is a string arm, and `a'/' ->` is a byte arm. The
wrong form is **[CE2076](error-catalog.md#ce2076)**, and the help gives the correct form:
for `'/' ->` on a `u8` scrutinee, the help is to write `a'/'` for the byte; for `a'/' ->`
on a `string` scrutinee, the help is to write `'/'` for a string arm.

#### String Arms

A string literal arm takes a double-quoted literal (`"get"`) or a single-quoted literal
(`'get'`). The scrutinee can be any `string`: a local, a parameter (`peek` too), a field,
an element, a `var`, a literal or a temporary. The match borrows a place. It owns a
temporary (`s.lower()`, `a.concat(b)`, an interpolation) and frees it on every path out of
the match. `match nom s:` hands the local to the match, and the match frees it at its end.

```sushi
fn run(string cmd, string key) i32:
    match cmd:
        "get" ->
            println("get {key}")
        "put" ->
            println("put {key}")
        'del' ->
            println("del {key}")
        _ ->
            println("unknown command: {cmd}")
            return 1
    return 0

fn main() i32:
    run("get", "answer")                # get answer
    run("del", "answer")                # del answer
    run("Mostly Harmless", "answer")    # unknown command: Mostly Harmless
    return 0
```

**The match compares BYTES**, by the rule of `==`: first the size, then the content
(`memcmp`). There is no case folding, no collation and no Unicode normalization. A prefix
of an arm does not match the arm, and a longer string does not match it. The compiler
reads the value of a literal after it processes the escapes, so `"a"` and `'a'` are one
value, and `"\t"` and a literal tab are one value. Two arms with one value are **[CE2075](error-catalog.md#ce2075)**.

**The arms are tested in source order.** Each arm is one comparison, and the first arm that
matches runs. There is no jump table for a string match.

**A pattern holds no interpolation hole.** A pattern is a fixed value, and `"{x}" ->` is
**[CE2123](error-catalog.md#ce2123)**. To match the braces as text, write `'{x}' ->` with single quotes, which do not
interpolate. To compare with a run-time value, bind the value and test it in the arm body
(`s -> if (s == x): ...`).

<!-- docs-sweep: error CE2123 -->
```sushi
fn same(string s, string x) bool:
    match s:
        "{x}" -> return true            # CE2123: a hole is a run-time value
        _ -> return false

fn main() i32:
    println(same("a", "a"))
    return 0
```

A string literal in a payload or a tuple element tests that position. A binding or a `_`
in the same position after it covers the other values:

```sushi
fn main(string[] args) i32:
    match args.get(1):
        Maybe.Some("--help") -> println("usage: app [--help | NAME]")
        Maybe.Some(name) -> println("Mostly Harmless, {name}")
        Maybe.None -> println("Mostly Harmless")
    return 0
```

```sushi
fn walk(string verb, string noun) string:
    match (verb, noun):
        ("go", "north") -> return "you walk north"
        ("go", dir) -> return "you go {dir}"
        ("look", _) -> return "you look around"
        _ -> return "you cannot do that"

fn main() i32:
    println(walk("go", "north"))        # you walk north
    println(walk("go", "south"))        # you go south
    println(walk("jump", "up"))         # you cannot do that
    return 0
```

### Tuple Patterns

A tuple pattern matches a tuple, one item for each element. An item is an enum pattern, an
integer literal, a string literal, a binding (bare, `poke` or `nom`), a `_`, or another
tuple pattern. A `bool`, float or struct element takes only a binding or a `_`. A tuple
pattern stands at the top of an arm, in an enum payload, and in another tuple pattern.

```sushi
enum Color:
    Red
    Green

fn describe((Color, i32) p) ~:
    match p:
        (Color.Red, 0) -> println("red zero")
        (Color.Red, n) -> println("red {n}")
        (Color.Green, _) -> println("green")

fn first(Maybe@((i32, i32)) m) i32:
    match m:
        Maybe.Some((x, _)) -> return x
        Maybe.None -> return 0

fn main() i32:
    describe((Color.Red, 3))            # red 3
    println(first(Maybe.Some((7, 8))))  # 7
    return 0
```

- A tuple pattern names as many items as the tuple has elements (**[CE2120](error-catalog.md#ce2120)**), and only a
  tuple takes a tuple pattern (**[CE2117](error-catalog.md#ce2117)**). An enum pattern arm and a literal arm do not
  fit a tuple scrutinee (**[CE2076](error-catalog.md#ce2076)**).
- A bare binding BORROWS its element, as a payload binding does (ruling 4 of the tuple
  design). `poke` points into the element, and `nom` takes it. The rules of
  [Binding Modes](#binding-modes) apply: `nom` needs a scrutinee that the match owns, and an
  arm that takes one owning element of a scrutinee takes all of them (**[CE2433](error-catalog.md#ce2433)**).
- **A tuple literal as the scrutinee builds no tuple** (ruling 3). `match (a, b):` reads
  each element once, from left to right, and matches it in place, with the rules of a
  named scrutinee. After the match, `a` and `b` are still usable, and a `poke` binding
  writes through to them. `match nom (a, b):` hands each element to the match, so a `nom`
  binding is legal and a later use of an owning element is **[CE2405](error-catalog.md#ce2405)**. Each element is
  its own scrutinee: an arm can take `a` and leave `b`, and the match destroys `b` at its
  end.

```sushi
fn main() i32:
    let i32 x = 0
    let i32 y = 5
    match (x, y):
        (0, 0) -> println("origin")
        (0, n) -> println("on the y axis at {n}")
        (_, _) -> println("elsewhere")
    return 0
```

### Alternatives

One arm can hold several patterns, with `|` between them. The arm runs when one of them
matches. The alternatives are tried from left to right.

```sushi
use <net/error>

enum Shape:
    Circle(i32)
    Ring(i32)
    Square(i32)

fn default_port(string scheme) i32:
    match scheme:
        "http" | "ws" -> return 80
        "https" | 'wss' -> return 443
        _ -> return 0

fn is_space(u8 c) bool:
    match c:
        a' ' | a'\t' | a'\n' -> return true
        _ -> return false

fn retry(NetError e) bool:
    match e:
        NetError.AddressInUse | NetError.TimedOut -> return true
        _ -> return false

fn radius(Shape s) i32:
    match s:
        Shape.Circle(r) | Shape.Ring(r) -> return r
        Shape.Square(_) -> return 0

fn small(Maybe@(i32) m) bool:
    match m:
        Maybe.Some(1 | 2 | 3) -> return true
        _ -> return false

fn main() i32:
    println(default_port("ws"))             # 80
    println(is_space(a'\t'))                # true
    println(retry(NetError.TimedOut))       # true
    println(radius(Shape.Ring(4)))          # 4
    let Maybe@(i32) two = Maybe.Some(2)
    println(small(two))                     # true
    return 0
```

- **Positions.** An alternative stands at the top of an arm and in every position inside
  a pattern: an enum payload (`Maybe.Some(1 | 2)`), a tuple element
  (`(Color.Red | Color.Blue, _)`) and an `Own(...)` pattern (`Own(1 | 2)`). Over a tuple
  literal scrutinee, each alternative at the top of the arm is a whole tuple pattern:
  `(Color.Red, 1) | (Color.Blue, 2) ->`.
- **Bindings.** Each alternative binds the same names, with the same types and the same
  mode (bare, `peek`, `poke` or `nom`), as in Rust and Python. The arm body reads one
  binding, whatever alternative matched. A name that only some alternatives bind, a name
  with two types and a name with two modes are **[CE2126](error-catalog.md#ce2126)**, at
  the alternative that differs, with a note at the first alternative. Write two arms when
  the alternatives must bind different things. A `poke` binding writes through to the
  scrutinee, and a `nom` binding takes the payload, whatever alternative matched.
- **Exhaustiveness.** The checker reads each alternative as a pattern of its own, so the
  alternatives can cover an enum with no `_` arm. An alternative that can never match is
  **[CE2118](error-catalog.md#ce2118)**, at that alternative: the arms above it and the
  earlier alternatives of the same list match every value it matches, as `2` in
  `1 | _ | 2`. `_` is a legal alternative, and it matches every value.
- **Duplicates.** Two alternatives with the same value (`"a" | 'a'`, `1 | 0x1`), in one
  arm or in two arms, are **[CE2075](error-catalog.md#ce2075)**, and so is a range
  alternative that shares a value with an earlier one (`1..=5 | 3`). An alternative that names
  an enum variant a second time (`Color.Red | Color.Red`, or a variant that an arm above
  matches) is **[CE2041](error-catalog.md#ce2041)**.
- **No mix.** Each alternative follows the arm-kind rule of the scrutinee, so a literal
  alternative and an enum alternative never stand in one match
  (**[CE2076](error-catalog.md#ce2076)**, **[CE2119](error-catalog.md#ce2119)**).
- **`||` is not two alternatives.** The lexer reads `||` as the logical operator, so
  `1||2` is a parse error (**[CE6001](error-catalog.md#ce6001)**) with the help
  `put a space: '1 | 2'`. `or` is not an alternative either.

<!-- docs-sweep: error CE2126 -->
```sushi
enum Shape:
    Circle(i32)
    Ring(i32)

fn radius(Shape s) i32:
    match s:
        Shape.Circle(r) | Shape.Ring(_) -> return r     # ERROR CE2126: 'r' is not bound here

fn main() i32:
    println(radius(Shape.Circle(2)))
    return 0
```

## Generics

A struct, an enum and a function take type parameters in `@(...)` after the name. The
compiler makes one copy of the declaration for each set of type arguments that the
program uses (monomorphization).

```sushi
struct Pair@(T, U):
    T first
    U second

enum Slot@(T):
    Empty
    Full(T)

fn identity@(T)(nom T x) T:
    return x

fn main() i32:
    let Pair@(i32, string) p = Pair(42, "answer")
    let i32 a = identity(nom 7)          # T comes from the argument
    let i32 b = identity@(i32)(nom 8)    # T is written at the call site
    println("{p.first} {a} {b}")                    # 42 7 8
    return 0
```

A function that gives its argument back takes it with `nom T`, because an unmarked
parameter is a borrow and a borrow cannot be returned.

**Type arguments.** A type is always written with its arguments (`Pair@(i32, string)`,
`Slot@(i32)`). At a call, the compiler infers the type arguments of a generic function
from the arguments. When no argument names a type parameter, write the type arguments
after the name: `empty@(i32)()`. Explicit type arguments are all or nothing: a wrong
count is [`CE2062`](error-catalog.md#ce2062). They are legal only on a direct call to a named free function; on a
method call or any other callee they are [`CE6102`](error-catalog.md#ce6102). A call that gives the compiler no
way to find a type argument is [`CE2060`](error-catalog.md#ce2060).

**Constraints.** `@(T: Perk)` limits `T` to the types that implement the perk (see
[Perks](#perks)). A type argument that does not implement it is [`CE4006`](error-catalog.md#ce4006). A constraint
is legal on a function, a struct and an enum, in its `@(...)` list, and on the
method-level type parameter of an extension (`extend Box@(T) pair_with@(U: Weigh)(U
other)`), which a call checks as it checks a free function.

**A template is checked where it is written.** The compiler checks a generic body one
time, also when no code calls it. In that check each type parameter is opaque: the body
can hold, move and pass on a `T`, and it can do with it only what a constraint promises.

| Constraint | What the body may do with a `T` |
|---|---|
| a user perk | call its methods |
| `Eq` | `==`, `!=`, `.eq()`, `contains` and `index_of` on a container of `T` |
| `Ord` | `<`, `<=`, `>`, `>=`, `.compare()` |
| `Display` | an interpolation hole, `print`, `println`, `.to_str()` |
| `Hashable` | `.hash()`; with `Eq`, a `HashMap` key |
| `Clone` | `.clone()`, also of a type that holds `T` |

Anything else on a `T` is the diagnostic of the same fault on a concrete type, at the
template, with a note at `T` and a help that names the constraint: a method is
[`CE2008`](error-catalog.md#ce2008), a comparison [`CE2514`](error-catalog.md#ce2514), a hole [`CE2035`](error-catalog.md#ce2035), `.clone()` [`CE4018`](error-catalog.md#ce4018). Arithmetic on a
`T` has no constraint and is [`CE2518`](error-catalog.md#ce2518); a field of `T` is [`CE2106`](error-catalog.md#ce2106) and a cast is
[`CE2014`](error-catalog.md#ce2014). A perk does not include another: `T: Ord` gives no `==`. A call, a written
type or a constructor that passes `T` to another generic needs the constraints of the
callee among the constraints of `T` ([`CE4006`](error-catalog.md#ce4006)).

```sushi
fn largest@(T: Ord)(nom T a, nom T b) T:
    if (a > b):
        return a
    return b

fn show_twice@(T: Display + Clone)(T x) string:
    let T y = x.clone()
    return "{x} {y}"

fn main() i32:
    println(largest(nom 3, nom 9))                  # 9
    println(show_twice("towel"))                    # towel towel
    return 0
```

**Bounds in an extension target.** An extension or a perk implementation on `Box@(T)`
inherits the bounds that `Box` declares, and its target can add one:
`extend Box@(T: Display) show()`, `extend (T: Clone)[] second()`. A receiver whose type
argument does not satisfy an added bound is [`CE4006`](error-catalog.md#ce4006) at the call. A bound is legal only at
the top level of an `extend` target ([`CE6110`](error-catalog.md#ce6110) elsewhere), never on a name that is a type
([`CE2124`](error-catalog.md#ce2124)), and never in the target of `Drop` ([`CE4019`](error-catalog.md#ce4019)).

The full guide, with every inference rule and limit, is [Generics](generics.md).

## Extension Methods

An `extend` declaration adds a method to a type. The body names the receiver `self`:

```sushi
struct Counter:
    i32 n

extend i32 squared() i32:
    return self * self

extend Counter bump(poke self) ~:
    self.n := self.n + 1

fn main() i32:
    let Counter c = Counter(0)
    c.bump()
    println("{c.n} {5.squared()}")                  # 1 25
    return 0
```

**The receiver mode.** The receiver is a parameter, and it takes the parameter modes. A
bare `self` is a read-only borrow, `peek self` is a read-only pointer, `poke self` writes
through to the caller's value, and `nom self` consumes the receiver. A write through a
receiver that is not `poke self` is [`CE2421`](error-catalog.md#ce2421).

**The bare return.** A method with no `| E` is bare, as a function with no `| E` is. It
returns its value bare: `return value`, and a `~` method may end with no `return`.
`return Result.Ok(...)` there is [`CE2091`](error-catalog.md#ce2091), and `??` there is [`CE0131`](error-catalog.md#ce0131), because the method
has no error channel to return an error through.

**The error channel.** A method may declare `| E` after its return type. Its call then
gives a `Result@(T, E)`, `??` is legal in the body, and the body spells both constructors
as a free function does: `return Result.Ok(x)` and `return Result.Err(e)`. A bare
`return x` there is [`CE2030`](error-catalog.md#ce2030). A chain of calls stops at a method with an unhandled
channel ([`CE2515`](error-catalog.md#ce2515)); write `??` after the call.

**Method type parameters.** A method may declare its own type parameters after its name:
`extend List@(T) mapv@(U)(fn(T) -> U f) List@(U) | StdError:`. The compiler finds them from
the arguments ([`CE2063`](error-catalog.md#ce2063) when it cannot), and a method call has no slot for explicit type
arguments.

**The target.** The target is a struct, an enum, a primitive, a built-in generic
(`List@(T)`) or a generic type of the program:

- `extend Box@(T)` applies to every instantiation, and `extend Box@(i32)` only to
  `Box@(i32)`. A target that mixes the two, `extend Pair@(i32, U)`, is [`CE2098`](error-catalog.md#ce2098).
- An array target binds its element: `extend T[]` applies to every array, and
  `extend i32[]` only to `i32[]`. Anything else in the element position is [`CE2101`](error-catalog.md#ce2101),
  a nested array (`extend T[][]`, `extend i32[3][]`) included. `extend T[]` covers a nested
  receiver: `T` is `i32[]` for an `i32[][]`.
- A function type is not a target ([`CE2110`](error-catalog.md#ce2110)).

A built-in method wins over an extension method: an extension method with the name of a
built-in method of its target is [`CE2097`](error-catalog.md#ce2097). The design record is
`docs/design/ufcs-combinators.md`, and the resolution order is
`docs/design/method-resolution.md`.

### Static Methods

A `static` marker before the method name declares a method with **no receiver**. It is
called on the TYPE name, not on a value, and it is how a type carries its own
constructor.

```sushi
struct Vec:
    i32 x
    i32 y

extend Vec static at(i32 x, i32 y) Vec:
    return Vec(x, y)

extend Vec static origin() Vec:
    return Vec(0, 0)

fn main() i32:
    let Vec v = Vec.at(3, 4)
    println("{v.x} {v.y}")
    return 0
```

A name behind a type's dot is a **member** of that type: a variant, or a static method,
never both. A local of the same name wins over the type.

Everything but the receiver is as on an instance method. The parameters take the ordinary four modes
and BORROW unless marked `nom`; an owning return belongs to the caller; `| E` opts into
the error channel exactly as on an instance method; and the declaration carries no
visibility marker, because a static is as visible as its target type.

`new` is a legal static name — `extend Box static new(i32 n) Box:` — which a free
function cannot have ([`CE6001`](error-catalog.md#ce6001)).

A static has **no `self`**, and the two places that could name one are one refusal:
a receiver mode in the signature (`extend Vec static at(poke self)`) and a mention of
`self` in the body are both [`CE0134`](error-catalog.md#ce0134). A `static` inside a perk implementation is
[`CE4014`](error-catalog.md#ce4014): a perk has no `Self`, so a contract cannot hold a constructor.

The target may be a struct, an enum, a primitive (`extend f64 static of_int(i32 v)
f64:`) or a generic type; an ARRAY target is [`CE2104`](error-catalog.md#ce2104), because an array type has no
spelling in an expression position and the declaration could never be called. On a
generic target there is no receiver to read the type argument from. The compiler reads
it from an argument whose parameter names the type parameter, or else from the declared
type of the position:

```sushi
struct Cage@(T):
    T[] items

extend Cage@(T) static holding(nom T item) Cage@(T):
    return Cage(from([item]))

extend Cage@(T) static empty() Cage@(T):
    return Cage(from([]))

fn main() i32:
    println("{Cage.holding(nom 9).items[0]}")       # 9: the argument makes T an i32
    let Cage@(i32) none = Cage.empty()              # T comes from the declared type
    println("{none.items.len()}")                   # 0
    return 0
```

A generic static whose parameters do not name the type parameter, in a position that
declares no type, is [`CE2060`](error-catalog.md#ce2060): nothing says which instantiation the call means. Bind the
result to an annotated name, or name the type parameter in a parameter.

A name has one home, so a static beside an instance method of the same name on one type
is [`CE0101`](error-catalog.md#ce0101), and a static spelling a VARIANT of the enum it extends is [`CE2103`](error-catalog.md#ce2103). A type
whose dot holds no such member is [`CE2102`](error-catalog.md#ce2102), and a VALUE whose type declares no such field
is [`CE2106`](error-catalog.md#ce2106) -- which is also what a method read without its parentheses answers, because a
bound-method value is deferred.

An ENUM value declares no field at all, so every name behind its dot is [`CE2106`](error-catalog.md#ce2106) too. That
covers `Result@(T, E)` and `Maybe@(T)`, which are ordinary enums: `pts.get(0).x` is
refused, and the value is taken first with `??`, `.realise(default)` or `match`. There is
no implicit unwrap, and a payload is read by a pattern.

`List.new()`, `List.with_capacity()`, `HashMap.new()`, `Own.alloc()` and
`f64.from_bits()` are the built-in statics — the same rule, on types the compiler
declares. The design record is `docs/design/method-resolution.md`.

## Perks

A perk is a contract: a set of method signatures. `extend T with P:` implements the perk
for a type, and `@(T: P)` asks for a type that implements it.

```sushi
perk Describe:
    fn describe() string

struct Point:
    i32 x
    i32 y

extend Point with Describe:
    fn describe() string:
        return "({self.x}, {self.y})"

fn show@(T: Describe)(T v) ~:
    println(v.describe())

fn main() i32:
    show(Point(1, 2))                               # (1, 2)
    return 0
```

An implementation method follows the rules of an extension method: a bare return, or an
error channel. The rules of the contract:

- The implementation gives every method of the perk ([`CE4005`](error-catalog.md#ce4005)), with the signature of the
  contract ([`CE4004`](error-catalog.md#ce4004)). A second implementation of one perk for one type is [`CE4002`](error-catalog.md#ce4002).
- A name has one home on a type: a perk method beside an extension method of the same name
  is [`CE4007`](error-catalog.md#ce4007).
- A type argument that does not implement a constraint is [`CE4006`](error-catalog.md#ce4006).
- A target may be a template: `extend Box@(T) with P` covers every `Box@(...)`, and
  `extend T[] with P` covers every dynamic array (`T` is the element type, and an `i32[][]`
  receiver has `T = i32[]`). There is no specialization: a template and a concrete target
  of the same perk on one base are [`CE4002`](error-catalog.md#ce4002), in either order. `extend T[][] with P` is
  [`CE2101`](error-catalog.md#ce2101).
- The perk must be in the scope of the unit, in an implementation, a constraint and a pack
  constraint alike: declared there, brought by the unit's own `use`, or re-exported by a
  `public use` chain. An aliased import gives `extend Dog with p.Named` and
  `@(T: p.Named)`. A perk out of scope is [`CE4003`](error-catalog.md#ce4003), with the import in the help; a plain
  `use` in another unit does not bring it. The predefined perks are in every scope.
- A perk has no type parameters, no inheritance, no default implementations and no `Self`
  type. A perk that declares `@(...)`, and an implementation method that declares its own
  type parameters, are both [`CE4010`](error-catalog.md#ce4010). A `static` in a perk implementation is [`CE4014`](error-catalog.md#ce4014): with
  no `Self`, a contract cannot hold a constructor.

A **perk method** takes the error channel, and the perk states it in the contract:
`fn read(poke u8[] into) i32 | IoError`. Every implementation repeats the channel
exactly. A channel that one side declares and the other does not, and two channels over
different error types, are both [`CE0133`](error-catalog.md#ce0133), which points at the contract and the
implementation together.

The guide is [Perks](perks.md).

### Predefined Perks: `Drop`, `Hashable`, `Eq`, `Ord`, `Display` and `Clone`

The compiler declares six perks. Every unit can name them with no import, and a
declaration of any of the six names is [`CE4001`](error-catalog.md#ce4001).

| Perk | Contract | Read by |
|------|----------|---------|
| `Drop` | `fn drop(poke self) ~` | scope exit |
| `Hashable` | `fn hash() u64` | `HashMap` keys, `.hash()` |
| `Eq` | `fn eq(Self other) bool` | `==`, `!=`, `contains`, `index_of`, `HashMap` keys |
| `Ord` | `fn compare(Self other) i32` | `<`, `<=`, `>`, `>=` |
| `Display` | `fn to_str() string` | an interpolation hole, `print`, `println` |
| `Clone` | `fn clone() Self` | `.clone()` on a type parameter |

`compare` returns a negative number, zero or a positive number.

**`Self` is a placeholder in the contract only.** A user perk still cannot write `Self`. An
implementation of a predefined perk writes its own type where the contract says `Self`:
`fn eq(Point other) bool`. A generic target writes its own instantiation:
`extend Box@(T) with Eq:` with `fn eq(Box@(T) other) bool`. A mismatch is [`CE4004`](error-catalog.md#ce4004), and its
help prints the contract with the target filled in.

**`Drop`** says that a type owns a resource that no field shows,
for example a file descriptor. A type that implements it MOVES like a `string`. When the
value goes out of scope, `drop()` runs first, and then the owning fields are destroyed.
At the end of a scope, the values are destroyed in the reverse order of their declaration.
Only the unit that declares the type may implement `Drop` for it ([`CE4012`](error-catalog.md#ce4012)). No unit
declares a primitive, a `string`, an array, `List`, `HashMap`, `Own`, `Maybe`, `Result` or
a predefined error type, so `Drop` on one of them is [`CE4016`](error-catalog.md#ce4016). A channel on `drop()` is
[`CE0133`](error-catalog.md#ce0133). A generic target is legal: `extend Sink@(T) with Drop`.
Only the compiler calls `drop()`: a written call is [`CE4020`](error-catalog.md#ce4020), on a value, through a `Drop`
constraint and on a field in a destructor.

**`Hashable`** is the constraint for a type that has a hash. Every type
with a derived hash implements it with no declaration. `extend T with Hashable` replaces
the derived hash of `T`, and the replacement applies everywhere the value is hashed: as a
field, as a payload, as a container element, as a map key and in a direct `.hash()` call
(an array's built-in `hash` included).

**`Eq`, `Ord` and `Display`** are derived for every struct and enum from what the type
holds. The compiler does not register them in a table: it answers each call from the type.
An implementation (`extend T with Eq`) is the override, and it wins in every position. A
type that holds a function value, a `ptr` or a `HashMap@(K, V)` has no derived contract, and
an implementation is the only way to give it one.

A constraint `@(T: Eq)`, `@(T: Ord)` or `@(T: Display)` is satisfied by the same top-level
rule as the operator. So `bool` satisfies `Eq` and does not satisfy `Ord`. Any other type is
[`CE4006`](error-catalog.md#ce4006).

**`Clone`** is satisfied by every type that holds no resource: a type that is not a `Drop`
type and holds none in a field, an element or a payload. A handle (`File`, `TcpStream`)
does not satisfy it; `.share()` gives a second owner of a handle. The built-in `.clone()`
is the contract, so an implementation is [`CE4017`](error-catalog.md#ce4017). A generic body that clones a `T`
writes `@(T: Clone)`; without it, the clone is [`CE4018`](error-catalog.md#ce4018).

#### Methods of the contracts

- `a.eq(b)` and `a.compare(b)` exist on every struct and enum, and on every primitive
  (`bool` included). A wrong argument type is [`CE2006`](error-catalog.md#ce2006).
- `x.to_str()` exists on every struct and enum. The primitives already had `to_str`.
- An explicit call reads an implementation when one exists. The operator reads the same one.

Two perks cannot give one type the same method name: that is [`CE4015`](error-catalog.md#ce4015), with a note at the
first one. A derived method is not a home. A type may implement a user perk that provides
`compare`. An explicit `a.compare(b)` then reads the implementation, and `<` still reads
`Ord`.

```sushi
struct Score:
    i32 points

extend Score with Ord:
    fn compare(Score other) i32:
        return other.points - self.points      # higher scores sort first

fn main() i32:
    let Score a = Score(9)
    let Score b = Score(3)
    println(a < b)                             # true: the override decides
    println(a.compare(b))                      # -6
    return 0
```

```sushi
struct Token:
    i32 id

extend Token with Drop:
    fn drop(poke self) ~:
        println("drop {self.id}")

struct Key:
    i32 a
    i32 b

extend Key with Hashable:
    fn hash() u64:
        return self.a as u64

fn main() i32:
    let Token first = Token(1)
    let Token second = Token(2)
    println("{first.id} {second.id}")               # 1 2
    println(Key(7, 9).hash())                       # 7
    return 0                             # drop 2, then drop 1
```

## Ownership Operations

An unmarked parameter is a borrow, and only `nom` consumes (see [Parameters](#parameters)).
A type MOVES when it owns a resource: `T[]`, `List@(T)`, `HashMap@(K, V)`, `Own@(T)`, a
`string` that owns heap memory, a capturing closure, a type that implements `Drop`, and a
composite that holds one of them. A string bound directly from a literal owns no heap
memory and copies. Every other type copies. These operations change ownership:

- **`.clone()`** is the only deep copy, and the compiler inserts no copy of its own. On a
  type that implements `Drop`, or holds one, it is [`CE2431`](error-catalog.md#ce2431), because the copy would be a
  second handle. On a type parameter it needs `@(T: Clone)` ([`CE4018`](error-catalog.md#ce4018)). A `File` and a `TcpListener` have `.share()` for that: a second owner of
  the same open file description.
- **`Own@(T)`** is a heap cell: `Own.alloc(v)` makes one, and `.get()` reads the payload.
  It lets a type hold itself (a list node, a tree).
- **A marked field take**, `nom s.out`, in a `let` initializer or a `return`, takes one
  owning field out of a local that the function owns. The take spends the whole receiver:
  the other owning fields are destroyed at the take, `drop()` does not run, and a later
  use of the local is [`CE2405`](error-catalog.md#ce2405). A take through a borrow, and `nom a.b.c`, are [`CE2411`](error-catalog.md#ce2411).
- **`??` over a named wrapper** spends the wrapper when the `Result` owns something in
  either arm: after `let string got = r??`, a use of `r` is [`CE2405`](error-catalog.md#ce2405).
- **`m.or_err(nom e)`** and **`r.map_err(f)`** take their receiver `nom self`: a named
  `Maybe` or `Result` that owns something is spent by the call ([`CE2435`](error-catalog.md#ce2435)). A borrowed
  `Maybe` is read through by `or_err` under `??`; a borrowed `Result` that owns something
  is [`CE2411`](error-catalog.md#ce2411) for `map_err`.
- **`e as T`** consumes `e` by position, with no marker: a declared conversion takes it,
  and an identity cast (`e` is a `T`) moves it to the new owner. A borrowed value that
  owns something is [`CE2411`](error-catalog.md#ce2411), and the form is `e.clone() as T`. A plain value is a copy.

The guides are [Memory Management](memory-management.md) and
[the borrow model](design/borrow-model.md).

## Closures

A lambda is a value of a function type. `|params| expr` has one expression, `|params|:`
starts a block body, and `|~|` takes no parameters. A lambda has a channel only when its
TYPE says `| E`: the annotation on the lambda (`|i32 x| -> i32 | E: ...`) or the expected
type (a parameter, a `let`, a field). The compiler never infers a channel from the body. A
block body of a bare lambda returns the value (`return x`); a block body with a channel
writes `return Result.Ok(...)`, as a function does. A bare lambda is the exception, as a
bare function is ([The error channel is opt-in](design/error-channel.md)).

```sushi
fn main() i32:
    let i32 n = 10
    let fn(i32) -> i32 add_n = |i32 x| x + n        # captures a copy of n
    let fn() -> i32 five = |~| 5
    println("{add_n(1)} {five()}")    # 11 5
    return 0
```

A lambda captures a plain value by copy and an owning value by move. A capture of a
borrow (`peek` or `poke`) is [`CE2094`](error-catalog.md#ce2094). A lambda given to a generic function writes the
types of its parameters (`|i32 x|`), because the compiler cannot infer them there. The
guides are [Closures](closures.md) and [First-Class Functions](first-class-functions.md).

## Variadic Functions

Sushi has three variadic forms:

| form | declaration | what the function gets |
|---|---|---|
| native | `fn sum(...i32 xs) i32:` | the trailing arguments in an owned `i32[]` |
| pack | `fn show@(...Ts: Perk)(...Ts xs) ~:` | one argument of each type, walked with `expand` |
| C | `fn printf(string fmt, ...) i32 = "printf"` | untyped C arguments, in an `unsafe external` block only |

```sushi
fn sum(...i32 xs) i32:
    let i32 total = 0
    foreach(x in xs.iter()):
        total := total + x
    return total

fn show_all@(...Ts: Hashable)(...Ts xs) ~:
    expand(x in xs):                                # checked once, unrolled once per argument
        println(x.hash())

fn main() i32:
    println(sum(1, 2, 3))                # 6
    let i32[] rest = from([4, 5])
    println(sum(rest...))                # 9: `rest...` moves the array in
    show_all(1, true)
    return 0
```

The native parameter comes last. `arr...` forwards a bare array variable and moves it. A
variadic parameter in a perk method or an extension method is [`CE0115`](error-catalog.md#ce0115). A pack cannot be
forwarded or indexed. The body of an `expand` is checked once, where it is written: the
binder has what the constraints of the pack promise. An `expand` may run zero times, so a
`return` inside it does not end the path ([`CE0107`](error-catalog.md#ce0107)). The binder takes the lints of a
`foreach` item, and `expand(_ in xs)` discards the element. The guide is [Variadic Functions](variadics.md).

## Foreign Functions

An `unsafe external` block declares C functions under a namespace:

```sushi
unsafe external "C" as libc because "absolute value from libc":
    fn abs(i32 n) i32 = "abs"

fn main() i32:
    println(libc.abs(-5))                           # 5
    return 0
```

A foreign function returns the raw C value, not a `Result`. `ptr` is an opaque foreign
pointer, and the compiler keeps it inside the foreign boundary (the `CE5xxx` codes). A
block with no `because "..."` is the warning [`CW5001`](error-catalog.md#cw5001). `nom` on a foreign parameter is
[`CE2428`](error-catalog.md#ce2428). A variadic C function is declared with `...`, and a fixed declaration of it
reads garbage on some platforms.

The block also declares what the foreign boundary needs and nothing more: a nullable
pointer is a `Maybe@(string)` or `Maybe@(ptr)` (a NULL is `Maybe.None`), a `u8[]`
parameter crosses as its data pointer, a `ptr` has foreign-memory methods
(`p.load_i64(off)`, `p.store_i32(off, v)`, `p.load_ptr(off)`, `p.offset(n)`,
`p.to_string(off)`), `errno()` reads the calling thread's `errno`, `var T name =
"symbol"` declares a read-only C global, and a link name may be a string constant (the
per-platform names are in `<sys/platform>`). The guide is [FFI](ffi.md).

## Module System

### Units

Sushi uses a unit system where each source file is a unit:

<!-- docs-sweep: skip (two units) -->
```sushi
# file: calc.sushi
public fn add(i32 a, i32 b) i32:
    return a + b

# file: main.sushi
use "calc"

fn main() i32:
    println(add(40, 2))          # 42
    return 0
```

### Importing a unit

`use "path"` imports another unit of the program, `use <module>` a standard-library
module, and `use <lib/name>` a library. **Every import stands above the first
declaration**, after the unit's own doc block if it has one; a `use` below a declaration
is [`CE3014`](error-catalog.md#ce3014).

An import may carry an `as NAME` clause. The clause decides WHERE the imported names
land, and nothing else:

| Form | What it binds |
|---|---|
| `use "math"` | every name `math` brings enters this unit's flat scope |
| `use "math" as my_math` | every name `math` brings is reachable as `my_math.<name>`, and **nothing** enters the flat scope |

<!-- docs-sweep: skip (two units) -->
```sushi
use "math" as my_math               # the unit next door
use <math> as std_math              # the standard library

fn main() i32:
    let f64 mine = my_math.sin(0.0).realise(0.0)
    let f64 theirs = std_math.sin(0.0)
    let i32 depth = my_math.MAX_DEPTH
    return 0
```

#### Scope is per unit, and it is not transitive

A unit sees its own declarations, plus what its own `use` statements bring. Nothing else.
An import is not re-exported: if `mid` imports `deep`, a unit that imports `mid` still
cannot name what `deep` declares, and `my_math.<name>` reaches what `math` *declares* and
never what `math` imported.

<!-- docs-sweep: skip (three units) -->
```sushi
# deep.sushi                    # mid.sushi              # top.sushi
public fn deep_value() i32:     use "deep"               use "mid"
    return 7                                             fn main() i32:
                                                             # CE2008 here
                                                             let i32 a = deep_value()
```

`top` adds `use "deep"`. The refusal is the ordinary "no such name" -- [`CE2008`](error-catalog.md#ce2008) for a
call, [`CE2001`](error-catalog.md#ce2001) for a type, [`CE1001`](error-catalog.md#ce1001) for a bare read -- with a help line naming the import
that would bring it.

**To name a type, import the unit that declares it.** A public signature may name a type
its caller cannot name, and there is no way round it: a `let` needs a written type, so a
value of an unnameable type cannot be bound. If `shapes.origin()` returns `geometry.Vec`,
a unit that calls `origin()` and binds the result imports `geometry` as well.

**A standard-library module is a flat import like any other.** `use <math>` puts `sqrt`
in the scope of the unit that wrote the line, and of no other. So is the built-in generic
an import activates: `HashMap` is a name in a unit that wrote `use <collections/hashmap>`.

**An FFI namespace belongs to the unit that declares the block.** An `unsafe external
"C" as libc` block binds `libc` where it is written, and nothing imports it.

#### Re-exporting an import: `public use`

`public use X` takes what X brings and makes it this unit's own, re-exported as public.
Every importer of this unit then gets the effect of `use X` in the same place this unit's
names land: flat behind a flat `use`, behind the dot of an aliased one. It is also an
ordinary `use` for the unit that writes it.

<!-- docs-sweep: skip (three units) -->
```sushi
# geometry.sushi          # shapes.sushi                 # main.sushi
public struct Vec:        public use "geometry"         use "shapes"
    i32 x                 public fn area(Vec v) i32:    fn main() i32:
    i32 y                     return v.x * v.y              let Vec v = Vec(2, 3)
                                                             println("{area(v)}")
                                                             return 0
```

`main` writes `Vec` with one import, because `shapes` hands it on. `use "shapes" as sh`
gives `sh.Vec` and `sh.area` alike. The rules:

- Only a `public use` re-exports. A plain `use` brings nothing to the unit's importers.
  Re-exports compose along `public use` chains and never along a plain `use`.
- Only the PUBLIC names travel. A unit cannot hand on what it may not name.
- A re-exported name is a candidate exactly as a flat import's is: this unit's own
  declaration wins over it, two re-exports offering different declarations of one name
  are [`CE3012`](error-catalog.md#ce3012) at the use, and the same declaration reached down two paths is one
  candidate.
- `public use` takes no `as` ([`CE3016`](error-catalog.md#ce3016)): a re-export is of names, not of a namespace.
- A `public use` that hands on nothing public warns ([`CW3005`](error-catalog.md#cw3005)).
- Every kind of `.slib` carries a `public use`: a source library ships the statement as
  text, a binary or hybrid one ships a manifest record of it.
- A library's `use <lib/b>` LOADS `b` in the consumer's build, plain or public, because
  the library's body calls it: loading is transitive. VISIBILITY is not: a `public use
  <lib/b>` gives the consumer `b`'s public names, as if the consumer wrote `use <lib/b>`,
  and a plain `use <lib/b>` gives none, so a bare name of `b` at the consumer is [`CE2008`](error-catalog.md#ce2008).
  If `b` is not on `SUSHI_LIB_PATH`, the consumer gets [`CE3502`](error-catalog.md#ce3502) with a note that names
  the `use`. One library reached by two paths is one library; two versions of one library
  in the graph are [`CE3519`](error-catalog.md#ce3519).

The standard library uses it: `use <io/fs>` alone brings `IoError`, `FileError` and
`SeekFrom`, because `<io/fs>` re-exports `<io/contracts>` and that re-exports `<io/error>`.

#### Where a qualified name may be written

The qualifier folds into the name after it, so resolution then runs exactly as it does
for the bare name -- against one unit instead of the flat scope. Every position that
turns written text into a name takes one:

| Position | Qualified form |
|---|---|
| a named type | `my_math.Vec` |
| a generic named type | `my_math.Box@(i32)` |
| a called function, generic included | `my_math.sin(0.0)` |
| a struct constructor, generic included | `my_math.Vec(1, 2)`, `my_math.Box(1)` |
| an enum constructor | `my_math.Sign.Plus` |
| an enum pattern | `my_math.Sign.Plus ->` |
| a named value | `my_math.MAX_DEPTH` |
| a perk in a constraint | `@(T: my_math.Loud)` |
| explicit type arguments | `my_math.empty@(i32)()` |

<!-- docs-sweep: skip (two units) -->
```sushi
use "geometry" as geo

struct Holder:
    geo.Vec spot                            # a field

fn total(geo.Vec v) i32:                    # a parameter
    return v.x + v.y

fn run() i32:
    let geo.Vec v = geo.Vec(1, 2)           # an annotation, and a constructor
    let geo.Sign s = geo.Sign.Plus          # an enum constructor
    match s:
        geo.Sign.Plus -> println("+")       # an enum pattern
        geo.Sign.Minus -> println("-")
    return total(v)
```

**One position cannot be qualified.** A fixed array's size is read while the unit's own
AST is built and an alias is bound long after that, so `i32[my_math.SIZE]` is [`CE2099`](error-catalog.md#ce2099).

A qualifier naming no namespace, or a name the namespace does not hold, is [`CE2001`](error-catalog.md#ce2001) in a
type position, with a help line drawn from what the namespace holds, and [`CE2008`](error-catalog.md#ce2008) in a
call.

**A generic struct is constructed through an alias as it is under a flat import.**
`my_math.Box(1)` is a `my_math.Box@(i32)`: a declared type gives the type arguments, and
with no declared type the arguments give them (see [Generics](generics.md)).

**Two units may export one name.** That is not an error by itself; it is an error only
where the unqualified name is written and nothing says which one is meant, and then it is
[`CE3012`](error-catalog.md#ce3012) at the use, with a note at each candidate. The unit's OWN declaration always
wins, so it never becomes ambiguous, and a flat `use <math>` does not take `sin` away
from a unit that declares its own.

**In one unit, one name has one declaration, whatever its kind.** A `fn`, a `const`, a
`var`, a `struct`, an `enum` and a `perk` share one set of names. The second declaration
of a name in source order is [`CE1005`](error-catalog.md#ce1005), with a note at the first, and the first keeps the
name. Two declarations of one kind keep that kind's code ([`CE0004`](error-catalog.md#ce0004), [`CE2046`](error-catalog.md#ce2046), [`CE4001`](error-catalog.md#ce4001),
[`CE0101`](error-catalog.md#ce0101), [`CE0105`](error-catalog.md#ce0105)), and a struct beside an enum is [`CE0006`](error-catalog.md#ce0006). Across units the name may
be used again: a unit's own `fn box` takes the call `box()` when a flat import brings a
struct `box`, and the struct is reached behind an alias, `sh.box(2)`. Two TYPES of one
name in two units stay refused, because a type is one per program.

<!-- docs-sweep: error CE1005 -->
```sushi
const i32 box = 3

fn box() i32:               # CE1005: function 'box' already declared in this unit as a constant
    return 4

fn main() i32:
    return 0
```

**A local variable wins.** A variable named `my_math` shadows the alias for the rest of
its scope, exactly as one shadows an FFI namespace.

**An alias is local to the unit that wrote it.** Nothing about it is exported, and a unit
that imports the aliasing unit does not see it.

**One name holds one namespace.** A second binding of the name -- another alias, an
`unsafe external` namespace, or one of the unit's own declarations -- is [`CE3013`](error-catalog.md#ce3013). Two
aliases for one import are legal and both work.

**An empty namespace warns.** An import that brings no name a qualified form can reach
makes its `as` clause useless: `use <collections/strings> as s` adds methods on `string`
and declares no name, so it is [`CW3004`](error-catalog.md#cw3004), a warning. The import still does its work. A module
that declares names binds them as usual: behind `use <io/fs> as io`, `io.open(...)`,
`io.FileMode.Write()` and `io.File` all work.

A namespace holds a unit's declarations **whatever their visibility**, so naming a
private one through the dot is [`CE3005`](error-catalog.md#ce3005) -- "not yours", never "no such name".

The full design is `docs/design/unit-namespaces.md`.

### Visibility

**Private is the default.** Six declarations carry the marker -- `fn`, `const`, `var`,
`struct`, `enum` and `perk` -- and each is private to the unit that declares it unless it says
`public`. Naming another unit's private declaration is [`CE3005`](error-catalog.md#ce3005). A generic function is no
exception.

```sushi
public const i32 MAX_DEPTH = 32     # another unit may read it
const i32 SCRATCH = 4096            # this unit only

public struct Point:                # another unit may name the type
    i32 x
    i32 y

enum Cursor:                        # this unit only
    Start
    Mid(i32)

public perk Loud:                   # another unit may implement it
    fn shout() i32

public fn helper() i32:
    return private_helper()

fn private_helper() i32:
    return 42
```

An **enum variant** carries no marker: it is as visible as its enum, because a private
variant would make a total `match` unwritable across a unit boundary.

An **extension** and a **perk implementation** carry no marker either. Each is exactly as
visible as the type it is attached to, so `extend Point doubled()` is public because
`Point` is, and `extend Cursor step()` is unreachable elsewhere because `Cursor` is not.
Writing `public` on an implementation method is [`CE6103`](error-catalog.md#ce6103).

A **private perk** hides the CONTRACT, not the method. Another unit may not implement it
(`extend X with Loud`) and may not constrain a type parameter with it (`@(T: Loud)`) --
both are [`CE4011`](error-catalog.md#ce4011) -- but a method it provides stays callable on any type you publish,
because method resolution is keyed on the receiver and blind to the caller.

**A public thing may not hand out a private one.** A public signature that names a private
type is [`CE3009`](error-catalog.md#ce3009), and a public constraint that names a private perk is [`CE3010`](error-catalog.md#ce3010). The rule
covers a return, an error arm, a parameter, a constant's type, a public struct's field and
a public enum's variant payload -- privacy on a type is worth nothing if a signature hands
the type out anyway.

The full design, with the reasoning for each ruling, is `docs/design/visibility.md`.

### Standard Library

Import stdlib modules with `use`:

```sushi
# List@(T) is built-in (no import needed)
# HashMap requires explicit import:
use <collections/hashmap>
use <collections/strings> # String utilities
use <io/fs>           # File, open() and the console handles
```

## Comments

Single-line comments only:

```sushi
# This is a comment
let i32 x = 42  # Inline comment
```

## Documentation Blocks

A documentation block is part of the declaration, not a comment near it. It opens with `##:` and
closes with `:##`:

```
DOC_BLOCK: /##:[^\n]*?:##|##:[\s\S]*?\n[ \t]*:##/
```

The closer is line-initial, or the block is a one-liner. Blocks do not nest. An unmatched `##:`
is [`CE6011`](error-catalog.md#ce6011), a `:##` with no opener is [`CE6012`](error-catalog.md#ce6012), and a line-initial `##:` inside a block is
[`CE6013`](error-catalog.md#ce6013).

A block stands in one of three positions:

| Position | Documents |
|---|---|
| Immediately above a declaration | that declaration |
| First item in a body | the function that encloses the body |
| First item in a file, attached to nothing | the unit |

The block attaches to the declaration on the next line; a blank line or a `#` comment breaks the
attachment. The text is dedented and not reflowed.

A tag is a Markdown list item: `- Parameter <name>:`, `- Returns:`, `- Errors:` or `- Example:`.
Everything else is prose, and the first paragraph is the summary. An `- Example:` introduces a
fenced code block, which `python tests/docs_sweep.py` compiles and runs; a tag with no fence
after it is [`CE7007`](error-catalog.md#ce7007), and a fence the block's own `:##` truncates is [`CE7008`](error-catalog.md#ce7008).

See [Documentation Blocks](documentation-blocks.md) for the positions, the tag vocabulary and
every diagnostic.

## Keywords

These words are reserved. A variable, a function or a type cannot take one of them as
its name ([`CE6001`](error-catalog.md#ce6001)):

- Declarations: `fn`, `let`, `const`, `var`, `struct`, `enum`, `error`, `perk`, `extend`,
  `with`, `static`, `public`, `use`
- Control flow: `if`, `elif`, `else`, `while`, `foreach`, `in`, `break`, `continue`,
  `match`, `return`, `expand`, `assert`
- Operators and literals: `and`, `or`, `xor`, `not`, `as`, `true`, `false`
- Parameter and binding modes: `nom`, `peek`, `poke`
- Foreign functions: `unsafe`, `external`, `because`
- Built-in forms: `new`, `from`, `print`, `println`
- Built-in type names: `bool`, `string`

`self` is not reserved. It names the receiver inside an extension method, and elsewhere it
is an ordinary name.

## String Literals

Sushi supports two string literal syntaxes:

**Double-quote strings** (`"..."`):
- Support interpolation with `{expr}` syntax
- All escape sequences supported
- Use for: string constants, interpolated strings

**Single-quote strings** (`'...'`):
- Plain string literals, no interpolation
- Same escape sequences as double-quote strings
- Use for: string arguments in interpolation, literal strings

```sushi
let string s1 = "double quotes"    # Supports interpolation
let string s2 = 'single quotes'    # No interpolation
let string s3 = 'can\'t'           # Escape sequences work
```

**Both quote styles are equivalent** except for interpolation support. Use whichever is more convenient.

### Escape Sequences

Both quote styles support the same escape sequences:

- `\\` - Backslash
- `\"` - Double quote
- `\'` - Single quote
- `\n` - Newline
- `\t` - Tab
- `\r` - Carriage return
- `\0` - Null character
- `\xNN` - Hexadecimal escape (e.g., `\x41` = 'A')
- `\uNNNN` - Unicode escape (e.g., `\u0041` = 'A')

## String Interpolation

Embed expressions in double-quote strings with `{expression}`:

```sushi
let i32 x = 42
let string name = "Arthur"

println("Hello {name}")
println("Answer: {x}")
println("Next: {x + 1}")
println("Squared: {x * x}")
```

**Supported types:** the integers, the floats, `bool`, `string`, every struct and enum, and
an array, a `List@(T)` or an `Own@(T)` whose element has a string form.
A `bool` prints as `true` or `false`. A struct or an enum prints through the predefined
perk `Display` (see [Predefined Perks](#predefined-perks-drop-hashable-eq-ord-and-display)).
The compiler derives the text from what the type holds, and `extend T with Display` with
`fn to_str() string` overrides it. `print` and `println` take the same values as a hole.

```sushi
struct Point:
    i32 x
    i32 y

enum Shape:
    Circle(i32)
    Rect(i32, i32)
    Empty

struct User:
    string name
    i32 age

fn main() i32:
    println("{Point(1, 2)}")            # Point(x: 1, y: 2)
    println("{Shape.Rect(3, 4)}")       # Shape.Rect(3, 4)
    println("{Shape.Empty}")            # Shape.Empty
    let User u = User("Arthur Dent", 42)
    println("{u}")                      # User(name: "Arthur Dent", age: 42)
    println("{u.name}")                 # Arthur Dent
    println(Point(0, 0))
    let i32[] xs = from([1, 2, 3])
    println("{xs}")                     # [1, 2, 3]
    println(from(["a, b", "c"]))        # ["a, b", "c"]
    return 0
```

**The derived format:**

- A struct prints its name and its fields as `name: value`. A generic struct prints its base
  name only: `Box(value: 1)`.
- An enum prints `Enum.Variant`, and a payload follows by position: `Shape.Circle(5)`.
  `Maybe` and `Result` print the same way when a type holds them: `Maybe.Some(3)`,
  `Maybe.None`, `Result.Ok(1)`, `Result.Err(StdError.Error)`.
- A string that a type holds prints in quotes, and its bytes are written as they are, with
  no escaping. A string in a hole of its own prints bare.
- An array and a `List@(T)` print as `[1, 2, 3]` and `["a", "b"]`. An empty one prints `[]`.
  The form is the same at the top level and when a type holds the array. A nested array
  prints `[[1], [2, 3]]`, and a `u8[]` prints as numbers (`[72, 105]`); `.to_string()`
  gives the text.
- An `Own@(T)` prints its payload. A float prints as `%g`, as a float hole does. A `bool`
  prints `true` or `false`, and `~` prints `~`.

**What is refused:**

- A type that holds a function value, a `ptr` or a `HashMap@(K, V)` has no string form, and
  neither has an array of one. A hole is [`CE2035`](error-catalog.md#ce2035), and `print` and `println` are [`CE2115`](error-catalog.md#ce2115).
  A note names the field or the element.
- A `HashMap@(K, V)` itself is refused: its iteration order is not specified, so its
  printed form could change from one run to the next.
- A top-level `Result` stays [`CE2037`](error-catalog.md#ce2037) in `print` and `println`, and is [`CE2035`](error-catalog.md#ce2035) in a hole.
- A top-level `Maybe` is [`CE2115`](error-catalog.md#ce2115) in `print` and `println`, and [`CE2035`](error-catalog.md#ce2035) in a hole. Handle
  the value first (`match`, `.realise(default)`, or `.or_err(nom e)??`). A type that HOLDS a `Maybe` or a
  `Result` prints them.
- Only the printed positions take a top-level array: `==`, `<`, `.to_str()` and a
  `Display` constraint on one are still refused.

### String Arguments in Interpolation

Use single-quote strings for string arguments inside interpolation expressions:

```sushi
use <collections/strings>

let string text = "hello"
let string[] parts = from(["a", "b"])
println("{text.pad_left(10, '*')}")       # Padding character
println("{text.find('world').realise(-1)}")   # Search string; find answers Maybe@(i32)
println("{text.replace('old', 'new')}")   # Multiple string args
println("{','.join(parts)}")              # Separator string
```

Single-quote strings work naturally in nested contexts where double quotes would require escaping.

A double-quoted string cannot stand inside an interpolation hole at all: the lexer knows
nothing about holes, so the inner quote closes the outer literal and the parse fails. The
error is [CE6001](error-catalog.md#ce6001) or [CE6002](error-catalog.md#ce6002), and its help names the two escapes -- single quotes inside the
hole, or bind the expression to a local first. When the literal starts with the hole
(`"{t.pad_left(3, "*")}"`), the error is [CE2026](error-catalog.md#ce2026) (unterminated interpolation), with no
help line.

A brace in a quoted literal inside a hole is a limit. The scanner that finds a hole counts
the braces and does not read the quotes. Thus `"{a'{'}"` and `"{a'}'}"` are
[CE6010](error-catalog.md#ce6010), and a single-quoted string with a brace (`"{'}'.len()}"`)
is CE6010 too. Write the byte as an escape: `a'\x7b'` for `{` and `a'\x7d'` for `}`.

```sushi
fn main() i32:
    println("{a'\x7b'} {a'\x7d'}")      # 123 125
    return 0
```

## Constants

### Declaration

Constants are declared with `const` and evaluated at compile-time:

```sushi
const i32 MAX_SIZE = 100
const string VERSION = "1.0.0"
const bool DEBUG = true
const f64 PI = 3.14159
```

### Constant Expressions

Constants support compile-time expressions with arithmetic, bitwise, logical, and comparison operators:

```sushi
const i32 BASE = 10
const i32 DOUBLE = 2 * BASE              # 20
const i32 COMPLEX = (100 + 50) / 3       # 50
const u32 FLAGS = 0x01 | 0x02 | 0x04     # 7
const bool IS_VALID = (100 > 50) and true # true
```

**Supported operations:**
- **Arithmetic**: `+`, `-`, `*`, `/`, `%` (numeric types)
- **Bitwise**: `&`, `|`, `^`, `~`, `<<`, `>>` (integer types only)
- **Logical**: `and`, `or`, `xor`, `not` (boolean type only)
- **Comparison**: `==`, `!=` (numeric, `bool`, `string`); `<`, `<=`, `>`, `>=` (numeric,
  `string` -- by bytes). Both operands must be of one type. A comparison of a struct or an
  enum in a constant initializer is [`CE0110`](error-catalog.md#ce0110): the derived `Eq` and `Ord` run only on
  run-time values. The same comparison in a function body is legal
- **Type casts**: `as` (between compatible types)

A constant always holds a value its type can hold: it is computed at the declared width,
and an operation whose result leaves the type is **[CE2077](error-catalog.md#ce2077)**. See
[Overflow](#overflow) for the two operator groups and for the `as` escape.

### Interpolation in a Constant

A string constant can interpolate, and a hole takes any constant expression. Each hole
prints exactly as the same expression prints at run time -- an integer at its declared
width, a float as `%g` -- so a constant and a body never disagree about a value's text. A struct
or an enum in a hole is [`CE0108`](error-catalog.md#ce0108), because `Display` runs only on a run-time value:

```sushi
const i32 ANSWER = 42
const string MESSAGE = "the answer is {ANSWER}"   # "the answer is 42"
const string BANNER = "{MESSAGE}!"                # constants nest

fn main() i32:
    println(BANNER)
    return 0
```

### Constant References

Constants can reference other constants:

```sushi
const i32 WIDTH = 100
const i32 HEIGHT = 50
const i32 AREA = WIDTH * HEIGHT  # 5000

const i32 BASE = 10
const i32 OFFSET = BASE * 2
const i32 TOTAL = OFFSET + BASE  # 30
```

The compiler detects circular dependencies:

```sushi
# ERROR: Circular constant dependency
const i32 A = B + 1
const i32 B = A + 1  # CE0109: circular dependency detected
```

### Array Constants

Fixed-size arrays with constant elements:

```sushi
const i32[3] PRIMES = [2, 3, 5]
const bool[2] FLAGS = [true, false]
const i32[4] POWERS = [1, 2, 4, 8]

# Can use expressions
const i32 BASE = 10
const i32[3] VALUES = [BASE, BASE * 2, BASE * 3]  # [10, 20, 30]
```

An array constant is used directly — no copy into a local is needed. Reads compile to a
`getelementptr` on the read-only global, so they cost nothing:

```sushi
const i32[3] PRIMES = [2, 3, 5]

fn main() i32:
    println(PRIMES[0])                  # 2
    println("second: {PRIMES[1]}")      # in interpolation too
    println(PRIMES.len())               # 3

    let Maybe@(i32) m = PRIMES.get(0)   # safe access
    println(m.realise(0))               # 2

    foreach(p in PRIMES.iter()):        # iteration
        println(p)

    return 0
```

A local may shadow an array constant, and the local wins:

```sushi
const i32[3] PRIMES = [2, 3, 5]

fn local_wins() i32:
    let i32[4] PRIMES = [7, 8, 9, 10]
    return PRIMES[0]         # 7, and .fill()/.reverse() work on it
```

A `string` element type works like any other:

```sushi
const string[2] NAMES = ["ford", "arthur"]

fn main() i32:
    println(NAMES[1])                   # arthur
    let string[2] copy = NAMES.clone()  # a local of its own (CE2436 without the clone)
    println(copy[0])                    # ford
    return 0
```

A fixed array of fixed arrays is a constant too. Read it with a chained index:

```sushi
const i32[2][3] TABLE = [[1, 2], [3, 4], [5, 6]]

fn main() i32:
    println("{TABLE.len()} {TABLE[0].len()} {TABLE[2][1]}")   # 3 2 6
    return 0
```

**Restrictions:**
- Array must be fixed-size (`T[N]`) at every depth. A dynamic array anywhere in the type
  (`i32[]`, `i32[2][]`, `i32[][2]`) is **[CE2015](error-catalog.md#ce2015)**
- All elements must be compile-time constant expressions
- **Immutable**: `.fill()`, `.reverse()` and `PRIMES[0] := 9` all write to their receiver, so each
  of them on a constant is **[CE2096](error-catalog.md#ce2096)**. The constant lives in read-only memory; copy it into a local
  and mutate that. (A local shadowing the constant is freely mutable.)

### Struct Constants

A struct is a constant when every argument of its construction is. Positional and named
construction both work, on the same all-or-nothing rule they follow in a body, and a
field whose type is another struct nests:

```sushi
struct Handle:
    i32 fd
    bool owned

struct Point:
    i32 x
    i32 y

struct Segment:
    Point start
    i32 length

const i32 STDOUT_FD = 1

const Handle OUT = Handle(STDOUT_FD, false)              # positional
const Handle ERR = Handle(fd: 2, owned: false)           # named
const Segment SEG = Segment(Point(3, 4), 7)              # nested

fn main() i32:
    println("{OUT.fd} {SEG.start.y}")                    # 1 4
    return 0
```

Only a name the compiler knows to be a struct starts a constant construction, so an
ordinary call is refused -- flat, and inside a field argument:

```sushi
const Handle BAD = Handle(pick())                # CE0108: function calls forbidden
const Segment ALSO_BAD = Segment(Point(pick(), 2), 3)   # CE0108, one level down
```

A struct constant lives in read-only memory like every other constant. Writing a field
is **[CE2096](error-catalog.md#ce2096)** and calling a `poke self` method on one is **[CE2400](error-catalog.md#ce2400)**, because read-only
storage cannot take a write. A `nom self` method TAKES the receiver, and unit-level
storage is never moved out of. On a type that owns a resource, that is **[CE2436](error-catalog.md#ce2436)** -- the
same code a `var` reads. On a plain type such as `Handle`, the method takes a copy, and
the call is legal. A `peek self` method reads the constant, and it is legal:

```sushi
struct Label:
    string text

const Label TITLE = Label("towel")

OUT.fd := 7          # CE2096: cannot assign to a field of constant 'OUT'
TITLE.release()      # CE2436 for a `release(nom self)`: a Label owns a string
```

### Enum Constants

An enum variant is a constant when every payload argument is. A payload-free variant is a
tag, in either spelling; a payload-carrying one is the tag plus its constant payloads,
laid out exactly as a run-time construction lays them out. A payload may be a struct, a
string or another enum, and a generic enum's variant is built against the declared type:

```sushi
enum Sign:
    Plus
    Minus

enum Shape:
    Dot
    Circle(i32)
    Labelled(string, i32)

const Sign DEFAULT = Sign.Plus                       # a tag; `Sign.Plus()` is the same
const Shape UNIT = Shape.Circle(1)
const Shape NAMED = Shape.Labelled("unit", 1)
const Maybe@(i32) NOTHING = Maybe.None               # the interned Maybe@(i32)

fn main() i32:
    match UNIT:
        Shape.Dot -> println("dot")
        Shape.Circle(r) -> println("circle {r}")     # circle 1
        Shape.Labelled(name, r) -> println("{name} {r}")
    return 0
```

A variant the enum does not declare, a payload count that does not fit and a payload of
the wrong type read the codes a body gets -- **[CE2045](error-catalog.md#ce2045)**, **[CE2050](error-catalog.md#ce2050)** and **[CE2049](error-catalog.md#ce2049)** -- and
a function call in a payload is **[CE0108](error-catalog.md#ce0108)**, as it is in a struct field. A `Result@(T, E)`
is an interned enum like `Maybe@(T)`, so `const Result@(i32, E) V = Result.Ok(42)` is a
constant by the same rule. A generic struct follows the same rule as a generic enum: `const Pair@(i32, bool) P = Pair(3, true)` builds
the instance the declaration names. A unit variable of an enum type takes the same
initializer, which is what lets storage start as `Maybe.None` and be filled on first use
(see the Unit Variables section).

### Restrictions

A constant is built from literals, other constants, operators, `as`, and a struct or an
enum variant whose every argument is a constant. Referring to another
constant is allowed and the order of declaration does not matter, so a constant may name one
declared further down the file. Indexing an array constant with a constant index works too,
and every bound is checked while compiling -- a constant cannot trap. Past the end is
**[CE2012](error-catalog.md#ce2012)** and a negative index is **[CE2056](error-catalog.md#ce2056)**, the codes an index in a body gets.

The other constant may belong to another unit. A flat `use "shapes"` brings its public
constants bare, and `use "shapes" as sh` puts them behind the dot, in the declared type
and in the initializer alike: `const sh.Shape SMALL = sh.UNIT`, `const i32 D = sh.SIZE * 2`,
`const sh.Point O = sh.Point(0, 0)`, `const sh.Shape T = sh.Shape.Circle(2)`. A private
constant is **[CE3005](error-catalog.md#ce3005)** here as in a body. The other unit's initializer is read in ITS
scope: a name inside it means what it meant where it was written. A standard-library
constant is a constant too -- with `use <math>`, `const f64 HALF = PI / 2.0` folds -- and
a unit's own declaration of the same name wins over it.

```sushi
const i32[3] PRIMES = [2, 3, 5]
const i32 SMALLEST = PRIMES[0]      # 2
const bool IS_TWO = SMALLEST == 2   # bool and string compare for equality
```

Constants cannot use:
- Function calls and method calls. A struct construction and an enum variant are not
  calls and are allowed -- see [Struct Constants](#struct-constants) and
  [Enum Constants](#enum-constants)
- Local variables (only other constants)
- Dynamic arrays
- A compile-time loop, so a generated table has to be spelled out element by element

There is no tuple constant, by design: a tuple literal is not a constant expression, so
`const (i32, i32) ORIGIN = (0, 0)` is **[CE0108](error-catalog.md#ce0108)**. A value that a program keeps for its
whole run and that has parts with a meaning is a struct constant. This is a decision of the
tuple design ([ruling 11](design/tuples.md#9-rulings)), not a limitation that a later
change removes.

```sushi
# ERROR: Not allowed in constants
const i32 X = get_value()     # CE0108: function calls forbidden
const i32 Y = some_local      # CE1001: the name is not a constant
const i32[] DYNAMIC = from([1, 2])  # CE2015: dynamic arrays forbidden
```

`+` on two strings is **[CE2509](error-catalog.md#ce2509)** in a constant exactly as it is in a body: Sushi has no
concatenation operator anywhere, interpolation is the way to combine strings.

Integer `/` and `%` in a constant mean what they mean in a body: division truncates toward
zero and a remainder takes the sign of its dividend, so `-7 / 2` is `-3` and `-7 % 2` is `-1`.

## Unit Variables

### Declaration

A **unit variable** is storage a unit keeps for the whole run of the program. It is
declared with `var` at the top level, beside a `const`, with the same shape: a type, a
name and an initializer. Where a constant is a value the compiler folds into every use, a
variable has an ADDRESS, so a rebind, a field assignment, a mutating method and a `poke`
all reach it.

```sushi
var i32 counter = 0                 # storage, initialized before main() runs

fn bump() ~:
    counter := counter + 1          # a rebind writes the storage

fn main() i32:
    bump()
    bump()
    println("{counter}")            # 2
    return 0
```

A unit variable is **private by default** and `public var` makes it visible to another
unit, exactly as for `fn`, `const`, `struct`, `enum` and `perk`. Reading, rebinding or
borrowing another unit's private variable is **[CE3005](error-catalog.md#ce3005)**. A public variable may not hand
out a private type (**[CE3009](error-catalog.md#ce3009)**). Behind an alias it is written
`t.count` like a constant, and `t.count := 3` and `poke t.count` reach the storage.

The console handles are the built-in example: `stdin`, `stdout` and `stderr` are
`public var File` declarations in `<io/fs>`, which is what lets `stdout.write(...)` call
a `poke self` contract method.

### The initializer

The initializer is a **constant expression**: a literal, another constant, operators,
`as`, an interpolation, or a struct built from constants -- everything a `const` accepts.
Nothing runs before `main`, so there is no initialization order to define, and a
variable cannot name another variable in its initializer (**[CE0108](error-catalog.md#ce0108)**); a constant cannot
name a variable at all (**[CE0108](error-catalog.md#ce0108)**).

One addition over a constant: an **empty container** is a legal initializer, because it
allocates nothing.

<!-- docs-sweep: skip (declarations only; the sweep compiles a block with a main) -->
```sushi
var i32[] table = from([])          # the descriptor {0, 0, null}
var u8[] bytes = new()
var List@(string) names = List.new()

fn remember(nom string s) ~:
    names.push(s)                   # a mutating method reaches the storage
```

`HashMap.new()` mallocs its buckets and is refused, and so is a `from([1, 2])` with
elements (**[CE0108](error-catalog.md#ce0108)** either way).

There is no tuple unit variable, by design: its initializer would be a tuple literal, which
is not a constant expression, so `var (i32, i32) cursor = (0, 0)` is **[CE0108](error-catalog.md#ce0108)**, the rule
of a [tuple constant](#restrictions).

### Borrowing, rebinding, and what is refused

A unit variable is borrowable like a local: `peek counter` and `poke counter` hand its
address to a function, one `poke` at a time (**[CE2403](error-catalog.md#ce2403)**), and `foreach(poke r in
table.iter())` points into its elements. A `let` bound from a read out of it borrows and
freezes it, exactly as it would a local: after `let string first = words[0]` on a
`var string[] words`, a `words.push(...)` while `first` lives is **[CE2412](error-catalog.md#ce2412)**.

Unit-level storage is **never moved out of**, and a `const` reads the same rule as a
`var`. Each is one object the program keeps for its whole run, so a `nom` argument, a
`let` bound straight from it, a `return` of it and a `nom self` method such as `close()`
are all **[CE2436](error-catalog.md#ce2436)** when the type owns a resource. Take an independent value with
`.clone()`. A plain value copies out freely, and a rebind is the one way to change what a
variable holds: the old value is dropped, the new one is stored.

<!-- docs-sweep: skip (declarations only; the sweep compiles a block with a main) -->
```sushi
use <io/fs>

fn redirect(nom File f) ~:
    stdout := f                     # legal: the old handle is dropped, `f` moves in

# ERROR CE2436: cannot move 'stdout': it is a unit variable
# let File mine = stdout
```

Nothing destroys a unit variable at exit. The process ends and the operating system
reclaims the pages; a variable that holds heap at that moment is not freed first.

A fixed array's size needs an integer CONSTANT: a variable has a run-time value,
so `i32[N]` with `var i32 N = 3` is **[CE2099](error-catalog.md#ce2099)**.

---

**See also:**
- [Standard Library](standard-library.md) - Built-in types and functions
- [Error Handling](error-handling.md) - Result@(T, E) and Maybe@(T)
- [Memory Management](memory-management.md) - RAII and ownership
- [Generics](generics.md) - Generic types and functions
