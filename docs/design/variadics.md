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
  are not allowed in v1, and neither is a dynamic-array element (`...T[]`): the call site copies
  each trailing argument into the synthesized array without moving the source, so a move-only
  element type would be freed twice (Sushi has move semantics for dynamic arrays only). Both are
  `CE0114`; a moved-element form is deferred to the spread/forwarding design.
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
- `CE0114` — variadic parameter must be the last parameter; a function may declare at most one;
  its element type must not be a reference or a dynamic array (`...T[]`). Also rejected in generic
  functions (generic variadics are out of scope for v1).
- `CE0115` — variadic parameter not allowed in a perk method or extension method.
- `CE0116` — a public *native* variadic (`...T`) function cannot appear in a `.slib` public API. A
  native variadic collects its trailing args into a runtime `T[]` inside one concrete function, so
  there is no template to monomorphize at the consumer; analogous to the CE5002 FFI boundary block.
  This blocks only v1 `...T` (`is_variadic`); v2 type packs (`...Ts`) ship as templates and are
  exportable (see "Cross-library packs" under the Phase-1 section below).
- Native call arity/type errors reuse existing `CE2009` / `CE2006`.

## Deferred (additive, not in v1)

- **Spread / forwarding** an existing array into a variadic slot (Go's `f(arr...)`).
- **Generic variadics** (`...T` in a generic function).
- **Variadics in perk / extension methods** (rejected with `CE0115` for now).
- **Public `.slib` export** of a native variadic function — blocked for v1 with `CE0116` (the
  `is_variadic` flag is not serialized into the library format yet), analogous to the CE5002 FFI
  boundary block.

## Variadic generics / parameter packs (Phase 1, landed)

Status: implemented. Distinct from and coexisting with the v1 `...T` (homogeneous array sugar) and
extern `...` (libc varargs) mechanisms.

### Syntax

```sushi
perk Display:
    fn display() string

extend i32 with Display:
    fn display() string:
        return "int:42"

extend string with Display:
    fn display() string:
        return self

extend bool with Display:
    fn display() string:
        return "yes"

fn print_all<...Ts: Display>(...Ts args) ~:
    expand(a in args):
        println(a.display())
    return Result.Ok(~)

fn main() i32:
    print_all(42, "hi", true)   # monomorphizes print_all__i32_string_bool.pack3
    print_all()                 # arity-0 allowed; expand body runs 0 times
    return Result.Ok(0)
```

### Semantics

- **`...Ts`** in the type-parameter list declares a **type pack**. An optional perk constraint
  (`...Ts: Display`) requires every bound element type to implement the named perk.
- **`...Ts args`** in the parameter list declares the corresponding **value pack**. The type-pack
  must appear last in the type-parameter list and the value pack must appear last in the parameter
  list.
- **`expand(x in pack): BODY`** is a compile-time-unrolled construct (the static analog of
  `foreach`): one copy of BODY is emitted per pack element, each `x` bound to that element's
  concrete type. It is not a runtime loop — no iterator or array is created.
  - `??` and early `return` are allowed inside `expand` bodies with correct RAII.
  - Local variables declared inside `expand` are scoped to each unrolled copy (straight-line +
    nested-block-scope model).
- **Monomorphization**: each distinct (arity, type-tuple) call site produces a separate specialized
  function. The mangled symbol uses a `.pack{N}` suffix to distinguish pack specializations from
  regular-generic symbols and to remain collision-free across arities. All specializations use
  `linkonce_odr` linkage for linker deduplication in multi-unit builds.
- **Pack elements are passed as separate positional arguments** — they are not boxed or collected
  into an array.
- **Arity zero** is valid: `print_all()` monomorphizes an arity-0 specialization; the `expand` body
  executes zero times.

### Diagnostics

- **CE0117** — type-pack `...Ts` must be the last type parameter; at most one pack per function.
- **CE0118** — cannot mix a type-pack `...Ts` with a v1 homogeneous `...T` in the same function.
- **CE0119** — malformed `expand` statement (wrong syntax, iterator variable, or target).
- **CE2090** — a pack element type at the call site does not satisfy the pack's perk constraint.

### Phase-1 limitations

- **Perk-constrained packs only**: an unconstrained `...Ts` (no `: PerkName`) can be declared and
  called, but the `expand` body cannot usefully operate on the elements without a perk (no
  methods are available). Unconstrained forwarding and pack indexing are deferred.
- **Cross-library packs (Phase 3, landed)**: a public `...Ts` pack ships in a `.slib` as an
  instantiable template (`templates.generic_functions`) and is monomorphized at the consumer's call
  sites, exactly like a regular cross-library generic. CE0116 still blocks v1 native `...T` export
  (a runtime array, not a template).
- **Plain function definitions only**: perk methods and extension methods may not declare a value
  pack (CE0115 applies to both v1 `...T` and Phase-1 `...Ts`).
- **No spread / forwarding**: `f(arr...)` and pack forwarding to another variadic are deferred.
- **No pack indexing**: individual pack elements cannot be addressed by index.
- **Same-enum-type element gap**: if all pack elements resolve to the same enum type, the
  instantiation-collection pass may raise CE2061 (a narrow limitation, separate from the
  perk-constraint mechanism).

### Internal representation

- `TypePack` in `semantics/generics/types.py` represents the variable-length type sequence.
- `monomorphize_function` builds a pack-aware substitution map; `expand_pack_param` fans a
  pack-typed value parameter into one concrete parameter per element.
- Name mangling: `.pack{N}` marker (e.g. `.pack3` for a three-element pack) encodes arity in a
  collision-free way distinct from regular-generic symbols.

Phase-0 unit tests (`test_p0t*`) cover the monomorphizer infrastructure; Phase-1 integration tests
(`tests/variadic/test_variadic_pack_*.sushi`) exercise the full compiler pipeline.
