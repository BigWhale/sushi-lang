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
(`fn c_printf(...) i32 = "printf"`). You may name the Sushi side anything; the
linker resolves the symbol after `=`. This is what lets you bind `printf` without
shadowing any Sushi name.

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

## Diagnostics

| Code | Severity | Rule |
|---|---|---|
| `CW5001` | warning (exit 1) | A block without `because`. Silenced by adding a reason. |
| `CE5001` | error | A link-name clashes with a compiler built-in extern of a **different** signature. An identical signature is allowed (LLVM deduplicates). |
| `CE5002` | error | An external - or any public function whose signature exposes a foreign `ptr` - appears in a `.slib` public API. FFI is a private unit detail and cannot propagate through Nori packages. |
| `CE5003` | error | An external signature uses a non-C-ABI type, or the ABI string is not `"C"`. |

## v1 scope and limitations

- Only the `"C"` ABI is accepted.
- Sushi stays **null-free**: no `null` literal; ptr-null-check is a future
  `is_null` intrinsic.
- String marshalling is a per-call copy, freed at scope exit.
- No errno/sentinel auto-mapping into `Result` (raw only). The `= "symbol"`
  suffix reserves room for an optional future error-convention annotation.
- No reverse FFI (exporting Sushi functions to C) and no callbacks into C.
- Externals and foreign `ptr` cannot appear in a library public API (`CE5002`).

## Worked example

See [examples/28-ffi.sushi](examples/28-ffi.sushi) for the runnable `strlen`
example used above.
