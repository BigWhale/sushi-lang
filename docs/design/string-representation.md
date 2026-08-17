# String Value Representation

Status: **Decided** (keep the 3-field fat pointer). Supersedes the open question in
issue #152. Related: #145 (string-value RAII), #146 (landed the representation), #149
and #151 (ABI hardening), #147 (struct-field RAII).

## Decision

A `string` value is a **3-field fat pointer**:

```
{ i8* data, i32 size, i8 owned }   // 16 bytes (aligned)
```

`owned` is a runtime ownership discriminator: `1` = heap buffer (RAII frees at scope
exit), `0` = global-backed literal or borrow (never freed). The destructor is
bit-guarded: `if owned: free(data)`.

We keep this representation. We do **not** switch to packing `owned` into the high bit
of `size` (the 2-field / 12-byte alternative).

## Why a runtime ownership bit at all

String-value RAII (#145) has to free heap strings (interpolation, string methods,
`to_string*`, int/float formatting) but must never free a global-backed literal. The
two are the **same `string` type** and flow indistinguishably through generics,
containers, `Result`/`Maybe` payloads, and `nom` parameters. Ownership therefore
cannot be recovered from the static type.

The considered alternative was **compile-time ownership tracking** (mark each binding
as owning/borrowed and thread that through the type system). That is invasive and
fragile: every generic instantiation, container element, and payload slot would have to
carry the owning-or-not fact. A **runtime bit** makes every `string` self-describing, so
RAII, move, and borrow all work uniformly wherever a string flows. This mirrors the
closure fat value's `drop_ptr` slot. The runtime bit won, and that choice is not in
question.

## Why 3-field, not high-bit-packed `owned`

Given a runtime bit, the only sub-choice is where to store it:

| | 3-field `{data, size, owned}` (chosen) | High-bit in `size` |
|---|---|---|
| String size | 16 B | 12 B |
| `Result@(string)`/`Maybe@(string)` enum | 20 B (crosses the x86-64 16-byte SysV boundary) | 16 B (no growth) |
| `size` reads | clean `i32`, no masking | **every** read must mask off the ownership bit |
| Failure mode of a missed site | a memcpy/memmove *length* is wrong → **loud crash**, easy to find | a masked *size* read is wrong by 2^31 → **silent corruption** |
| Number of hazard sites | few (mem* length arguments) | many (`.len()`, `%.*s` precision, comparison, bounds, every method) |
| `size` max | full `i32` | `2^31 - 1` |

High-bit packing trades a small, closed, **loud** problem for a large, open, **silent**
one. It would have to mask the ownership bit at every one of the dozens of places that
read a string's size; missing one yields a length off by 2^31 that corrupts rather than
crashes. The 3-field shape keeps `size` a clean `i32`, keeps `owned` orthogonal and
inspectable, and was simpler to land.

## The ABI fallout, and why it does not justify switching

The 16-byte, 3-field shape produced three ABI bugs, all now fixed:

1. ARM64 undef-register poisoning of the `owned` byte (#146).
2. `Result@(string)`/`Maybe@(string)` payload-size corruption — the enum data array had to
   be sized to preserve `owned@12` (#146).
3. x86-64 out-of-bounds: the 20-byte enum plus passing a string's raw `i32 size`
   (adjacent to `owned` + padding) as a `mem*` length let garbage upper bits reach
   glibc's SIMD routines (#149, then all remaining sites hardened in #151).

These fall into two categories, both closed and both **guardable**:

- **Manual payload byte-copies** — fixed by marking enum-payload store/load `align=1`.
- **Manual `mem*` lengths** — fixed by using the `i64`-length `llvm.memcpy`/`memmove`
  intrinsics with the `i32` size zero-extended (#149/#151). The by-value passing/return
  of the 20-byte enum itself is handled correctly by LLVM's target ABI lowering; the
  backend has no manual `sret`/`byval`, so there is no separate aggregate-ABI hazard.

The residual worry (#152) is recurrence: a *new* site that passes a string's `i32` size
to a `mem*` routine. That is cheaply prevented by a lint/CI check that flags any
`i32`-length `llvm.mem*` intrinsic — a proportionate guardrail, not a reason to rebuild
the string ABI and take on the pervasive masking obligation above.

## Consequence

The rule for compiler code touching strings: **never pass a string's `i32` size field
directly as a `mem*` length — zero-extend to `i64` and use the `i64`-length intrinsic.**
See `sushi_lang/backend/runtime/strings.py` and the stdlib `declare_memcpy` helper
(`sushi_lang/sushi_stdlib/src/libc_declarations.py`) for the pattern.

## Update (Phase 9, 2026-08-14): `string` is no longer a copy type

This document's decision — the 3-field fat pointer, the runtime `owned` bit — is **unchanged**. What
changed is a different question entirely: whether a `string` *value* is copied or moved at an
ownership sink. See `docs/design/ownership-conventions.md` for the full model; the short version:

A `string` now **moves** by value like every other type that owns heap (`T[]`, `List@(T)`,
`Own@(T)`, `HashMap@(K, V)`, a capturing closure). Passing a `string` local to a `nom` parameter,
rebinding it, or putting it in a constructor field moves it — reusing the source afterward is
**CE2405**. The one exception: a `string` bound directly from a string literal (`let string s =
"hi"`) owns nothing (it points into `.rodata` with `owned = 0`), so *that binding* classifies as
owning no heap and behaves like a copy — both the original and any number of downstream bindings of
it stay usable, because nothing was ever transferred. This exception is tracked per-**binding**, not
per-**type**: `BuiltinType.STRING` carries no such flag, so a `string` field inside a struct, or a
`string` produced by interpolation, a method call, or a rebind, is a ordinary MOVE with no exception
— see `docs/memory-management.md` for worked examples.

**Why the runtime `owned` bit still matters, given that the compiler now tracks ownership statically
for strings too.** It would be tempting to think a compile-time MOVE/PLAIN classification makes the
runtime bit redundant. It does not, for the same reason the bit existed in the first place (see
"Why a runtime ownership bit at all", above): static tracking is necessarily conservative at every
point it cannot prove ownership statically — a rebind whose value depends on a runtime branch, a
value arriving through a generic type parameter, a `string` read out of a container. In every one of
those cases the compiler's honest answer is "treat this as owning a buffer, to be safe" (the same
default an unstated `FunctionType.captures` takes), and the runtime bit is what makes that
conservative answer **free**: freeing a `string` whose `owned` bit happens to be `0` — a
conservatively-registered borrow, a literal that flowed through a code path the compiler could not
fully track — costs one branch and no `free()` call. Without the bit, "when in doubt, assume owning"
would mean "when in doubt, actually free," which is unsound the moment the doubt is wrong. The bit
is what lets the type system be conservative without being wrong.
