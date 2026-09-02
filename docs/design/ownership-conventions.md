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
    ELEMENT_ASSIGN    # arr[i] := <source>
    CONTAINER_INSERT  # List.push/.insert, HashMap.insert (key AND value), T[].push
    RETURN            # return Result.Ok(<source>)
    CAPTURE           # a lambda's captured environment slot
    OWN_ALLOC         # Own.alloc(<source>)
    MATCH_SCRUTINEE   # match nom <source>: -- ruling R11
    RECEIVER          # h.close() on a `nom self` method -- ruling R25
    TRY               # <source>?? -- the unwrap spends a wrapper the writer owns (#548)
```

**Closedness is the property that fixes the recurring bug, not the naming.** Today nobody can answer
"what are all the positions?" — #250's triage said two, its own fix found five, #277 says one, the
2026-07-30 audit found eleven. The set is rediscovered empirically each time. An enum makes it
impossible to add a thirteenth without declaring it, and makes coverage assertable. `ELEMENT_ASSIGN`
is the twelfth, and it is the mechanism working: `arr[i] := v` (#261) could not be added without
naming its position here, so the (BORROWED, MOVE) cell rejected `arr[0] := arr[1]` on an owning
element type from the first line of the implementation.

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
| **PLAIN** | owns nothing | `i32`, `bool`, `f64`, a struct of only these |
| **MOVE** | `owns_resource(T, drops)` — transitively contains `T[]`, `List@(T)`, `Own@(T)`, `HashMap@(K,V)`, `string`, or a capturing closure, **or declares a resource by implementing `Drop`** | `i32[]`, `struct W { i32[] }`, `Maybe@(Own@(T))`, `Buffer[2]`, `string`, `struct { string name }`, `File`, `TcpStream` |

**Two ways to own, one predicate.** Most types own HEAP, and the answer is STRUCTURAL —
the predicate walks the fields. A file or a socket holds one `i32` descriptor, so no field
walk can find what it owns; such a type must be able to SAY that it owns a resource. It says
so by implementing the compiler-known `Drop` perk (`HANDLES.md` ruling R2):

```sushi
perk Drop:
    fn drop(poke self) ~
```

A type that implements it MOVES, and its `drop()` runs at scope exit — before its own
owning fields are destroyed, so a handle is still readable while its owner closes itself
down. Scope exit destroys in **reverse declaration order**: the last binding opened is the
first closed.

`drop()` is bare by construction, because a destructor has nowhere to put a `Result`; a
channel on it is CE0133 like any other contract mismatch. Only the unit that DECLARES a
type may implement `Drop` for it (CE4012, ruling R2b) — the orphan rule, narrowed to one
perk, because `PerkImplementationTable.replace` would otherwise let a consumer silently
stop a handle from closing. A GENERIC target reads its **base** name for that rule: the
key the implementation registers under carries the type arguments (`Crate<T>`,
`Box<i32>`), which matches no declaration record, so the rule would go silent on the one
shape a generic `Drop` needs.

**`drops` is a required argument, with no default.** `owns_resource` and `type_class_of`
both take the set of types that implement `Drop`, and a caller that cannot supply it does
not compile (ruling R2a). The reason is the failure mode: a forgotten argument answers
False for every handle in the program, and a false answer here is a leaked descriptor with
no diagnostic. The semantics side reads it from the perk table; the backend side reads it
through `drops_of(codegen)`, and every backend caller already holds `codegen`.

**Shipped as one predicate, not two.** The design as originally written kept a `COPY` tier for
`string` alongside a `PLAIN`/`MOVE` split, on the theory that unifying the move predicate with the
backend's `needs_cleanup` was unsound (`docs/design/move-semantics.md` §2 argued the two questions
must stay separate). Phase 9 did the opposite: it made `string` move, which makes "does this need
freeing?" and "does this move?" the same question for every type, including `string`. The single
predicate is `owns_resource` (`sushi_lang/semantics/typesys.py`); the backend's
`needs_cleanup` (`sushi_lang/backend/destructors.py`) is the same rule with the tables
supplied, and it is the ONLY backend cleanup predicate. It used to be two, and a third walk
in `memory/dynamic_arrays.py` re-derived the same rule a third way: one gated destructor
RECURSION and another gated cleanup REGISTRATION, and the two disagreeing is what #162 and
#183 were. A `Drop` type is where they parted company — the field walk found nothing in a
handle, so a value that moved correctly was never registered and its `drop()` never ran.
`tests/unit/test_cleanup_predicates_agree.py` is the gate that keeps them one.

**The string exception lives on the BINDING, not the type.** A `string` bound directly from a
string literal (`let string s = "hi"`) owns nothing — it points into `.rodata` with the runtime
`owned` bit clear — so classifying it as PLAIN for *that binding* is exact, not an approximation.
This is "option B": the flag (`BorrowState.owns_no_heap`) is recorded on the binding by the borrow
checker, re-derived on every rebind (never inherited — a rebound string may now own a heap buffer),
and is invisible to `owns_resource`/`type_class_of`, which always answer MOVE for `BuiltinType.STRING`.
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
| **OWNED** | a registered owner in this scope | a bare `Name` bound by `let` or a by-value parameter, **and a marked field TAKE** — `nom s.field`, the one field read that is not a borrow (P7 ruling R28, `docs/design/borrow-model.md` S10c) |
| **BORROWED** | names storage owned elsewhere, for a shorter lifetime | a `match` payload binding, a `foreach` binding, a `peek`/`poke` parameter, a `let` bound from any of these, **and every read THROUGH a still-live owner** — `s.field`, `own.get()`, `arr[i]`, `list.get(i)??` |
| **FRESH** | nothing owns it yet | a constructor, a call result, `.clone()`, a literal, `arr.pop()` / `List.pop()` (which REMOVE the element, so the container stops owning it) |

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

**A reference has the type class of its REFERENT.** The two halves of a decision must not answer
each other's question: the borrow is the PROVENANCE (a `peek`/`poke` parameter is BORROWED, per
the table in §4.2), and the type class asks only "does this value own heap?". `type_class_of` used
to short-circuit any `ReferenceType` to PLAIN — reading the borrow into the ownership answer — and
that made the (BORROWED, MOVE) cell UNREACHABLE through a reference. The checker then classified
`f(a)` on a `poke i32[]` parameter as ADOPT and stayed silent while the backend classified the
same transfer from the TARGET type and answered REJECT. One question, two answers: #301's CE0129
ICE, #310's compile-clean double free through a `let` bound from a reference, #311's ref-to-ref
rebind. A reference now classifies as `referenced_type`, so all three are the ordinary CE2411 and
`.clone()` (which derefs a reference receiver) is the escape.

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

**Mutating through a binding needs no new code for the `peek`/`poke`-parameter case.** A
reference parameter is read-only/exclusive per the ordinary borrow rules, so a write through a
`peek` one is **CE2408** ("cannot modify through peek reference"). That rule became TOTAL in
R1: the same three write shapes CE2414 rejects for a binding — a mutating method on or under it,
a field assignment, and a `poke` borrow of it — are rejected for a `peek` reference, next to
the rebind that was checked before. One helper, four call sites, keyed on `_MUTATING_METHODS`
so the method list is never copied. A *bound* borrow
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

    Reads the Provenance the borrow pass stamped on `source`, asks classify() what that means for
    `target_type`, and performs the answer: MOVE marks the source moved and returns the
    value as-is; ADOPT returns it as-is; REJECT raises CE0129 (internal -- the borrow pass should
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

**A conditional move carries a runtime drop flag (#414).** "Mark the source moved" is a
compile-time fact, but a move inside an if arm, a match arm, or a loop body does not dominate the
owner's scope exit: on the paths that skip the move, the static skip leaked the value. The borrow pass
counts branch depth; a move recorded deeper than its owner's declaration lands the name in the
callable's `conditional_move_names` stamp (on the BODY block, so the perk-method wrapper shares
it). The backend arms those bindings with an entry-block `i1` drop flag — set live at declaration
(re-armed on every loop iteration and on a rebind), cleared at each move site — and every free
gate goes through `MoveTracker.emit_free_unless_moved`, which skips a statically moved slot,
emits an `if (flag)` free for a flagged one, and frees unconditionally otherwise. An
unconditional move keeps the zero-cost static skip; the gate
`tests/memory/conditional_moves/` holds the leak batch.

### 5.1 The decision is computed in semantics, not in the backend

This is what makes it a *single* authority rather than a backend-local one.

Before the seam, the backend asked `is_owned_local` — "is this name registered for cleanup?" — as a
proxy for "is this a borrow of something still live?". Those coincide for a `let` local and a fresh
temporary, and diverge for exactly one thing: a pattern binding. The backend has LLVM values and
cleanup registries; it does not reliably have provenance. Semantics has the AST, the types, the
scopes and `borrow_state` — it *knows* a match binding is a binding, which is why provenance had to
be computed there.

**What is shipped stamps `Provenance`, not the final `Ownership` decision** — a half-step short of
what this section originally proposed, and a better split. The borrow pass is the only
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
The borrow pass disagreed with itself.

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

Shipped as one function, `owns_resource` (`sushi_lang/semantics/typesys.py`), not two. The design as
originally written proposed keeping `is_owning_type` (narrow, base-cases-only) and
`type_moves_by_value` (transitive) as separate predicates and auditing every `is_owning_type` call
site to see whether it could switch to the wide one. That audit is superseded: Phase 9 merged both
predicates — and the backend's independent `needs_cleanup` — into one, because deleting the `COPY`
type class (§4.1) removed the one type (`string`) the two predicates disagreed about. There is now
exactly one question — "does this type own heap?" — asked by semantics, the borrow checker, and the
backend alike.

Two consequences that the original audit predicted correctly:

- The escaping-closure use-after-free is gone by construction: the env destructor's field set and
  the capture's move decision cannot disagree, because both read `owns_resource`.
- `HashMap@(K, V)` no longer moves *by accident*. `owns_resource` names `HashMap` explicitly
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
**CE2414** — a mutating method, a field assignment, and a `poke` borrow of the binding are all
rejected (#253; the compiled binding is a private copy, so such a write could never reach the
owner). A rebind of the binding ITSELF (`n := 99`) stays legal: it re-initializes a local, the
Rust `Some(mut n) => n = 99` shape, and does not claim to write through. Consuming a binding
whose type owns heap is **CE2411**, with `.clone()` as the escape.

### 8.1 What it was before this design (historical)

None of the coherent answers. The borrow checker modelled it as an untyped local
(`BorrowState(name=binding)` with no `var_type`, so the predicate was always False and
this bug class was undiagnosable *in principle*). Codegen created it as a non-owning borrow
(`register_cleanup=False`). Eight consuming uses read "absent from every cleanup registry" as "free
to take". It was in fact a shallow byte-copy of an owner's fat pointer — which is why mutation
through it was silently discarded (#253), re-wrapping it double-freed (#277), and shadowing through
it read the wrong field index (#279).

**What it is now:** typed. `register_pattern_bindings` (`semantics/passes/borrow/bindings.py`) stamps each
`match` binding's `var_type` from the variant the typecheck pass already resolved, and the `foreach` binding is
stamped from the container's element type — `owns_resource` finally has something to answer on, and
the three bugs above are closed rather than patched individually: #277 and #279 by the typing
itself, and #253 by **CE2414**, which rejects every write through a binding (the read-only rule
above, enforced instead of asserted).

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

**How to opt into a mutable binding** — DECIDED and SHIPPED, all three phases (#300,
2026-08-16). The spelling is the binding site, with Sushi's own vocabulary:
`foreach(poke r in rows.iter())`, `Own(poke x)`, and a top-level match binding
`Shape.Poly(poke p)` bind a POINTER into the owner's storage, so a write through the
binding reaches the owner in place; `peek` is the copy-free read-only twin, and value and
reference bindings mix in one pattern. The binding registers with its full `ReferenceType`,
which wires in every existing rule by construction: a write through `peek` is CE2408, a
consuming use is CE2411, and the owner is FROZEN for the binding's scope (CE2412) exactly
like a `let`-borrow's — including the tag-change hazard (rebinding the scrutinee under a
live payload borrow, Rust's E0506). The match half rests on the phase-2 enum layout
(`{i32 tag, [K x i64] data}`, naturally aligned payload offsets from one authority), which
is what retired the `align=1` family and made an interior payload pointer safe to hand out.
Fences: an iterable whose items have no address (a range, `.entries()`) is **CE2423**; a
`poke` binding out of a `peek` owner is CE2408; out of a constant is CE2400; a TEMPORARY
scrutinee is CE2404 (no storage to point into); and a reference binding in a NESTED pattern
is **CE2424** — nested extraction walks through temporary copies, so a pointer into one is
a silently lost write. Rust's scrutinee-side spelling (`match &mut x`) stays
foreclosed-by-none but unimplemented.

**Numbering, untangled.** Two different issues get invoked near this decision and are easy to
conflate:

- **#242** is `docs/design/move-semantics.md` §3.1's residual-copy-tier issue: a plain field read
  (`s.field`) or container get-out silently deep-copied, with no escape if that copy were ever
  upgraded to a hard error. Closed by this design: §4.2 makes such a read `BORROWED`, and §8 makes a
  `let` of one bind rather than own — no new syntax needed, and `.clone()` (already the escape a hard
  error would have required) is the one this ships with.
- **#252** is a distinct, later request: a first-class *reference-typed* local binding
  (`let peek T x = ...`) as its own kind of value. That is NOT what #242 needed and is NOT what §8
  implements — §8's binding still declares an ordinary value type `T`; it is tracked as a borrow by
  provenance, not by a reference type. #252 was first **rejected** as **CE2413** (the form parsed
  but would have been an untracked alias), and then built as #409 on 2026-09-03: `let poke T x =
  <place>` is a checked, block-scoped POINTER binding with the mode on the declaration -- the
  zero-copy mutation path into an `Own@(T)` payload that §8's value binding cannot be. CE2413 is
  retired; `docs/design/borrowing.md` mechanism 3b is the record.

So this decision does not "depend on #252" the way an earlier note claimed — the prerequisite #242
needed was a `let` that can *borrow* rather than copy-or-error, and that is exactly what §8's binding
classification supplies without any new grammar. A `match`/`foreach` binding and a `let x = s.field`
binding are the same kind of thing under §4.2 — both `BORROWED`, both reject a consuming use with
CE2411, both escape with `.clone()` — which is exactly what Rust requires
(`match &x { Some(v) => take(v.clone()) }`).

## 8.5 Where a reference type may appear (decided, R4)

The grammar's `?type` rule is recursive and universal, so `peek T` / `poke T` parses in EVERY
type position. Semantics defines **three**, and rejects the other positions at the declaration until
each is designed. A borrow is a promise about a lifetime, and every rejected position is one where
nothing relates the borrow to the value it names.

| position | status | code |
|---|---|---|
| function parameter — `fn f(peek T x)` | **supported** — the position the whole subsystem is built for | — |
| parameter inside a function type — `fn(peek i32) -> i32`, and the lambda `\|peek i32 x\|` that satisfies it | **supported** (promoted to tested support by R4; it had worked untested) | — |
| `let` binding — `let poke T x = <place>` / `let peek T x = <place>` | **supported** (#409, 2026-09-03) — a block-scoped borrow binding; `borrowing.md` mechanism 3b | — |
| struct field | rejected | CE2415 (#315) |
| enum variant payload | rejected | CE2416 (#316) |
| return type | rejected | CE2417 (#314) |
| nested reference — `peek peek T` | rejected | CE2418 (#317) |
| generic type argument — `List@(peek T)` | rejected | CE2419 (#318) |
| extension / perk-impl target — `extend peek T` | rejected | CE2420 (#319) |
| FFI signature | rejected | CE5003 |
| variadic element — `...peek T` | rejected | CE0114 |

Three notes on the shape of this, because each was a decision rather than a detail:

- **One walk, six sites.** `contains_reference` (`semantics/type_predicates.py`) is the single
  question; the six emits live at the collect/validate sites, which is the convention the `ptr`
  gates already follow (the predicate module stays free of the reporter). **The function-type
  carve-out is load-bearing**: the walk does not descend into `FunctionType.param_types`, because
  a borrow parameter inside a function type is the supported position wherever that function type
  appears — as a struct field, as a generic argument, anywhere. It DOES reject a reference in a
  function type's RETURN, by the same reasoning as CE2417.
- **Six codes, not one parameterized code.** Each position has its own rationale and its own way
  out, which is what the registry's long-form text carries, and each will be lifted separately as
  its feature is designed — a shared code could only be retired all at once. Precedents: foreign
  `ptr` (CE5002/5008/5009/5012) and the variadic marker (CE0114/0115/0116).
- **No `Maybe`/`Result` exemption on CE2419**, unlike the `ptr` twin CE5012. `Maybe@(peek T)` and
  `Result@(peek T, E)` are exactly how a returned borrow escaped into a `match` (#314), so
  exempting them would leave open the hole CE2417 closes.

Rejecting a position is reported and then **kept** where the declaration is a table entry (a
struct field, an enum payload): dropping it would be error recovery that reports a spurious arity
error at every construction of the type. The report already stops the compile before codegen,
which is what the internal errors needed protecting from.

## 8.6 Method receivers and method parameters (decided 2026-08-15, #298)

> **SUPERSEDED by `docs/design/borrow-model.md`** (ruled 2026-08-16, issue #354). The
> exception this section records became the RULE: an unmarked parameter is a borrow in
> every kind of callable, and a consume is spelled `nom` at both ends. So the asymmetry
> below — `eat(s)` consumes but `b.eat(s)` does not — is gone, and with it the reason
> this section had to exist. Everything it says about what a borrow parameter may not do
> still holds, and applies to a plain function's parameters too: a write through one is
> CE2422, and consuming one is CE2411 with `.clone()` as the escape. Read it for that
> reasoning, and read the borrow-model spec for what the modes are.

A by-value parameter is owned by the CALLEE (§4.2). An **extension or perk method** is the
one exception, and it is now a **decision rather than a compromise**: its parameters --
`self` included -- are BORROWS, and the caller keeps ownership.

| | plain function | extension / perk method |
|---|---|---|
| by-value parameter | callee owns and frees it; the call site consumes | **borrow**; the caller keeps it |
| a later use of the argument | CE2405 | legal |
| a write through a by-value parameter | legal (the callee owns it) | rejected, **CE2422** |
| consuming a by-value parameter in the body | legal (the callee owns it) | rejected, **CE2411** |
| `self` | — | the same borrow: writes **CE2421**, consuming uses **CE2411** |

The asymmetry is observable — `eat(s)` consumes, `b.eat(s)` does not — and it was verified
sound in both directions: the method form is leak-clean and double-free-clean, because a
method-call argument is not a consuming use at the call site either. The alternative,
extending callee-owns to method bodies, would make every method argument consuming
(`text.count(needle)` would eat `needle`, and the same for every stdlib string method that
takes one by value). That blast radius buys symmetry and nothing else, so the borrow
reading — the one the documentation already gave users — is now the permanent rule.

**The borrow is now ENFORCED, in both directions, for every parameter.** "The caller keeps
ownership" was a statement about the ABI that nothing in the checker made true, so the two
things a borrow must not do were both accepted and both corrupted memory:

- **Writing through one.** A method parameter is materialized as a private SHALLOW copy,
  so `self.n := 42` was silently lost and `self.label := "..."` on an owning field was a
  double free plus a leak (#326) — #253's shape, for the receivers CE2414 does not cover.
  The explicit parameters had the identical bug, found while #333 was fixed. A write is
  now **CE2421** for the receiver and **CE2422** for a by-value parameter, with the same
  relational rendering CE2414 uses, and the same three shapes CE2414 rejects: a mutating
  method under it (`self.items.push(9)` — which did not even reach codegen, it was a
  CE0129), a field assignment, and a `poke` borrow of it.
- **Handing one to a position that takes ownership.** The value gets a second owner and
  both free it. `return self`, `eat(self)`, `sink.push(self)` and all three again for an
  explicit parameter were compile-clean double frees (#333); two of them printed the
  caller's buffer back after the allocator had reused it. Every one is now **CE2411**,
  with no per-sink work: the parameter's provenance is BORROWED, and the (BORROWED, MOVE)
  cell already said REJECT.

**There is no `string` carve-out any more (RULED 2026-08-15, #338).** Until that ruling,
a method's `string` parameter was exempt from the consuming rule: `begin_function` clears
its owned bit (#145), so consuming it transferred nothing and
`extend T with Display: fn display() string: return self` stayed legal. The price was a
dangling read — a returned `string` self is a non-owning VIEW of the receiver's buffer,
and nothing relates the view's lifetime to the receiver's, so when the receiver was a
local of the CALLING function, the buffer was freed at that function's scope exit and the
view outlived it:

```sushi
fn make_tag() string:
    let string s = "tag-{1}"
    return Result.Ok(s.say_it())   # was: a view of `s`, which dies on the next line
```

That compiled clean, leaked nothing, double-freed nothing, and read freed memory. The
ruling removed the exemption: a `string` method parameter is a borrow like every other
owning type, every consuming use of one is **CE2411**, and the `Display` idiom is spelled
`return self.clone()`. The checker half is one deletion (the parameter no longer gets
`owns_no_heap`, so its provenance is BORROWED and the (BORROWED, MOVE) cell rejects).

The backend owned-bit clear (#145) STAYS: it guarantees a method body can never free the
caller's buffer, and since the ruling it guards read paths only — no consuming use
compiles.

**The ruling exposed a second defect, fixed with it: `string.clone()` did not copy a
view.** The clone emitter mirrored the destructor's owned-bit guard and passed an
`owned = 0` string through unchanged. That is correct for a literal (rodata is immortal)
and wrong for a view of a heap buffer — the two are indistinguishable from the bit — so
`return self.clone()`, the exact escape the ruling names, returned the same dangling view
as `return self`. `.clone()` on a `string` now copies unconditionally; the destructor
keeps its guard. A clone that sometimes aliases was a hole in the ".clone() is the only
deep copy" contract, independent of #338.

The working version is spelled **`poke self`** (#327, SHIPPED 2026-08-16), an opt-in
first parameter carrying the borrow vocabulary the language already has —

```sushi
extend Counter bump(poke self) ~:
    self.n := self.n + 1
    return ~
```

— the receiver arrives by POINTER, so the write reaches the caller's value and an owning
field's old buffer is freed exactly once. It inherits the write gates at the call site,
because a `poke self` call IS a write to the receiver root: through a `peek` parameter
it is CE2408, on a binding CE2414, on a temporary CE2404, on a constant CE2400.
`peek self` states the read-only default explicitly; a perk declares the mode in its
signature and the impl must match (CE4004); the receiver stays a borrow for consuming
purposes (CE2411, `.clone()` escapes); and the parameter is CE2425 anywhere but first in
an extension/perk method. This followed the order #252 → CE2413 and #253 → CE2414 set:
reject the unchecked form first, then ship the feature -- and #409 shipped the `let` half
in 2026-09-03, retiring CE2413.

**Reads are unaffected, and one of them became expressible.** A field read, a read-only
method under the receiver, `.clone()` of an owning field, and `.clone()` of the whole
receiver all stay legal — the last is the escape CE2411 names. `peek self` and
`peek self.field` now WORK; they used to be
`CE2400: cannot borrow 'self': variable does not exist`, which was true of the borrow
checker's state and puzzling to anyone who could see `self` on the line above. And a
PLAIN parameter — an i32, a struct of primitives — was never affected in either
direction: it copies, and (BORROWED, PLAIN) adopts.

**Four read-only receivers, one gate.** A `match`/`foreach` binding (CE2414), a `peek`
reference (CE2408), the method receiver (CE2421) and a by-value method parameter (CE2422)
are the same rule with four rationales: a write through any of them cannot reach the value
it appears to write. Each was found as its own bug, and each time all three write shapes
had to be re-covered by hand. The checker now holds them as a TABLE of kinds behind one
dispatcher (`_reject_readonly_write`), called from the four write sites, so a fifth kind is
one row rather than a fifth walk; `tests/unit/test_readonly_receiver_matrix.py` pins all
twelve cells and fails if a kind in the table has no row in the matrix. The codes stay
separate for the reason the six position codes do (§8.5): each carries its own escape.
Here the escapes are what separate the last two — a by-value parameter is redeclared
`poke T`, and a receiver `poke self` (#327, shipped 2026-08-16).

The mechanisms themselves — the six ways a borrow is created, their extents, and the gate
that backs each rule — are `docs/design/borrowing.md`.

## 9. Risks, and how they resolved

**Annotation completeness was a new failure mode.** `Provenance` had to survive both
monomorphization and field resolution, with the same going-wrong shape as
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
