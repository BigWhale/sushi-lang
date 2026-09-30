# First-Class Functions

[← Back to Documentation](index.md)

This guide is about first-class functions in Sushi: **function types** (`fn(i32) -> i32`) and
**function values**. You can refer to a named function, store it, pass it, and call through it.
Sushi also has [closures](closures.md) (lambda literals that capture values). This guide is about
the function values that capture nothing. Closures use the same types and the same calls.

## Table of Contents

- [Overview](#overview)
- [Function types](#function-types)
- [Function values](#function-values)
- [Calling through a function value](#calling-through-a-function-value)
- [Functions in data structures](#functions-in-data-structures)
- [Custom error types](#custom-error-types)
- [How it compiles](#how-it-compiles)
- [Type compatibility](#type-compatibility)
- [Error codes](#error-codes)
- [Limitations and deferred features](#limitations-and-deferred-features)

## Overview

A **function value** lets you treat a function like any other value: bind it to a variable, put
it in a struct field or a `List`, pass it to another function, and call it indirectly. This
replaces hand-rolled `match`-based dispatch with ordinary data — the building block for callback
APIs, dispatch tables, and visitor-style code.

```sushi
fn add_one(i32 x) i32:
    return x + 1

fn apply(fn(i32) -> i32 f, i32 v) i32:
    return f(v)

fn main() i32:
    let fn(i32) -> i32 g = add_one     # reference a function by name
    let i32 out = apply(g, 41)   # pass it, call through it -> 42
    println(out)
    return 0
```

A plain function reference like `add_one` above captures nothing. It points to a function
that is already compiled. A [closure](closures.md) is a lambda literal (`|x| ...`) that can
capture values from its scope. The two have the same function type and the same call syntax.

## Function types

A function type names the shape of a callable. It mirrors the function-declaration syntax for
return and error types:

| Syntax | Meaning |
| --- | --- |
| `fn(i32) -> i32` | takes an `i32`, returns `i32`, error type implicitly `StdError` |
| `fn(i32, string) -> bool` | two parameters, returns `bool` |
| `fn() -> ~` | no parameters, blank (`~`) return |
| `fn(i32) -> i32 \| MathError` | explicit custom error type (the `\| E` mirrors `fn f() i32 \| MathError`) |

The arrow `->` is required, and the return type is mandatory. Function types nest and compose
like any other type — they work as parameter types, struct fields, and generic type arguments
(`List@(fn(i32) -> i32)`).

## Function values

You make a function value when you write the name of a **top-level function** in a value
position, with no call parentheses:

```sushi
fn double(i32 x) i32:
    return x * 2

fn main() i32:
    let fn(i32) -> i32 f = double    # `double` here is a value, not a call
    return 0
```

These functions can be values:

- A top-level function of the same unit.
- A function of another unit, as a bare name (`plain`) or behind an alias (`l.plain`). The unit
  can be a source unit, a `public use` re-export or a library. A private function of another unit
  is CE3005, and a bare name with two candidates is CE3012. A bare name finds the unit's own
  function before an imported function of the same name.
- A **generic** function, in every position where the type is solved: an annotated `let`, an
  argument, a rebind, a `return`, a field, a payload, a `.realise()` default, and a generic
  callee. A value that no position type solves is CE2093. `identity@(i32)` in a value position is
  a parse error (CE6001): let the position give the type.

Extension methods, perk methods and FFI externals have different calling conventions. They
cannot be values.

```sushi
fn identity@(T)(nom T x) T:
    return x

fn apply(fn(nom i32) -> i32 f, i32 v) i32:
    return f(nom v)

fn main() i32:
    let fn(nom i32) -> i32 g = identity    # T = i32, from the annotation
    println(g(nom 4))           # 4
    println(apply(identity, 2)) # 2, T = i32 from the parameter
    return 0
```

## Calling through a function value

Call a function value the same as a named function: `f(args)`. Every Sushi function returns
`Result@(T, E)`, so an indirect call gives the same `Result` as a direct call. Use `??`,
`.realise()`, `.is_ok()` or a `match` on it:

```sushi
fn run_twice(fn(i32) -> i32 f, i32 v) i32:
    let i32 once = f(v)
    let i32 twice = f(once)
    return twice
```

## Functions in data structures

### Struct fields

A function value can be a struct field. `h.op(v)` calls the function in the field `op`:

```sushi
struct Handler:
    fn(i32) -> i32 op

fn run(Handler h, i32 v) i32:
    return h.op(v)    # call through the field
```

### Lists (dispatch tables)

`List@(fn(...))` is the idiomatic way to hold a collection of functions — a dispatch table you can
iterate:

```sushi
fn dispatch(List@(fn(i32) -> i32) ops, i32 v) i32 | StdError:
    let i32 acc = v
    foreach(f in ops.iter()):
        acc := f(acc)??
    return Result.Ok(acc)
```

`.get(i)` returns `Maybe@(fn(...))`, the same as for every element type. Unwrap it and call it
in one expression: `ops.get(0)??(v)`. You cannot write an array of function values: the `[]` in
`fn() -> T[]` binds to the return type `T[]`. Use `List@(fn(...))` for a collection.

## Custom error types

The error type is part of the function type, so it threads through an indirect call correctly:

```sushi
enum DivError:
    DivByZero

fn safe_div(i32 a, i32 b) i32 | DivError:
    if (b == 0):
        return Result.Err(DivError.DivByZero)
    return Result.Ok(a / b)

fn run(fn(i32, i32) -> i32 | DivError op, i32 x, i32 y) i32 | DivError:
    return Result.Ok(op(x, y))    # propagates DivError out of the indirect call
```

A function whose type omits `| E` has the implicit `StdError` error type, exactly like an
ordinary `fn f() T` declaration.

## How it compiles

A function value is a **four-word fat pointer** `{fn_ptr, env_ptr, drop_ptr, clone_ptr}`. The
[closures guide](closures.md#how-it-compiles) gives the full description.

- A Sushi `fn add(i32) i32` becomes an LLVM function that returns a `Result@(i32, StdError)`. A
  reference to it as a value holds the address of a small adapter thunk, and `env_ptr`,
  `drop_ptr` and `clone_ptr` are null.
- A call through a function value is one indirect `call`. It passes `env_ptr` as a hidden first
  argument, and the thunk of a plain reference ignores it.
- A value that captures nothing (all the values on this page) allocates no environment and needs
  no cleanup. A **capturing** lambda allocates an environment on the heap (see
  [Closures](closures.md)).

## Type compatibility

Function types are **invariant**: two are compatible only when the arity, every parameter type,
the return type, and the error type match exactly. There is no implicit conversion between
function types (no variance, no coercion). A mismatch is a clean diagnostic — see below.

## Error codes

| Code | Meaning |
| --- | --- |
| **CE2092** | function value type mismatch at a call-through: wrong arity, parameter type, return type or error type |
| **CE2093** | a **generic** function value that no position type solves |
| **CE2002** | a function value assigned to a variable or parameter of an incompatible function type (the general assignment-mismatch error) |
| **CE3005** | a function value of a private function of another unit |
| **CE3012** | a bare function name with two candidates from two imports |

Extension methods, perk methods and FFI externals cannot be values. A bare name that is not a
constant, a variable or a top-level function is an undeclared identifier (**CE1001**). You reach
an external only through its namespace.

## Limitations and deferred features

- **Extension methods, perk methods and FFI externals** cannot be values (different ABIs).
- **Bound method values** (`obj.method` as a callable) and C callbacks are not available.
- **A generic function value needs a position type.** Write the `fn(...)` type on the `let`, or
  pass the value where a parameter type gives it. There is no `identity@(i32)` value spelling.

A call through any expression works: a struct field (`h.op(1)`), a container get-out
(`ops.get(0)??(2)`), a call result (`get_fn()??(3)`) and a parenthesized expression (`(f)(6)`).
The [Closures guide](closures.md) gives the capture rules and the `<collections/iter>`
combinators (`map`, `filter`, `fold`, `compose`).

The [Closures & First-Class Functions design note](design/closures.md) gives the design reasons
and the options that were considered.
