# Unit namespaces

**Status: DRAFT.** This document measures a problem and surveys the answers. It rules on
nothing. It exists because the visibility work (`docs/design/visibility.md`) kept arriving
at the same wall from different directions, and the wall is not a visibility rule.

The question: Sushi has one flat global namespace. `use` brings a whole unit into it and
gives you no way to say which unit you meant. What should replace that, and what does the
replacement cost?

Read `docs/design/type-identity.md` first. It is normative, and section 4.2 below is a
request to change it.

## 1. The problem, measured

| Case | Today |
|---|---|
| `use "math" as my_math` | `CE6001: unexpected token 'as'`. The rule is `use_stmt: USE (stdlib_import \| lib_import \| user_import)` (`sushi_lang/grammar.lark:7`) — no alias clause exists |
| Two libraries, each exporting `public fn sine` | `CE3003: duplicate public symbol 'sine' found in units: lib/liba/liba, lib/libb/libb` (`sushi_lang/semantics/units.py:229`). **The program cannot be compiled at all**, and no escape exists |
| Two units, each declaring `const i32 LIMIT` | The same `CE3003` |
| Two units, each declaring `struct Point` | `CE0004: duplicate struct 'Point'` (`sushi_lang/semantics/passes/collect/structs.py:102`) |

The third and fourth rows are not aliasing problems. `StructType` compares and hashes on
its name alone (`sushi_lang/semantics/typesys.py:75`), so one name **is** one type for the
whole program. There is nothing to alias: it is the identity that would have to change.

Privacy does not help. A private declaration still occupies the one namespace — that is
`visibility.md` section 1's deciding fact — so making a type private frees no name.

## 2. Sushi already has a namespace, for one kind of symbol

The FFI boundary solved this problem years ago:

```sushi
unsafe external "C" as libc because "the platform's own printf":
    fn printf(string fmt) i32 = "printf"
```

`libc.printf(...)` then resolves through a real namespace. The machinery is small and it is
already the shape a unit namespace needs:

| Piece | Where |
|---|---|
| `ExternalTable.by_namespace: Dict[str, Dict[str, ExternalSig]]`, with `is_namespace(ns)` and `lookup(ns, name)` | `sushi_lang/semantics/passes/collect/externals.py:31` |
| `_resolve_external_call` — a `DotCall` whose receiver is a `Name` that is a registered namespace **and not a local variable** | `sushi_lang/semantics/passes/types/__init__.py:129` |
| The grammar's `AS NAME` clause | `sushi_lang/grammar.lark:29` |

Two properties are worth naming, because a unit namespace would want both. A local
variable shadows a namespace, so `libc` as a variable name is not stolen by the FFI block.
And the namespace is bound by the *declaration*, not derived from a file name, so the
author chooses the word.

What it does not do: it namespaces **functions only**, it is per-block rather than
per-unit, and no type ever crosses it — a `ptr` is opaque by design (`docs/ffi.md`).

## 3. How other languages answer it

| Model | Languages | Mechanism |
|---|---|---|
| Always qualified; no flat namespace | Go (`math.Sin`, aliased `import m "math"`), Zig (`const std = @import("std")` — an import is a **value**), Swift, OCaml | The module's name is always available as a prefix, and unqualified use is not offered |
| Flat by default; selective import | Rust (`use foo::bar as baz`), Python (`import math as m`, `from math import sin`), C++ (`namespace`, `namespace f = a::b`), Java (a collision forces full qualification) | A path exists; the author chooses what to pull into scope |
| No answer; prefixes by convention | C — hence `png_read_info`, `SDL_Init` | None |

Sushi is in the third row.

The dividing fact is not syntax. Every language in the first two rows has **a name that is
not the symbol** — a module path, a package, a crate. Sushi's `use "math"` names a *file*,
and after the import that file's name reaches nothing: it is not on the AST
(`Program` carries no unit name), not on a type, and not in a mangled symbol
(`sushi_lang/backend/functions/declarations.py:40` emits `name=fn.name`, unqualified).

Zig is the closest fit to Sushi's temperament — an import binds a name, access is always
dotted, and there is no global scope to pollute. Rust is the closest fit to Sushi's
current shape, because unqualified use already works and would keep working.

## 4. The two halves, and only one of them is cheap

### 4.1 Functions and constants — tractable

`use "math" as my_math` binds a namespace. A `UnitTable.by_namespace` twin of
`ExternalTable.by_namespace` resolves `my_math.sin()` through the same `DotCall` seam that
already serves `libc.printf()`. An unqualified `sin()` keeps working while it is
unambiguous, and becomes an error naming both candidates when it is not. That is backward
compatible: every program that compiles today still compiles.

The one genuinely new piece is in the back end. Symbol names are flat and unqualified, so
two units each declaring `sin` would collide at link time. They need mangling by unit —
machinery that exists for generics (`semantics/generics/name_mangling.py`) but has never
been applied to a plain function.

### 4.2 Types — a change to type identity

`my_math.Vec` and `other.Vec` would be two types with one name, and nominal identity
forbids it. Making it work means the interned name becomes qualified — `math::Vec` rather
than `Vec` — which reaches:

- `StructType.__hash__` / `__eq__` and the enum twins (`semantics/typesys.py:75`, `:290`)
- the struct and enum tables, which are keyed by that name
- monomorphized instance names, which are built by string concatenation from it
- every match site that reads an interned name as text, which `CLAUDE.md` records as a
  deliberate internal use of `<...>`
- `display_type`, which must render a qualified name back to something a user wrote

This is a type-identity change, not a visibility one, and `visibility.md` section 8
already says so. It belongs in `docs/design/type-identity.md`, argued there.

### 4.3 Method resolution does not move

A method is found on the receiver's **type** (`docs/design/method-resolution.md`), not
through a scoped bound, so `v.length()` needs no namespace in either half. This is the same
reason `visibility.md` Ruling 2 needs no enforcement code.

## 5. Visibility comes first, and the reason is measured

Private-by-default shrinks the namespace before it is designed. Across the three
library-shaped modules in `sushi_lang/sushi_stdlib/src_sushi/`, the exported
type-and-constant surface goes from 18 names to 5:

| Module | Exported types and constants today | After the flip |
|---|---|---|
| `compression/zlib.sushi` | 13 (1 enum, 4 structs, 8 lookup tables) | 1 (`ZError`) |
| `encoding/msgpack.sushi` | 3 | 2 (`MsgValue`, `MpError`) |
| `toolchain/slib.sushi` | 2 | 2 (both are in public signatures) |

Most collisions available today are between names that were never meant to be API. A
namespace design also needs to know which names are even **in** the namespace, so this
ordering is a dependency and not a convenience.

## 6. Open questions

1. **Which model.** Always-qualified (Go, Zig) or flat-with-selective-import (Rust,
   Python)? Always-qualified is the smaller language and the bigger migration.
2. **Is the alias mandatory or optional?** `use "math"` with no `as` must keep working, so
   what namespace does an unaliased unit get — its file stem, or none?
3. **Does a namespace carry types at all**, or only functions and constants? Answering
   "only functions" ships section 4.1 alone and leaves `CE0004` in place for types.
4. **Selective import.** Is `use "math" as m` enough, or is a name-level form needed?
   `visibility.md` section 4 records that Rust's trait rule is only expressible on top of
   name-level import, so this question also decides whether a sealed perk becomes
   possible.
5. **Symbol mangling by unit.** Always, or only on a collision? Always is simpler and
   changes every symbol name in every object file.
6. **What replaces `CE3003`.** Today it refuses the program. Under a namespace it should
   become a diagnostic only at an ambiguous *use*, naming both candidates.

## 7. What this does not decide

**Anything.** The status line is not decoration. Two things are recorded as facts rather
than proposals: the FFI namespace is the existing precedent, and the type half is a
type-identity question.

**The interim rule.** Until this lands, a name clash with a unit's or a library's private
declaration is refused with a diagnostic that says so, and the shadowing path that used to
half-handle it was removed (`visibility.md`, Ruling 5's decision). Refusing is the only
safe answer in a flat namespace: on the source path a library's own bodies compile in the
same program, so letting a consumer's declaration win would silently change what the
library calls. That is the hazard `CE5007` guards on the binary path.
