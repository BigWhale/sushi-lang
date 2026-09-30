# 16. Foreign Pointers

[Chapter 14](14-stdlib-ffi-libraries.md) showed you how to call C: declare a function in an
`unsafe external` block, call it through its namespace, wrap the raw result. But it politely
stepped around a question every real C API forces on you: what happens when C hands you a
**pointer**? `malloc` returns one. `fopen` returns one. Practically every interesting C
library communicates through opaque handles. This chapter is about `ptr` — Sushi's type for
exactly that — and about the fence the compiler builds around it.

If you come from Python, `ptr` is roughly what `ctypes.c_void_p` is: a foreign address you
can carry around but not look inside. Java programmers: this is the `MemorySegment` of
Java's modern FFI (or the `long`-typed JNI handle of the old days, done honestly). The
difference is how hard Sushi works to keep it from ever pretending to be a normal value.

## A token, not a value

A `ptr` is an **opaque token**. It is the one type in Sushi that deliberately lives outside
the four guarantees: the borrow checker ignores it, RAII never frees it, and it carries no
bounds promise. You cannot compare it, do arithmetic on it or print it. You can hand it
back to another external function, and, in the unit that declares the `unsafe external`
block, you can read and write the memory behind it at a byte offset
([Reading foreign memory](#reading-foreign-memory) below).

A `ptr` is never null. A plain `ptr` return asserts that C cannot answer NULL, and a NULL
there stops the program at the call with `RE2025`. When C can answer NULL (`fopen` of a
missing file, `getenv` of a missing key), declare the return `Maybe@(ptr)` or
`Maybe@(string)`. The NULL then arrives as `Maybe.None`, and there is nothing left to test.
There is no `is_null`, and there will be none.

Where does one come from? Only from an external call. There is no `null` literal, no cast
produces a `ptr` (`0 as ptr` is a compile error), and Sushi has no uninitialized variables —
so every `ptr` in a running program traces back to a C function that returned it. That
single fact is the wall everything else leans on.

Here is the full life of a handle — born in `malloc`, carried through a safe wrapper's
`Result@(ptr, StdError)`, and handed back to `free`:

```sushi
--8<-- "docs/tutorial/examples/16-foreign-pointers/borrow-bytes.sushi"
```

Output:

```
returned to the universe
```

The wrapper gives you this: `borrow_bytes` is ordinary Sushi, so its callers get the error
channel back (`Result.Ok`/`Result.Err`, `match`, all of it). `Maybe@(ptr)` works the same
way. But a `Result` around a handle adds error handling only, **not** RAII. Here the `free` is
still your job, in `give_back`. The next section shows
how a wrapper struct with `Drop` gives RAII back.

!!! note "Holding is the safe half"
    The insight the quarantine rests on: *holding* a raw pointer is harmless —
    only creating and using it are dangerous. Sushi gates both behind `unsafe external`:
    only an external call creates a `ptr`, and only the unit that declares the block can
    read the memory behind one. A `ptr` in a variable, a struct field, a `Result`, or a
    plain `ptr[]` array threatens nobody.

## Reading foreign memory

C often answers through memory: `clock_gettime` fills a `struct timespec`, and `stat`
fills a `struct stat`. So a `ptr` has a closed set of methods. Each one takes a byte offset,
an `i32`:

| Method | Answers |
|---|---|
| `p.load_i64(off)`, `p.load_u8(off)`, ... (every integer and float width) | the value at `off` |
| `p.store_i64(off, v)`, `p.store_u8(off, v)`, ... | `~`; `v` has exactly that type |
| `p.load_ptr(off)` | `Maybe@(ptr)`; a NULL slot is `Maybe.None` |
| `p.store_ptr(off, q)` | `~` |
| `p.offset(n)` | the `ptr` at `p + n` |
| `p.to_string(off)` | an owned copy of the C string at `off` |

```sushi
--8<-- "docs/tutorial/examples/16-foreign-pointers/read-memory.sushi"
```

Output:

```
42 and 7
Mostly Harmless
Harmless
```

Nothing checks the offset. An offset past the end of the buffer reads memory that the
program does not own, exactly as in C. The offsets of a C struct change with the platform,
so do not write them as numbers: `use <sys/platform>` gives `ST_SIZE_OFFSET`, `STAT_SIZE`,
the `errno` numbers and the other per-platform constants.

The boundary has four more tools, and the [FFI guide](../ffi.md) shows each one:

- **A byte buffer.** A `u8[]` parameter crosses as the pointer to its first byte. Declare it
  `poke u8[]`, and C can fill the array in place (`read`, `recv`).
- **`errno()`.** It answers the `errno` of the last failed C call. Read it directly after the
  call. Only a unit with an `unsafe external` block can call it (`CE5014`).
- **A C global.** `var i32 optind = "optind"` in the block declares a C global variable.
  `libc.optind` reads it, and a write is `CE5016`.
- **A link name that is a constant.** `fn stat(string p, ptr buf) i32 = STAT_SYMBOL` takes
  the symbol from `<sys/platform>`, because a platform can give `stat` another name.

## What a `ptr` refuses to do

Because a handle is a token with no inspectable inside, the compiler rejects every
operation that would treat it as a value with behavior:

| You write | The compiler says |
|---|---|
| `a == b` (or `<`, arithmetic, `not`, `~`) | `CE5010` — no comparable identity, no arithmetic |
| `p.hash()` (any method outside the foreign-memory set) | `CE5011` — a `ptr` has those methods and no others |
| `HashMap@(i32, ptr)`, `List@(ptr)`, `MyBox@(ptr)` | `CE5012` — only `Result@(ptr, E)` and `Maybe@(ptr)` carry a `ptr` |
| `println("{p}")` | `CE2035` — no string form |
| `0 as ptr`, `p as i64` | `CE2014` — no forging, no laundering into an integer |

That can feel strict until you ask what the alternative would mean. Two handles comparing
"equal" tells you nothing C didn't already know; a hash of an address is garbage the moment
C reallocates; and a collection of raw handles is a collection of lies about ownership. If
you find yourself wanting any of these, you actually want the next section.

## The wrapper struct: giving a handle a personality

The idiomatic home for a foreign handle is a **struct**. The raw `ptr` is a field, and the
struct (real Sushi, with all the rules) gets the methods:

```sushi
--8<-- "docs/tutorial/examples/16-foreign-pointers/towel-struct.sushi"
```

Output:

```
towel surrendered, 42 bytes returned
```

A `Towel` knows things that its raw pointer cannot know (here, its size), and the
`surrender()` extension method gives the cleanup a name and a place. This is the *newtype*
idiom: wrap the foreign thing once, then use the wrapper.

`surrender()` is still a manual step. To make the cleanup automatic, implement the `Drop`
perk (chapter 12) on the wrapper. The compiler then calls `drop()` when the `Towel` is
destroyed, on every path out of the scope. This is how `File` and `TcpStream` own their
descriptors.

```sushi
--8<-- "docs/tutorial/examples/16-foreign-pointers/towel-drop.sushi"
```

Output:

```
holding a towel of 42 bytes
towel freed, 42 bytes returned
end of main
```

The `Towel` is a temporary that the `match` owns, so it is destroyed at the end of the
`match`, before `end of main`.

## Two fences: `public` and the unit gate

Two compile-time rules keep `ptr` boxed into the unsafe realm:

**A `public fn` may not expose `ptr`** (`CE5008`): not as a parameter, not as a return
type, not inside `Result@(ptr, E)`, and not inside a struct. A struct with a `ptr` field
(`Towel`) also counts, so `public fn issue(i64 n) Towel` is refused. What a unit exports
must be Sushi-shaped: digested values such as an `i64` or a `string`. Keep the wrapper
struct and the functions that take it in the unit that declares the externals, and export
functions that do the work.

**No danger zone, no `ptr`** — the type name itself may only be spelled in a unit that
declares an `unsafe external` block (`CE5009`). A unit without externals could never
produce a handle anyway, so a `ptr` type written there is dead plumbing, and the compiler
says so. The pleasant side effect: `grep` your codebase for `unsafe external` and you have
found every file that can possibly touch a raw foreign pointer.

!!! note "Why bother, if holding is safe?"
    The fences don't add memory safety — that's already covered by "can't forge, can't
    deref". They add **legibility**. FFI is supposed to be a thin, auditable layer
    (*"FFI is not Sushi"*), and these rules make the layer's edges visible in the source
    instead of in someone's memory of how the code is organized.

## What you learned

- `ptr` is an **opaque token** for C handles: exempt from borrow checking and RAII, with no
  arithmetic and no `null` literal anywhere in the language.
- A NULL from C is a `Maybe@(ptr)` or `Maybe@(string)` at the boundary. A plain `ptr`
  return asserts non-null (`RE2025`), and there is no `is_null`.
- In the unit with the `unsafe external` block, a `ptr` reads and writes foreign memory at
  a byte offset: `load_<width>`, `store_<width>`, `load_ptr`, `store_ptr`, `offset` and
  `to_string`.
- A `ptr` value can **only** be born from an external call — no cast or literal produces
  one — so a program without `unsafe external` blocks cannot have one at runtime.
- **Holding is safe**: variables, private params/returns, `Result@(ptr, E)`, `Maybe@(ptr)`,
  struct fields, and `ptr[]` arrays all work. Wrapping in `Result` restores the error
  channel but **not** RAII.
- **Everything else is forbidden**: no comparisons or arithmetic (`CE5010`), no other
  methods (`CE5011`),
  no generic containers beyond `Result`/`Maybe` (`CE5012`), no interpolation, no casts.
- The **wrapper struct** is the idiom: put the handle in a field, attach extension methods
  to the struct, and implement `Drop` to free the handle automatically.
- Two fences keep FFI legible: `public fn` signatures may not expose `ptr`, also not
  inside a struct (`CE5008`), and
  `ptr` may only be named in a unit with an `unsafe external` block (`CE5009`).

The complete reference — marshalling rules, byte buffers, `errno()`, C globals, variadic
externs, every diagnostic — is the [FFI guide](../ffi.md).
