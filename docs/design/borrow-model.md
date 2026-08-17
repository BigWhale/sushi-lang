# The Borrow Model: Four Parameter Modes

**Status: DECIDED.** Ruled 2026-08-16.

This document is the normative spec for how a value crosses a call boundary. It supersedes
`docs/design/borrowing.md` sections 1 to 5, and `docs/design/ownership-conventions.md`
section 8.6.

`docs/design/ownership-conventions.md` stays normative for everything else about ownership:
the two type classes, the three provenances, the 3x2 table, and the ten consuming
positions. This document changes only one of those ten — the call argument — and it changes
it from "always a consume" to "whatever the callee declares".

## 1. The rule

**A parameter mode is a property of the declaration. It is not a property of the callee's
implementation.**

Before this ruling the compiler read the convention off the body. Six kinds of callee gave
four different answers, and two of them disagreed with each other:

| callee kind | did the body free its parameters? |
|---|---|
| user function | yes |
| extension or perk method | no |
| user FFI extern | not applicable |
| stdlib, generated IR | no |
| stdlib, written in Sushi | yes |
| `.slib` concrete function | yes |

The last two rows are the same language feature with opposite behaviour, so the meaning of
a stdlib call changed as a module moved from generated IR to Sushi source. The stdlib row
also made the two halves of the compiler disagree: the borrow checker marked every call
argument moved, and the backend did not. That is a false `CE2405` at every stdlib call site
that passes an owning value.

One declared mode per parameter removes the question. Every callee kind reads its modes
from the same place.

## 2. The four modes

```sushi
fn f(string name) ~:          # borrow            -- caller frees; name stays usable
fn f(nom string name) ~:      # consume           -- callee frees; CE2405 after
fn f(peek string name) ~:     # borrow by pointer -- read only; caller frees
fn f(poke string name) ~:     # borrow by pointer -- read/write; caller frees
```

The default mode has no name of its own. It is *a borrow*. When you explain it, say that it
does not pass the value.

| | `string x` | `nom string x` | `peek string x` | `poke string x` |
|---|---|---|---|---|
| what crosses | 16-byte descriptor | 16-byte descriptor | pointer | pointer |
| who frees | caller | **callee** | caller | caller |
| callee may read | yes | yes | yes | yes |
| callee may write through it | no — CE2422 | yes (its own copy) | no — CE2408 | **yes, caller sees it** |
| callee may keep it | no — CE2411 | **yes** | no — CE2411 | no — CE2411 |
| caller may use it after | yes | no — CE2405 | yes | yes |
| how many at once | many | one | many | one, exclusive |

`peek` and `poke` keep their meaning from `docs/design/borrowing.md`. They lose the `&`.
The `&` was the only overlap between the borrow vocabulary and bitwise-and, and a borrow
mode is not an address-of operator.

## 3. Mark a marked mode at both ends

A marked mode is written at the declaration **and** at the call site. The default mode is
unmarked at both ends.

```sushi
f(name)                       # borrow
f(nom name)                   # consume -- you can see it at the call site
f(peek name)                  # borrow by pointer, read only
f(poke name)                  # borrow by pointer, read/write
```

The symmetry gives two things:

- **A consume stays visible at the call site.** Without the marker, `f(s)` would not show
  whether `s` survived the call. A reader would have to open the callee.
- **Stdlib and library call sites do not change.** `chdir(p)` already passes the default
  mode.

A mismatch between the two ends is a diagnostic, not a coercion:

- a `nom` marker at a borrow parameter, or a missing `nom` marker at a `nom` parameter, is
  **CE2427**;
- a missing or wrong `peek` / `poke` marker is **CE2006**, as it is today — a reference
  parameter has a `ReferenceType`, so the mismatch is already an argument type mismatch.

`poke T` coerces to `peek T` at a call site, and nowhere else (section 7).

## 4. The representation does not change

This is what makes the flip cheap. A by-value `string` parameter lowers to `{i8*, i32, i8}`
and is passed into a fresh alloca. **The 16-byte descriptor is copied. The `data` pointer
aliases the caller's buffer.** Two descriptors, one buffer.

```
caller  s     { data = 0x1000, size = 8, owned = 1 }
                       |
                       +------> heap: "Trillian\0"   <-- ONE buffer
                       |
callee  name  { data = 0x1000, size = 8, owned = 1 }
```

The same shape holds for `T[]`, `List@(T)`, `HashMap@(K, V)`, `Own@(T)` and a closure
value. Only `T[N]` and a large plain struct copy real content, which is where borrow by
pointer earns its keep.

The ABI of a borrow and of a `nom` is therefore identical. Three things differ, and all
three are bookkeeping:

1. A borrow clears the `owned` bit in the callee's copy, so the callee's destructor is a
   runtime no-op. A `nom` keeps the bit, because the callee is the one owner.
2. A borrow is not registered for cleanup in `begin_function`. A `nom` is.
3. A borrow does not mark the caller's binding moved. A `nom` does.

**This is why the bug class is invisible.** Both descriptors look valid whichever side
frees. The damage appears later, and somewhere else.

## 5. What each callee kind declares

| callee kind | default for an unmarked parameter | may declare `nom` |
|---|---|---|
| user function | borrow | yes |
| extension or perk method | borrow | yes, except the receiver |
| lambda / closure | borrow | yes |
| stdlib function | borrow | it does not today |
| `.slib` concrete function | borrow | yes; the manifest carries the mode |
| struct or enum constructor | **consume** — a field takes ownership | not written |
| container insert (`List.push`, `HashMap.insert`, `Own.alloc`) | **consume** | not written |
| FFI extern | not applicable — **CE2428** on `nom` | no |

The last three rows are the ones that are not function calls in the surface language, and
they are unchanged by this ruling:

- **A constructor still consumes.** A struct or enum field takes ownership, so
  `Person(name)` moves `name`. The borrow checker sees a constructor and a function call as
  the same `Call` node, so the mode lookup applies to the function call only.
- **A container insert is its own consuming use.** It is not a call argument.
- **FFI is outside the mode system.** A C callee never receives a Sushi value. The compiler
  marshals the string into a fresh `char*` that the caller owns and frees. `nom` on an
  extern parameter has no meaning, and is **CE2428**.

## 6. Where the mode lives

`Param.mode` is derived, never stored twice. Hold this invariant:

> The mode is `PEEK` or `POKE` **if and only if** the parameter's type is a `ReferenceType`
> with that mutability.

One derivation function answers it — `param_mode` in `sushi_lang/semantics/param_modes.py`
— and `tests/unit/test_param_mode_invariant.py` pins it. The AST records only the extra
bit that the type cannot carry (`Param.is_nom`); everything else is read off the type.

`FunctionType.param_modes` carries the same tuple, normalized through the same function, so
a function type built without modes and a function type built with all-default modes are
the same type.

One resolver answers "what are the modes of this callee?" for every kind in the section 5
table. It is modelled on the closed `ConsumingUse` enum: `CalleeKind` is a closed set, and
a member with no row fails a unit test statically. Pass 3 and the backend call the same
resolver, which is what stops the two halves drifting the way they did before this ruling.

## 7. Rules that follow

- **A function type carries the mode, and stays invariant.** `fn(nom string) -> i32` and
  `fn(string) -> i32` are different types, in both directions. Without this, one
  indirection defeats the rule — which is exactly what #335 showed for `peek` and `poke`.
  The `poke` to `peek` coercion is a property of the call-site position, not of the type
  pair, so it does not travel into a stored function type.
- **A perk declares the mode, and the implementation must match it.** This is CE4004, which
  is already the rule for `peek self` and `poke self`.
- **You may return, store and capture a `nom` parameter.** The callee owns it. A borrow
  parameter in any of those positions is CE2411, and the escape is `.clone()`.
- **Generic parameters are uniform.** `fn f@(T)(T x)` borrows for every instantiation. A
  pass-through such as `fn identity@(T)(T x) T` needs `nom T x`. There is no per-
  instantiation mode, because the mode is declared and not inferred.
- **`nom self` is not part of this work.** The receiver stays a borrow. `peek self` and
  `poke self` continue, without the `&`.
- **A variadic `...T` keeps the callee as the owner** of the collected array. The array is
  synthesized by the caller and has no other owner, so it adopts. A consuming variadic
  spelling is deferred and rejected.

## 8. `.clone()`

`.clone()` makes a fresh, independent value. It does not transfer by itself. **The mode of
the parameter decides who frees the result.**

- `f(nom s.clone())` gives the callee an independent copy. The callee may do what it wants
  with it. This is the case that matters.
- `f(s.clone())` at a borrow parameter leaves the caller with the temporary. The scope-temp
  machinery frees it.

Both shapes work with no new code, because a `.clone()` result is FRESH, and FRESH adopts
at every type class.

## 9. Diagnostics

New:

| code | what |
|---|---|
| **CE2427** | the argument's mode marker does not match the parameter's declared mode |
| **CE2428** | `nom` in a position with no consume semantics — an FFI extern parameter |

Re-aimed:

- **CE2405** (use after move) now fires from a call argument only when the parameter is
  `nom`. At a borrow parameter it no longer fires at all, which deletes the false positive
  at every stdlib call site.
- **CE2410** (cannot move `main`'s argv view) is re-aimed at the declaration. Under borrow
  by default, passing `args` to an unmarked parameter is legal and correct; passing it to a
  `nom` parameter is the error.
- **CE2422** (cannot write through a by-value method parameter) becomes the general rule for
  a borrow parameter of any callable, not only of a method.

Unchanged: CE2411, CE2408, CE2421, CE2414, CE2426, CE2412, CE2401, CE2403, CE2407.

## 10. What this makes possible

The immediate reason for the ruling was the stdlib question: "who frees a `string` that a
program gives to a stdlib function?". The answer is now in the signature, so the question
does not have to be re-asked as each stdlib module moves from generated IR to Sushi source.

Two more follow:

- **A library can declare a borrow.** The `.slib` manifest carries the mode as its own
  field, so a consumer sees the same signature the library author wrote. Before this, the
  manifest serialized `peek string` into a type string that the consumer's parser could
  not read back.
- **The default is the safe one.** The mode a careless author gets is the one that cannot
  double-free, and the dangerous one has to be written down at both ends.

## 11. Not designed

- **A consuming variadic** (`nom ...T`). Rejected today.
- **`nom self`.** Rejected today.
- **Lifetimes.** Nothing relates a borrow to the value it names, so a borrow still cannot
  be returned or stored (CE2415, CE2416, CE2417, CE2419).
- **Mode inference at a call site.** The marker is written, never deduced. A deduced marker
  would put the visibility of a consume back inside the callee, which is the property this
  ruling exists to give.
