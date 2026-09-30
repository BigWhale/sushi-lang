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
| `"C"` | The ABI. Only `"C"` is accepted; the slot reserves room for others. |
| `as libc` | The **namespace binding**, chosen by you. It binds only in the unit that declares the block. Foreign names never enter Sushi's global scope. |
| `because "<reason>"` | **Optional.** The acknowledgment that silences the `CW5001` warning (see below). |
| `fn name(params) ret = "symbol"` | One foreign declaration. No body. The Sushi-visible `name` and the C link `symbol` are separated. |
| `var type name = "symbol"` | One C global variable, read-only from Sushi ([External variables](#external-variables)). |

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

The link name is a string literal, or a string constant when the symbol differs per
platform: `= STAT_SYMBOL` or `= platform.STAT_SYMBOL`, where `<sys/platform>` declares
the link names of `stat`, `lstat` and `readdir` for the host (a platform can name them
another way, as macOS x86_64 does with `stat$INODE64`). The constant is folded in the unit
that declares the block, and every rule below reads the folded name. A constant of another
type is `CE5015`, and a name that is no constant is `CE1001`.

**The C symbol must be foreign.** An `unsafe external` reaches OUT of the program, so the
name after `=` may not be one this build defines -- a function of any unit, a constant, a
symbol a linked library brought in, its private ones included, or one the standard library
generates (`string_len`, `sushi_sin`, and about 145 more). That is **CE5013**, and it names
where the symbol is defined. A generated name is refused whether the program links that
stdlib unit or not, so adding a `use` line never breaks a build that compiled before.

The reason is that there is no link step left to keep the two apart. A program's units share
one LLVM module and a linked library's module is merged into it, so a declaration and a
definition of one name unify: the call enters the program's own body with the declared
signature, unchecked. A library-*private* body could be run that way from consumer code, and
with a mismatched signature the return value is read out of the wrong register. Two
namespaces may still declare the same FOREIGN symbol when the two signatures agree, and
`CE5001` is the rule for two that do not.

One C symbol has one signature in one program (#1099). Two declarations of one link name, in
one unit or in two, agree only when each parameter, the return and `var_arg` have the SAME C
type. `i32` against `u32` is `CE5001` (`int` and `unsigned int`), and so is a fixed signature
against a `var_arg` one. Two spellings of one C type agree: `string` and `Maybe@(string)`
are `char*`, `ptr` and `Maybe@(ptr)` are `void*`, and a link name written as a constant is
the name it folds to. The built-ins that the compiler declares itself (`malloc`, `free`,
`exit`, `strlen`, `strcmp`, `memcmp`, `printf`, `sprintf`, the errno location function) are
the first declaration of their names, so `fn malloc(i32 n) ptr = "malloc"` is `CE5001` too.
External variables follow the same rule.

## Types at the boundary

External signatures are limited to the **C-representable subset**:

- Primitives: `i8`..`i64`, `u8`..`u64`, `f32`, `f64`, `bool`
- `ptr` - the opaque foreign pointer type (below)
- `~` - genuine C `void`
- `string` - auto-marshalled to/from C `char*` (below)
- `Maybe@(string)` and `Maybe@(ptr)` - a pointer that may be NULL
  ([Null at the boundary](#null-at-the-boundary))
- `u8[]`, `peek u8[]` and `poke u8[]` - a byte buffer, as a parameter only
  ([Byte buffers](#byte-buffers))

Anything else (`Result@(T,E)`, any other `Maybe@(T)`, structs, other arrays,
references, named user types) is a hard error: **`CE5003`**. The check is a strict
allowlist, so an unknown user type cannot slip through.

### The `ptr` type

C traffics in raw pointers (`char*`, `FILE*`). FFI introduces an opaque,
**unmanaged** handle type `ptr` (LLVM `i8*`):

- The **borrow checker ignores it** - aliasing through a `ptr` is not tracked.
- **RAII never frees it** - a `ptr` has no destructor; you call the matching C
  free yourself.
- **No bounds guarantees** - the memory behind a `ptr` is not checked; the
  [foreign-memory methods](#reading-and-writing-foreign-memory) read and write it
  at a byte offset, as C does.
- **Never null** - a plain `ptr` return asserts non-null (below).

Sushi is and stays a **null-free** language. There is no `null` literal, and a null
is never a Sushi value, not even in the unit that declares the block. A null is a
state of the C boundary, and it becomes a `Maybe` at the call.

### Null at the boundary

A C function that may answer NULL is declared with `Maybe@(string)` or
`Maybe@(ptr)`. The form is legal at the top level of a parameter or a return, and
nowhere else: a `Maybe@(i32)`, a nested `Maybe` and a `Result` stay `CE5003`.

| Position | Declared | NULL | Any other pointer |
|---|---|---|---|
| return | `Maybe@(string)` | `Maybe.None` | `Maybe.Some` of an owned copy |
| return | `Maybe@(ptr)` | `Maybe.None` | `Maybe.Some(p)` |
| return | `string` or `ptr` | **`RE2025`**: the program stops at the call | the value |
| parameter | `Maybe@(string)` or `Maybe@(ptr)` | `Maybe.None` crosses as NULL | `Maybe.Some(v)` crosses as `v`, marshalled as a plain one |

```sushi
unsafe external "C" as libc because "reading the process environment":
    fn getenv(string key) Maybe@(string) = "getenv"
    fn strtol(string s, Maybe@(ptr) end, i32 base) i64 = "strtol"

fn main() i32:
    match libc.getenv("SUSHI_NO_SUCH_KEY_42"):
        Maybe.Some(v) -> println("set: {v}")
        Maybe.None -> println("not set")
    println(libc.strtol("42", Maybe.None, 10))
    return 0
```

A plain `string` or `ptr` return is the declaration "C never answers NULL here". The
call tests the pointer once, and a NULL ends the program with `RE2025`. A wrong
declaration therefore fails at the call, and not later in `strlen` or in the next C
call. There is no `is_null(ptr)`: with the `Maybe` at the boundary there is nothing
left to test, and an `is_null` would bring a null `ptr` back as a value.

A `Maybe.None` argument marshals nothing and registers nothing for the scope-exit
free. A trailing variadic argument cannot be a `Maybe` (`CE5005`): it has no declared
type to say how to marshal one.

### Return types: an external function is bare

A Sushi function has an error channel only when its signature writes `| E` (see
[The error channel is opt-in](design/error-channel.md)). An external function is always
bare: a C function returns a raw value with no error channel and cannot construct a Sushi
`Result` across the ABI.

```sushi
fn strlen(string s) i64 = "strlen"   # returns raw i64, NOT Result@(i64, StdError)
fn malloc(i64 n) Maybe@(ptr) = "malloc"   # NULL is Maybe.None
fn free(ptr p) ~        = "free"     # ~ here is genuine C void, NOT Result@(~)
```

Because `libc.strlen(s)` yields a plain `i64`, you **cannot** apply `??` or
`.realise()` to it - it is not a `Result`/`Maybe`. Attempting `libc.strlen(s)??`
is a clean type error (**`CE2507`**), as it is on the call of any bare function.

### String auto-marshalling (and the no-leak contract)

A Sushi `string` is a UTF-8 fat pointer with three fields,
`{i8* data, i32 size, i8 owned}`. C expects a null-terminated `char*`. At the boundary the compiler marshals automatically:

- A `string` **argument** is copied into a fresh null-terminated `char*` for the
  call. That temporary is registered in a per-scope cleanup list and freed via
  libc `free` at scope exit - on **every** path (normal block end, early
  `return`, and `??` propagation). It is freed exactly once. **No leak.**
- A `string` **return** is converted from the C `char*` back into a Sushi fat
  pointer.

The copy is per-call. The marshalling is invisible in your source, but the
freeing is real: inspect the IR with `./sushic --dump-ll` and you will see a
`free` of the marshalled `char*` in the function's cleanup path.

### Byte buffers

A `u8[]` parameter crosses as the pointer to its first byte. The count is a separate
parameter, as C declares it, and the Sushi side passes `buf.len()` or the size it
allocated.

| Written | Crosses as | The C side may |
|---|---|---|
| `u8[] buf` or `peek u8[] buf` | `i8*` to the first byte | read `len` bytes |
| `poke u8[] buf` | `i8*` to the first byte | read and write `len` bytes |
| `nom u8[] buf` | refused, `CE2428` | a C function cannot take ownership of a Sushi array |
| a `u8[]` return | refused, `CE5003` | C cannot answer a Sushi array; a `ptr` plus a copy is the route |

A C function fills a `poke` buffer in place and answers a count. The wrapper allocates
the buffer with `from([0; n])`, so every byte has a value and `len` is `n`, and then
cuts it to the count with `.truncate(count)`. Nothing sets a length past what was
written, so no uninitialized byte is ever readable.

<!-- docs-sweep: skip (reads a file that the page does not ship) -->
```sushi
unsafe external "C" as libc because "reading bytes from a descriptor":
    fn open(string path, i32 flags, ...) i32 = "open"
    fn read(i32 fd, poke u8[] buf, i64 n) i64 = "read"
    fn close(i32 fd) i32 = "close"

fn main() i32:
    let i32 fd = libc.open("towel.txt", 0)
    let u8[] buf = from([0; 64])
    let i64 got = libc.read(fd, poke buf, buf.len() as i64)
    let i32 rc = libc.close(fd)
    buf.truncate(got as i32)
    println(buf.to_string())
    return Result.Ok(rc)
```

The `poke` argument is a write for the borrow checker, as any `poke` argument is, and
it is marked at both ends. The array is a borrow at the call and stays the caller's;
nothing is registered for a scope-exit free, unlike a marshalled `string`. A `u8[]` in
the `...` position of a variadic extern is `CE5005`. Other element types (`i32[]`,
`f64[]`) and a fixed `u8[N]` stay `CE5003` for now.

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
| 1 | Borrow checking (`peek`/`poke`) | aliasing is not tracked through foreign pointers |
| 2 | RAII / move semantics | a foreign-returned `ptr` is unmanaged; you free it yourself |
| 3 | `Result` / `Maybe` | no auto-wrapping; check C sentinels (errno/-1/NULL) by hand |
| 4 | Bounds safety | the memory behind a `ptr` is not bounds-checked |

A block **without** `because "<reason>"` compiles (exit 1) but emits one
non-fatal warning, **`CW5001`**, stating the contract plus signature-driven
notes (a `ptr` return is unmanaged; a primitive return is raw,
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
ordinary Sushi (so it follows the error-channel rule of every function), and it
marshals data, folds C sentinels into `Result`, and manages pointer lifetimes. A wrapper
that can fail writes `| E`. A wrapper over a total C function, as below, can be bare:

```sushi
unsafe external "C" as libc because "string length via libc strlen":
    fn strlen(string s) i64 = "strlen"

# Safe wrapper - normal Sushi, upholds all four guarantees again.
fn length(string s) i64:
    return libc.strlen(s)

fn main() i32:
    let i64 n = length("Mostly Harmless")
    println("len = {n}")
    return 0
```

The boundary is sharp: **raw, exempt, namespaced foreign calls inside
`unsafe external`; Result-clean, guarantee-upholding Sushi everywhere else.**
That convention is load-bearing, not cosmetic.

### Reading `errno`

A failed libc call answers a sentinel (usually -1 or NULL) and leaves the cause in
`errno`. The built-in `errno()` answers the calling thread's `errno` as an `i32`. The
two platform symbols (`__error` on macOS, `__errno_location` on Linux) live in the
compiler and never in Sushi source. A wrapper maps the number to an error variant:

```sushi
unsafe external "C" as libc because "removing a file":
    fn unlink(string path) i32 = "unlink"

enum RemoveError:
    Missing
    Other(i32)

fn remove(string path) ~ | RemoveError:
    if (libc.unlink(path) == -1):
        let i32 code = errno()
        if (code == 2):
            return Result.Err(RemoveError.Missing)
        return Result.Err(RemoveError.Other(code))
    return Result.Ok(~)

fn main() i32:
    match remove("/no/such/file"):
        Result.Ok(_) -> println("removed")
        Result.Err(RemoveError.Missing) -> println("missing")
        Result.Err(RemoveError.Other(code)) -> println("errno {code}")
    return 0
```

Three rules apply:

- **Read it first.** Read `errno()` directly after the failed call and before any
  `close`, `free` or other C call, because those can overwrite it
  (`docs/design/stdlib-syscall-layer.md`, "The order of the calls").
- **Only in the danger zone.** `errno()` is callable only in a unit that declares an
  `unsafe external` block (`CE5014`), the confinement of `ptr` (`CE5009`). A unit's own
  `fn errno` is an ordinary declaration and wins over the built-in.
- **The numbers are per platform.** `ENOENT` is 2 on both platforms, but most errno
  numbers differ between macOS and Linux. A wrapper reads them from `<sys/platform>`.

A wrapper that restores RAII for a foreign handle looks like:

```sushi
fn close_handle(ptr h) ~:
    libc.free(h)            # guarantee 2 (RAII) restored by hand
```

A wrapper may also *return* the handle it acquired - `ptr` flows through a
`Result` (and through `Maybe@(ptr)`) like any other value:

<!-- docs-sweep: skip (uses the unsafe external block declared earlier on the page) -->
```sushi
fn grab() ptr | StdError:
    let ptr p = libc.malloc(8 as i64)
    return Result.Ok(p)

fn main() i32:
    match grab():
        Result.Ok(p) -> libc.free(p)
        Result.Err(_) -> println("alloc failed")
    return 0
```

Holding a `ptr` is the safe half of the FFI contract (it cannot be dereferenced
in Sushi); wrapping one in `Result`/`Maybe` adds the error channel, **not** RAII
or null-checking - freeing the handle is still your job.

## `ptr` is unit-confined

A **public declaration may not expose `ptr` anywhere in its signature**: not as
a parameter, not as a return type or error arm, not inside `Result@(ptr, E)` or
`Maybe@(ptr)`, and not inside a struct or an enum that holds a `ptr`. The check
walks into the fields of a struct. It applies to a `public fn`, a public
generic, an extension method and a perk method. The compiler refuses a violation
with **`CE5008`**.

FFI is a private implementation detail of the unit that declares the
`unsafe external` block. Thus a struct that holds a `ptr` stays in its unit, and
what the unit exports is Sushi-shaped: fully digested values (`string`, `i64`,
...) and functions that do the foreign work inside:

```sushi
unsafe external "C" as libc because "a scratch buffer from the C heap":
    fn malloc(i64 n) ptr = "malloc"
    fn free(ptr p) ~ = "free"

struct Buffer:
    ptr raw
    i64 size

fn open_buffer(i64 n) Buffer:
    return Buffer(libc.malloc(n), n)

fn close_buffer(Buffer b) ~:
    libc.free(b.raw)

public fn scratch_size(i64 n) i64:
    let Buffer b = open_buffer(n)
    let i64 size = b.size
    close_buffer(b)
    return size
```

`Buffer`, `open_buffer` and `close_buffer` are private, so their `ptr` is
legal. `scratch_size` is public, and its signature holds only an `i64`.
Private functions are unrestricted: inside the FFI unit, `ptr` parameters and
returns flow freely. The same rule holds at the library boundary (`CE5002`);
`CE5008` applies it between the units of one program.

> The `CE5008` help text says that struct fields may carry `ptr` across units.
> The check does not allow this: a `public struct` with a `ptr` field in a public
> signature is `CE5008`, and a private struct in a public signature is `CE3009`.

## No danger zone, no `ptr`

The type name `ptr` may only be **spelled in a unit that declares an
`unsafe external` block** (`CE5009`). A unit without externals has no way to
ever *produce* a `ptr` value - there is no `null` literal, no cast yields a
`ptr` (`CE2014`), and uninitialized `let` does not exist - so a `ptr` type
written there is dead plumbing at best. The gate makes the unsafe realm
textually identifiable: grep a codebase for `unsafe external` and you have
found every file that can traffic in raw foreign handles.

Other units never hold a raw handle. They call the public functions of the FFI
unit, which take and return Sushi values.

## Reading and writing foreign memory

C answers through memory: `clock_gettime` fills a `struct timespec`, `stat` fills a
`struct stat`, `getaddrinfo` builds a list of `addrinfo` records. Inside the unit that
declares the `unsafe external` block, a `ptr` has a closed set of methods that read and
write the memory behind it. The offset is a byte offset, an `i32` like every index.

| Method | Answers | Meaning |
|---|---|---|
| `p.load_i8(off)` ... `p.load_i64(off)`, `p.load_u8(off)` ... `p.load_u64(off)`, `p.load_f32(off)`, `p.load_f64(off)` | the value | a load of that width at byte offset `off` |
| `p.store_i8(off, v)` ... `p.store_f64(off, v)` | `~` | a store of that width; `v` has exactly that type |
| `p.load_ptr(off)` | `Maybe@(ptr)` | a pointer read; a NULL is `Maybe.None` |
| `p.store_ptr(off, q)` | `~` | a pointer store |
| `p.offset(n)` | `ptr` | the address `p + n`, for a walk over an array of records |
| `p.to_string(off)` | `string` | an owned copy of the NUL-terminated C string at `off` |

```sushi
unsafe external "C" as libc because "reading the wall clock":
    fn malloc(i64 n) ptr = "malloc"
    fn free(ptr p) ~ = "free"
    fn clock_gettime(i32 id, ptr ts) i32 = "clock_gettime"

fn main() i32:
    let ptr ts = libc.malloc(16)
    let i32 rc = libc.clock_gettime(0, ts)
    let i64 seconds = ts.load_i64(0)
    let i64 nanos = ts.load_i64(8)
    println("{seconds}.{nanos}")
    libc.free(ts)
    return rc
```

Every access is unaligned-safe (`align 1`): a byte offset says nothing about
alignment. Nothing is bounds-checked. An offset past the end of the C buffer reads or
writes memory the program does not own, exactly as in C; this is guarantee 4 of
[the safety contract](#the-safety-contract-four-suspended-guarantees). The offsets of
a C struct differ per platform and per architecture, so a wrapper reads them from
`<sys/platform>` and never writes them as numbers.

The methods need a `ptr`, and only a unit that declares an `unsafe external` block can
name one (`CE5009`), so no other unit can read foreign memory. `CE5008` keeps every
`ptr` out of a public signature. A C-layout struct (a named type with C offsets and
alignment) is a later feature on top of these loads and stores.

## External variables

A C global variable is declared with `var` inside the block:

```sushi
unsafe external "C" as libc because "reading getopt state and the environment":
    var i32 optind = "optind"
    var Maybe@(ptr) environ = "environ"

fn main() i32:
    println(libc.optind)
    match libc.environ:
        Maybe.Some(_) -> println("the process has an environment")
        Maybe.None -> println("no environment")
    return 0
```

- **The type** is a number, `bool`, `ptr` or `Maybe@(ptr)`, and anything else is
  `CE5003`. A `char*` global is a `ptr` (or a `Maybe@(ptr)`), not a `string`: copy it out
  with `to_string(0)` when you want the text.
- **A read is namespaced** like a call, `libc.environ`, and it loads the global at the
  moment of the read. A `Maybe@(ptr)` read tests the pointer, and NULL is
  `Maybe.None`; a plain `ptr` read asserts non-null (`RE2025`), as a return does.
- **It is read-only.** A write (`libc.optind := 1`) is `CE5016`. A write to a C global
  can come later with `poke` semantics.
- **The symbol** follows the rules of a function's link name: it may be a string
  constant, and one this build defines is `CE5013`. The namespace binds only in the unit
  that declares the block, so another unit that names the variable gets `CE1001`.

On Linux the environment of the process is the global `environ`, which `posix_spawnp`
takes as a `char**`. On macOS a main executable reaches `environ` too.

## What `ptr` cannot do

A `ptr` is an **opaque token**, not a value with behavior. The compiler
rejects every operation that would pretend otherwise:

| Attempt | Diagnostic |
|---|---|
| `a == b`, `a < b`, arithmetic, `not`/`~`/`-` on a `ptr` | `CE5010` - no comparable identity, no arithmetic, no truthiness |
| `p.hash()` or any method outside [the foreign-memory set](#reading-and-writing-foreign-memory) | `CE5011` - a `ptr` has those methods and no others |
| `HashMap@(i32, ptr)`, `List@(ptr)`, `Tagged@(ptr)` (any generic argument) | `CE5012` - only `Result@(ptr, E)` and `Maybe@(ptr)` carry a `ptr` |
| `"{p}"` interpolation | `CE2035` - no string form |
| `0 as ptr`, `p as i64` | `CE2014` - cannot be forged from or laundered into an integer |

What remains is exactly the *holding* set: local variables, private function
parameters and returns, `Result@(ptr, E)`/`Maybe@(ptr)`, struct fields, and
plain arrays (`ptr[]`), all in the unit that declares the `unsafe external` block. If a handle needs behavior - equality, hashing,
methods, a place in a collection - wrap it in a concrete struct and give the
*struct* those things; the struct is real Sushi and plays by all the rules.

There is no null test either, and there will be none: a C function that may answer
NULL is declared `Maybe@(ptr)` ([Null at the boundary](#null-at-the-boundary)).

## Diagnostics

| Code | Severity | Rule |
|---|---|---|
| `CW5001` | warning (exit 1) | A block without `because`. Silenced by adding a reason. |
| `CE5001` | error | Two declarations of one link-name (two `unsafe external` declarations, or one and a compiler built-in) have other C types. Two that agree are allowed. |
| `CE5002` | error | An external - or any public function whose signature exposes a foreign `ptr` - appears in a `.slib` public API. FFI is a private unit detail and cannot propagate through Nori packages. |
| `CE5003` | error | An external signature uses a non-C-ABI type, or the ABI string is not `"C"`. `Maybe@(string)` and `Maybe@(ptr)` are the two `Maybe` forms it admits. |
| `CE5004` | error | A variadic external (`...`) declares no fixed parameter. The C ABI needs at least one named argument for `va_start`. |
| `CE5005` | error | A non-C-ABI value is passed as a variadic (`...`) argument at a call site. |
| `CE5008` | error | A public declaration exposes a foreign `ptr` in its signature (parameter, return, error arm, inside `Result`/`Maybe`, or inside a struct field). Keep the declaration private. |
| `CE5009` | error | `ptr` is named in a unit that declares no `unsafe external` block. No danger zone, no ptr. |
| `CE5010` | error | A `ptr` is used with an operator (comparison, arithmetic, bitwise, logical). An opaque handle has no identity or arithmetic. |
| `CE5011` | error | A method outside the foreign-memory set is called on a `ptr`. Wrap the handle in a struct and extend the struct. |
| `CE5012` | error | A `ptr` appears as a generic type argument outside `Result`/`Maybe` (e.g. `HashMap@(i32, ptr)`, `List@(ptr)`). |
| `RE2025` | runtime | A foreign return declared `string` or `ptr` was NULL. Declare it `Maybe@(string)` / `Maybe@(ptr)`. |
| `CE5015` | error | A link name written as a constant is not a string constant. |
| `CE5016` | error | A write to an external variable. It is read-only from Sushi. |
| `CE5014` | error | `errno()` is called in a unit that declares no `unsafe external` block. |
| `CE5013` | error | A link-name names a symbol this build **defines** -- a function of any unit, a constant, one a linked library brought in, or one the standard library generates. FFI names foreign symbols only. The note says where the symbol is defined. |

## Linking: what can actually be resolved

The `= "symbol"` string is the **link name**. It becomes an undefined external
symbol reference in the generated object file; the linker must satisfy it. What
satisfies it is the important part.

Sushi links binaries with the C compiler driver: `cc prog.o -o prog` (plus
`-lm` on Linux). There is **no explicit `-lc`** and **no way to pass
`-l<lib>` or `-L<path>`**. libc is linked anyway, for two reasons:

1. **The C compiler driver links the C runtime into every executable** by default
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
the linker about additional libraries. Thus FFI is limited to the
default-linked C runtime surface.

## Scope and limitations

- Only the `"C"` ABI is accepted.
- **Only symbols in always-linked libraries** (libc/libSystem, plus libm on
  Linux) can be resolved. There is no way to link an external library - no
  `-l`/`-L` mechanism - so a non-libc symbol compiles but fails at link time.
  See [Linking](#linking-what-can-actually-be-resolved) above.
- Sushi stays **null-free**: no `null` literal. A pointer that C may answer NULL
  for is a `Maybe@(string)` or a `Maybe@(ptr)` at the boundary.
- String marshalling is a per-call copy, freed at scope exit.
- No errno/sentinel auto-mapping into `Result`: an extern answers the raw value, and
  a wrapper reads `errno()` and builds the error by hand. The `= "symbol"` suffix
  reserves room for an optional future error-convention annotation.
- No reverse FFI (exporting Sushi functions to C) and no callbacks into C.
- Externals and foreign `ptr` cannot appear in a library public API (`CE5002`).
- The namespace of an `unsafe external` block binds only in the unit that
  declares it. Another unit that names it gets `CE1001`. To use a foreign
  function from two units, declare it in each unit, or export a Sushi wrapper.
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
