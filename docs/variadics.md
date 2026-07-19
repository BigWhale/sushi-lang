# Variadics

[← Back to Documentation](index.md)

Complete guide to variadic functions in Sushi: the two safe native mechanisms (homogeneous
`...T` arrays and heterogeneous `...Ts` parameter packs), the unsafe extern `...` form for C
varargs, and how packs cross `.slib` library boundaries.

## Table of Contents

- [Overview](#overview)
- [Native homogeneous variadics (`...T`)](#native-homogeneous-variadics-t)
- [Spread / forwarding (bloom)](#spread-forwarding-bloom)
- [Parameter packs (`...Ts` + `expand`)](#parameter-packs-ts-expand)
- [Perk constraints on packs](#perk-constraints-on-packs)
- [`expand` semantics](#expand-semantics)
- [How packs compile (monomorphization)](#how-packs-compile-monomorphization)
- [Packs across `.slib` libraries](#packs-across-slib-libraries)
- [Extern variadics (`...`)](#extern-variadics)
- [Choosing a form](#choosing-a-form)
- [Error codes](#error-codes)
- [Limitations and deferred features](#limitations-and-deferred-features)

## Overview

Sushi has **three deliberately separate** variadic forms. They look similar at the call site but
serve different purposes:

| Form | World | Element types | Lowering |
| --- | --- | --- | --- |
| `...T` | safe, native | one homogeneous `T` | collected into an owned runtime `T[]` |
| `...Ts` + `expand` | safe, native | heterogeneous, generic | monomorphized + compile-time-unrolled |
| `...` | unsafe, FFI | untyped C varargs | LLVM `var_arg` (libc `printf`-family) |

The first two are memory-safe, bounds-checked, and null-free like the rest of the language. The
third exists only inside `unsafe external "C"` blocks for calling libc — see the
[FFI guide](ffi.md).

## Native homogeneous variadics (`...T`)

A trailing parameter written `...T name` collects every trailing call argument into an **owned
dynamic array** `T[]`. The marker `...` is a **prefix on the element type**, and the variadic
parameter must come last.

```sushi
fn sum(...i32 nums) i32:
    let i32 total = 0
    foreach(n in nums.iter()):
        total := total + n
    return Result.Ok(total)

fn main() i32:
    let i32 a = sum(1, 2, 3, 4).realise(0)   # 10
    let i32 b = sum().realise(0)             # 0 — zero args is valid
    println("{a} {b}")
    return Result.Ok(0)
```

Key properties:

- The element type `T` is **one concrete type** for every trailing argument (homogeneous).
- **Zero trailing arguments is valid** — the callee receives an empty array.
- A variadic may follow fixed parameters: `fn log(string prefix, ...i32 values) ~:`.
- The synthesized `T[]` is **moved into the callee**, which owns it and RAII-destroys it at scope
  exit. The lowered LLVM function stays non-variadic.
- Reference element types (`...&peek T`) are rejected (**CE0114**). A dynamic-array element
  (`...T[]`) is allowed — each trailing array argument is moved into the callee's array-of-arrays,
  not copied.

Native variadics are allowed only in **plain function definitions** — not perk or extension
methods (**CE0115**) — and are **not** exportable through a `.slib` public API (**CE0116**), because
a single concrete runtime function carrying an array ABI has no template to monomorphize at a
consumer.

## Spread / forwarding (bloom)

An existing array can be forwarded into a `...T` slot with postfix `...`, written directly after
the array expression: `arr...`. This is called a **bloom** — the array "opens" and its elements
fan out to fill the trailing arguments — mirroring Go's `f(arr...)`.

```sushi
fn sum(...i32 nums) i32:
    let i32 total = 0
    foreach(n in nums.iter()):
        total := total + n
    return Result.Ok(total)

fn main() i32:
    let i32[] xs = from([1, 2, 3])
    let i32 s = sum(xs...).realise(0)   # bloom: xs is MOVED into the variadic slot
    println("sum = {s}")                # sum = 6
    return Result.Ok(0)
```

Key properties:

- **Move, not copy.** The source array is consumed — moved into the callee's synthesized `T[]`.
  Do not use the source array after a bloom call.
- **Source must be a bare variable** (a `Name`) of array type. Blooming an arbitrary expression
  (a call result, a field access, an inline array literal) is not supported in v1.
- **Sole, last trailing argument.** A bloom must be the only trailing argument at the call site —
  it cannot be mixed with individual trailing arguments, and blooming into a non-variadic
  parameter or anywhere but the variadic slot is **CE0120**.
- **Type-checked.** The bloomed array's element type must match the variadic's declared element
  type; a mismatch, or blooming a non-array value, is **CE2006**.

`sys/process`'s `run()` uses this to forward a computed argument list:

```sushi
use <sys/process>

let string[] argv = from(["-c", "seq 1 100000"])
let ProcessOutput out = run("sh", argv...).realise(ProcessOutput(1, "", ""))
```

## Parameter packs (`...Ts` + `expand`)

A **parameter pack** is the heterogeneous, generic cousin of `...T`. Instead of collecting one type
into a runtime array, a pack binds a **variable-length tuple of concrete types** per call site and
is expanded at compile time — so the arguments may each have a *different* type.

A pack has two halves:

- a **type pack** `...Ts` in the type-parameter list, and
- a **value pack** `...Ts name` in the parameter list, whose element type is that type pack.

```sushi
perk Describe:
    fn describe() string

extend i32 with Describe:
    fn describe() string:
        return "int {self}"

extend string with Describe:
    fn describe() string:
        return "text '{self}'"

fn show_all@(...Ts: Describe)(...Ts args) ~:
    expand(a in args):
        println(a.describe())
    return Result.Ok(~)

fn main() i32:
    show_all(42, "Mostly Harmless")   # i32 then string, in one call
    show_all()                        # arity 0 is valid
    return Result.Ok(0)
```

`show_all(42, "Mostly Harmless")` monomorphizes a concrete `show_all__i32_string.pack2`; a different
mix of types produces a different instance. There is no runtime type tag, no boxing, and no
heterogeneous container — the same zero-cost static-dispatch story as ordinary generics.

A pack can follow fixed parameters, just like `...T`:

```sushi
fn log@(...Ts: Render)(string label, ...Ts items) ~:
    println("{label}:")
    expand(it in items):
        println("  - {it.render()}")
    return Result.Ok(~)
```

## Perk constraints on packs

A pack is normally written **perk-constrained** (`...Ts: Describe`). The constraint is what makes the
body usable: every element has a *different* concrete type, so for `expand(a in args): a.describe()`
to type-check, the compiler needs a guarantee that `.describe()` is valid on *every* element type,
whatever they turn out to be. The perk bound *is* that guarantee, checked once at the definition —
the same upfront-checking model Sushi already uses for ordinary generics.

If a call supplies an element type that does not implement the perk, the call fails with **CE2090**
naming the offending element and constraint.

An **unconstrained** pack (`...Ts` with no bound) may be declared, but its `expand` body cannot call
anything on the elements — there is no statically-known API common to all possible types. Useful
work with unconstrained packs needs forwarding or indexing, which are
[deferred](#limitations-and-deferred-features).

## `expand` semantics

`expand(x in pack):` is a **compile-time-unrolled** construct, distinct from the runtime `foreach`.
Conceptually each element produces one copy of the body with `x` bound to that element's concrete
value:

- The body runs **once per element, in declaration order**; arity 0 means the construct vanishes.
- `x` is the concrete per-element value — `x.method()` dispatches statically to that type's impl.
- The body is ordinary straight-line code: it can read and update surrounding locals, so `expand`
  can **accumulate** a result, not just produce output:

  ```sushi
  fn print_row@(...Ts: Show)(...Ts cells) ~:
      let string line = ""
      expand(c in cells):
          line := "{line}[{c.show()}]"
      println(line)
      return Result.Ok(~)
  ```

- Early `return` and the `??` propagation operator work inside `expand`; any owned per-element
  temporaries are RAII-dropped exactly once, including on the early-exit paths.

## How packs compile (monomorphization)

Packs reuse Sushi's generics pipeline — **compile-time monomorphization with static dispatch**:

1. **Pass 1.5** collects each call site's ordered tuple of concrete trailing-argument types plus the
   arity.
2. **Pass 1.6** monomorphizes one concrete function per `(arity, type-tuple)`, expanding the value
   pack into N ordinary parameters and **unrolling** the `expand` body — each expansion typed to its
   concrete element. After this step there is no pack node left for later passes to see.
3. The mangled name encodes the arity and ordered types (e.g. `show_all__i32_string.pack2`), so
   distinct instances never collide and identical ones dedupe.

The result is straight-line, per-element-typed code with **zero loops, type tags, or boxing** for
the expansion.

## Packs across `.slib` libraries

A pack function is generic, so it ships across a library boundary exactly like a regular generic.
The producer serializes the function as a re-parsable source template in the `.slib` `templates`
section; the consumer re-parses it, registers it alongside its own definitions, and monomorphizes it
at **its own** call sites:

```sushi
# in the library (built with `sushic --lib`)
perk Display:
    fn display() string

public fn show_all@(...Ts: Display)(...Ts args) ~:
    expand(a in args):
        println(a.display())
    return Result.Ok(~)
```

```sushi
# in a consumer program
use <lib/format_lib>

extend i32 with Display:
    fn display() string:
        return "i:{self}"

extend string with Display:
    fn display() string:
        return "s:{self}"

fn main() i32:
    show_all(42, "hi")    # monomorphized in the consumer
    return Result.Ok(0)
```

The library ships the `perk Display` **definition** so the consumer need not redeclare it; the
consumer still supplies its own `extend <type> with Display` implementation for each type it
instantiates the pack with. See the [Libraries guide](libraries.md) for the full template mechanism.

> Native `...T` cannot cross a library boundary (it is a single concrete runtime function with an
> array ABI, not a template) — that is the `...T` vs `...Ts` distinction, enforced by **CE0116**.

## Extern variadics (`...`)

Inside an `unsafe external "C"` block, a bare trailing `...` after at least one fixed parameter binds
the `printf` family. This is the *only* place untyped C varargs exist; it lowers to a true LLVM
`var_arg` call and follows C promotion rules. It is unrelated to the native forms above — see the
[FFI guide](ffi.md) for details and the four-guarantee story.

```sushi
unsafe external "C" as libc because "formatted output":
    fn printf(string fmt, ...) i32 = "printf"
```

## Choosing a form

- Need an **owned collection** you can store, push to, iterate repeatedly, or pass on, and every
  argument is the **same type**? Use **`...T`**.
- Need **mixed argument types** behind a common perk (a `printf`/logging/formatting style API), or a
  generic function that works over a variable number of typed arguments? Use **`...Ts` + `expand`**.
- Calling **libc**? Use extern **`...`** inside an `unsafe external "C"` block.

## Error codes

| Code | Meaning |
| --- | --- |
| **CE0114** | `...T` must be last, at most one per function; element type must not be a reference (a dynamic-array element `...T[]` is allowed); also rejected in generic functions |
| **CE0115** | a variadic parameter (`...T` or `...Ts`) is not allowed in a perk or extension method |
| **CE0116** | a public *native* `...T` function cannot be exported through a `.slib` public API (does not apply to `...Ts` packs) |
| **CE0117** | a type-pack `...Ts` must be the last type parameter; at most one pack per function |
| **CE0118** | cannot mix a type-pack `...Ts` with a native `...T` in the same function |
| **CE0119** | malformed `expand` statement |
| **CE0120** | a bloom argument `arr...` used somewhere illegal (into a non-variadic parameter, or not the sole, last trailing argument) |
| **CE2090** | a pack element type does not satisfy the pack's perk constraint |
| **CE2006** | (reused) blooming a non-array value, or an array of the wrong element type, into a `...T` slot |

## Limitations and deferred features

- **Perk-constrained packs only** — an unconstrained `...Ts` can be declared but its `expand` body
  cannot operate on elements yet.
- **Plain functions only** — packs (and `...T`) are not allowed in perk or extension methods
  (CE0115).
- **Bloom source must be a bare variable** — `arr...` requires `arr` to be a `Name`; blooming an
  arbitrary expression (a call result, a field access, an inline literal) is not supported in v1.
- **No pack forwarding** (`g(pack...)`) and **no pack indexing** (`args.0`) — bloom spreads a single
  `...T` array, not a `...Ts` pack — deferred.
- **Native `...T` is not exportable** via `.slib` (CE0116); `...Ts` packs are.

The deeper design rationale lives in the [Variadics design note](design/variadics.md).
