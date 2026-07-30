# Ownership Conventions: One Authority for Every Consuming Use

*Design doc, 2026-07-30. Status: **proposed**. Supersedes the ad-hoc "ownership sink" handling
described in `docs/design/move-semantics.md` §3, which stays accurate as a statement of the
intended rule and inaccurate as a description of the implementation.*

---

## 1. The problem

`docs/design/move-semantics.md` §3 states a rule: **at a position that takes ownership, a bare
owned value moves; a value read through a still-live owner is copied; a fresh temporary is stored
as-is.** There is no function implementing that rule. Every position re-derives it, and no two
derivations agree.

Measured on `f2523db1`:

- **Eleven positions take ownership. Three implement the rule.** The other eight implement a
  fragment. Ten of eleven are wrong for at least one source shape.
- **Four spellings of "reads through a live owner"**: `expression_reads_continuing_owner`
  (= `MemberAccess or Own.get()`), a raw `isinstance(..., MemberAccess)` at
  `backend/statements/variables.py:395`, `isinstance(..., (Name, MemberAccess))` at
  `backend/types/arrays/utils.py:46`, and nothing at all in `backend/statements/returns.py`. The
  two raw ones silently omit the `Own.get()` half that #256 added.
- **Two spellings of "is this an owner"**: `MemoryManager.is_owned_local`
  (`backend/memory/scopes.py:643`) and `move_owning_arg_into_container`
  (`backend/expressions/memory.py:522`), which re-implements the same registry union by hand,
  **omits `_closure_cleanup`, and has no copy branch at all** — so a borrowed source is neither
  moved nor copied, it is aliased. Every defective position is one that called this half-helper.
- **Two ownership predicates**: `is_owning_type` (base cases only) and `type_moves_by_value`
  (transitive), which disagree for owning structs and enums, `Maybe@(Own@(T))`, fixed arrays of
  owning elements, and `HashMap@(K, V)`. Closure capture uses the narrow one; everything else uses
  the wide one.
- **The decision is never a value.** Each position fuses deciding with emitting, so the eleven
  cannot be compared except by reading them side by side, and none can be unit-tested.

The consequence is a bug that has been fixed four times at four different positions — #238, #250,
#256, #277 — and is still live at eight more. A fifth point fix is not a plan.

## 2. Vocabulary

The concept is ownership; the industry already names it. Adopting the established terms rather than
inventing new ones:

| language | the position | the classification |
|---|---|---|
| Swift (SIL) | a **consuming use** (lifetime-ending use); operand is `@owned` vs `@guaranteed` | **ownership convention**; surface `consuming` / `borrowing`, the `consume` operator |
| Rust | a **place** a value is moved into; rustc models moves out of move paths | ownership + move semantics |
| Hylo | `sink` — a parameter-passing keyword beside `let`, `inout`, `set` | passing conventions |
| Mojo | an `owned` parameter; the `^` transfer operator | `owned` / `borrowed` / `inout` |
| C++ | a "sink argument" (Sutter/Meyers idiom) | move semantics |

Sushi adopts Swift's decomposition, which is the one that survives the case that breaks the others:

> A **consuming use** is a position that requires ownership of a value.
> The **ownership convention** at that use is how a given source satisfies the requirement.

The distinction matters. At a `COPY` the source is *not* consumed — but the use is still a consuming
use, because the position requires ownership and copying is how it is satisfied. Words like
*transfer*, *handoff* and *move* are all false in that case; *consuming use* is not. This is exactly
what SILGen does when an `@owned` operand is fed from a `@guaranteed` value.

**"Sink" is deliberately retired.** It is a genuine term of art — a Hylo keyword and a C++ idiom —
but `backend/llvm_optimization.py:241` already uses `add_sinking_pass()` in LLVM's unrelated sense
(moving instructions down the CFG), in the same package. Ten of the thirty-two existing uses already
write "ownership sink" rather than "sink", which is the codebase compensating for a word that does
not carry its own meaning.

## 3. The two enumerations

### 3.1 `ConsumingUse` — where (a closed set)

```python
class ConsumingUse(Enum):
    CALL_ARG          # f(x), including struct/enum constructor calls and indirect calls
    LET               # let T x = <source>
    REBIND            # x := <source>
    FIELD_ASSIGN      # obj.field := <source>
    STRUCT_FIELD      # S(field: <source>)
    ENUM_PAYLOAD      # E.Variant(<source>), incl. Result.Ok / Maybe.Some
    ARRAY_ELEMENT     # from([<source>, ...]) and [<source>, ...]
    CONTAINER_INSERT  # List.push/.insert, HashMap.insert (key AND value), T[].push
    RETURN            # return Result.Ok(<source>)
    CAPTURE           # a lambda's captured environment slot
    OWN_ALLOC         # Own.alloc(<source>)
```

**Closedness is the property that fixes the recurring bug, not the naming.** Today nobody can answer
"what are all the positions?" — #250's triage said two, its own fix found five, #277 says one, the
2026-07-30 audit found eleven. The set is rediscovered empirically each time. An enum makes it
impossible to add a twelfth without declaring it, and makes coverage assertable.

`RETURN` is a genuine special case and must stay a distinct variant: a returned value is emitted
*before* scope cleanup runs, which is why `return Result.Ok(w.items)` handed the caller an
already-freed buffer in #256. It is not merely `LET` at a different address.

### 3.2 `Ownership` — how the source satisfies it

```python
class Ownership(Enum):
    MOVE   # the source owned it; mark the source moved, store as-is
    COPY   # the source keeps living and keeps owning; store an independent deep copy
    ADOPT  # nothing owned it; store as-is
```

## 4. The classification rule

Two inputs: the **type class** of `T`, and the **provenance** of the source expression.

### 4.1 Type class

| class | definition | examples |
|---|---|---|
| **PLAIN** | owns no heap | `i32`, `bool`, `f64`, a struct of only these |
| **COPY** | owns heap, but policy is to duplicate rather than transfer | `string`; string-only and plain+string composites |
| **MOVE** | `type_moves_by_value(T)` — transitively contains `T[]`, `List@(T)`, `Own@(T)`, `HashMap@(K,V)` or a capturing closure | `i32[]`, `struct W { i32[] }`, `Maybe@(Own@(T))`, `Buffer[2]` |

The COPY tier exists because a `string` is a fat pointer with a runtime `owned` bit, and Sushi
deliberately keeps it a copy type (`docs/design/string-representation.md`). It is Rust's `Copy`
tier, derived structurally rather than opted into.

### 4.2 Source provenance

| provenance | meaning | expression shapes |
|---|---|---|
| **OWNED** | a registered owner in this scope | a bare `Name` bound by `let` or a by-value parameter |
| **BORROWED** | names storage owned elsewhere, for a shorter lifetime | a `match` payload binding, a `foreach` binding, a `&peek`/`&poke` parameter |
| **THROUGH_OWNER** | reads *through* a still-live owner | `s.field`, `own.get()`, `arr[i]`, `list.get(i)??` |
| **FRESH** | nothing owns it yet | a constructor, a call result, `.clone()`, a literal |

### 4.3 The table

|  | PLAIN | COPY | MOVE |
|---|---|---|---|
| OWNED | ADOPT | COPY | **MOVE** |
| BORROWED | ADOPT | COPY | **COPY** |
| THROUGH_OWNER | ADOPT | COPY | **COPY** |
| FRESH | ADOPT | ADOPT | ADOPT |

The single cell that every shipped bug in this family got wrong is **(BORROWED, MOVE)**. #238 fixed
it at three positions, #250 at five, #256 at six, #277 reports it at one more; it is currently wrong
at eight. The table also makes the omission that caused #250 unmissable: `THROUGH_OWNER × MOVE`
must copy whether the field's type *is* the resource (`i32[]`) or merely *contains* one.

> **Open question — resolve before implementing.** The `(BORROWED, MOVE)` cell above says COPY,
> which is the safe, implementable-today answer. If Sushi instead decides a pattern binding is a
> **true borrow**, that cell becomes a diagnostic ("cannot consume a borrowed binding", the analogue
> of Rust's "cannot move out of"), which needs `let`-borrow bindings (#252) to be ergonomic. See
> §8. That is one cell's worth of difference in this design and roughly eight tests' worth of
> difference in what "green" means.

## 5. The seam

Every consuming use routes through exactly one function:

```python
def consume(codegen, source: Expr, value: ir.Value,
            target_type: Type, use: ConsumingUse) -> ir.Value:
    """The only way a value may be given to a new owner.

    Reads the Ownership decision stamped by semantics, performs it, and returns the
    value the caller should store. Raises an internal error if no decision was stamped.
    """
```

### 5.1 The decision is computed in semantics, not in the backend

This is what makes it a *single* authority rather than a backend-local one.

The backend currently asks `is_owned_local` — "is this name registered for cleanup?" — as a proxy
for "is this a borrow of something still live?". Those coincide for a `let` local and a fresh
temporary, and diverge for exactly one thing: a pattern binding. The backend has LLVM values and
cleanup registries; it does not reliably have provenance. Semantics has the AST, the types, the
scopes and `borrow_state` — it *knows* a match binding is a binding.

So `Ownership` is computed in Pass 2/3 and **stamped on the AST node**. Two consumers read it:

- the **borrow checker**, to decide whether to mark the source moved (and therefore whether a later
  use is CE2405);
- the **backend**, to decide what to emit.

They cannot drift, because there is only one computation. This is the same transformation Tier 4.6
applied to try-expression types: the backend stopped re-inferring and started reading the
annotations Pass 2 stamps, with **CE0124** as the loud failure for a missing one. A missing
`Ownership` annotation gets the same treatment — a new internal code, not a silent fallback.

That single computation also closes two defects that no amount of backend tidying reaches:
`l.push(a)` followed by a use of `a` compiles clean today (the backend moves, semantics never
marks), and a `foreach` binding used once by value is rejected with a CE2405 whose "moved here"
note points at the *same span as the use* (semantics registers it as an owned local, the backend
creates it as a borrow).

### 5.2 Nothing may bypass the seam

A shared helper that callers may decline to call is not an authority. The ownership-transferring
primitives become private to the seam module:

| primitive | today | after |
|---|---|---|
| `MemoryManager.mark_struct_as_moved` | called from 6+ modules | seam only |
| `DynamicArrayManager.mark_as_moved` | called from 3+ modules | seam only |
| `emit_value_clone` | called from 8+ modules | seam only |
| `deep_copy_struct` | 1 external caller, one-line delegate | **deleted** |
| `clone_owning_source` | 1 external caller | **deleted** |
| `deep_copy_if_owning_struct` | 2 callers | **deleted** |
| `move_owning_arg_into_container` | 5 callers, no copy branch | **deleted** |

Enforced by a CI gate that fails when any backend module outside the seam references them. This is
not a new mechanism: `tests/unit/test_borrow_dispatch_is_total.py` already pins the borrow checker's
`Expr` dispatch against the AST union, with **CE0125** as the runtime backstop, and it is the one
guard in this repo that has actually held. `tests/unit/test_consuming_use_coverage.py` is its twin.

## 6. Collapsing the predicate

With `CAPTURE` routed through the seam, `is_owning_type` has no remaining consumer that wants the
narrow answer. Its four call sites — `backend/runtime/closures.py:149` and `:212`,
`semantics/passes/borrow.py:653`, `semantics/passes/types/visitor.py:439` — all become seam calls or
`type_moves_by_value`. **Delete `is_owning_type`.**

Two consequences worth stating:

- The escaping-closure use-after-free disappears by construction: the env destructor's field set and
  the capture's move decision stop being able to disagree.
- `HashMap@(K, V)` stops moving *by accident*. It currently moves only because
  `semantics/generics/hashmap.py:664` declares `buckets` as a placeholder `i32[]` that the file's own
  comment disowns as not the real layout. Under one predicate, `HashMap` is named explicitly and the
  placeholder stops being load-bearing.

`docs/design/move-semantics.md:198-201` required an audit of every `is_owning_type` call site before
the two-predicate split shipped, and predicted its result ("expected: yes — they are all
move/ownership sites"). The audit was not performed. This is that audit, with the opposite finding.

## 7. Pairing COPY with destruction

A separate axis, and the seam only fixes half of it. The seam decides *whether* to copy;
`emit_value_clone` decides *how*, and it has drifted from its inverse:

- `backend/expressions/memory.py:586` dispatches on `(DynamicArrayType, StructType, EnumType)`. The
  module does not import `ArrayType` at all. `backend/destructors.py:74` includes it.
- `_clone_type_key` (`memory.py:592`) likewise lacks the `ArrayType` arm that `_dtor_type_key`
  (`destructors.py:103`) has, so every fixed-array type would collapse to one shared `linkonce_odr`
  clone body. **Adding the first without the second replaces a double free with a miscompile.**
- `emit_value_clone`'s own docstring calls it the "exact structural inverse" of
  `emit_value_destructor` and warns that cloning fewer buffers is a double free. It is a promise in
  prose.

Make it structural: one handler per type kind, supplying `emit_clone` and `emit_destroy` **from the
same object**, registered exactly like the existing `register_clone_emitter_factory` /
`register_hash_emitter_factory` inversion in `sushi_stdlib/src/common.py` — which is the one piece of
this subsystem that is already right, and keeps `grep -rn "sushi_lang\.backend" sushi_lang/semantics/`
empty. A missing clone arm then becomes a missing method on a handler, not a silently-skipped
`isinstance` branch.

This also collapses the 8 independent `isinstance` type-kind ladders and the 13 sites that spell
`("Own<", "List<", "HashMap<")` by hand, of which `CONTAINER_PREFIXES` currently serves 2.

## 8. The open decision

Everything above is settled except one cell, and it is a language question, not an implementation
one: **what is a `match` / `foreach` binding?**

Today it is none of the three coherent answers. The borrow checker models it as an untyped local
(`BorrowState(name=binding)` with no `var_type`, so it can never be marked moved and this bug class
is undiagnosable in principle). Codegen creates it as a non-owning borrow
(`register_cleanup=False`). Eight consuming uses read "absent from every cleanup registry" as "free
to take". It is in fact a shallow byte-copy of an owner's fat pointer — which is why mutation
through it is silently discarded (#253), re-wrapping it double-frees (#277), and shadowing through
it reads the wrong field index (#279).

| | (A) true borrow | (B) owned copy | (C) binding modes |
|---|---|---|---|
| mutation through it | writes land — fixes #253 | legal and locally visible | per mode |
| `(BORROWED, MOVE)` cell | **diagnostic** | **COPY** | per mode |
| cost | zero-copy | a deep copy per arm entry | zero |
| blocked on | `let`-borrow bindings (#252) | nothing | pattern-mode inference |

(A) is the honest endpoint and the one every current document already claims. (B) is implementable
today and makes the whole matrix correct immediately. The recommendation is **(A) staged, with (B)
as the interim**: ship the table in §4.3 as written, then tighten `(BORROWED, MOVE)` to a diagnostic
once `let`-borrow bindings land. Both are semantics-tightening only — no working program changes
meaning — so the interim costs nothing but a deep copy.

## 9. Risks

**Annotation completeness becomes a new failure mode.** `Ownership` must survive Pass 1.6
monomorphization and Pass 1.7 transformation. There is precedent (`resolved_enum_type`,
`resolved_scrutinee_type`) and there is precedent for it going wrong: **CE0121** exists because a
missed `resolved_scrutinee_type` silently dropped match-arm bindings. Expect latent gaps to surface
as internal errors during rollout. That is the correct direction — loud beats silent — but it will
not be quiet.

**`RETURN` and `CAPTURE` are the two variants most likely to need per-use behaviour** beyond the
table: the first because it emits before scope cleanup, the second because the environment is
heap-allocated and type-erased through `drop_ptr`. Both should be implemented last, after the
straightforward nine confirm the shape.

**The seam must not become a second `is_owned_local`.** Its input is provenance computed in
semantics, not a registry lookup performed in the backend. If it ends up consulting cleanup
registries to decide, the split has been reintroduced inside the seam.
