# String Value Representation

Status: **Decided** (keep the 3-field fat pointer; the size is an `i64`).

## Decision

A `string` value is a **3-field fat pointer**:

```
{ i8* data, i64 size, i8 owned }   // 24 bytes (aligned)
```

| Field | Offset | Type |
|---|---|---|
| `data` | 0 | `i8*` |
| `size` | 8 | `i64` |
| `owned` | 16 | `i8` |
| padding | 17..24 | |

`owned` is a runtime ownership discriminator: `1` = heap buffer (RAII frees at scope
exit), `0` = global-backed literal or borrow (never freed). The destructor is
bit-guarded: `if owned: free(data)`.

The `size` field is an `i64` (ADR 0001, the index type is `i64`). This is the expand step
of that change: the surface `size()` and `len()` still answer an `i32`. A read truncates
the `i64` to the `i32`, and a write zero-extends an `i32` size. A later step changes the
surface type.

We keep this representation. We do **not** switch to packing `owned` into the high bit
of `size` (the 2-field alternative).

### The one seam

`sushi_lang/backend/string_layout.py` is the one definition of the type and of its size
field. The backend type mapper and the stdlib generators both get the type from it.

| Entry | What it does |
|---|---|
| `string_type()` | the LLVM type |
| `string_size(b, s)` | the size of a value, as the surface `i32` |
| `string_size_i64(b, s)` | the size of a value at the stored width, for a byte count |
| `string_size_at(b, p)` | the size of the string at an address, as the surface `i32` |
| `with_string_size(b, s, n)` | a value with a new size (an `i32` size is zero-extended) |
| `string_value(b, data, n, owned)` | a new value |
| `string_constant(data, n, owned)` | a new constant |
| `STRING_BYTES`, `SIZE_OFFSET`, `OWNED_OFFSET` | the ABI size and the byte offsets |

The gate `tests/unit/test_string_size_is_one_seam.py` refuses a raw access to field 1
outside this module, and a second spelling of the type.

## Why a runtime ownership bit at all

String-value RAII has to free heap strings (interpolation, string methods,
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
| String size | 24 B | 16 B |
| `Result@(string, E)`/`Maybe@(string)` enum | 32 B (`{i32, [3 x i64]}`) | 24 B (`[2 x i64]`) |
| `size` reads | clean integer, no masking | **every** read must mask off the ownership bit |
| Failure mode of a missed site | a type mismatch in the IR → **loud crash**, easy to find | a masked *size* read is wrong by 2^63 → **silent corruption** |
| Number of hazard sites | few, all in the one seam | many (`.len()`, `%.*s` precision, comparison, bounds, every method) |

Every enum is `{i32 tag, [K x i64] data}` with the payload at offset 8, so
`Result@(string, StdError)` and `Maybe@(string)` are `{i32, [3 x i64]}`, 32 bytes. The
3-field shape costs one word more than the high-bit shape, here and in each `string[]`
element.

High-bit packing trades a small, closed, **loud** problem for a large, open, **silent**
one. It would have to mask the ownership bit at every one of the dozens of places that
read a string's size; missing one yields a wrong length that corrupts rather than
crashes. The 3-field shape keeps `size` a clean integer, keeps `owned` orthogonal and
inspectable, and is simpler.

## The ABI fallout, and why it does not justify switching

The 24-byte, 3-field shape has two ABI hazards, and both are **guardable**:

- **Manual payload byte-copies.** The enum layout `{i32 tag, [K x i64] data}` puts the
  payload base at an 8-aligned offset, and every payload offset is naturally aligned
  (`TypeSizing.payload_field_offsets` is the one authority), so the `owned` byte and the
  size survive every payload store and load. A generator that copies a string into a
  payload by hand copies `STRING_BYTES`, never a number it spells itself.
- **Manual `mem*` lengths.** The backend uses the `i64`-length `llvm.memcpy`/`memmove`
  intrinsics. A size read as the surface `i32` is zero-extended; `string_size_i64` gives
  the stored `i64` directly. The by-value passing/return of the enum itself is handled
  correctly by LLVM's target ABI lowering; the backend has no manual `sret`/`byval`, so
  there is no separate aggregate-ABI hazard.

Two CI gates prevent a recurrence. `tests/unit/test_mem_intrinsic_is_one_seam.py` refuses
an `llvm.mem*` declaration outside the one seam (`backend/expressions/memory.py`) and any
declaration with an `i32` length. `tests/unit/test_string_size_is_one_seam.py` refuses a
raw read or write of the size field outside `backend/string_layout.py`.

## Consequence

The rules for compiler code touching strings:

- Read and write the size only through `backend/string_layout.py`.
- **Never pass a size as a `mem*` length without the `i64` width**: use
  `string_size_i64`, or zero-extend the `i32` from `string_size`, and use the
  `i64`-length intrinsic.

See `sushi_lang/backend/runtime/strings.py` and the stdlib `declare_memcpy` helper
(`sushi_lang/sushi_stdlib/src/libc_declarations.py`) for the pattern.

## A `string` moves

This section answers a different question from the decision above: whether a `string` *value*
is copied or moved at an ownership sink. See `docs/design/ownership-conventions.md` for the full
model; the short version:

A `string` **moves** by value like every other type that owns heap (`T[]`, `List@(T)`,
`Own@(T)`, `HashMap@(K, V)`, a capturing closure). Passing a `string` local to a `nom` parameter,
rebinding it, or putting it in a constructor field moves it — reusing the source afterward is
**[CE2405](../error-catalog.md#ce2405)**. The one exception: a `string` bound directly from a string literal (`let string s =
"hi"`) owns nothing (it points into `.rodata` with `owned = 0`), so *that binding* classifies as
owning no heap and behaves like a copy — both the original and any number of downstream bindings of
it stay usable, because nothing was ever transferred. This exception is tracked per-**binding**, not
per-**type**: `BuiltinType.STRING` carries no such flag, so a `string` field inside a struct, or a
`string` produced by interpolation, a method call, or a rebind, is an ordinary MOVE with no exception
— see `docs/memory-management.md` for worked examples.

**Why the runtime `owned` bit still matters, given that the compiler tracks ownership statically
for strings too.** It would be tempting to think a compile-time MOVE/PLAIN classification makes the
runtime bit redundant. It does not, for the reason in "Why a runtime ownership bit at all",
above: static tracking is necessarily conservative at every
point it cannot prove ownership statically — a rebind whose value depends on a runtime branch, a
value arriving through a generic type parameter, a `string` read out of a container. In every one of
those cases the compiler's honest answer is "treat this as owning a buffer, to be safe" (the same
default an unstated `FunctionType.captures` takes), and the runtime bit is what makes that
conservative answer **free**: freeing a `string` whose `owned` bit happens to be `0` — a
conservatively-registered borrow, a literal that flowed through a code path the compiler could not
fully track — costs one branch and no `free()` call. Without the bit, "when in doubt, assume owning"
would mean "when in doubt, actually free," which is unsound the moment the doubt is wrong. The bit
is what lets the type system be conservative without being wrong.

## The order operators read bytes

`<`, `>`, `<=` and `>=` on two strings are a byte order, which agrees with Rust and Go.
`emit_string_order` (`backend/runtime/strings.py`) builds one three-way `i32` and then applies
the operator once, so all four operators share a single path:

1. `n` is the smaller of the two sizes.
2. When `n` is zero, no call is made and the byte difference is zero. `memcmp` requires two
   valid pointers even for a length of zero, and an empty string may carry a null `data`.
3. Otherwise the byte difference is `memcmp(lhs.data, rhs.data, n)`, with `n` zero-extended
   from `i32` to `i64` — the standing rule of this document.
4. The answer is the byte difference when it is non-zero, and `lhs.size - rhs.size` otherwise.
   The sizes are read as the surface `i32`, and a size is non-negative, so that subtraction
   cannot overflow.

`memcmp` compares as `unsigned char`. That is what makes the order a byte order rather than a
signed-char order, and UTF-8 keeps code points in numerical order, so the byte order and the
code-point order agree. The length tiebreak in step 4 is what makes a prefix less than the
longer string that starts with it.

The `owned` bit takes no part in a comparison. Both operands are read, never consumed, so a
literal and a heap-backed string compare the same way and an owning temporary is still freed
when its scope ends.

What this order is NOT: a collation. It puts every capital before every lowercase letter, and
it does not normalize, so the two spellings of `é` are neither equal nor adjacent. Equality
(`==`, `!=`) keeps its own faster path, which answers from the two sizes alone when the lengths
differ and never reaches `memcmp`.

## Update (2026-09-30): the bytes in place, and a string that takes a buffer

Two primitives of the representation are in the language now (#1091). `s[i]` reads the
byte `data[i]` after a bounds check against `size`, and `string.from_bytes(nom b)` moves
a `u8[]` descriptor's `data` into the string with `size = len` and `owned = 1`. The
array's capacity is dropped: a string frees `data` and nothing reads past `size`.

A string made this way has no NUL after its last byte, and that is correct for the
representation: every string operation reads `size`, and a C boundary copies through
`emit_to_cstr`, which writes its own NUL. `u8[].to_string()` still copies and still
writes a NUL, which nothing needs. `s[i] := v` is refused ([`CE2113`](../error-catalog.md#ce2113)): a literal's `data`
is in `.rodata`, and a string is immutable in every other position too.

## Update (2026-10-02): `s := s.concat(x)` appends in place

`concat` copies both operands into a new buffer of the exact size. A loop of
`s := s.concat(x)` therefore copied the whole string at every step, and was quadratic: 50k
appends to a 289 KB string copied about 7 GB, which took 240 ms.

The rebind frees the old value right after the copy. So the compiler recognizes the one
form where the receiver is the target, and calls the generated `string_append` instead.
SHIR-to-SLIR lowering makes this peephole:

- When the old value's `owned` bit is 1, the buffer goes to `realloc` and `x` is copied
  to its end. This is the same rule that the destructor uses, so the buffer that the
  general path would free is the buffer that grows. When `x` points into the old buffer,
  it is read at its new address.
- When the bit is 0 (a literal, or the copy that a borrowed parameter holds), the bytes
  are copied, as `concat` does. The result owns its buffer, so the next append grows it.

The target is a local, a unit variable or a `poke` reference. A local that a move may
have emptied (a static move or a drop flag) takes the general path. The representation
does not change: there is no capacity field, so the growth relies on `realloc`, which
extends a large block in place on macOS and on glibc. Other forms (`s := "{s}{x}"`, a
chain of two `concat` calls) still copy; a `string[]` and one `join` is the linear
spelling for them.
