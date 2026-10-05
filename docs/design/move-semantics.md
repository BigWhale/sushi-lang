# Move-by-Value Unification for Owning Composites

*Design doc. Status: **implemented**. This document states the move-by-value rule. The normative
rules are in `docs/design/ownership-conventions.md` (one ownership predicate, a `string` moves, a
read through an owner borrows) and `docs/design/borrow-model.md` (an unmarked parameter BORROWS,
and only `nom` consumes at a call).
Companion to `docs/memory-management.md` (user-facing rules) and
`docs/design/string-representation.md` (the fat-pointer decision).*

---

## 1. Decision

**Owning user structs and owning enums move by value**, the same as `T[]`, `List@(T)`, `Own@(T)`,
and capturing closures. The rules:

- Every value that *owns a resource* **moves** at an ownership sink: a rebind (`let W b = a`), a
  construction field, an array-literal element, a `return`, a closure capture, a container
  insert, and a `nom` argument (`take(nom a)`). Reusing the source is **[CE2405](../error-catalog.md#ce2405)**
  (use-after-move).
- An UNMARKED call argument is not a sink. It is a **borrow** (`borrow-model.md`): `look(a)`
  lends `a`, the callee frees nothing, and `a` stays usable.
- `.clone()` is the **single, explicit** way to copy an owning value. It is auto-derived for
  structs and enums. It is REFUSED on a type that declares a resource (`Drop`) or holds one:
  [CE2431](../error-catalog.md#ce2431), and the escape is `.share()` where the type offers it.
- Plain-data composites (primitives, and composites of only those) **copy**. A `string` owns
  heap and moves; only a string bound directly from a literal owns nothing and copies. The
  class is derived from a type's SHAPE (plus the `Drop` perk), so there is nothing to opt into
  and no way for a type to lie about what it owns.

**Why.** When a composite copies and a bare container moves, a refactor of `f(list)` into
`f(WrapperStruct)` turns a move into a hidden O(n) deep copy. Sushi makes every copy of an owning
value explicit (`.clone()`), so an owning composite moves like the resource that it holds.

---

## 2. The rule: a type moves iff it owns a resource

> **A type moves by value iff `owns_resource(T, drops)` answers true.**

There are two ways to own a resource:

- **Structural**: `T[]`, `List@(T)`, `HashMap@(K, V)`, `Own@(T)`, `string`, a capturing
  closure, or a struct, enum or fixed array that holds one of these (the predicate recurses over
  fields, variant payloads and elements, with a cycle guard).
- **Declared**: the type implements the `Drop` perk (`fn drop(poke self) ~`), for example `File`.

Everything else is plain and copies. `owns_resource` is in `sushi_lang/semantics/typesys.py`,
and the `drops` argument is required. The backend reads the same fact through `needs_cleanup`
(`backend/destructors.py`), and `tests/unit/test_cleanup_predicates_agree.py` keeps the two in
step. The interned type-identity names keep the `<...>` form (`"List<i32>"`), so a name-prefix
check in the compiler tests `List<`, not `List@(`.

---

## 3. Context-by-context semantics

**Ownership sinks move; a read from a continuing owner borrows**, and a consuming use of that read
is [CE2411](../error-catalog.md#ce2411) (`ownership-conventions.md` §4.2, §8).

| Context | Rule | Notes |
|---|---|---|
| unmarked call argument, bare `Name` | **borrow**; the source stays usable | `borrow-model.md` |
| `nom` call argument (`take(nom a)`) | **move**, [CE2405](../error-catalog.md#ce2405) on reuse | the marker is written at both ends ([CE2427](../error-catalog.md#ce2427)) |
| `let b = a` rebind, bare `Name` | **move**, [CE2405](../error-catalog.md#ce2405) on reuse | |
| construction field value, bare `Name` (`Buffer(data: x)`) | **move** | consumes by POSITION, unmarked |
| array-literal element, bare `Name` (`from([r1, r2])`) | **move** | |
| `return x` of a local | **move** | |
| closure capture | **move** | |
| **`s.field` / `MemberAccess` source** in a sink | **borrow**; a consuming use is [CE2411](../error-catalog.md#ce2411) | `nom s.out` in a `let` or a `return` TAKES the field (`borrow-model.md` §10c) |
| **`own.get()` deref source** in a sink | **borrow**; a consuming use is [CE2411](../error-catalog.md#ce2411) | `get()` reaches through a live owner |
| container get-out (`list.get(i)`, also under `.or_err(nom e)??`, and `arr[i]`) | **borrow**; a consuming use is [CE2411](../error-catalog.md#ce2411) | |
| struct-field read (`let x = s.field`) | **borrow**; the owner is frozen while it lives ([CE2412](../error-catalog.md#ce2412)) | |
| `match` / `foreach` bindings | **borrow**: a write or a rebind through it is [CE2414](../error-catalog.md#ce2414), a consuming use [CE2411](../error-catalog.md#ce2411) | `poke` and `nom` binding modes are the escapes |
| `peek` / `poke` arguments | **borrow** | |
| HashMap/List insert of struct/enum values | **move** | consumes by POSITION |

`.clone()` is the explicit copy in every "borrow" row where the author wants a value of their own.
`let poke T x = <place>` and `let peek T x = <place>` bind a checked pointer into a place
(`borrowing.md` mechanism 3b).

---

## 4. Auto-derived `.clone()` for structs and enums

The explicit escape hatch, so `take(nom buf.clone())` gives the callee a value of its own.

The derive pass registers `hash` and `clone` for every struct and enum through one function,
`derived_method` (`semantics/generics/builtin_methods.py`), into the per-compilation table
`semantics/derived_methods.py`. The backend's one deep-clone entry is `copy_out`
(`backend/ownership.py`), and `emit_value_clone` (`backend/expressions/memory.py`) may be called
only from that seam and from `expressions/memory.py`
(`tests/unit/test_clone_entry_is_copy_out.py`).

`.clone()` is total over types: primitives, `string`, fixed and dynamic arrays, `List@(T)`,
`HashMap@(K, V)`, `Own@(T)` and a function value (the fat pointer's `clone_ptr`, see
`docs/design/closures.md`) all have one; `tests/unit/test_clone_totality.py` is the gate. The one
refusal is by rule: a type that declares a resource (the `Drop` perk, such as `File`) or holds one
is [CE2431](../error-catalog.md#ce2431), because a copy of it would be a second handle. The escape is `.share()` where the type
offers it.

---

## 5. Out of scope

- **Partial moves**: `let x = s.field` does not consume `s`. It borrows (§3).
- **Move-out of containers**: a container's `.get()` never transfers ownership. The reader gets a
  borrow.
- **A `Copy`/`Move` perk or user override**: move-ness stays structural (plus `Drop`) and automatic.
