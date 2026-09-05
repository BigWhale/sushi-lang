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
- [Enums](#enums)
- [Pattern Matching](#pattern-matching)
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
    return Result.Ok(0)
```

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
    return Result.Ok(0)
```

Without the outer parentheses the second line starts a new statement, and the parse
fails with CE6001.

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
- `string` - UTF-8 null-terminated string
- `~` - Blank type (only for return types)

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

**Decimal literals** (base 10, no prefix):
```sushi
let i32 grouped = 1_000_000   # underscores allowed
let f64 pi = 3.141_592        # in a float's fraction too
let f64 big = 1_0.2_5e1_0     # and in all three parts at once
```

**Common features**:
- Every literal format supports underscore separators for readability, decimal and
  float included. One underscore, and it must have a digit on each side — so `1__0`,
  `1_`, `0x_FF` and `3._14` are rejected (**CE6006**), each naming the fix
- Prefixes are case insensitive (`0xFF` == `0xff`, `0B1111` == `0b1111`)
- A literal is **context-typed**: it takes its type from context (annotation,
  argument, field, operand). With no numeric context it defaults to `i32`.

**Context typing**: a bare literal is typed by its
expected type and range-checked at compile time, so no cast is needed to write a
literal of a non-`i32` type. A decimal literal uses value ranges (signed/unsigned per
type); a hex/binary/octal literal uses the target's bit-pattern width (so `0xFF` is a
valid `i8` — the pattern `-1`); an `f32` rejects overflow to infinity (precision loss
on `f64`->`f32` is silently rounded). An out-of-range literal is `CE2073`, and an
operation whose *result* leaves the type is `CE2077` (see [Overflow](#overflow)). This is
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

**No-context default** (`CE2070`): a literal with no numeric context defaults to
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
- Only numeric types can be cast
- Float-to-integer truncates toward zero
- No implicit conversions
- No casting to/from strings or arrays

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
`return from([])` in an `extend S empty() u8[]` both mean `u8[]`.

### Function Types

A function type describes a first-class function value (a bare function pointer). The return
type is mandatory; the optional `| E` names the error type (defaults to `StdError`).

```sushi
fn(i32) -> i32                 # takes i32, returns i32 (error type StdError)
fn(i32, string) -> bool        # two parameters
fn() -> ~                      # no parameters, blank return
fn(i32) -> i32 | MathError     # explicit custom error type
```

Reference a plain top-level function by name to get a value of that type, then store, pass, or
call through it:

```sushi
let fn(i32) -> i32 f = add_one     # `add_one` used as a value
let i32 r = f(41)??                # call through it -> Result, like a direct call
```

Function types are invariant (arity, parameters, return, and error type must match exactly).
A plain top-level function is referenceable as above; a **closure** — a capturing lambda literal
(`|i32 x| x + n`) — is also a `fn(...)`-typed value and shares the same call syntax. A **generic**
function is referenceable when the expected function type is explicit (`let fn(i32) -> i32 g =
identity`); otherwise it is `CE2093`.

You can also call through any expression that evaluates to a function value, not just a bare name —
a fn-typed struct field (`obj.handler(x)`, when no method of that name exists), a container get-out
(`fns.get(0)??(x)`), or a parenthesized expression (`(e)(x)`). See the
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

### Reference bindings

A `let` may bind a **reference** into storage another variable owns, with the mode on the
declaration: `let poke T x = <place>` writes through, `let peek T x = <place>` reads
through. The place is a local, a field or element of one, a unit variable, or an
`Own@(T)`'s payload (`o.get()`); it is written bare, and a call result or a `??` is a
temporary with no address to bind (**CE2404**). The binding is block-scoped, and while it
lives the owner is frozen: mutating, rebinding or moving the owner and then using the
binding is **CE2412**.

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
    return Result.Ok(0)
```

One `poke` binding of an owner at a time (**CE2403**); a `peek` beside a live `poke`, or
the reverse, is **CE2407**; a write through a `peek` binding is **CE2408**; a `poke`
binding out of a `peek` parameter is **CE2408** too. Consuming the binding stays
**CE2411** -- it names storage the owner still frees -- and `.clone()` is the escape. A
constant has no address to bind (**CE2400**); a unit variable has one.

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

    return Result.Ok(0)
```

## Functions

### Declaration

```sushi
fn function_name(param1_type param1_name, param2_type param2_name) return_type:
    # Function body
    return Result.Ok(value)
```

**Example:**

```sushi
fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

fn greet(string name) ~:
    println("Hello, {name}!")
    return Result.Ok(~)
```

### Return Types

All functions implicitly return `Result@(T, E)`:

```sushi
fn divide(i32 a, i32 b) i32:  # Actually returns Result@(i32, StdError)
    if (b == 0):
        return Result.Err(StdError.Error)
    return Result.Ok(a / b)
```

### Parameters

A parameter declares one of four **modes**. The mode says who frees the value, and a marked
mode is written at the declaration and at the call site alike:

| declaration | call site | who frees | notes |
|---|---|---|---|
| `string x` | `f(s)` | caller | the default; the argument stays usable |
| `nom string x` | `f(nom s)` | **callee** | a later use of the argument is `CE2405` |
| `peek string x` | `f(peek s)` | caller | by pointer, read only; many at once |
| `poke string x` | `f(poke s)` | caller | by pointer, read/write; one, exclusive |

**Unmarked (a borrow):**
```sushi
fn modify(i32 x) i32:
    x := x + 1          # the callee's own copy
    return Result.Ok(x)
```

**`nom` (a consume):**
```sushi
fn eat(nom string s) ~:
    println(s)
    return Result.Ok(~)  # s is freed here

fn main() i32:
    let string base = "Ford"
    let string s = "{base} Prefect"
    eat(nom s)
    # println(s)          # ERROR CE2405: s was handed over
    return Result.Ok(0)
```

**Borrowed by pointer:**
```sushi
fn increment(poke i32 counter) ~:
    counter := counter + 1
    return Result.Ok(~)

fn read_value(peek i32 x) i32:
    return Result.Ok(x)
```

The rule and its reasoning are [docs/design/borrow-model.md](design/borrow-model.md).

## Operators

### Operand types

Two numeric operands of one operator must have the same type. Sushi converts no
numeric type on its own, so the operands say what the result is: `+ - * / %`, the
comparisons `== != < <= > >=`, and the bitwise `& | ^` all refuse a mixed pair
with **CE2510**.

<!-- docs-sweep: error CE2510 -->
```sushi
fn main() i32:
    let u8 low = 0x34
    let u32 wide = 0x1200
    let u32 both = low | wide          # CE2510: u8 and u32
    return Result.Ok(0)
```

`as` makes the widths agree, and then the operation says what it means:

```sushi
fn main() i32:
    let u8 low = 0x34
    let u32 wide = 0x1200
    let u32 both = (low as u32) | wide  # 0x1234
    return Result.Ok(0)
```

A shift is the exception. Its right operand is a count, not a second value: it
says how far to move the bits, and the result keeps the type of the left operand.
The count can be of any numeric type.

```sushi
fn main() i32:
    let u64 value = 8
    let u8 places = 8
    let u64 shifted = value << places   # 2048
    return Result.Ok(0)
```

A count is also limited by the width of the value it shifts, because a count at or
above that width moves every bit out of the type. A count the compiler can read --
a literal, a constant, an expression of them -- is **CE2512**:

<!-- docs-sweep: error CE2512 -->
```sushi
fn main() i32:
    let u8 high = 0x12
    let u8 shifted = high << 8          # CE2512: a u8 count runs from 0 to 7
    return Result.Ok(0)
```

Cast the value to the width the shift is meant to reach:

```sushi
fn main() i32:
    let u8 high = 0x12
    let u32 reached = (high as u32) << 8    # 0x1200
    return Result.Ok(0)
```

A computed count -- a loop index, a value read from a file -- cannot be read at
compile time, so it is not an error. It has a defined answer instead: a shift by a
count at or above the width moves every bit out of the type and gives **0**. An
arithmetic right shift fills from the sign bit, so it leaves the sign behind: 0 for
a positive value and -1 for a negative one. A negative count is out of range at the
other end and answers the same way.

```sushi
fn shift(u8 value, u8 places) u8:
    return Result.Ok(value << places)

fn main() i32:
    println("{shift(0x12, 3).realise(0)}")     # 144
    println("{shift(0x12, 8).realise(0)}")     # 0 -- every bit has left the u8
    return Result.Ok(0)
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

### Overflow

An expression whose value the compiler reads is computed at the **declared width**, and an
operation whose result the type cannot hold is a compile error (**CE2077**). That covers a
constant and a fold of literals in a body — one expression has one meaning:

<!-- docs-sweep: error CE2077 -->
```sushi
fn main() i32:
    let u8 sum = 200 + 100    # CE2077: '+' gives 300, which is out of range for u8
    println(sum)
    return Result.Ok(0)
```

The **overflow-checked** operators are `+`, `-`, `*`, `/`, `%` and unary minus. Division
has one such case, the smallest signed value over `-1`, and unary minus has one, the
smallest signed value: neither has an answer the type can hold.

The **width-defined** operators compute at the width and never report, because the bits
that leave the type are lost by design: `~`, `&`, `|`, `^`, `<<` and `>>`. So `200 << 1`
on a `u8` is 144, and `~0` on a `u32` is 4294967295.

An `as` cast is the escape. It asks for the bit pattern, so it truncates: `300 as u8` is
44. A wider type is the other answer.

**Run time does not change.** Only an expression the compiler reads is checked, so two
locals still wrap:

```sushi
fn main() i32:
    let u8 a = 200
    let u8 b = 100
    let u8 sum = a + b        # 44 at run time, and nothing reports it
    println(sum)
    return Result.Ok(0)
```

### Comparison

- `==` - Equal
- `!=` - Not equal
- `<` - Less than
- `<=` - Less than or equal
- `>` - Greater than
- `>=` - Greater than or equal

Equality accepts the numeric types, `bool` and `string`. An order (`<`, `<=`, `>`, `>=`)
accepts the numeric types and `string`. Both operands must be of one type: a mixed pair is
CE2513, two numeric types of different widths are CE2510, and a type that carries no such
comparison is CE2514. A `bool` has no order, because `a < b` on two bools is almost always
a typo for `!=`. Use `match` to ask which variant an enum holds, and compare the fields of a
struct one at a time.

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
array there is CE2005, and a `Result@(T, E)` or a `Maybe@(T)` is CE2516. `not 5` does
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
over a `u64` / `u32`, and `from_bits()` goes back. A float operand is **CE2004**.

<!-- docs-sweep: error CE2004 -->
```sushi
fn main() i32:
    let f64 value = 1.5
    let f64 masked = value & 1.0    # CE2004: a float has no bits
    return Result.Ok(0)
```

```sushi
fn main() i32:
    let f64 value = 1.5
    let u64 bits = value.to_bits()
    let u64 sign = (bits >> 63) & 1      # 0
    let f64 back = f64.from_bits(bits)   # 1.5
    return Result.Ok(0)
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

### String

There is no `+` concatenation operator for strings. Build strings with
interpolation instead:

```sushi
let string a = "foo"
let string b = "bar"
let string combined = "{a}{b}"   # "foobar"
```

### Other

- `as` - Type casting
- `??` - Error propagation. Postfix on an expression, and also on a `foreach` binder
  (`foreach(line?? in it)`), where it unwraps the loop's item

## Control Flow

### If-Elif-Else

Parentheses required around conditions. A condition is a `bool` and nothing else: a
`Result@(T, E)` or a `Maybe@(T)` is CE2516 (test one with `.is_ok()` / `.is_some()`),
and every other type is CE2005 — an integer carries no truth value, so write the
question (`n != 0`). The same rule covers a `while` condition and both operands of
`and`, `or` and `xor` and the operand of `not`, so `not 5` is refused exactly as
`if (5)` is.

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

**Two things are walkable.** An ITERATOR -- what `.iter()` answers on an array or a
`List@(T)`, what `.keys()` / `.values()` / `.entries()` answer on a `HashMap`, and what a
range is. Or **any type carrying `next()` answering `Maybe@(T)`**: the loop calls it until
it answers `None`, and that is the whole protocol. There is no type to implement and no
perk to name, so a struct becomes walkable by gaining one method.

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
    let BufReader@(File) r = BufReader.new(nom f, 8192)??
    foreach(line?? in r.lines()):        # the first read failure leaves show()
        println(line)
    return Result.Ok(~)

fn main() i32:
    match show("/etc/hosts"):
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
```

A `??` binder over an item that is not a `Result` has nothing to unwrap and is
**CE2517**. An iterable that is neither an iterator nor a type with `next()` is
**CE2033**. A reference binding (`foreach(poke r in ...)`) takes no `??` marker: it points
INTO storage, and there is nothing to unwrap there.

`foreach` CONSUMES its iterable, and a protocol iterator is destroyed when the loop ends --
by `break` and by `return` as well as at the end of the input.

The argument behind all of this -- why the failure rides in the ITEM rather than on the
loop head, why the protocol is not a perk, and why a line iterator's stop is sticky -- is
[Iteration (design)](design/iteration.md).

## Arrays

See [Standard Library](standard-library.md) for complete array API.

### Fixed Arrays

Stack-allocated, compile-time size:

```sushi
let i32[5] arr = [1, 2, 3, 4, 5]
let i32 first = arr.get(0)??  # .get returns Maybe@(i32); ?? unwraps it
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
    return Result.Ok(0)
```

It may also name an integer constant, so a size that repeats across declarations can
be written once. The constant may be an expression, and a named size works wherever a
type does -- a local, a struct field, a parameter, a return type:

```sushi
const i32 MAX_BITS = 4

struct Counts:
    i32[MAX_BITS] slots

fn walk(i32[MAX_BITS] counts) i32:
    return Result.Ok(counts.len())

fn main() i32:
    let i32[MAX_BITS] counts = [1, 2, 3, 4]
    return Result.Ok(walk(counts).realise(0))
```

The constant must be declared in the **same unit**. A size is read while that unit's
AST is built, before any pass holds a program-wide constant table, so a constant in
another unit is reachable as a value but not as a size.

A size that cannot count elements is **CE2099**: a name that is no integer constant
of this unit, a constant that is not an integer, or a zero. A zero-length array does
not exist in Sushi.

<!-- docs-sweep: error CE2099 -->
```sushi
fn main() i32:
    let i32[0] nothing = [1]        # CE2099: an array holds at least one element
    return Result.Ok(0)
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
    return Result.Ok(0)
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
    return Result.Ok(from([0; n]))

fn main() i32:
    let i32[] xs = from([10, 20, 30])
    let i32[] prev = from([-1; xs.len()])
    println("{prev.len()} {prev[0]}")       # 3 -1
    return Result.Ok(0)
```

A count the compiler CAN read and that is not a count is **CE2017** -- a zero, a
negative, or, in a fixed array or a constant, a value it cannot read:

<!-- docs-sweep: error CE2017 -->
```sushi
fn main() i32:
    let i32 n = 4
    let i32[4] t = [7; n]           # CE2017: a fixed array needs a readable count
    println(t[0])
    return Result.Ok(0)
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
    return Result.Ok(0)
```

#### A range element

An element may be a **range**, and it fills the slots it spans. `start..end` is exclusive
and `start..=end` is inclusive, and the direction follows `foreach`, so a descending
range descends:

```sushi
fn main() i32:
    let i32[]  up      = from([0..5])       # 0 1 2 3 4
    let i32[]  through = from([0..=5])      # 0 1 2 3 4 5
    let i32[]  down    = from([5..0])       # 5 4 3 2 1
    let i32[6] table   = [0..=5]
    let i32[]  mixed   = from([-1, 0..3, 99])   # -1 0 1 2 99
    println("{up.len()} {through.len()} {down.len()} {table[5]} {mixed.len()}")
    return Result.Ok(0)
```

A range yields **i32**, exactly as `foreach(i in 0..5)` does, so `let i64[] a =
from([0..5])` is a type mismatch. It obeys the same position rule as a repeat: a bound in
a `from()` literal may be any i32 expression, and a fixed array or a constant needs one
the compiler can read. A bound it cannot read there is **CE2019**, and so is a readable
range that yields nothing:

<!-- docs-sweep: error CE2019 -->
```sushi
fn main() i32:
    let i32[] a = from([3..3])      # CE2019: this range yields no value
    println(a.len())
    return Result.Ok(0)
```

A range cannot carry a repeat count. `value; count` repeats ONE value, and a range is
already a sequence, so `[0..2; 3]` is **CE2020**.

What CE2011 compares is the **expanded** count, so a run of 144 is 144 slots. When a
literal has a run, CE2011 lists every run with the span it fills, because the compiler
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

`new()` is a value, not only a declaration form. It takes its element type from the position
it stands in, so it spells the empty array anywhere one is expected -- a call argument, an
enum payload, a rebind, a struct field, and the default of a `.realise()`:

```sushi
fn count(i32[] xs) i32:
    return Result.Ok(xs.len())

fn mk(bool good) i32[]:
    if (not good):
        return Result.Ok(new())
    return Result.Ok(from([1, 2, 3]))

let i32 none = count(new())??
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

The index is bounds-checked like a read (**RE2020** at run time; **CE2012** for a literal
index past the end of a fixed array, **CE2056** for a negative one). An owning element that the write replaces is freed
first. The assignment takes ownership of the value, so an owned source is moved (later use
is **CE2405**) and a value read out of a container needs `.clone()` (**CE2411**).

The write must be able to reach the owner. It is rejected through a `peek` parameter
(**CE2408**), a `match`/`foreach` binding (**CE2414**), a method receiver without
`poke self` (**CE2421**), an unmarked parameter (**CE2422**), a `let` binding that borrows
from an owner (**CE2426**), an unbound chained receiver such as `o.get().items`
(**CE2429**), and a constant (**CE2096**).

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
  their arguments by position, and a name written there is `CE6104`

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
both -- an undeclared variant is CE2045, and the value takes the type of its position.
That holds for a generic enum too: `let Maybe@(string) m = Maybe.None` constructs a
`Maybe@(string)`, and the binding owns it exactly as `Maybe.None()` would.

### Pattern Matching

Required to access enum data:

```sushi
match s2:
    Status.Idle() ->
        println("Idle")
    Status.Running(task_id) ->
        println("Running task {task_id}")
    Status.Error(msg) ->
        println("Error: {msg}")
```

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

```sushi
use <io/fs>

match result:
    Result.Err(FileError.NotFound()) ->
        println("File not found")
    Result.Err(_) ->
        println("Other file error")
    Result.Ok(f) ->
        println("File opened")
```

### Binding Modes

A payload binding carries a MODE, and the three are the ones a parameter has. The bare
form is the common case and is unchanged.

| pattern | the binding is | write through it | may be given away |
|---|---|---|---|
| `Ok(x)` | a read-only view | no (CE2414) | no (CE2411) |
| `Ok(poke x)` | a pointer into the scrutinee's payload | yes, and it reaches the owner | no |
| `Ok(nom x)` | the value itself, now the arm's | yes | yes |

`nom` TAKES the payload, so the match has to own its scrutinee. A temporary -- a call
result, a constructor, a `??` -- is owned by construction. A place expression belongs to
its owner until the match says `nom`, and then the local is consumed exactly as
`f(nom r)` consumes it.

| scrutinee | the match |
|---|---|
| `match open("out.log", FileMode.Write()):` | OWNS a temporary; `nom` bindings are legal |
| `match r:` | BORROWS the local; a `nom` binding is CE2432 |
| `match nom r:` | CONSUMES the local; `nom` bindings are legal, and a later `r` is CE2405 |

<!-- docs-sweep: skip (a fragment: `report` is the narrative's, and neither match returns) -->
```sushi
use <io/fs>
use <io/buf>

match open("out.log", FileMode.Write()):
    Result.Ok(f) -> f.writeln("Mostly Harmless")     # a borrow: no marker, unchanged
    Result.Err(e) -> report(e)

match open("out.log", FileMode.Write()):
    Result.Ok(nom f) -> BufWriter.new(nom f, 4096)      # takes it, and says so
    Result.Err(e) -> report(e)
```

An arm takes the variant WHOLE: if any binding in it is `nom`, every other owning payload
of that variant must be `nom` too (CE2433). `nom` is not valid inside an `Own(...)`
pattern (CE2434), and a `peek`/`poke` binding still needs a scrutinee with storage -- a
read through a live owner has none (CE2404).

### Exhaustiveness

The compiler enforces that all variants are matched:

```sushi
enum Color:
    Red()
    Green()
    Blue()

# ERROR: Non-exhaustive match (missing Blue)
match color:
    Color.Red() -> println("Red")
    Color.Green() -> println("Green")
```

### Integer Matching

A match on an integer scrutinee dispatches on literal arms. Each literal takes
the scrutinee's type under the usual context-typing rule (a non-decimal literal
is a bit pattern; out of range is CE2073). Two arms with the same value are one
duplicate arm (CE2075), whatever their radix. Because integer values cannot be
enumerated, the match must end with a `_` arm (CE2074). Literal arms and enum
pattern arms never mix in one match (CE2076).

```sushi
fn tag_name(u8 t) string:
    match t:
        0xc0 ->
            return Result.Ok("nil")
        0xc2 ->
            return Result.Ok("false")
        0xc3 ->
            return Result.Ok("true")
        _ ->
            return Result.Ok("other")

fn main() i32:
    let u8 tag = 0xc0
    println(tag_name(tag).realise("err"))
    return Result.Ok(0)
```

## Module System

### Units

Sushi uses a unit system where each source file is a unit:

```sushi
# file: math.sushi
use "math"

fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)
```

### Importing a unit

`use "path"` imports another unit of the program, `use <module>` a standard-library
module, and `use <lib/name>` a library. **Every import stands above the first
declaration**, after the unit's own doc block if it has one; a `use` below a declaration
is `CE3014`.

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
    return Result.Ok(0)
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
    return Result.Ok(7)                                  fn main() i32:
                                                             # CE2008 here
                                                             let i32 a = deep_value()??
```

`top` adds `use "deep"`. The refusal is the ordinary "no such name" -- `CE2008` for a
call, `CE2001` for a type, `CE1001` for a bare read -- with a help line naming the import
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

#### Where a qualified name may be written

The qualifier folds into the name after it, so resolution then runs exactly as it does
for the bare name -- against one unit instead of the flat scope. Every position that
turns written text into a name takes one:

| Position | Qualified form |
|---|---|
| a named type | `my_math.Vec` |
| a generic named type | `my_math.Box@(i32)` |
| a called function, generic included | `my_math.sin(0.0)` |
| a struct constructor | `my_math.Vec(1, 2)` |
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
    return Result.Ok(v.x + v.y)

fn run() i32:
    let geo.Vec v = geo.Vec(1, 2)           # an annotation, and a constructor
    let geo.Sign s = geo.Sign.Plus          # an enum constructor
    match s:
        geo.Sign.Plus -> println("+")       # an enum pattern
        geo.Sign.Minus -> println("-")
    return Result.Ok(total(v)??)
```

**One position cannot be qualified.** A fixed array's size is read while the unit's own
AST is built and an alias is bound long after that, so `i32[my_math.SIZE]` is `CE2099`.

A qualifier naming no namespace, or a name the namespace does not hold, is `CE2001` in a
type position and `CE2008` in a call, each with a help line drawn from what the namespace
does hold.

**Two units may export one name.** That is not an error by itself; it is an error only
where the unqualified name is written and nothing says which one is meant, and then it is
`CE3012` at the use, with a note at each candidate. The unit's OWN declaration always
wins, so it never becomes ambiguous, and a flat `use <math>` no longer takes `sin` away
from a unit that declares its own.

**A local variable wins.** A variable named `my_math` shadows the alias for the rest of
its scope, exactly as one shadows an FFI namespace.

**An alias is local to the unit that wrote it.** Nothing about it is exported, and a unit
that imports the aliasing unit does not see it.

**One name holds one namespace.** A second binding of the name -- another alias, an
`unsafe external` namespace, or one of the unit's own declarations -- is `CE3013`. Two
aliases for one import are legal and both work.

**An empty namespace warns.** `use <io/fs> as io` binds nothing, because the import
enables methods on `stdin` and brings no name: that is `CW3004`, a warning, and the
import still does its work.

A namespace holds a unit's declarations **whatever their visibility**, so naming a
private one through the dot is `CE3005` -- "not yours", never "no such name".

The full design is `docs/design/unit-namespaces.md`.

### Visibility

**Private is the default.** Five declarations carry the marker -- `fn`, `const`, `struct`,
`enum` and `perk` -- and each is private to the unit that declares it unless it says
`public`. Naming another unit's private declaration is `CE3005`. A generic function is no
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
    return Result.Ok(private_helper()??)

fn private_helper() i32:
    return Result.Ok(42)
```

An **enum variant** carries no marker: it is as visible as its enum, because a private
variant would make a total `match` unwritable across a unit boundary.

An **extension** and a **perk implementation** carry no marker either. Each is exactly as
visible as the type it is attached to, so `extend Point doubled()` is public because
`Point` is, and `extend Cursor step()` is unreachable elsewhere because `Cursor` is not.
Writing `public` on an implementation method is `CE6103`.

An extension may declare method-level type parameters and an error channel —
`extend List@(T) map@(U)(fn(T) -> U f) List@(U) | StdError:` — solved and handled at
the call site (`xs.map(f)??`). The success returns bare; `Result.Err(e)` is the one
spelled constructor. Array targets take a concrete element (`extend i32[]`) or a bare
name that binds a type parameter (`extend T[]`). The design record is
`docs/design/ufcs-combinators.md`.

### Static methods

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
    return Result.Ok(0)
```

A name behind a type's dot is a **member** of that type: a variant, or a static method,
never both. A local of the same name wins over the type, as it always has.

Everything but the receiver is unchanged. The parameters take the ordinary four modes
and BORROW unless marked `nom`; an owning return belongs to the caller; `| E` opts into
the error channel exactly as on an instance method; and the declaration carries no
visibility marker, because a static is as visible as its target type.

`new` is a legal static name — `extend Box static new(i32 n) Box:` — which a free
function cannot have (`CE6001`).

A static has **no `self`**, and the two places that could name one are one refusal:
a receiver mode in the signature (`extend Vec static at(poke self)`) and a mention of
`self` in the body are both `CE0134`. A `static` inside a perk implementation is
`CE4014`: a perk has no `Self`, so a contract cannot hold a constructor.

The target may be a struct, an enum, a primitive (`extend f64 static of_int(i32 v)
f64:`) or a generic type; an ARRAY target is `CE2104`, because an array type has no
spelling in an expression position and the declaration could never be called. On a
generic target the type argument comes from the declared type at the call site, because
there is no receiver to read it from:

```sushi
struct Cage@(T):
    T item

extend Cage@(T) static holding(T item) Cage@(T):
    return Cage(item)

let Cage@(i32) a = Cage.holding(9)
```

A generic static in a position that declares no type — a bare
`println("{Cage.holding(9).item}")` — is `CE2060`: there is no receiver and no
annotation, so nothing says which instantiation was meant. Bind the result first.

A name has one home, so a static beside an instance method of the same name on one type
is `CE0101`, and a static spelling a VARIANT of the enum it extends is `CE2103`. A type
whose dot holds no such member is `CE2102`.

`List.new()`, `List.with_capacity()`, `HashMap.new()`, `Own.alloc()` and
`f64.from_bits()` are the built-in statics — the same rule, on types the compiler
declares. The design record is `docs/design/method-resolution.md`.

A **perk method** takes the same error channel, and the perk states it in the contract:
`fn read(poke u8[] into) i32 | IoError`. Every implementation repeats the channel
exactly; a channel one side declares and the other does not, and two channels over
different error types, are both `CE0133`, which points at the contract and the
implementation together. A perk method has no method-level type parameters (`CE4010`
covers the perk itself) and no `Self` type, so a contract cannot promise to return
another one of the implementing type.

A **private perk** hides the CONTRACT, not the method. Another unit may not implement it
(`extend X with Loud`) and may not constrain a type parameter with it (`@(T: Loud)`) --
both are `CE4011` -- but a method it provides stays callable on any type you publish,
because method resolution is keyed on the receiver and blind to the caller.

**A public thing may not hand out a private one.** A public signature that names a private
type is `CE3009`, and a public constraint that names a private perk is `CE3010`. The rule
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
use <io/fs>           # stdio functions
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
is `CE6011`, a `:##` with no opener is `CE6012`, and a line-initial `##:` inside a block is
`CE6013`.

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
after it is `CE7007`, and a fence the block's own `:##` truncates is `CE7008`.

See [Documentation Blocks](documentation-blocks.md) for the positions, the tag vocabulary and
every diagnostic.

## Keywords

Reserved keywords:

- `fn` - Function declaration
- `let` - Variable declaration (block-scoped)
- `const` - Constant declaration (compile-time, no address)
- `var` - Unit variable declaration (storage with an address, one per program)
- `struct` - Struct definition
- `enum` - Enum definition
- `if`, `elif`, `else` - Conditionals
- `while` - Loop
- `foreach`, `in` - For-each loop
- `break`, `continue` - Loop control
- `match` - Pattern matching
- `return` - Function return
- `and`, `or`, `not` - Logical operators
- `true`, `false` - Boolean literals
- `as` - Type casting
- `unit` - Unit declaration
- `public` - Visibility marker (`fn`, `const`, `var`, `struct`, `enum`, `perk`)
- `use` - Module import
- `extend` - Extension method
- `static` - A method with no receiver, called on the type name
- `self` - Extension method receiver

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

**Supported types:** All primitives, strings

### String Arguments in Interpolation

Use single-quote strings for string arguments inside interpolation expressions:

```sushi
use <collections/strings>

let string text = "hello"
println("{text.pad_left(10, '*')}")       # Padding character
println("{text.find('world')}")           # Search string
println("{text.replace('old', 'new')}")   # Multiple string args
println("{','.join(parts)}")              # Separator string
```

Single-quote strings work naturally in nested contexts where double quotes would require escaping.

A double-quoted string cannot stand inside an interpolation hole at all: the lexer knows
nothing about holes, so the inner quote closes the outer literal and the parse fails with
CE6001 or CE6002. The diagnostic names this shape and the two escapes -- single quotes
inside the hole, or bind the expression to a local first.

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
  `string` -- by bytes). Both operands must be of one type
- **Type casts**: `as` (between compatible types)

A constant always holds a value its type can hold: it is computed at the declared width,
and an operation whose result leaves the type is **CE2077**. See
[Overflow](#overflow) for the two operator groups and for the `as` escape.

### Interpolation in a Constant

A string constant can interpolate, and a hole takes any constant expression. Each hole
prints exactly as the same expression prints at run time -- an integer at its declared
width, a float as `%g` -- so a constant and a body never disagree about a value's text:

```sushi
const i32 ANSWER = 42
const string MESSAGE = "the answer is {ANSWER}"   # "the answer is 42"
const string BANNER = "{MESSAGE}!"                # constants nest

fn main() i32:
    println(BANNER)
    return Result.Ok(0)
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

    return Result.Ok(0)
```

A local may shadow an array constant, and the local wins:

```sushi
const i32[3] PRIMES = [2, 3, 5]

fn local_wins() i32:
    let i32[4] PRIMES = [7, 8, 9, 10]
    return Result.Ok(PRIMES[0])         # 7, and .fill()/.reverse() work on it
```

A `string` element type works like any other:

```sushi
const string[2] NAMES = ["ford", "arthur"]

fn main() i32:
    println(NAMES[1])                   # arthur
    let string[2] copy = NAMES          # an ordinary local
    println(copy[0])                    # ford
    return Result.Ok(0)
```

**Restrictions:**
- Array must be fixed-size (`T[N]`), not dynamic (`T[]`)
- All elements must be compile-time constant expressions
- **Immutable**: `.fill()`, `.reverse()` and `PRIMES[0] := 9` all write to their receiver, so each
  of them on a constant is **CE2096**. The constant lives in read-only memory; copy it into a local
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
    return Result.Ok(0)
```

Only a name the compiler knows to be a struct starts a constant construction, so an
ordinary call is refused as it always was -- flat, and inside a field argument:

```sushi
const Handle BAD = Handle(pick())                # CE0108: function calls forbidden
const Segment ALSO_BAD = Segment(Point(pick(), 2), 3)   # CE0108, one level down
```

A struct constant lives in read-only memory like every other constant. Writing a field
is **CE2096**, and calling a `poke self` method on one is **CE2400** -- that method takes
its receiver's address, and a constant has no frame slot to point at:

```sushi
OUT.fd := 7          # CE2096: cannot assign to a field of constant 'OUT'
OUT.release()        # CE2400: cannot borrow 'OUT': only a local variable can be borrowed
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
    return Result.Ok(0)
```

A variant the enum does not declare, a payload count that does not fit and a payload of
the wrong type read the codes a body gets -- **CE2045**, **CE2050** and **CE2049** -- and
a function call in a payload is **CE0108**, as it is in a struct field. A `Result@(T, E)`
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
**CE2012** and a negative index is **CE2056**, the codes an index in a body gets.

The other constant may belong to another unit. A flat `use "shapes"` brings its public
constants bare, and `use "shapes" as sh` puts them behind the dot, in the declared type
and in the initializer alike: `const sh.Shape SMALL = sh.UNIT`, `const i32 D = sh.SIZE * 2`,
`const sh.Point O = sh.Point(0, 0)`, `const sh.Shape T = sh.Shape.Circle(2)`. A private
constant is **CE3005** here as in a body. The other unit's initializer is read in ITS
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

```sushi
# ERROR: Not allowed in constants
const i32 X = get_value()     # CE0108: function calls forbidden
const i32 Y = some_local      # CE1001: the name is not a constant
const i32[] DYNAMIC = from([1, 2])  # CE2015: dynamic arrays forbidden
```

`+` on two strings is **CE2509** in a constant exactly as it is in a body: Sushi has no
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
    return Result.Ok(~)

fn main() i32:
    bump()
    bump()
    println("{counter}")            # 2
    return Result.Ok(0)
```

A unit variable is **private by default** and `public var` makes it visible to another
unit, exactly as for `fn`, `const`, `struct`, `enum` and `perk`. Reading, rebinding or
borrowing another unit's private variable is **CE3005**. A public variable may not hand
out a private type (**CE3009**). Behind an alias it is written
`t.count` like a constant, and `t.count := 3` and `poke t.count` reach the storage.

The console handles are the built-in example: `stdin`, `stdout` and `stderr` are
`public var File` declarations in `<io/fs>`, which is what lets `stdout.write(...)` call
a `poke self` contract method.

### The initializer

The initializer is a **constant expression**: a literal, another constant, operators,
`as`, an interpolation, or a struct built from constants -- everything a `const` accepts.
Nothing runs before `main`, so there is no initialization order to define, and a
variable cannot name another variable in its initializer (**CE0108**); a constant cannot
name a variable at all (**CE0108**).

One addition over a constant: an **empty container** is a legal initializer, because it
allocates nothing.

<!-- docs-sweep: skip (declarations only; the sweep compiles a block with a main) -->
```sushi
var i32[] table = from([])          # the descriptor {0, 0, null}
var u8[] bytes = new()
var List@(string) names = List.new()

fn remember(string s) ~:
    names.push(s)                   # a mutating method reaches the storage
    return Result.Ok(~)
```

`HashMap.new()` mallocs its buckets and is refused, and so is a `from([1, 2])` with
elements (**CE0108** either way).

### Borrowing, rebinding, and what is refused

A unit variable is borrowable like a local: `peek counter` and `poke counter` hand its
address to a function, one `poke` at a time (**CE2403**), and `foreach(poke r in
table.iter())` points into its elements. A `let` bound from a read out of it
(`let string first = names[0]`) borrows and freezes it, exactly as it would a local.

A unit variable is **never moved out of**. It owns its storage for the whole run, so a
`nom` argument, a `let` bound straight from it, a `return` of it and a `nom self` method
such as `close()` are all **CE2436** when the type owns a resource. A plain value copies
out freely, and a rebind is the one way to change what the variable holds: the old value
is dropped, the new one is stored.

<!-- docs-sweep: skip (declarations only; the sweep compiles a block with a main) -->
```sushi
use <io/fs>

fn redirect(nom File f) ~:
    stdout := f                     # legal: the old handle is dropped, `f` moves in
    return Result.Ok(~)

# ERROR CE2436: cannot move 'stdout': it is a unit variable
# let File mine = stdout
```

Nothing destroys a unit variable at exit. The process ends and the operating system
reclaims the pages; a variable that holds heap at that moment is not freed first.

A fixed array's size still wants an integer CONSTANT: a variable has a run-time value,
so `i32[N]` with `var i32 N = 3` is **CE2099**.

---

**See also:**
- [Standard Library](standard-library.md) - Built-in types and functions
- [Error Handling](error-handling.md) - Result@(T) and Maybe@(T)
- [Memory Management](memory-management.md) - RAII and ownership
- [Generics](generics.md) - Generic types and functions
