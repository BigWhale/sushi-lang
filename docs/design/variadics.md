# Design: Variadic Functions (P2-2)

Status: accepted. Implemented across two PRs — `variadic-extern` (P2-2c) and
`variadic-native` (P2-2b).

## Summary

Sushi gains two distinct, deliberately separated variadic mechanisms, mirroring how FFI already
separates `ptr` (unmanaged, foreign) from `Own<T>` (RAII, native):

- **Foreign / unsafe world — untyped C varargs.** A bare trailing `...` is allowed **only** inside
  an `unsafe external "C"` block. It maps directly to an LLVM `var_arg=True` declaration so that
  variadic libc functions such as `printf` can be bound. This is Rust's stance: `...` exists only at
  the `extern "C"` boundary, never in safe native code.
- **Native / safe world — homogeneous array sugar.** A typed `...T` trailing parameter on a plain
  native function collects the trailing call arguments into an owned dynamic array `T[]`. The callee
  iterates it with the existing `.iter()`/`foreach`/`.len()` API, and it is RAII-destroyed at scope
  exit. This is the Go/Java/Swift consensus model.

A `va_list`-style intrinsic for native code was rejected: it would import C's unsafety into safe
Sushi, contradicting the bounds-checked / RAII / no-null safety model.

## Syntax

```sushi
# Native: prefix marker on the last parameter, element type T
fn log_all(string prefix, ...i32 values) ~:
    foreach(v in values.iter()):
        println(v)

log_all("nums", 1, 2, 3)   # values = [1, 2, 3]
log_all("empty")           # values = []  (zero variadic args allowed)

# Extern: bare trailing ... after at least one fixed parameter
unsafe external "C" as libc because "formatted output via libc":
    fn printf(string fmt, ...) i32 = "printf"
```

## Semantics

- **Element type** is a single concrete `T` (homogeneous). Reference element types (`&peek`/`&poke`)
  are not allowed in v1.
- **Zero trailing arguments** is valid; the native callee receives an empty `T[]`.
- **Native ownership.** The call site synthesizes a `T[]`, which is moved into the callee; the
  callee owns and destroys it via the normal dynamic-array RAII path. The LLVM function itself stays
  non-variadic — the variadic parameter lowers to one extra `T[]` struct parameter.
- **Extern lowering.** The extern declaration lowers to an LLVM `var_arg=True` declaration. Trailing
  arguments undergo C default-argument promotion: `i8`/`i16`/`bool` → `i32`, `f32` → `f64`; `string`
  is marshalled to a `char*` and freed at scope exit on every path; `ptr` is passed as-is. Externs
  remain the single exception to implicit `Result` wrapping (they return raw C values).
- **Extern requires ≥1 fixed parameter** — the C ABI needs a named argument for `va_start`.

## Diagnostics

- `CE5004` — variadic external requires at least one fixed parameter.
- `CE5005` — non-C-ABI type passed as a variadic argument to an external call.
- `CE0114` — variadic parameter must be the last parameter; a function may declare at most one.
- `CE0115` — variadic parameter not allowed in a perk method or extension method.
- Native call arity/type errors reuse existing `CE2009` / `CE2006`.

## Deferred (additive, not in v1)

- **Spread / forwarding** an existing array into a variadic slot (Go's `f(arr...)`).
- **Generic variadics** (`...T` in a generic function).
- **Variadics in perk / extension methods** (rejected with `CE0115` for now).
- **Public `.slib` export** of a native variadic function — blocked for v1 (the `is_variadic`
  flag is not serialized into the library format yet), analogous to the CE5002 FFI boundary block.
