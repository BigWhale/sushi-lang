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
the four guarantees: the borrow checker ignores it, RAII never frees it, it carries no
bounds or null promises, and there is nothing you can *do* with one in Sushi — no
dereference, no arithmetic, nothing. The only meaningful move is to hand it back to another
external function.

Where does one come from? Only from an external call. There is no `null` literal, no cast
produces a `ptr` (`0 as ptr` is a compile error), and Sushi has no uninitialized variables —
so every `ptr` in a running program traces back to a C function that returned it. That
single fact is the wall everything else leans on.

Here is the full life of a handle — born in `malloc`, carried through a safe wrapper's
`Result@(ptr)`, and handed back to `free`:

```sushi
--8<-- "docs/tutorial/examples/16-foreign-pointers/borrow-bytes.sushi"
```

Output:

```
returned to the universe
```

Note what the wrapper buys you: `borrow_bytes` is ordinary Sushi, so its callers get the
error channel back (`Result.Ok`/`Result.Err`, `match`, all of it). `Maybe@(ptr)` works the
same way. But be precise about what it does *not* buy: wrapping a handle in `Result` adds
error handling, **not** RAII or null-checking. The `free` is still your job — guarantee 2
is restored by hand, in `give_back`, or not at all.

!!! note "Holding is the safe half"
    This is the same insight Rust's FFI is built on: *holding* a raw pointer is harmless —
    only creating and using it are dangerous. Sushi gates creation behind `unsafe external`
    and doesn't offer dereferencing at all, so a `ptr` sitting in a variable, a struct
    field, a `Result`, or a plain `ptr[]` array threatens nobody.

## What a `ptr` refuses to do

Because a handle is a token with no inspectable inside, the compiler rejects every
operation that would treat it as a value with behavior:

| You write | The compiler says |
|---|---|
| `a == b` (or `<`, arithmetic, `not`, `~`) | `CE5010` — no comparable identity, no arithmetic |
| `p.hash()` (or any method) | `CE5011` — an opaque handle has no methods |
| `HashMap@(i32, ptr)`, `List@(ptr)`, `MyBox@(ptr)` | `CE5012` — only `Result@(ptr, E)` and `Maybe@(ptr)` carry a `ptr` |
| `println("{p}")` | `CE2035` — no string form |
| `0 as ptr`, `p as i64` | `CE2014` — no forging, no laundering into an integer |

That can feel strict until you ask what the alternative would mean. Two handles comparing
"equal" tells you nothing C didn't already know; a hash of an address is garbage the moment
C reallocates; and a collection of raw handles is a collection of lies about ownership. If
you find yourself wanting any of these, you actually want the next section.

## The wrapper struct: giving a handle a personality

The idiomatic home for a foreign handle is a **struct**. The raw `ptr` rides inside as a
field, and the struct — which is real Sushi and plays by all the rules — is what gets
methods, crosses unit boundaries, and appears in your APIs:

```sushi
--8<-- "docs/tutorial/examples/16-foreign-pointers/towel-struct.sushi"
```

Output:

```
towel surrendered, 42 bytes returned
```

A `Towel` knows things its raw pointer never could — its size, here — and the
`surrender()` extension method gives the handle's cleanup a name and a place. This is the
Rust *newtype* idiom, compiler-encouraged: wrap the foreign thing once, then program
against the wrapper forever.

## Two fences: `public` and the unit gate

Two compile-time rules keep `ptr` boxed into the unsafe realm:

**A `public fn` may not expose `ptr`** — not as a parameter, not as a return type, not
tucked inside `Result@(ptr, E)` (`CE5008`). What a unit exports must be Sushi-shaped:
digested values, or wrapper structs like `Towel`. Struct fields *may* carry a `ptr` across
units — that is the deliberate escape hatch, and it is safe because a `ptr` is inert
outside its home unit (the `libc` namespace it came from isn't even visible there).

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
  dereference, no arithmetic, and no `null` literal anywhere in the language.
- A `ptr` value can **only** be born from an external call — no cast or literal produces
  one — so a program without `unsafe external` blocks cannot have one at runtime.
- **Holding is safe**: variables, private params/returns, `Result@(ptr, E)`, `Maybe@(ptr)`,
  struct fields, and `ptr[]` arrays all work. Wrapping in `Result` restores the error
  channel but **not** RAII — freeing is your job.
- **Doing is forbidden**: no comparisons or arithmetic (`CE5010`), no methods (`CE5011`),
  no generic containers beyond `Result`/`Maybe` (`CE5012`), no interpolation, no casts.
- The **wrapper struct** is the idiom: put the handle in a field, attach extension methods
  to the struct, export the struct.
- Two fences keep FFI legible: `public fn` signatures may not expose `ptr` (`CE5008`), and
  `ptr` may only be named in a unit with an `unsafe external` block (`CE5009`).

The complete reference — marshalling rules, variadic externs, every diagnostic — is the
[FFI guide](../ffi.md). And if a future need for null-checking arises, it will arrive as an
`is_null(ptr)` intrinsic, never as `==` — the Guide is quite firm on this point.
