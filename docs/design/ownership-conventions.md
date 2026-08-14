# Ownership Conventions: One Authority for Every Consuming Use

*Design doc, 2026-07-30. Status: **implemented** (Phase 9, 2026-08-14). Supersedes the ad-hoc
"ownership sink" handling described in `docs/design/move-semantics.md` §3 — that document is now
historical and cross-links back here. The shipped seam lives in `sushi_lang/semantics/ownership.py`
(the rule, `classify()`) and `sushi_lang/backend/ownership.py` (`consume`/`bind`/`copy_out`/
`relinquish`/`relinquish_temp`, the only module allowed to move-mark a value —
`tests/unit/test_consuming_use_coverage.py` is the no-bypass gate).*

*One change from the design as originally written: the **`COPY` type class was deleted**. A
`string` moves like every other heap-owning type; the one exception is a binding initialised
directly from a string literal, which owns nothing and is tracked at the BINDING (not the type)
level — see §4.1. The **`THROUGH_OWNER` provenance was folded into `BORROWED`**: a field read, an
index, and a container get-out classify the same as a `match`/`foreach` binding — see §4.2. Both
simplifications turn the original 4x3 table (§4.3) into a 3x2 one.*

*The one language question this design raised — what a `match`/`foreach` binding is — is settled in
§8: **a read-only borrow**. It is not an open question; §4.3's table is final.*

---

## 1. The problem (historical)

This section describes the state the design fixed, on `f2523db1` (2026-07-30), before the seam
existed. It is kept for the record; §5 describes what shipped.

`docs/design/move-semantics.md` §3 stated a rule: **at a position that takes ownership, a bare
owned value moves; a value read through a still-live owner is copied; a fresh temporary is stored
as-is.** There was no function implementing that rule. Every position re-derived it, and no two
derivations agreed.

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
    ADOPT  # nothing owned it; store as-is
    REJECT # the source may not be consumed at all -- CE2411
```

**Shipped without `COPY`.** The design as originally written had a third answer, `COPY` — "the
source keeps living and keeps owning; store an independent deep copy" — for the one type class
(`string`) that owned heap but was designated to duplicate rather than transfer. Phase 9 deleted it:
a `string` now moves like every other heap-owning type (§4.1), so there is nothing left for `COPY`
to answer. `REJECT` was always implicit in the design (the (BORROWED, MOVE) cell); it is spelled out
here because the shipped enum names it.

## 4. The classification rule

Two inputs: the **type class** of `T`, and the **provenance** of the source expression.

### 4.1 Type class

Two classes, not three:

| class | definition | examples |
|---|---|---|
| **PLAIN** | owns no heap | `i32`, `bool`, `f64`, a struct of only these |
| **MOVE** | `owns_heap(T)` — transitively contains `T[]`, `List@(T)`, `Own@(T)`, `HashMap@(K,V)`, `string`, or a capturing closure | `i32[]`, `struct W { i32[] }`, `Maybe@(Own@(T))`, `Buffer[2]`, `string`, `struct { string name }` |

**Shipped as one predicate, not two.** The design as originally written kept a `COPY` tier for
`string` alongside a `PLAIN`/`MOVE` split, on the theory that unifying the move predicate with the
backend's `needs_cleanup` was unsound (`docs/design/move-semantics.md` §2 argued the two questions
must stay separate). Phase 9 did the opposite: it made `string` move, which makes "does this need
freeing?" and "does this move?" the same question for every type, including `string`. The single
predicate is `owns_heap` (`sushi_lang/semantics/typesys.py`); the backend's `needs_cleanup` is now a
thin alias of it (`sushi_lang/backend/destructors.py`).

**The string exception lives on the BINDING, not the type.** A `string` bound directly from a
string literal (`let string s = "hi"`) owns nothing — it points into `.rodata` with the runtime
`owned` bit clear — so classifying it as PLAIN for *that binding* is exact, not an approximation.
This is "option B": the flag (`BorrowState.owns_no_heap`) is recorded on the binding by the borrow
checker, re-derived on every rebind (never inherited — a rebound string may now own a heap buffer),
and is invisible to `owns_heap`/`type_class_of`, which always answer MOVE for `BuiltinType.STRING`.
It is a binding-level fact because `BuiltinType.STRING` is a bare enum member with nowhere to carry
a per-value flag, unlike `FunctionType`, which is a dataclass and carries `captures` the same way.
One consequence worth stating plainly: **a struct with a string field is a MOVE type**, full stop —
`Named(name: "hi", id: 1)` passed by value moves, even though the string it was built from is a
literal, because the option-B flag is a fact about a *bare `string` binding*, not about a value
nested inside a struct field. See `docs/memory-management.md` for the worked example.

### 4.2 Source provenance

Three, not four:

| provenance | meaning | expression shapes |
|---|---|---|
| **OWNED** | a registered owner in this scope | a bare `Name` bound by `let` or a by-value parameter |
| **BORROWED** | names storage owned elsewhere, for a shorter lifetime | a `match` payload binding, a `foreach` binding, a `&peek`/`&poke` parameter, a `let` bound from any of these, **and every read THROUGH a still-live owner** — `s.field`, `own.get()`, `arr[i]`, `list.get(i)??` |
| **FRESH** | nothing owns it yet | a constructor, a call result, `.clone()`, a literal, `List.pop()` (which REMOVES the element, so the container stops owning it) |

**`THROUGH_OWNER` merged into `BORROWED`.** The design as originally written kept these as separate
provenances because they had different outcomes in the table (BORROWED rejected, THROUGH_OWNER
copied — see §4.3's original text below). Once the compiler stopped inserting an automatic copy at
a read (§8 supplies the escape — an implicit borrowed `let` binding, not a hard error), the two
provenances have identical outcomes at every type class, so they are one case: reading through a
live owner, wherever the read appears, is `BORROWED`.

### 4.3 The table

Shipped as 3x2, not 4x3:

|  | PLAIN | MOVE |
|---|---|---|
| OWNED | ADOPT | **MOVE** |
| BORROWED | ADOPT | **REJECT — CE2411** |
| FRESH | ADOPT | ADOPT |

(`sushi_lang/semantics/ownership.py:_TABLE`, unit-tested cell by cell in
`tests/unit/test_ownership_table.py`.)

The single cell that every shipped bug in this family got wrong is **(BORROWED, MOVE)**. #238 fixed
it at three positions, #250 at five, #256 at six, #277 reported it at one more before the seam
existed. Per §8 it is not a code-generation question at all — it is rejected, with `.clone()` as the
explicit escape.

**Two different consuming uses read `REJECT` two different ways** (§5), and this is what makes the
table stable across the merge in §4.2: `consume()` (a genuine consuming use — a call argument, a
constructor field, a return) turns `REJECT` into the **CE2411** diagnostic; `bind()` (a `let`)
turns the identical `REJECT` into "the binding BORROWS instead of owning" (§8), no diagnostic at
all. A `let x = s.field` and `take(s.field)` see the same table cell and reach opposite surface
behaviour, because a `let` does not require ownership the way a call argument does — see §5's `bind`
vs `consume` split.

### 4.4 The rejected cell

Consuming a borrowed binding or a read-through-owner whose type owns heap is **CE2411**
(`sushi_lang/internals/errors/borrow.py`), rendered as:

```
error [CE2411]: cannot consume 'copied': another owner keeps this value.
  |     sink(copied)
  `          ---+---
  = note: 'copied' borrows here, and the owner keeps the value
    demo.sushi:8:5
    |     let Own@(i32) copied = outer.get()
    `     ^
  = help: clone it to take an independent value: `copied.clone()`
```

It is a **relational** error, so it carries a second location per the tier-3 rule in `CLAUDE.md`:
the use, and the binding site it borrows from (or the owner's declaration, for a direct read like
`h.inner`). Rendering it with one location is a bug.

**Mutating through a binding needs no new code for the `&peek`/`&poke`-parameter case.** A
reference parameter is already read-only/exclusive per the ordinary borrow rules, so a write
through a `&peek` one is **CE2408** ("cannot modify through &peek reference"). A *bound* borrow
(a `let` reading through an owner, §8) gets its own diagnostic instead — **CE2412**, "cannot mutate
the owner while this binding borrows from it" — because the thing being protected is not the
binding's own mutability but the owner changing out from under it.

## 5. The seam

Shipped as `sushi_lang/backend/ownership.py`. Every consuming use routes through one of two entry
points, both built on the shared `classify()` table:

```python
def consume(codegen, source, value: ir.Value,
            target_type: Optional[Type], use: ConsumingUse) -> ir.Value:
    """Give `value` to a new owner, and return what the caller should store.

    Reads the Provenance Pass 3 stamped on `source`, asks classify() what that means for
    `target_type`, and performs the answer: MOVE marks the source moved and returns the
    value as-is; ADOPT returns it as-is; REJECT raises CE0129 (internal -- Pass 3 should
    already have reported CE2411 for the same source before codegen runs).
    """


def bind(codegen, source, value: ir.Value,
         target_type: Optional[Type]) -> tuple[ir.Value, bool]:
    """Bind a `let` to its initializer, and say whether the binding OWNS the value.

    consume() with ONE answer mapped differently: where consume() cannot satisfy REJECT
    and raises, bind() returns (value, False) -- the binding owns nothing, i.e. it
    BORROWS (see S8). Returns (the value to store, whether the caller must register the
    local for cleanup).
    """
```

Two more functions round out the seam: `copy_out` (the ONE place `emit_value_clone` is reached from
— an explicit `.clone()` and a few internal reader positions that need an independent copy) and
`relinquish`/`relinquish_temp` (state that a binding or a compiler-synthesized temporary transferred
ownership, for the two shapes that have no source `Expr` to stamp a decision on). All five are
listed in the module's `__all__`; nothing else in the backend may call the primitives underneath
them (§5.2).

### 5.1 The decision is computed in semantics, not in the backend

This is what makes it a *single* authority rather than a backend-local one.

Before the seam, the backend asked `is_owned_local` — "is this name registered for cleanup?" — as a
proxy for "is this a borrow of something still live?". Those coincide for a `let` local and a fresh
temporary, and diverge for exactly one thing: a pattern binding. The backend has LLVM values and
cleanup registries; it does not reliably have provenance. Semantics has the AST, the types, the
scopes and `borrow_state` — it *knows* a match binding is a binding, which is why provenance had to
be computed there.

**What is shipped stamps `Provenance`, not the final `Ownership` decision** — a half-step short of
what this section originally proposed, and a better split. Pass 3 (the borrow checker) is the only
side that can compute *where a value came from* — it has the AST, the scopes and `borrow_state` — so
it stamps `Provenance` on the source node (`expr.ownership_provenance`). The backend supplies the
other half, the resolved *target type* at its position, and both sides call the identical
`classify(provenance, type_class)` to reach `Ownership`. Reusing one pure function is what makes two
calls agree rather than one stamped value: the borrow checker uses its answer to decide whether to
mark the source moved (and therefore whether a later use is CE2405, or — for a `let` — whether the
binding borrows instead, §8); the backend uses its answer to decide what to emit. If a source somehow
reaches the backend with no `Provenance` stamped, the seam raises **CE0129** (internal) rather than
guessing — this is the same transformation Tier 4.6 applied to try-expression types (CE0124), and it
is the correct direction here for the identical reason: reaching this code path with no stamp means
Pass 3 disagreed with itself.

That single computation closed two defects no amount of backend tidying alone could have reached:
`l.push(a)` followed by a use of `a` used to compile clean (the backend moved, but semantics never
marked the source, so CE2405 never fired), and a `foreach` binding consumed by value used to be
rejected with a CE2405 whose "moved here" note pointed at the *same span as the use* (semantics
registered it as an owned local while the backend created it as a borrow). Both are typed bindings
with a real `Provenance` now (§8.1), so both fire the correct diagnostic at the correct span.

### 5.2 Nothing may bypass the seam

A shared helper that callers may decline to call is not an authority. The ownership-transferring
primitives are private to the seam module (`sushi_lang/backend/ownership.py`'s internal `_mark_moved`
and `_clone`); every other backend module reaches them only through `consume`/`bind`/`copy_out`/
`relinquish`/`relinquish_temp`.

Enforced by a CI gate that fails when any backend module outside the seam references the primitives
directly: `tests/unit/test_consuming_use_coverage.py`. This mirrors an existing, proven mechanism:
`tests/unit/test_borrow_dispatch_is_total.py` pins the borrow checker's `Expr` dispatch against the
AST union, with **CE0125** as the runtime backstop, and it is the guard in this repo that had
already held before this design shipped.

## 6. Collapsing the predicate

Shipped as one function, `owns_heap` (`sushi_lang/semantics/typesys.py`), not two. The design as
originally written proposed keeping `is_owning_type` (narrow, base-cases-only) and
`type_moves_by_value` (transitive) as separate predicates and auditing every `is_owning_type` call
site to see whether it could switch to the wide one. That audit is superseded: Phase 9 merged both
predicates — and the backend's independent `needs_cleanup` — into one, because deleting the `COPY`
type class (§4.1) removed the one type (`string`) the two predicates disagreed about. There is now
exactly one question — "does this type own heap?" — asked by semantics, the borrow checker, and the
backend alike.

Two consequences that the original audit predicted correctly:

- The escaping-closure use-after-free is gone by construction: the env destructor's field set and
  the capture's move decision cannot disagree, because both read `owns_heap`.
- `HashMap@(K, V)` no longer moves *by accident*. `owns_heap` names `HashMap` explicitly
  (`GenericTypeRef.base_name in ('Own', 'List', 'HashMap')`, and the equivalent `StructType.name`
  prefix check after monomorphization) rather than inferring it from a placeholder `buckets: i32[]`
  field — the placeholder is no longer load-bearing for this question.

## 7. Pairing clone with destruction

Shipped as `sushi_lang/backend/lifecycle.py` — one handler table per composite type kind (dynamic
array, fixed array, struct, enum), each registering a `destroy` emitter
(`sushi_lang/backend/destructors.py`) and a `clone` emitter (`sushi_lang/backend/expressions/memory.py`)
under one shared identity key (`composite_type_key`) and one shared symbol mangler
(`lifecycle_symbol`). A kind with one half and not the other is a loud `KeyError` at dispatch, and
`tests/unit/test_lifecycle_handlers.py` asserts totality statically — the "one handler per type
kind, registered like `register_clone_emitter_factory`/`register_hash_emitter_factory`" plan this
section originally proposed, now real.

This closes the specific drift the design called out: a self-referential type's out-of-line clone
and destructor bodies must swap **both** `codegen.builder` and `codegen.func` (#257), and
`get_or_emit_lifecycle_func` is the one place that does it for both halves, so they cannot drift back
apart the way the old separately-maintained twins (`_dtor_type_key`/`_clone_type_key`,
`_select_inline_destructor`/`_inline_clone`) did.

**One thing changed about when clone runs.** With the `COPY` type class deleted (§4.1), the compiler
never invokes `emit_value_clone` automatically at an ownership sink — only an explicit `.clone()`
call and a small number of reader positions that need an independent copy (`copy_out`, §5) reach it.
"Pairing clone with destruction" is therefore about **every clone the compiler can still emit**
staying the exact structural inverse of the matching destructor — not, as originally scoped, about
keeping an *automatic* per-sink copy correct.

A missing clone arm is a missing method on a handler, not a silently-skipped
`isinstance` branch.

This also collapsed the 8 independent `isinstance` type-kind ladders that predated it, and most of
the sites that used to spell `("Own<", "List<", "HashMap<")` by hand now share
`semantics/generics/cloning.py`'s `CONTAINER_PREFIXES`.

## 8. Decided: a binding is a read-only borrow

**A `match` payload binding and a `foreach` loop binding are read-only borrows of storage their
scrutinee or container still owns.** Reads are free and copy nothing. Writing through one is
**CE2408**. Consuming one whose type owns heap is **CE2411**, with `.clone()` as the escape.

### 8.1 What it was before this design (historical)

None of the coherent answers. The borrow checker modelled it as an untyped local
(`BorrowState(name=binding)` with no `var_type`, so `owns_heap(None)` was always False and
this bug class was undiagnosable *in principle*). Codegen created it as a non-owning borrow
(`register_cleanup=False`). Eight consuming uses read "absent from every cleanup registry" as "free
to take". It was in fact a shallow byte-copy of an owner's fat pointer — which is why mutation
through it was silently discarded (#253), re-wrapping it double-freed (#277), and shadowing through
it read the wrong field index (#279).

**What it is now:** typed. `_register_pattern_bindings` (`semantics/passes/borrow.py`) stamps each
`match` binding's `var_type` from the variant Pass 2 already resolved, and the `foreach` binding is
stamped from the container's element type — `owns_heap` finally has something to answer on, and
the three bugs above are closed by construction rather than patched individually.

### 8.2 Why, from precedent

| language | binding is | mutate through it | take ownership out |
|---|---|---|---|
| **Rust** | chosen by the scrutinee form — `match x` moves, `match &x` borrows, `match &mut x` borrows mutably (RFC 2005 match ergonomics) | yes, via `&mut` | yes, via `match x`; partially moves the scrutinee |
| **Zig** | copy; `\|*item\|` gives a pointer | only with the explicit `*` | n/a |
| **C#** | copy, and **readonly** — assigning a `foreach` variable is compile error CS1656 | no; `foreach (ref var x in span)` was added later as an opt-in | n/a |
| **Swift** | copy (value semantics + COW); `borrowing` / `consuming` made explicit for `~Copyable` types | local only, discarded | explicit `consuming` |
| **Go** | copy | local only, **silently discarded** | n/a |
| **OCaml / Haskell** | immutable binding | impossible by construction | n/a |

Two things fall out.

**Nobody deliberately chose Sushi's current behaviour.** Every language that copies either makes the
binding immutable (C#, the ML family) or has value semantics so the copy is what the user already
expects (Swift, Go). A binding that *looks* mutable, *is* a copy, and silently discards the write is
the one combination no design picked. Go is closest, and its loop-variable semantics were considered
enough of a footgun to change scoping in 1.22.

**Every language that lets you mutate through the binding makes you ask for it** — Rust's `&mut`,
Zig's `*`, C#'s `ref`. None makes it the silent default, because it is an aliasing hazard.

C# is the closest precedent for the choice made here: a mainstream language that hit this exact
problem, made the binding read-only with a compile error on mutation, and added the mutable opt-in
later, only where it was demonstrably needed.

### 8.3 Cost, stated honestly

This was **not** purely semantics-tightening. It broke source compatibility: the call-argument
position used to copy a borrowed binding silently, so `eat(p)` had to become `eat(p.clone())`. No
program silently changed behaviour — every break is a compile error — but a program that compiled
before this shipped can require a `.clone()` it did not need before.

The blast radius is smaller than it sounds, because only `(BORROWED, MOVE)` is affected:

```sushi
foreach(i in 0..10):              # PLAIN                -- unaffected
foreach(n in numbers.iter()):     # i32, PLAIN           -- unaffected
match name_opt:
    Maybe.Some(s) -> println(s)   # reading s is free    -- unaffected
```

**One thing shifted since this table was first drawn.** `string` was a `COPY` type when this
section was written, which made every `string` binding categorically exempt. Phase 9 deleted `COPY`
(§4.1) — a `string` is `MOVE` now — so a `match`/`foreach` binding of a `string` payload is affected
the same as any other owning binding: `println(s)` above stays fine because printing is not a
consuming use (nothing takes ownership to print), but `take(s)` where `take` accepts `string` by
value is now **CE2411**, escaped with `s.clone()`. Only a binding whose payload transitively owns
heap and is handed to a genuine consuming use is rejected — and essentially every program that did
that before this design shipped was already double-freeing.

### 8.4 What is NOT decided

**How to opt into a mutable binding**, if it is ever wanted. Zig and Sushi's own `&peek`/`&poke`
vocabulary point at the binding site (`Shape.Poly(&poke p)`); Rust points at the scrutinee
(`match &mut x`). Deferred on purpose, exactly as C# deferred `ref`: ship read-only, and let real
code demonstrate the need. Nothing in this design forecloses either spelling.

**Numbering, untangled.** Two different issues get invoked near this decision and are easy to
conflate:

- **#242** is `docs/design/move-semantics.md` §3.1's residual-copy-tier issue: a plain field read
  (`s.field`) or container get-out silently deep-copied, with no escape if that copy were ever
  upgraded to a hard error. Closed by this design: §4.2 makes such a read `BORROWED`, and §8 makes a
  `let` of one bind rather than own — no new syntax needed, and `.clone()` (already the escape a hard
  error would have required) is the one this ships with.
- **#252** is a distinct, later request: a first-class *reference-typed* local binding
  (`let &peek T x = ...`) as its own kind of value. That is NOT what #242 needed and is NOT what §8
  implements — §8's binding still declares an ordinary value type `T`; it is tracked as a borrow by
  provenance, not by a reference type. #252 was assessed and **rejected** as **CE2413**: the form
  parses but would add a second, overlapping way to say the same thing the implicit binding already
  says, with no additional checked semantics today.

So this decision does not "depend on #252" the way an earlier note claimed — the prerequisite #242
needed was a `let` that can *borrow* rather than copy-or-error, and that is exactly what §8's binding
classification supplies without any new grammar. A `match`/`foreach` binding and a `let x = s.field`
binding are the same kind of thing under §4.2 — both `BORROWED`, both reject a consuming use with
CE2411, both escape with `.clone()` — which is exactly what Rust requires
(`match &x { Some(v) => take(v.clone()) }`).

## 9. Risks, and how they resolved

**Annotation completeness was a new failure mode.** `Provenance` had to survive Pass 1.6
monomorphization and Pass 1.7 transformation, with the same going-wrong shape as
`resolved_scrutinee_type`'s **CE0121**. It resolved the intended way: a missing stamp is **CE0129**
(internal), so a latent gap surfaces loudly during rollout rather than silently misclassifying —
exactly the trade the design accepted going in.

**`RETURN` and `CAPTURE` did need per-use behaviour** beyond the plain table, as predicted. `RETURN`
still routes through `consume()` with `ConsumingUse.RETURN` — the special case turned out to be
"emit the value before scope cleanup runs," which was already how `returns.py` worked and needed no
new table cell. `CAPTURE` reads its `Provenance` off a `Param`, not an `Expr` (`Lambda.captures` is a
list of `Param`, not a list of expressions) — a plumbing difference, not a rule difference; `classify`
answers it exactly like every other use.

**The seam did not become a second `is_owned_local`.** `consume`/`bind` take `Provenance` computed in
semantics and the resolved target type from the backend; neither queries a cleanup registry to
decide. `resolver_for` (`backend/ownership.py`) exists only to resolve an `UnknownType` name to its
struct/enum table entry before classifying it — a type lookup, not an ownership-registry lookup —
which is the distinction this risk was about.
