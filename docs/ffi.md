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
| `link "z", "m"` | **Optional.** The C libraries that the block needs. The linker gets `-lz -lm` ([Linking an external library](#linking-an-external-library)). |
| `as libc` | The **namespace binding**, chosen by you. It binds only in the unit that declares the block. Foreign names never enter Sushi's global scope. Two units may each declare a block with the same alias and the same functions (a stdlib module included); each unit reads its own block. One link name still has one signature in the program ([`CE5001`](error-catalog.md#ce5001)). |
| `because "<reason>"` | **Optional.** The acknowledgment that silences the [`CW5001`](error-catalog.md#cw5001) warning (see below). |
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
type is [`CE5015`](error-catalog.md#ce5015), and a name that is no constant is [`CE1001`](error-catalog.md#ce1001).

**The C symbol must be foreign.** An `unsafe external` reaches OUT of the program, so the
name after `=` may not be one this build defines -- a function of any unit, a constant, a
symbol a library brought in, its private ones included, or one the standard library
generates (`string_len`, `sushi_sin`, and about 145 more). That is **[CE5013](error-catalog.md#ce5013)**, and it names
where the symbol is defined. A generated name is refused whether the program links that
stdlib unit or not, so adding a `use` line never breaks a build that compiled before.

The rule compares the SYMBOL that a function or a constant emits, not its Sushi name. A
function or a constant `f` of the unit `geo/vec` emits `geo$vec$f` (`main` alone keeps its
name). So a wrapper
may have the name of the C function it calls (`fn sin(f64 x) f64` beside `= "sin"`), and a
user function named `getpid` does not break another unit's extern of `getpid`. A link name
that spells an emitted symbol (`= "geo$vec$f"`) is still [`CE5013`](error-catalog.md#ce5013), and so is `user_main`,
the symbol of the body of `main`. A monomorphized instance of a generic emits a symbol too,
and the rule sees it: `ident@(i32)` of the unit `inst` emits `inst$ident__i32`.

The reason is that there is no link step left to keep the two apart. A program's units share
one LLVM module, and the units of a library are units of the program, so a declaration and a
definition of one name unify: the call enters the program's own body with the declared
signature, unchecked. A library-*private* body could be run that way from consumer code, and
with a mismatched signature the return value is read out of the wrong register. Two
namespaces may still declare the same FOREIGN symbol when the two signatures agree, and
[`CE5001`](error-catalog.md#ce5001) is the rule for two that do not.

One C symbol has one signature in one program (#1099). Two declarations of one link name, in
one unit or in two, agree only when each parameter, the return and `var_arg` have the SAME C
type. `i32` against `u32` is [`CE5001`](error-catalog.md#ce5001) (`int` and `unsigned int`), and so is a fixed signature
against a `var_arg` one. Two spellings of one C type agree: `string` and `Maybe@(string)`
are `char*`, `ptr` and `Maybe@(ptr)` are `void*`, and a link name written as a constant is
the name it folds to. External variables follow the same rule.

`malloc`, `free` and `exit` are the compiler's own: each is the first declaration of its
name, so `fn malloc(i32 n) ptr = "malloc"` is [`CE5001`](error-catalog.md#ce5001) too. Every other libc symbol that
the compiler declares (`write`, `fwrite`, `fprintf`, `setvbuf`, the ctype functions, the
stdio handles, and the rest) is independent of a user declaration of the same name. Each
call goes through the type of its own declaration, so a user `write` with a wrong count
type does not change what `println` calls. As in C, a user declaration that does not match
the C library is the user's fault.

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
references, named user types) is a hard error: **[`CE5003`](error-catalog.md#ce5003)**. The check is a strict
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
nowhere else: a `Maybe@(i32)`, a nested `Maybe` and a `Result` stay [`CE5003`](error-catalog.md#ce5003).

| Position | Declared | NULL | Any other pointer |
|---|---|---|---|
| return | `Maybe@(string)` | `Maybe.None` | `Maybe.Some` of an owned copy |
| return | `Maybe@(ptr)` | `Maybe.None` | `Maybe.Some(p)` |
| return | `string` or `ptr` | **[`RE2025`](error-catalog.md#re2025)**: the program stops at the call | the value |
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
call tests the pointer once, and a NULL ends the program with [`RE2025`](error-catalog.md#re2025). A wrong
declaration therefore fails at the call, and not later in `strlen` or in the next C
call. There is no `is_null(ptr)`: with the `Maybe` at the boundary there is nothing
left to test, and an `is_null` would bring a null `ptr` back as a value.

A `Maybe.None` argument marshals nothing and registers nothing for the scope-exit
free. A trailing variadic argument cannot be a `Maybe` ([`CE5005`](error-catalog.md#ce5005)): it has no declared
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

A C function that never returns writes `!` as its return: `fn exit(i32 code) ! = "exit"`.
It crosses as C `void`. A call of it ends the path, as a call of a Sushi `!` function
does, so the code after it needs no `return` ([the never-return design](design/never-return.md)).

Because `libc.strlen(s)` yields a plain `i64`, you **cannot** apply `??` or
`.realise()` to it - it is not a `Result`. Attempting `libc.strlen(s)??`
is a clean type error (**[`CE2507`](error-catalog.md#ce2507)**), as it is on the call of any bare function.

### String auto-marshalling (and the no-leak contract)

A Sushi `string` is a UTF-8 fat pointer with three fields,
`{i8* data, i64 size, i8 owned}`. C expects a null-terminated `char*`. At the boundary the compiler marshals automatically:

- A `string` **argument** is copied into a fresh null-terminated `char*` directly
  before the call. The call frees the copy with libc `free` directly after the C
  function returns, so the copy lives for the call alone. It is freed exactly once.
  **No leak.**
- A `string` **return** is converted from the C `char*` back into a Sushi fat
  pointer.
- The `errno` of the C call is kept across the frees of the arguments and across the
  conversion of the answer: an `errno()` directly after the call reads the errno of the
  C call, not the errno of a `free` or of the `malloc` of the answer.

The copy is per-call. The marshalling is invisible in your source, but the
freeing is real: inspect the IR with `./sushic --dump-ll` and you will see a
`free` of the marshalled `char*` directly after the call, between a read and a write of
`errno`.

### Byte buffers

A `u8[]` parameter crosses as the pointer to its first byte. The count is a separate
parameter, as C declares it, and the Sushi side passes `buf.len()` or the size it
allocated.

| Written | Crosses as | The C side may |
|---|---|---|
| `u8[] buf` or `peek u8[] buf` | `i8*` to the first byte | read `len` bytes |
| `poke u8[] buf` | `i8*` to the first byte | read and write `len` bytes |
| `nom u8[] buf` | refused, [`CE2428`](error-catalog.md#ce2428) | a C function cannot take ownership of a Sushi array |
| a `u8[]` return | refused, [`CE5003`](error-catalog.md#ce5003) | C cannot answer a Sushi array; a `ptr` plus a copy is the route |

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
the `...` position of a variadic extern is [`CE5005`](error-catalog.md#ce5005). Other element types (`i32[]`,
`f64[]`) and a fixed `u8[N]` stay [`CE5003`](error-catalog.md#ce5003) for now.

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
  `char*` and freed directly after the call, identical to a fixed `string`
  argument. Passing a non-C-ABI value (a struct, `Maybe`, array, ...) is [`CE5005`](error-catalog.md#ce5005).

A variadic extern must declare at least one fixed parameter ([`CE5004`](error-catalog.md#ce5004)): the C ABI's
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
non-fatal warning, **[`CW5001`](error-catalog.md#cw5001)**, stating the contract plus signature-driven
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

error RemoveError:
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
  (`docs/design/stdlib-syscall-layer.md`, "The order of the calls"). The frees of the
  marshalled `string` arguments and the conversion of a `string` answer do not count: the
  call keeps `errno` across them.
- **Only in the danger zone.** `errno()` is callable only in a unit that declares an
  `unsafe external` block ([`CE5014`](error-catalog.md#ce5014)), the confinement of `ptr` ([`CE5009`](error-catalog.md#ce5009)). A unit's own
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
with **[`CE5008`](error-catalog.md#ce5008)**.

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
returns flow freely. The same rule holds at the library boundary ([`CE5002`](error-catalog.md#ce5002));
[`CE5008`](error-catalog.md#ce5008) applies it between the units of one program.

> The [`CE5008`](error-catalog.md#ce5008) help text says that struct fields may carry `ptr` across units.
> The check does not allow this: a `public struct` with a `ptr` field in a public
> signature is [`CE5008`](error-catalog.md#ce5008), and a private struct in a public signature is [`CE3009`](error-catalog.md#ce3009).

## No danger zone, no `ptr`

The type name `ptr` may only be **spelled in a unit that declares an
`unsafe external` block** ([`CE5009`](error-catalog.md#ce5009)). A unit without externals has no way to
ever *produce* a `ptr` value - there is no `null` literal, no cast yields a
`ptr` ([`CE2014`](error-catalog.md#ce2014)), and uninitialized `let` does not exist - so a `ptr` type
written there is dead plumbing at best. The gate makes the unsafe realm
textually identifiable: grep a codebase for `unsafe external` and you have
found every file that can traffic in raw foreign handles.

Other units never hold a raw handle. They call the public functions of the FFI
unit, which take and return Sushi values.

## Reading and writing foreign memory

C answers through memory: `clock_gettime` fills a `struct timespec`, `stat` fills a
`struct stat`, `getaddrinfo` builds a list of `addrinfo` records. Inside the unit that
declares the `unsafe external` block, a `ptr` has a closed set of methods that read and
write the memory behind it. The offset is a byte offset. It is an index position: it takes
a value of any integer type, and a literal there takes `i64`. The value becomes an `i64`
before the access (a signed type is sign-extended, an unsigned type is zero-extended).

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
name one ([`CE5009`](error-catalog.md#ce5009)), so no other unit can read foreign memory. [`CE5008`](error-catalog.md#ce5008) keeps every
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
  [`CE5003`](error-catalog.md#ce5003). A `char*` global is a `ptr` (or a `Maybe@(ptr)`), not a `string`: copy it out
  with `to_string(0)` when you want the text.
- **A read is namespaced** like a call, `libc.environ`, and it loads the global at the
  moment of the read. A `Maybe@(ptr)` read tests the pointer, and NULL is
  `Maybe.None`; a plain `ptr` read asserts non-null ([`RE2025`](error-catalog.md#re2025)), as a return does.
- **It is read-only.** A write (`libc.optind := 1`) is [`CE5016`](error-catalog.md#ce5016). A write to a C global
  can come later with `poke` semantics.
- **The symbol** follows the rules of a function's link name: it may be a string
  constant, and one this build defines is [`CE5013`](error-catalog.md#ce5013). The namespace binds only in the unit
  that declares the block, so another unit that names the variable gets [`CE1001`](error-catalog.md#ce1001).

On Linux the environment of the process is the global `environ`, which `posix_spawnp`
takes as a `char**`. On macOS a main executable reaches `environ` too.

## What `ptr` cannot do

A `ptr` is an **opaque token**, not a value with behavior. The compiler
rejects every operation that would pretend otherwise:

| Attempt | Diagnostic |
|---|---|
| `a == b`, `a < b`, arithmetic, `not`/`~`/`-` on a `ptr` | [`CE5010`](error-catalog.md#ce5010) - no comparable identity, no arithmetic, no truthiness |
| `p.hash()` or any method outside [the foreign-memory set](#reading-and-writing-foreign-memory) | [`CE5011`](error-catalog.md#ce5011) - a `ptr` has those methods and no others |
| `HashMap@(i32, ptr)`, `List@(ptr)`, `Tagged@(ptr)` (any generic argument) | [`CE5012`](error-catalog.md#ce5012) - only `Result@(ptr, E)` and `Maybe@(ptr)` carry a `ptr` |
| `"{p}"` interpolation | [`CE2035`](error-catalog.md#ce2035) - no string form |
| `0 as ptr`, `p as i64` | [`CE2014`](error-catalog.md#ce2014) - cannot be forged from or laundered into an integer |

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
| [`CW5001`](error-catalog.md#cw5001) | warning (exit 1) | A block without `because`. Silenced by adding a reason. |
| [`CE5001`](error-catalog.md#ce5001) | error | Two declarations of one link-name (two `unsafe external` declarations, or one and a compiler built-in) have other C types. Two that agree are allowed. |
| [`CE5002`](error-catalog.md#ce5002) | error | An external - or any public function whose signature exposes a foreign `ptr` - appears in a `.slib` public API. FFI is a private unit detail and cannot propagate through Nori packages. |
| [`CE5003`](error-catalog.md#ce5003) | error | An external signature uses a non-C-ABI type, or the ABI string is not `"C"`. `Maybe@(string)` and `Maybe@(ptr)` are the two `Maybe` forms it admits. |
| [`CE5004`](error-catalog.md#ce5004) | error | A variadic external (`...`) declares no fixed parameter. The C ABI needs at least one named argument for `va_start`. |
| [`CE5005`](error-catalog.md#ce5005) | error | A non-C-ABI value is passed as a variadic (`...`) argument at a call site. |
| [`CE5008`](error-catalog.md#ce5008) | error | A public declaration exposes a foreign `ptr` in its signature (parameter, return, error arm, inside `Result`/`Maybe`, or inside a struct field). Keep the declaration private. |
| [`CE5009`](error-catalog.md#ce5009) | error | `ptr` is named in a unit that declares no `unsafe external` block. No danger zone, no ptr. |
| [`CE5010`](error-catalog.md#ce5010) | error | A `ptr` is used with an operator (comparison, arithmetic, bitwise, logical). An opaque handle has no identity or arithmetic. |
| [`CE5011`](error-catalog.md#ce5011) | error | A method outside the foreign-memory set is called on a `ptr`. Wrap the handle in a struct and extend the struct. |
| [`CE5012`](error-catalog.md#ce5012) | error | A `ptr` appears as a generic type argument outside `Result`/`Maybe` (e.g. `HashMap@(i32, ptr)`, `List@(ptr)`). |
| [`RE2025`](error-catalog.md#re2025) | runtime | A foreign return declared `string` or `ptr` was NULL. Declare it `Maybe@(string)` / `Maybe@(ptr)`. |
| [`CE5015`](error-catalog.md#ce5015) | error | A link name written as a constant is not a string constant. |
| [`CE5016`](error-catalog.md#ce5016) | error | A write to an external variable. It is read-only from Sushi. |
| [`CE5014`](error-catalog.md#ce5014) | error | `errno()` is called in a unit that declares no `unsafe external` block. |
| [`CE5017`](error-catalog.md#ce5017) | error | A name in a `link` clause is empty, holds a `/` or white space, or starts with `-`. |
| [`CE5018`](error-catalog.md#ce5018) | error | A name in a `link` clause is a file name (`libz`, `z.so`). The help gives the library name. |
| [`CE5019`](error-catalog.md#ce5019) | error | One `link` clause names a library two times. |
| [`CE5013`](error-catalog.md#ce5013) | error | A link-name names a symbol this build **defines** -- a function of any unit, a constant, one a linked library brought in, or one the standard library generates. FFI names foreign symbols only. The note says where the symbol is defined. |

## Linking: what can actually be resolved

The `= "symbol"` string is the **link name**. It becomes an undefined external
symbol reference in the generated object file; the linker must satisfy it.

Sushi links binaries with the C compiler driver: `cc <objects> -o prog`, then
`-L<dir>` for each `-L` directory, then `-l<name>` for each library of the
program, then the libraries of the C runtime. The C compiler driver links the C
runtime into every executable (libc or libSystem, and the startup objects), and
every Sushi binary calls libc (`malloc`, `free`, `printf`, `fopen` and others).

On Linux, libm is part of the C runtime of Sushi, and every program links it
(`-lm`): LLVM lowers a float `%` to a call of `fmod`, and glibc keeps `fmod` in
libm. On macOS, libSystem holds libm.

The namespace in `as libc` is a **pure Sushi-side label** and drives nothing at
link time. It names no library: `as banana` links identically. The `link`
clause names the libraries.

A symbol that is not in the C runtime and not in a library of a `link` clause
compiles, but the link fails with [`CE3008`](error-catalog.md#ce3008).

## Linking an external library

A `link` clause on the block names the C libraries that its declarations need.
The block that needs the symbols names the library:

```sushi
unsafe external "C" link "z" as zlib because "CRC-32 from zlib":
    fn crc32(u64 crc, string buf, u32 len) u64 = "crc32"

fn checksum(string s) u64:
    return zlib.crc32(0, s, s.len() as u32)

fn main() i32:
    println(checksum("Mostly Harmless"))
    return 0
```

- **A name is the name for `-l`.** `link "z"` gives `-lz`, and the linker finds
  `libz.so`, `libz.dylib` or `libz.a`. A name is not empty, holds no `/` and no
  white space, and does not start with `-` ([`CE5017`](error-catalog.md#ce5017)). A
  name has no `lib` prefix and no `.so`, `.dylib` or `.a` suffix
  ([`CE5018`](error-catalog.md#ce5018)): write `link "z"`, not `link "libz.so"`.
- **One clause names a library one time** ([`CE5019`](error-catalog.md#ce5019)). Two
  blocks can name the same library. The compiler collects the libraries of every
  block of the program, and gives each one to the linker one time, in the order
  of its first appearance. The order matters to GNU ld for a static library.
- **`sushic -L <dir>`** adds a directory where the linker looks for a library. The
  flag is repeatable. A relative directory is relative to the directory where you
  run `sushic`. For example, Homebrew OpenSSL on macOS:
  `./sushic app.sushi -L /opt/homebrew/opt/openssl@3/lib`.
- **A library that the linker cannot find** is [`CE3008`](error-catalog.md#ce3008).
  The output of `cc` is a note, and each library of the program gets a note at the
  `link` clause that asked for it. A note also gives the `-L` directories.
- **A library (`.slib`).** The consumer of a library compiles its blocks, so it
  collects their clauses as the clauses of its own units.

pkg-config, a C struct by value, a C callback and rpath are not available yet.

## Scope and limitations

- Only the `"C"` ABI is accepted.
- A symbol resolves when it is in the C runtime (libc/libSystem, plus libm on
  Linux) or in a library of a `link` clause. See
  [Linking an external library](#linking-an-external-library) above.
- Sushi stays **null-free**: no `null` literal. A pointer that C may answer NULL
  for is a `Maybe@(string)` or a `Maybe@(ptr)` at the boundary.
- String marshalling is a per-call copy, freed directly after the call. The frees and
  the conversion of the answer keep `errno`.
- No errno/sentinel auto-mapping into `Result`: an extern answers the raw value, and
  a wrapper reads `errno()` and builds the error by hand. The `= "symbol"` suffix
  reserves room for an optional future error-convention annotation.
- No reverse FFI (exporting Sushi functions to C) and no callbacks into C.
- Externals and foreign `ptr` cannot appear in a library public API ([`CE5002`](error-catalog.md#ce5002)).
- The namespace of an `unsafe external` block binds only in the unit that
  declares it. Another unit that names it gets [`CE1001`](error-catalog.md#ce1001). To use a foreign
  function from two units, declare it in each unit, or export a Sushi wrapper.
- A user type named `ptr` is shadowed by the reserved `ptr` type in type
  position; avoid `ptr` as a user type name.

## Worked example

See [examples/28-ffi.sushi](examples/28-ffi.sushi) for the runnable `strlen`
example used above.
[examples/31-ffi-link.sushi](examples/31-ffi-link.sushi) links zlib with a `link`
clause. The [language guide](language-guide.md#calling-c-libraries) teaches the `link`
clause step by step.
