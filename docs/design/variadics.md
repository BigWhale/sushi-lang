# Design: Variadic Functions (P2-2)

Status: accepted and implemented: extern varargs (P2-2c) and native `...T` (P2-2b).

## Summary

Sushi has two distinct, deliberately separated variadic mechanisms, mirroring how FFI
separates `ptr` (unmanaged, foreign) from `Own@(T)` (RAII, native):

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
        println("{prefix}: {v}")

# Extern: bare trailing ... after at least one fixed parameter
unsafe external "C" as libc because "formatted output via libc":
    fn printf(string fmt, ...) i32 = "printf"

fn main() i32:
    log_all("nums", 1, 2, 3)     # values = [1, 2, 3]
    log_all("empty")             # values = []  (zero variadic args allowed)
    return 0
```

## Semantics

- **Element type** is a single concrete `T` (homogeneous). Reference element types (`peek`/`poke`)
  are rejected with [`CE0114`](../error-catalog.md#ce0114). A dynamic-array element `...T[]` is allowed: each trailing array
  argument is *moved* (not copied) into the synthesized array-of-arrays, so there is no double-free.
  The callee sees an `i32[][]` for `...i32[]`. Both call forms work: the individual arguments
  (`total(a, b)`) and a bloom of an `i32[][]` variable (`total(rows...)`) — see "Spread /
  forwarding (bloom)" below.
- **Zero trailing arguments** is valid; the native callee receives an empty `T[]`.
- **Native ownership.** The call site synthesizes a `T[]`, which is moved into the callee; the
  callee owns and destroys it via the normal dynamic-array RAII path. The LLVM function itself stays
  non-variadic — the variadic parameter lowers to one extra `T[]` struct parameter.
  This is the one place the collected array's owner still depends on the callee's implementation, so
  it is stated in exactly one place (`CalleeModes.variadic_callee_owns`): a Sushi `...T` body
  registers the array, and a STDLIB variadic (`run`) is generated IR that frees nothing, so there the
  CALLER keeps it. A
  consuming variadic spelling (`nom ...T`) is deferred — see `docs/design/borrow-model.md` S7.
- **Extern lowering.** The extern declaration lowers to an LLVM `var_arg=True` declaration. Trailing
  arguments undergo C default-argument promotion: `i8`/`i16`/`bool` → `i32`, `f32` → `f64`; `string`
  is marshalled to a `char*` and freed at scope exit on every path; `ptr` is passed as-is. An extern
  returns the raw C value and never a `Result` (an extern has no error channel).
- **Extern requires ≥1 fixed parameter** — the C ABI needs a named argument for `va_start`.

## Diagnostics

- [`CE5004`](../error-catalog.md#ce5004) — variadic external requires at least one fixed parameter.
- [`CE5005`](../error-catalog.md#ce5005) — non-C-ABI type passed as a variadic argument to an external call.
- [`CE0114`](../error-catalog.md#ce0114) — variadic parameter must be the last parameter; a function may declare at most one;
  its element type must not be a reference. A dynamic-array element (`...T[]`) is allowed. Also
  rejected in generic functions (use a type pack `...Ts` for a generic variadic, below).
- [`CE0115`](../error-catalog.md#ce0115) — variadic parameter not allowed in a perk method or extension method.
- [`CE0116`](../error-catalog.md#ce0116) — a public *native* variadic (`...T`) function cannot appear in a `.slib` public API. A
  native variadic collects its trailing args into a runtime `T[]` inside one concrete function, so
  there is no template to monomorphize at the consumer; analogous to the [CE5002](../error-catalog.md#ce5002) FFI boundary block.
  This blocks only native `...T` (`is_variadic`); type packs (`...Ts`) ship as templates and are
  exportable (see "Cross-library packs" under the type-pack section below).
- [`CE0120`](../error-catalog.md#ce0120) — a bloom argument `arr...` used somewhere illegal: into a non-variadic parameter, or
  not as the sole, last trailing argument at the call site.
- Type mismatch when blooming (a non-array source, or an array of the wrong element type) reuses
  [`CE2006`](../error-catalog.md#ce2006). Native call arity/type errors otherwise reuse existing [`CE2009`](../error-catalog.md#ce2009) / [`CE2006`](../error-catalog.md#ce2006).

## Spread / forwarding (bloom)

An existing array can be forwarded into a `...T` slot with the postfix `...` operator, written
directly after the array expression: `arr...`. The operation is called **bloom** — the array
"opens" and its elements fan out to fill the trailing call arguments — mirroring Go's `f(arr...)`.

```sushi
fn sum(...i32 nums) i32:
    let i32 total = 0
    foreach(n in nums.iter()):
        total := total + n
    return total

fn main() i32:
    let i32[] xs = from([1, 2, 3])
    let i32 s = sum(xs...)   # bloom: xs is MOVED into the variadic slot
    println("sum = {s}")                # sum = 6
    return 0
```

Semantics:

- **Move, not copy.** The bloomed array is moved into the callee's synthesized `T[]` — the same
  ownership transfer as a native variadic's own array construction, just skipping the copy. The
  caller must not use the source array after the call. At a STDLIB variadic the array stays the
  caller's instead (see "Native ownership" above), so `run("cmd", argv...)` leaves `argv` readable.
- **Source must be a bare variable.** `arr...` requires `arr` to be a `Name` referring to an
  array-typed local/parameter; blooming an arbitrary expression (a call result, a field access, a
  literal array) is not supported.
- **Sole, last trailing argument.** A bloom must be the only trailing argument — it cannot be
  mixed with individual trailing arguments (`sum(1, xs...)` is not a bloom call shape), and it
  cannot appear anywhere but the variadic slot. Any other placement is [`CE0120`](../error-catalog.md#ce0120).
- **Type-checked like any other argument.** The element type of the bloomed array must match the
  variadic's declared element type; a mismatch (or blooming a non-array value) is [`CE2006`](../error-catalog.md#ce2006).
- **Only into `...T`.** Blooming into a non-variadic parameter is [`CE0120`](../error-catalog.md#ce0120) — there is no fixed-arity
  spread.

`...T[]` combined with bloom is an ordinary case: an `i32[][]` variable `rows` blooms into a
`...i32[]` parameter (`total(rows...)`), and the outer array moves in whole, its inner arrays
with it. The individual-argument form (`total(a, b)`) moves each array in per element (see
Semantics above).

## Deferred (additive)

- **Generic variadics** (`...T` in a generic function).
- **Variadics in perk / extension methods** (rejected with [`CE0115`](../error-catalog.md#ce0115)).
- **Public `.slib` export** of a native variadic function — blocked with [`CE0116`](../error-catalog.md#ce0116) (the
  `is_variadic` flag is not serialized into the library format yet), analogous to the [CE5002](../error-catalog.md#ce5002) FFI
  boundary block.
- **Pack forwarding** (`f(pack...)`, forwarding a parameter pack into another variadic) and **pack
  indexing** — bloom covers only a single `...T` array source, not `...Ts` packs.

## Variadic generics / parameter packs

Status: implemented. Distinct from and coexisting with the native `...T` (homogeneous array sugar) and
extern `...` (libc varargs) mechanisms.

### Syntax

```sushi
perk Render:
    fn display() string

extend i32 with Render:
    fn display() string:
        return "int:42"

extend string with Render:
    fn display() string:
        return self.clone()

extend bool with Render:
    fn display() string:
        return "yes"

fn print_all@(...Ts: Render)(...Ts args) ~:
    expand(a in args):
        println(a.display())

fn main() i32:
    print_all(42, "hi", true)   # monomorphizes print_all__i32_string_bool.pack3
    print_all()                 # arity-0 allowed; expand body runs 0 times
    return 0
```

### Semantics

- **`...Ts`** in the type-parameter list declares a **type pack**. An optional perk constraint
  (`...Ts: Render`) requires every bound element type to implement the named perk.
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
  regular-generic symbols and to remain collision-free across arities. Like every generic
  instance, a specialization carries the prefix of its declaring unit
  (`<unit>$print_all__i32_string_bool.pack3`) and takes the linkage of the function it comes
  from: `internal` for a private function, `external` for a `public` one.
- **Pack elements are passed as separate positional arguments** — they are not boxed or collected
  into an array.
- **Arity zero** is valid: `print_all()` monomorphizes an arity-0 specialization; the `expand` body
  executes zero times.

### Diagnostics

- **[CE0117](../error-catalog.md#ce0117)** — type-pack `...Ts` must be the last type parameter; at most one pack per function.
- **[CE0118](../error-catalog.md#ce0118)** — cannot mix a type-pack `...Ts` with a native homogeneous `...T` in the same function.
- **[CE0119](../error-catalog.md#ce0119)** — malformed `expand` statement (wrong syntax, iterator variable, or target).
- **[CE2090](../error-catalog.md#ce2090)** — a pack element type at the call site does not satisfy the pack's perk constraint.
- **[CE0147](../error-catalog.md#ce0147)** — the pack name is a type only in its own `...Ts args` parameter. A parameter
  `Ts x`, a return type, a `let` type, `Ts[]` and `List@(Ts)` are refused where the
  template is written, and the analysis stops before the generic passes.

### Type-pack limitations

- **Perk-constrained packs only**: an unconstrained `...Ts` (no `: PerkName`) can be declared and
  called, but the `expand` body cannot usefully operate on the elements without a perk (no
  methods are available). Unconstrained forwarding and pack indexing are deferred.
- **Cross-library packs**: a public `...Ts` pack ships in a `.slib` as an
  instantiable template (`templates.generic_functions`) and is monomorphized at the consumer's call
  sites, exactly like a regular cross-library generic. [CE0116](../error-catalog.md#ce0116) still blocks native `...T` export
  (a runtime array, not a template).
- **Plain function definitions only**: perk methods and extension methods may not declare a value
  pack ([CE0115](../error-catalog.md#ce0115) applies to both native `...T` and type packs `...Ts`).
- **No pack forwarding**: a value pack cannot be forwarded into another variadic (`g(pack...)`) —
  bloom (see "Spread / forwarding (bloom)" above) only spreads a single `...T` array, not a
  `...Ts` pack.
- **No pack indexing**: individual pack elements cannot be addressed by index.

Pack elements that all resolve to one enum type work like any other pack
(`tests/variadic/type_packs/test_variadic_pack_enum_repeated_runtime.sushi`).

### Internal representation

- `TypePack` in `semantics/generics/types.py` represents the variable-length type sequence.
- `monomorphize_function` builds a pack-aware substitution map; `expand_pack_param` fans a
  pack-typed value parameter into one concrete parameter per element.
- Name mangling: `.pack{N}` marker (e.g. `.pack3` for a three-element pack) encodes arity in a
  collision-free way distinct from regular-generic symbols.

The fixtures under `tests/variadic/type_packs/` (`test_variadic_pack_*.sushi`) exercise the
full compiler pipeline.
