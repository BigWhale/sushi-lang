# Foreign Function Interface (FFI)

Sushi's Foreign Function Interface lets you call external C functions directly,
with no runtime boundary. It is the escape hatch that makes bootstrapping toward
self-hosting possible (calling libc and, eventually, LLVM) and the only way to
bind a C symbol for which no Sushi equivalent exists.

FFI is deliberately an **anti-pattern**. The guiding rule is **"FFI is not
Sushi"**: prefer writing it in Sushi. Where you cannot, wall the foreign call off
behind a hand-audited safe wrapper. This guide explains the syntax, the type
rules at the boundary, the safety contract, and the diagnostics that enforce it.

> See also: [Error Handling](error-handling.md) (the Result-exemption) and
> [Memory Management](memory-management.md) (the unmanaged `ptr` and the no-leak
> string marshalling).

## The `unsafe external` block

Foreign functions are declared in a single walled-off block, the "danger zone":

```sushi
unsafe external "C" as libc because "bootstrap: call libc for the backend":
    fn strlen(string s) i64               = "strlen"
    fn malloc(i64 n) ptr                  = "malloc"
    fn free(ptr p) ~                      = "free"
```

| Element | Meaning |
|---|---|
| `unsafe external` | A contextual keyword pair, valid only as a top-level declaration. |
| `"C"` | The ABI. Only `"C"` is accepted in v1; the slot reserves room for others. |
| `as libc` | The **namespace binding**, chosen by you. Foreign names never enter Sushi's global scope. |
| `because "<reason>"` | **Optional.** The acknowledgment that silences the `CW5001` warning (see below). |
| `fn name(params) ret = "symbol"` | One foreign declaration. No body. The Sushi-visible `name` and the C link `symbol` are separated. |

### Call sites are always namespaced

```sushi
let i64 n = libc.strlen(s)      # visibly foreign; cannot clash with any Sushi name
let ptr h = libc.malloc(16 as i64)
```

`libc.foo(args)` is member access on the namespace introduced by `as libc` - the
direct analog of Go's `C.foo()`. There is no `unsafe { }` block at the call site;
in Sushi the namespace prefix *is* the call-site marker. A local variable shadows
the namespace: if a local named `libc` is in scope, `libc.foo()` resolves against
that local instead.

### Link-name separation

The Sushi-visible name and the C symbol are decoupled
(`fn c_printf(string fmt, ...) i32 = "printf"`). You may name the Sushi side
anything; the linker resolves the symbol after `=`. This is what lets you bind
`printf` without shadowing any Sushi name.

## Types at the boundary

External signatures are limited to the **C-representable subset**:

- Primitives: `i8`..`i64`, `u8`..`u64`, `f32`, `f64`, `bool`
- `ptr` - the opaque foreign pointer type (below)
- `~` - genuine C `void`
- `string` - auto-marshalled to/from C `char*` (below)

Anything else (`Result<T,E>`, `Maybe<T>`, structs, arrays `T[]`, references,
named user types) is a hard error: **`CE5003`**. The check is a strict allowlist,
so an unknown user type cannot slip through.

### The `ptr` type

C traffics in raw pointers (`char*`, `FILE*`). FFI introduces an opaque,
**unmanaged** handle type `ptr` (LLVM `i8*`):

- The **borrow checker ignores it** - aliasing through a `ptr` is not tracked.
- **RAII never frees it** - a `ptr` has no destructor; you call the matching C
  free yourself.
- **No null/bounds guarantees** - a returned `ptr` may be null; dereferencing is
  your responsibility.

Sushi is and stays a **null-free** language. There is no `null` literal. In v1 a
returned `ptr` is callable and returnable but not null-checkable; if a real need
arises it will become an `is_null(ptr) -> bool` intrinsic, never a `null` literal.

### Return types and the Result-exemption

Sushi's universal rule is that **every `fn` implicitly returns `Result<T, E>`**.
External functions are the **single exception**: a C function returns a raw value
with no error channel and cannot construct a Sushi `Result` across the ABI.

```sushi
fn strlen(string s) i64 = "strlen"   # returns raw i64, NOT Result<i64, StdError>
fn malloc(i64 n) ptr    = "malloc"   # returns raw ptr (may be null)
fn free(ptr p) ~        = "free"     # ~ here is genuine C void, NOT Result<~>
```

Because `libc.strlen(s)` yields a plain `i64`, you **cannot** apply `??` or
`.realise()` to it - it is not a `Result`/`Maybe`. Attempting `libc.strlen(s)??`
is a clean type error (**`CE2507`**). This is not an ad-hoc carve-out: externals
live in a separate list and never reach the implicit-Result wrapping at all.

### String auto-marshalling (and the no-leak contract)

A Sushi `string` is a `{ptr, len}` UTF-8 struct; C expects a null-terminated
`char*`. At the boundary the compiler marshals automatically:

- A `string` **argument** is copied into a fresh null-terminated `char*` for the
  call. That temporary is registered in a per-scope cleanup list and freed via
  libc `free` at scope exit - on **every** path (normal block end, early
  `return`, and `??` propagation). It is freed exactly once. **No leak.**
- A `string` **return** is converted from the C `char*` back into a Sushi fat
  pointer.

The copy is per-call. The marshalling is invisible in your source, but the
freeing is real: inspect the IR with `./sushic --dump-ll` and you will see a
`free` of the marshalled `char*` in the function's cleanup path.

## Variadic externs

C variadic functions (`printf`-family) are bound with a bare trailing `...` after
at least one fixed parameter:

```sushi
unsafe external "C" as libc because "formatted output via libc":
    fn printf(string fmt, ...) i32 = "printf"
```

The `...` lowers to an LLVM `var_arg` declaration. Untyped C varargs are confined
to `unsafe external` blocks on purpose - they carry no type or count information,
so they are exactly as unsafe as in C, and that danger belongs only at the foreign
boundary. Native Sushi variadics use the safe, typed `...T` array form instead (see
the variadics guide); they are a different mechanism and never produce a C
`var_arg` call.

Two boundary rules apply to the trailing arguments at each call:

- **Default-argument promotion**, exactly as C performs it: `i8`/`i16`/`bool`
  widen to `i32`, and `f32` widens to `f64`. A format string must match the
  *promoted* type - `%d` for any narrow integer, `%f` for an `f32` (it arrives as a
  `double`). This is the classic C varargs footgun; the compiler performs the
  promotion but cannot check it against your format string.
- **C-ABI-only**: each trailing argument must be a C-representable value
  (a primitive, `ptr`, or `string`). A `string` trailing argument is marshalled to
  `char*` and freed at scope exit on every path, identical to a fixed `string`
  argument. Passing a non-C-ABI value (a struct, `Maybe`, array, ...) is `CE5005`.

A variadic extern must declare at least one fixed parameter (`CE5004`): the C ABI's
`va_start` needs a named argument to anchor on.

## The safety contract: four suspended guarantees

`unsafe` suspends exactly **four** Sushi guarantees:

| # | Guarantee | What FFI suspends |
|---|---|---|
| 1 | Borrow checking (`&peek`/`&poke`) | aliasing is not tracked through foreign pointers |
| 2 | RAII / move semantics | a foreign-returned `ptr` is unmanaged; you free it yourself |
| 3 | `Result` / `Maybe` | no auto-wrapping; check C sentinels (errno/-1/NULL) by hand |
| 4 | Bounds / null safety | a returned `ptr` may be null and is not bounds-checked |

A block **without** `because "<reason>"` compiles (exit 1) but emits one
non-fatal warning, **`CW5001`**, stating the contract plus signature-driven
notes (a `ptr` return is unmanaged and may be null; a primitive return is raw,
not `Result`; a `string` param/return needs marshalling; a `ptr` param is not
aliasing-tracked). It is one speed bump per danger zone, never per call.

Adding `because "<reason>"` **silences the warning** and records the rationale.
Writing the reason means you have read the contract; afterward the build is
clean (important: the self-hosting compiler uses FFI heavily and must build
clean).

`unsafe` does **not** propagate (the Go model): a normal Sushi function calling
`libc.foo()` is still a normal Sushi function. There is no viral `unsafe fn`
coloring.

## The safe-wrapper pattern

The four guarantees are restored in a hand-written wrapper. The wrapper is
ordinary Sushi (so it *does* follow the implicit-`Result` rule), and it marshals
data, folds C sentinels into `Result`, and manages pointer lifetimes:

```sushi
unsafe external "C" as libc because "string length via libc strlen":
    fn strlen(string s) i64 = "strlen"

# Safe wrapper - normal Sushi, upholds all four guarantees again.
fn length(string s) i64:
    return Result.Ok(libc.strlen(s))

fn main() i32:
    let i64 n = length("Mostly Harmless").realise(0 as i64)
    println("len = {n}")
    return Result.Ok(0)
```

The boundary is sharp: **raw, exempt, namespaced foreign calls inside
`unsafe external`; Result-clean, guarantee-upholding Sushi everywhere else.**
That convention is load-bearing, not cosmetic.

A wrapper that restores RAII for a foreign handle looks like:

```sushi
fn close_handle(ptr h) ~:
    libc.free(h)            # guarantee 2 (RAII) restored by hand
    return Result.Ok(~)
```

A wrapper may also *return* the handle it acquired - `ptr` flows through the
implicit `Result` wrapping (and through `Maybe<ptr>`) like any other value:

```sushi
fn grab() ptr:
    let ptr p = libc.malloc(8 as i64)
    return Result.Ok(p)

fn main() i32:
    match grab():
        Result.Ok(p) -> libc.free(p)
        Result.Err(_) -> println("alloc failed")
    return Result.Ok(0)
```

Holding a `ptr` is the safe half of the FFI contract (it cannot be dereferenced
in Sushi); wrapping one in `Result`/`Maybe` adds the error channel, **not** RAII
or null-checking - freeing the handle is still your job.

## `ptr` is unit-confined

A **`public fn` may not expose `ptr` anywhere in its signature** - not as a
parameter, not as a return type, not inside `Result<ptr, E>` or `Maybe<ptr>`.
Violations are rejected at compile time with **`CE5008`**.

FFI is a private implementation detail of the unit that declares the
`unsafe external` block. What a unit exports must be Sushi-shaped: either
fully digested values (`string`, `i64`, ...) or a **wrapper struct**:

```sushi
struct Handle:
    ptr raw                 # struct fields MAY carry ptr across units
    i64 size

public fn open_buffer(i64 n) Handle:
    return Result.Ok(Handle(libc.malloc(n), n))

public fn close_buffer(Handle h) ~:
    libc.free(h.raw)
    return Result.Ok(~)
```

The wrapper-struct escape hatch is deliberate (the Rust newtype idiom): a `ptr`
riding inside a named struct is self-documenting, gives extension methods a
receiver to attach to (`h.close()`), and is inert in other units anyway - the
foreign namespace it came from is not visible there. Private functions are
unrestricted: inside the FFI unit, `ptr` parameters and returns flow freely.
The same rule already held at the library boundary (`CE5002`); `CE5008`
enforces it one level down, between the units of a single program.

## No danger zone, no `ptr`

The type name `ptr` may only be **spelled in a unit that declares an
`unsafe external` block** (`CE5009`). A unit without externals has no way to
ever *produce* a `ptr` value - there is no `null` literal, no cast yields a
`ptr` (`CE2014`), and uninitialized `let` does not exist - so a `ptr` type
written there is dead plumbing at best. The gate makes the unsafe realm
textually identifiable: grep a codebase for `unsafe external` and you have
found every file that can traffic in raw foreign handles.

Other units still *hold* handles - through the wrapper structs the FFI unit
declares. They just never name the raw type themselves.

## What `ptr` cannot do

A `ptr` is an **opaque token**, not a value with behavior. The compiler
rejects every operation that would pretend otherwise:

| Attempt | Diagnostic |
|---|---|
| `a == b`, `a < b`, arithmetic, `not`/`~`/`-` on a `ptr` | `CE5010` - no comparable identity, no arithmetic, no truthiness |
| `p.hash()` or any method call on a `ptr` | `CE5011` - an opaque handle has no methods |
| `HashMap<i32, ptr>`, `List<ptr>`, `Tagged<ptr>` (any generic argument) | `CE5012` - only `Result<ptr, E>` and `Maybe<ptr>` carry a `ptr` |
| `"{p}"` interpolation | `CE2035` - no string form |
| `0 as ptr`, `p as i64` | `CE2014` - cannot be forged from or laundered into an integer |

What remains is exactly the *holding* set: local variables, private function
parameters and returns, `Result<ptr, E>`/`Maybe<ptr>`, struct fields, and
plain arrays (`ptr[]`). If a handle needs behavior - equality, hashing,
methods, a place in a collection - wrap it in a concrete struct and give the
*struct* those things; the struct is real Sushi and plays by all the rules.

If null-checking is ever needed it will arrive as an `is_null(ptr) -> bool`
intrinsic, never as `==` or a `null` literal.

## Diagnostics

| Code | Severity | Rule |
|---|---|---|
| `CW5001` | warning (exit 1) | A block without `because`. Silenced by adding a reason. |
| `CE5001` | error | A link-name clashes with a compiler built-in extern of a **different** signature. An identical signature is allowed (LLVM deduplicates). |
| `CE5002` | error | An external - or any public function whose signature exposes a foreign `ptr` - appears in a `.slib` public API. FFI is a private unit detail and cannot propagate through Nori packages. |
| `CE5003` | error | An external signature uses a non-C-ABI type, or the ABI string is not `"C"`. |
| `CE5004` | error | A variadic external (`...`) declares no fixed parameter. The C ABI needs at least one named argument for `va_start`. |
| `CE5005` | error | A non-C-ABI value is passed as a variadic (`...`) argument at a call site. |
| `CE5008` | error | A `public fn` exposes a foreign `ptr` in its signature (parameter, return, or inside `Result`/`Maybe`). Keep the function private or wrap the pointer in a struct. |
| `CE5009` | error | `ptr` is named in a unit that declares no `unsafe external` block. No danger zone, no ptr. |
| `CE5010` | error | A `ptr` is used with an operator (comparison, arithmetic, bitwise, logical). An opaque handle has no identity or arithmetic. |
| `CE5011` | error | A method is called on a `ptr`. Wrap the handle in a struct and extend the struct. |
| `CE5012` | error | A `ptr` appears as a generic type argument outside `Result`/`Maybe` (e.g. `HashMap<i32, ptr>`, `List<ptr>`). |

## Linking: what can actually be resolved

The `= "symbol"` string is the **link name**. It becomes an undefined external
symbol reference in the generated object file; the linker must satisfy it. What
satisfies it is the important part.

Sushi links binaries by invoking the C compiler driver as `clang prog.o -o prog`
(plus `-lm` on Linux). There is **no explicit `-lc`** and **no way to pass
`-l<lib>` or `-L<path>`**. libc is linked anyway, for two compounding reasons:

1. **The clang driver links the C runtime into every executable** by default
   (libc/libSystem plus the startup objects). This happens for any program,
   FFI or not.
2. **Every Sushi binary already depends on libc.** The core runtime and stdlib
   emit direct calls to `malloc`, `free`, `realloc`, `printf`, `fopen`, `fread`,
   `fwrite`, `fclose`, `exit`, and friends. libc is a structural dependency of
   the output, not an optional add-on.

So an FFI declaration resolves at link time **if and only if its symbol already
lives in an always-linked library** - libc/libSystem, plus libm on Linux. That
is exactly why the bootstrapping leaf targets (`strlen`, `fopen`, `malloc`) were
chosen: they cost nothing to link because the binary already pulls libc in.

The namespace in `as libc` is a **pure Sushi-side label** and drives nothing at
link time. It emits no `-l` directive and names no library file - `as banana`
would link identically. It only controls how call sites read.

**Consequence.** A symbol that is *not* in an always-linked library (a
third-party `libfoo`, or anything needing `-l`/`-L`) will **compile but fail to
link** with an `undefined symbol` error. There is currently no mechanism to tell
the linker about additional libraries. v1 FFI is therefore limited to the
default-linked C runtime surface.

## v1 scope and limitations

- Only the `"C"` ABI is accepted.
- **Only symbols in always-linked libraries** (libc/libSystem, plus libm on
  Linux) can be resolved. There is no way to link an external library - no
  `-l`/`-L` mechanism - so a non-libc symbol compiles but fails at link time.
  See [Linking](#linking-what-can-actually-be-resolved) above.
- Sushi stays **null-free**: no `null` literal; ptr-null-check is a future
  `is_null` intrinsic.
- String marshalling is a per-call copy, freed at scope exit.
- No errno/sentinel auto-mapping into `Result` (raw only). The `= "symbol"`
  suffix reserves room for an optional future error-convention annotation.
- No reverse FFI (exporting Sushi functions to C) and no callbacks into C.
- Externals and foreign `ptr` cannot appear in a library public API (`CE5002`).
- Externals are currently visible across compilation units rather than strictly
  per-unit; treat the namespace as program-global for now.
- A user type named `ptr` is shadowed by the reserved `ptr` type in type
  position; avoid `ptr` as a user type name.

## Future work: external (non-libc) libraries

Support for linking arbitrary external libraries (a `-l`/`-L` mechanism) is
**deliberately deferred**, not forgotten. The reasoning:

- The near-term goal is **self-hosting**, and the chosen route is to emit LLVM IR
  as text and shell out to the C toolchain (`clang`/`llc`). That needs only
  process-spawning and file I/O - both libc - so it links today. For example,
  `fn system(string cmd) i32 = "system"` is already callable and is enough to
  invoke the toolchain. The self-host route does **not** require external-library
  linking.
- A real external-library feature is not just a flag. It pulls in library
  resolution and portability (install paths, pkg-config, versioning, rpath),
  **ABI struct-by-value marshalling** (real libraries pass structs, which `CE5003`
  currently forbids), and distribution (a binary needing `libfoo` is no longer
  self-contained). It also reopens the package-boundary that `CE5002` and the
  Nori/Omakase model intentionally close.

When Sushi turns toward general-purpose use, this will be planned as a
first-class concern (linking + struct marshalling + resolution together), not
bolted on as a `-l` passthrough. Until a concrete non-libc need appears, FFI
stays scoped to the always-linked C runtime.

## Worked example

See [examples/28-ffi.sushi](examples/28-ffi.sushi) for the runnable `strlen`
example used above.
