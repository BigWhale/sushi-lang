# Design: The FFI Memory Features

**Status:** implemented (2026-09-30): #1085, #1086, #1087, #1088, #1089, #1090, #1091.
The user-facing rules are in `docs/ffi.md`; this record says why each feature has the
shape it has, and where its one seam is.

## The problem

The standard library is about 10,000 lines of Python that emit LLVM IR by hand. The
generators exist because the FFI could not express what they do: a byte buffer across the
boundary, a struct that C fills, `errno`, a platform choice, and a C function that answers
NULL. A Sushi program could hold a `ptr` and give it back to C, and nothing more. These
seven features make a `ptr` readable and writable inside the unit that declares the block.
After them, a stdlib unit can be written in Sushi.

The four suspended guarantees of `docs/ffi.md` do not change. Bounds safety is still
suspended for the memory behind a `ptr`, and the features make that explicit at each call.
`CE5008` keeps every `ptr` out of a public signature, and `CE5009` keeps it in a unit that
declares an `unsafe external` block.

## One home for the boundary rule

`sushi_lang/semantics/ffi_boundary.py` says what crosses the C boundary and how. Four
readers ask it, and before this work each one had its own copy: the collector
(`intern_boundary_type`), the `externs` pass (`is_c_abi_type`, `is_c_abi_param`,
`is_c_abi_variable`, `is_c_abi_scalar`), the backend lowering
(`backend/runtime/externs/user_externs.py`) and the CW5001 notes.
`sushi_lang/backend/expressions/calls/foreign.py` is the backend half: `marshal_argument`
takes one argument in, `unmarshal_return` takes a return out, and an external variable
read goes through `unmarshal_return` too, so a C global follows the rule of a return.

## Null is a state of the boundary (#1085)

Sushi has no null value, and this work does not add one, not even in the FFI unit. A
nullable C pointer is a `Maybe@(string)` or a `Maybe@(ptr)` at the top level of a
declared parameter or return.

- **Why a `Maybe` and not `is_null(ptr)`.** An `is_null` makes a null `ptr` a value that
  flows through the program. With a `Maybe` at the call, the null never enters Sushi, and
  the ordinary `match` rules force the program to handle it. The `is_null` that
  `docs/ffi.md` and the CE5010 help promised is withdrawn (ruled 2026-09-29).
- **Why a plain pointer asserts non-null.** A plain `string` or `ptr` return is the
  declaration "C never answers NULL here". The call tests the pointer once, and a NULL is
  `RE2025`. A wrong declaration then fails at the call, and not later in `strlen` or in
  the next C call, where it was a SIGSEGV.
- **Only the two pointers.** `Maybe@(i32)` has no C representation, and a nested `Maybe`
  or a `Result` is not a pointer, so each stays `CE5003`. A `Maybe` in the `...` position
  stays `CE5005`: a trailing variadic argument has no declared type to marshal it by.
- **The Maybe is the interned one.** The collector interns the written
  `Maybe@(ptr)` through `ensure_maybe_type_in_table`, so every reader of the external
  table meets the enum the rest of the program names.

## Foreign memory (#1086)

A `ptr` has one closed set of methods: a load and a store of each integer and float width
at a byte offset, `load_ptr` (a `Maybe@(ptr)`, by the null rule above), `store_ptr`,
`offset` and `to_string`. The set is one table, `semantics/foreign_memory.py`, which the
method family `foreign_ptr` (`passes/types/method_registry.py`) and the backend handler
`try_emit_foreign_ptr_method` both read. Every other method on a `ptr` stays `CE5011`.

- **A byte offset, an `i32`.** A C struct is read at the offsets `<sys/platform>` states.
  The offset is an `i32` position like every index (#870).
- **`align 1`.** A byte offset says nothing about alignment, so every load and store is
  unaligned-safe.
- **No gate of its own.** The methods need a `ptr` receiver, and only a unit with a block
  can name the type (`CE5009`), so the confinement comes with the type.
- **Not a C-layout struct.** A named type with C offsets and alignment is a larger
  feature, and it can be built on these loads and stores later.

A `ptr` PARAMETER was never put in the typecheck pass's variable table, because no body
read its type before these methods. `validate_and_register_parameters` now registers it.

## `errno()` (#1087)

`errno()` is a built-in free function that answers an `i32`. It is on the call path after
a declaration and a stdlib row, so a unit's own `fn errno` wins over it. It is callable
only in a unit that declares an `unsafe external` block (`CE5014`), the same confinement
as `ptr`: only a C call can leave an errno behind.

The platform symbols (`__error` on macOS, `__errno_location` on Linux) live in the
compiler, in `ERRNO_LOCATION_SYMBOLS` (`semantics/externs_manifest.py`). They are
reserved externs of the signature `() -> ptr`, so a user block that declares one with that
signature shares the declaration, and another signature is `CE5001`. The errno NUMBERS
differ per platform and are in `<sys/platform>`. The order rule of the generators applies
to a Sushi wrapper too: read `errno()` directly after the failed call
(`docs/design/stdlib-syscall-layer.md`).

## A byte buffer (#1088)

A `u8[]` parameter (bare, `peek` or `poke`) crosses as the pointer to its first byte, and
the count is a separate C parameter. The rule is a parameter only: C cannot answer a
Sushi array, so a return is `CE5003`, and a C callee cannot own one, so `nom` is `CE2428`
like every `nom` at the boundary (the ticket said CE5003; the existing rule for `nom`
decides).

A C function fills a `poke` buffer in place. The wrapper allocates it with
`from([0; n])` and cuts it to the count with `.truncate(count)`, so no uninitialized byte
is ever readable, and no new intrinsic sets a length. A named array stays the caller's
borrow; a temporary gets an owner at the call and is freed at scope exit. Other element
types and a fixed `u8[N]` stay `CE5003` until a need appears.

## The platform file (#1089)

Sushi has no conditional compilation, and it will not get one for this: an extern in a
dead branch is still declared, and at `--opt none` its call survives and fails to link.
So the compiler selects a FILE. `PLATFORM_SOURCE_MODULES` (`semantics/stdlib_registry.py`)
maps `sys/platform` to `src_sushi/_platform/<os>_<arch>.sushi`, and
`SOURCE_STDLIB_MODULES` holds the host's file, so every other reader sees an ordinary
source module. Another host is `CE3021`.

- **Two files.** `darwin_arm64` and `linux_x86_64`, the two platforms a CI job tests. A
  file for a platform with no CI job has no check against its C library, so macOS
  x86_64 and Linux aarch64 have none (ruled 2026-09-30 and 2026-08-29).
- **Constants only.** A platform file holds `public const` declarations, and every file
  declares the same names of the same types in the same order.
- **The probe makes the values; the fixtures test them.** `tests/platform_probe/probe.c`
  prints a whole file from `offsetof`, `sizeof` and the host's headers. A check against
  the probe is circular: it finds drift and not a wrong probe. So the test is
  `tests/stdlib/platform/`, whose fixtures cause each behaviour a constant names (an
  `open` flag, a clock, an errno, a `stat` field, an `addrinfo` offset, a socket option)
  and compare C's answer with the constant, in the Linux CI and in the macOS CI. The
  probe comparison is by hand (`tests/platform_probe/compare.py`, ruled 2026-09-30), and
  pytest checks only that the files declare the same names.
- **A link name may be a constant.** A symbol can differ per platform (`stat$INODE64` on
  macOS x86_64, which has no file today). So the link name after `=` accepts a string constant, bare or
  behind an alias (`= STAT_SYMBOL`, `= platform.STAT_SYMBOL`). `fold_link_names`
  (`passes/types/externals.py`) folds it in the declaring unit in the `ffi-clash` step,
  where every unit's constants and aliases exist, and before `CE5013` and `CE5001` read
  the name. A constant of another type is `CE5015`, and a name that is no constant is
  `CE1001`.
- **The SIGPIPE pair is two facts.** macOS has both `SO_NOSIGPIPE` and `MSG_NOSIGNAL`
  (0x80000), so a single "send flags" constant would state a policy and not a fact. The
  files state both, with 0 where a platform has none, and the library chooses.

## An external variable (#1090)

`var <type> <name> = "<symbol>"` in a block declares a C global of a number, `bool`,
`ptr` or `Maybe@(ptr)`. A `string` is refused: a `char*` global is a `ptr`, and a
`string` would copy at every read. A read is namespaced (`libc.environ`) and loads the
global at the moment of the read, with the null rule of a return. The collector keeps the
variables beside the functions in the external table (`ExternalTable.variables`), and the
namespace answers a binding of kind `"extern variable"`.

A write (`libc.optind := 1`) is `CE5016`. A write to a C global needs a rule for who else
reads it and when, and it can come later with `poke` semantics.

## The string's own bytes (#1091)

Every string method is a byte loop, an ASCII test or a search. Written over `to_bytes()`
and `to_string()`, each would copy twice. Two primitives remove both copies, and they stay
in the compiler as the string's own:

- **`s[i]`** reads `data[i]` after a bounds check against `size` (`RE2020`). A write is
  `CE2113`: a literal is in `.rodata`, and a string is immutable everywhere.
- **`string.from_bytes(nom b)`** moves the array's `data` into a string with
  `size = len` and `owned = 1`. The grammar now accepts `string` before a dot, as it
  accepts `f64` and `f32`, and the three statics on a primitive type name are one table,
  `PRIMITIVE_STATICS` (`passes/types/calls/dotcall.py`). The static declares its parameter
  `nom`, so the borrow pass applies the ordinary marker rule and spends the array.

A string made by `from_bytes` has no NUL after its last byte. That is correct for the
representation (`docs/design/string-representation.md`): every string operation reads
`size`, and the C boundary copies through `emit_to_cstr`.

An index on anything else was `CE2002` with the text of an assignment (`cannot assign
string to array type`); it is `CE2114` now.

## What is not here

- A C-layout struct declaration.
- A write to a C global.
- An element type other than `u8` in a byte buffer.
- A checked `string.from_bytes` that validates UTF-8.
- The move of any stdlib unit from a Python generator to Sushi. These features are its
  preconditions, and the move is separate work.
