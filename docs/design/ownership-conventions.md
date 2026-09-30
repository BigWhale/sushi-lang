# Ownership Conventions: One Authority for Every Consuming Use

*Design doc, 2026-07-30. Status: **implemented** (Phase 9, 2026-08-14). Supersedes the ad-hoc
"ownership sink" handling described in `docs/design/move-semantics.md` §3 — that document is the
record of the earlier decision and cross-links back here. The shipped seam lives in `sushi_lang/semantics/ownership.py`
(the rule, `classify()`) and `sushi_lang/backend/ownership.py` (`consume`/`bind`/`copy_out`/
`relinquish`/`relinquish_temp`, the only module allowed to move-mark a value —
`tests/unit/test_consuming_use_coverage.py` is the no-bypass gate).*

*The one language question this design raised — what a `match`/`foreach` binding is — is settled in
§8: **a read-only borrow**. It is not an open question; §4.3's table is final.*

---

## 1. The problem

Many positions take ownership of a value: a call argument, a `let`, a constructor field, a
`return`, a container insert, and more (§3.1). Each one must answer the same question: does the
source move, does the position adopt a fresh value, or is the transfer an error? When each
position derives the answer for itself, the derivations disagree, and each disagreement is a
leak, a double free or a lost write. This design makes the answer ONE function (`classify()`,
§4) behind ONE seam (§5), over a CLOSED set of positions (§3.1).

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

The distinction matters when a position is fed a copy. The source is *not* consumed — but the use is
still a consuming use, because the position requires ownership and the copy is how it is satisfied. Words like
*transfer*, *handoff* and *move* are all false in that case; *consuming use* is not. This is exactly
what SILGen does when an `@owned` operand is fed from a `@guaranteed` value.

**Sushi does not use the word "sink".** It is a genuine term of art — a Hylo keyword and a C++
idiom — but `backend/llvm_optimization.py` uses `add_sinking_pass()` in LLVM's unrelated sense
(moving instructions down the CFG), in the same package, so the word does not carry its own
meaning here.

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
    TRY               # <source>?? -- the unwrap spends a wrapper the writer owns
```

**Closedness is the property that matters, not the naming.** The set is closed: a new position
cannot be added without a declaration here, and coverage is assertable. For example,
`arr[i] := v` is `ELEMENT_ASSIGN`, so the (BORROWED, MOVE) cell rejects `arr[0] := arr[1]` on an
owning element type.

`RETURN` is a genuine special case and must stay a distinct variant: a returned value is emitted
*before* scope cleanup runs, so that `return Result.Ok(w.items)` never hands the caller an
already-freed buffer. It is not merely `LET` at a different address.

### 3.2 `Ownership` — how the source satisfies it

```python
class Ownership(Enum):
    MOVE   # the source owned it; mark the source moved, store as-is
    ADOPT  # nothing owned it; store as-is
    REJECT # the source may not be consumed at all -- CE2411
```

There is no `COPY` answer: the compiler never inserts a deep copy, and a `string` moves like every
other heap-owning type (§4.1). `REJECT` is the (BORROWED, MOVE) cell.

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
so by implementing the predefined `Drop` perk. The compiler declares it; a program does
not (a user declaration is CE4001). Its contract is:

<!-- docs-sweep: skip (the predefined contract; a program that declares it is CE4001) -->
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

**One predicate.** "Does this need freeing?" and "does this move?" are the same question for
every type, `string` included. The predicate is `owns_resource`
(`sushi_lang/semantics/typesys.py`); the backend's `needs_cleanup`
(`sushi_lang/backend/destructors.py`) is the same rule with the tables supplied, and it is the
ONLY backend cleanup predicate: destructor recursion and cleanup registration both ask it. A
`Drop` type shows why: a field walk finds nothing in a handle, so a second predicate would move
the value correctly and never register it, and its `drop()` would never run.
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
`Named(name: "hi", id: 1)` handed to a consuming position (`eat(nom n)`) moves, even though the string it was built from is a
literal, because the option-B flag is a fact about a *bare `string` binding*, not about a value
nested inside a struct field. See `docs/memory-management.md` for the worked example.

### 4.2 Source provenance

Three, not four:

| provenance | meaning | expression shapes |
|---|---|---|
| **OWNED** | a registered owner in this scope | a bare `Name` bound by `let`, a `nom` parameter, **and a marked field TAKE** — `nom s.field`, the one field read that is not a borrow (P7 ruling R28, `docs/design/borrow-model.md` S10c) |
| **BORROWED** | names storage owned elsewhere, for a shorter lifetime | a `match` payload binding, a `foreach` binding, an unmarked parameter (a borrow by default), a `peek`/`poke` parameter, a `let` bound from any of these, **and every read THROUGH a still-live owner** — `s.field`, `own.get()`, `arr[i]`, `list.get(i)??` |
| **FRESH** | nothing owns it yet | a constructor, a call result, `.clone()`, a literal, `arr.pop()` / `List.pop()` (which REMOVE the element, so the container stops owning it) |

**A read through a live owner is `BORROWED`.** The compiler inserts no automatic copy at a read
(§8 supplies the escape — an implicit borrowed `let` binding, not a hard error), so a field read,
an index and a container get-out have the same outcome as a `match`/`foreach` binding at every
type class.

### 4.3 The table

The table is 3x2:

|  | PLAIN | MOVE |
|---|---|---|
| OWNED | ADOPT | **MOVE** |
| BORROWED | ADOPT | **REJECT — CE2411** |
| FRESH | ADOPT | ADOPT |

(`sushi_lang/semantics/ownership.py:_TABLE`, unit-tested cell by cell in
`tests/unit/test_ownership_table.py`.)

The cell that matters is **(BORROWED, MOVE)**. Per §8 it is not a code-generation question at
all — it is rejected (CE2411), with `.clone()` as the explicit escape.

**A reference has the type class of its REFERENT.** The two halves of a decision must not answer
each other's question: the borrow is the PROVENANCE (a `peek`/`poke` parameter is BORROWED, per
the table in §4.2), and the type class asks only "does this value own heap?". `type_class_of`
classifies a `ReferenceType` as its `referenced_type`, so the (BORROWED, MOVE) cell is reachable
through a reference: a consume through one is CE2411, and `.clone()` (which derefs a reference
receiver) is the escape. If a reference classified as PLAIN, the checker would answer ADOPT while
the backend classified the same transfer from the TARGET type and answered REJECT.

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
`peek` one is **CE2408** ("cannot modify through peek reference"). The rule is total: the same
three write shapes CE2414 rejects for a binding — a mutating method on or under it, a field
assignment, and a `poke` borrow of it — are rejected for a `peek` reference, and so is a rebind.
One helper, four call sites, keyed on the `mutates` column of `borrow/methods.py:METHOD_EFFECTS`
so the method list is never copied. A *bound* borrow
(a `let` reading through an owner, §8) gets its own diagnostic instead — **CE2412**, "cannot mutate
the owner while this binding borrows from it" — because the thing being protected is not the
binding's own mutability but the owner changing out from under it.

## 5. The seam

The seam is `sushi_lang/backend/ownership.py`. Every consuming use routes through one of two entry
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

**A conditional move carries a runtime drop flag.** "Mark the source moved" is a
compile-time fact, but a move inside an if arm, a match arm, or a loop body does not dominate the
owner's scope exit: on the paths that skip the move, a static skip would leak the value. The borrow pass
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

The backend has LLVM values and cleanup registries; it does not reliably have provenance. A
cleanup-registry lookup ("is this name registered for cleanup?") is not a proxy for "is this a
borrow of something still live?": the two diverge for a pattern binding. Semantics has the AST,
the types, the scopes and `borrow_state` — it *knows* a match binding is a binding — so provenance
is computed there.

**The borrow pass stamps `Provenance`, not the final `Ownership` decision.** The borrow pass is the only
side that can compute *where a value came from* — it has the AST, the scopes and `borrow_state` — so
it stamps `Provenance` on the source node (`expr.ownership_provenance`). The backend supplies the
other half, the resolved *target type* at its position, and both sides call the identical
`classify(provenance, type_class)` to reach `Ownership`. Reusing one pure function is what makes two
calls agree rather than one stamped value: the borrow checker uses its answer to decide whether to
mark the source moved (and therefore whether a later use is CE2405, or — for a `let` — whether the
binding borrows instead, §8); the backend uses its answer to decide what to emit. If a source somehow
reaches the backend with no `Provenance` stamped, the seam raises **CE0129** (internal) rather than
guessing, as a try-expression with no stamped type is CE0124: reaching this code path with no stamp
means the borrow pass disagreed with itself.

Because both sides compute the answer from one stamp, `l.push(a)` marks `a` moved in the borrow
pass, so a later use of `a` is CE2405, and a `foreach` binding is a typed binding with a real
`Provenance` (§8.1), so a consuming use of it is CE2411 at the correct span.

### 5.2 Nothing may bypass the seam

A shared helper that callers may decline to call is not an authority. The ownership-transferring
primitives are private to the seam module (`sushi_lang/backend/ownership.py`'s internal `_mark_moved`
and `_clone`); every other backend module reaches them only through `consume`/`bind`/`copy_out`/
`relinquish`/`relinquish_temp`.

Enforced by a CI gate that fails when any backend module outside the seam references the primitives
directly: `tests/unit/test_consuming_use_coverage.py`. This mirrors an existing, proven mechanism:
`tests/unit/test_borrow_dispatch_is_total.py` pins the borrow checker's `Expr` dispatch against the
AST union, with **CE0125** as the runtime backstop.

## 6. Collapsing the predicate

One function, `owns_resource` (`sushi_lang/semantics/typesys.py`), answers "does this type own a
resource?" for semantics, the borrow checker and the backend alike (the backend's `needs_cleanup`
is the same rule with the tables supplied, §4.1). Two consequences:

- The closure environment destructor's field set and the capture's move decision cannot
  disagree, because both read `owns_resource`.
- `owns_resource` names `HashMap` explicitly (`GenericTypeRef.base_name` in the container bases,
  and `generic_base_of(ty)` on a monomorphized `StructType`, which reads `generic_base` and never
  the name). It does not infer ownership from the placeholder `buckets: i32[]` field.

## 7. Pairing clone with destruction

`sushi_lang/backend/lifecycle.py` holds one handler table per composite type kind (dynamic
array, fixed array, struct, enum), each registering a `destroy` emitter
(`sushi_lang/backend/destructors.py`) and a `clone` emitter (`sushi_lang/backend/expressions/memory.py`)
under one shared identity key (`composite_type_key`) and one shared symbol mangler
(`lifecycle_symbol`). A kind with one half and not the other is a loud `KeyError` at dispatch, and
`tests/unit/test_lifecycle_handlers.py` asserts totality statically.

A self-referential type's out-of-line clone and destructor bodies must swap **both**
`codegen.builder` and `codegen.func`, and `get_or_emit_lifecycle_func` is the one place that does
it for both halves, so they cannot drift apart.

**When clone runs.** The compiler never invokes `emit_value_clone` automatically at a consuming
use — only an explicit `.clone()` call and a small number of reader positions that need an
independent copy (`copy_out`, §5) reach it. "Pairing clone with destruction" is therefore about
**every clone the compiler can emit** staying the exact structural inverse of the matching
destructor.

A missing clone arm is a missing method on a handler, not a silently-skipped
`isinstance` branch.

A site that asks "is this an `Own`, a `List` or a `HashMap`?" asks `generic_base_of` /
`is_instance_of` (`semantics/type_predicates.py`), not the interned name.

## 8. Decided: a binding is a read-only borrow

**A `match` payload binding and a `foreach` loop binding are read-only borrows of storage their
scrutinee or container still owns.** Reads are free and copy nothing. Writing through one is
**CE2414** — a mutating method, a field assignment, and a `poke` borrow of the binding are all
rejected (the compiled binding is a private copy, so such a write could never reach the
owner). A rebind of the binding ITSELF (`n := 99`) is **CE2414** too: the compiled copy is
shallow, so the store frees a payload the scrutinee still owns. Rust's `Some(mut n) => n = 99`
is not a model here: Rust's binding either moves the payload out or is a reference, and Sushi's
bare binding is neither. The mode
is the answer here as it is for every other write: `poke` to reach the owner, `nom` to take
the payload. Consuming a binding whose type owns heap is **CE2411**, with `.clone()` as the
escape.

### 8.1 How the binding is typed

`register_pattern_bindings` (`semantics/passes/borrow/bindings.py`) stamps each `match` binding's
`var_type` from the variant the typecheck pass resolved, and the `foreach` binding is stamped from
the container's element type, so `owns_resource` has a type to answer on. A write through a
binding is **CE2414**, and a consuming use of an owning binding is **CE2411**.

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

**No language chooses a binding that looks mutable, is a copy, and discards the write.** Every
language that copies either makes the binding immutable (C#, the ML family) or has value semantics so the copy is what the user already
expects (Swift, Go). A binding that *looks* mutable, *is* a copy, and silently discards the write is
the one combination no design picked. Go is closest, and its loop-variable semantics were considered
enough of a footgun to change scoping in 1.22.

**Every language that lets you mutate through the binding makes you ask for it** — Rust's `&mut`,
Zig's `*`, C#'s `ref`. None makes it the silent default, because it is an aliasing hazard.

C# is the closest precedent for the choice made here: a mainstream language that hit this exact
problem, made the binding read-only with a compile error on mutation, and added the mutable opt-in
later, only where it was demonstrably needed.

### 8.3 What the rule rejects

Only `(BORROWED, MOVE)` is affected. Reads, plain bindings and unmarked arguments are not:

```sushi
foreach(i in 0..10):              # PLAIN                -- unaffected
foreach(n in numbers.iter()):     # i32, PLAIN           -- unaffected
match name_opt:
    Maybe.Some(s) -> println(s)   # reading s is free    -- unaffected
```

A `string` payload is MOVE, but `println(s)` is not a consuming use, and `take(s)` is fine too,
because an unmarked parameter is a borrow. Only a consuming position rejects the binding:
`take(nom s)` where `take` declares `nom string s` is **CE2411**, escaped with
`take(nom s.clone())`.

### 8.4 Mutable and reference bindings

**How to opt into a mutable binding.** The spelling is the binding site, with Sushi's own vocabulary:
`foreach(poke r in rows.iter())`, `Own(poke x)`, and a top-level match binding
`Shape.Poly(poke p)` bind a POINTER into the owner's storage, so a write through the
binding reaches the owner in place; `peek` is the copy-free read-only twin, and value and
reference bindings mix in one pattern. The binding registers with its full `ReferenceType`,
which wires in every existing rule by construction: a write through `peek` is CE2408, a
consuming use is CE2411, and the owner is FROZEN for the binding's scope (CE2412) exactly
like a `let`-borrow's — including the tag-change hazard (rebinding the scrutinee under a
live payload borrow, Rust's E0506). The match half rests on the enum layout
(`{i32 tag, [K x i64] data}`, naturally aligned payload offsets from one authority), which
makes an interior payload pointer safe to hand out.
Fences: an iterable whose items have no address (a range, `.entries()`) is **CE2423**; a
`poke` binding out of a `peek` owner is CE2408; out of a constant is CE2400; a scrutinee
that is neither a place nor a temporary the match owns is CE2404 (a `poke` binding into a
temporary scrutinee is legal: the match owns the temporary); and a reference binding in a NESTED pattern
is **CE2424** — nested extraction walks through temporary copies, so a pointer into one is
a silently lost write. Rust's scrutinee-side spelling (`match &mut x`) stays
foreclosed-by-none but unimplemented.

A pattern binding and a `let x = s.field` binding are borrows by PROVENANCE: each declares an
ordinary value type `T`, both reject a consuming use with CE2411, and both escape with `.clone()`
— which is what Rust requires (`match &x { Some(v) => take(v.clone()) }`). `let peek T x = <place>`
and `let poke T x = <place>` are the reference-typed form: a checked, block-scoped POINTER binding
with the mode on the declaration, and the zero-copy mutation path into an `Own@(T)` payload
(`docs/design/borrowing.md` mechanism 3b).

## 8.5 Where a reference type may appear (decided, R4)

The grammar's `?type` rule is recursive and universal, so `peek T` / `poke T` parses in EVERY
type position. Semantics defines **three**, and rejects the other positions at the declaration until
each is designed. A borrow is a promise about a lifetime, and every rejected position is one where
nothing relates the borrow to the value it names.

| position | status | code |
|---|---|---|
| function parameter — `fn f(peek T x)` | **supported** — the position the whole subsystem is built for | — |
| parameter inside a function type — `fn(peek i32) -> i32`, and the lambda `\|peek i32 x\|` that satisfies it | **supported** | — |
| `let` binding — `let poke T x = <place>` / `let peek T x = <place>` | **supported** — a block-scoped borrow binding; `borrowing.md` mechanism 3b | — |
| struct field | rejected | CE2415 |
| enum variant payload | rejected | CE2416 |
| return type | rejected | CE2417 |
| nested reference — `peek peek T` | rejected | CE2418 |
| generic type argument — `List@(peek T)` | rejected | CE2419 |
| extension / perk-impl target — `extend peek T` | rejected | CE2420 |
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
  `Result@(peek T, E)` are how a returned borrow would escape into a `match`, so exempting
  them would leave open the hole CE2417 closes.

Rejecting a position is reported and then **kept** where the declaration is a table entry (a
struct field, an enum payload): dropping it would be error recovery that reports a spurious arity
error at every construction of the type. The report already stops the compile before codegen,
which is what the internal errors needed protecting from.

## 8.6 Method receivers and method parameters (decided 2026-08-15)

> **SUPERSEDED by `docs/design/borrow-model.md`** (ruled 2026-08-16). An unmarked parameter
> is a borrow in every kind of callable, and a consume is spelled `nom` at both ends. Read
> that document for what the modes are. This section keeps what a borrow parameter may not
> do, and that applies to the parameters of every callable, `self` included: a write through
> one is CE2421 (the receiver) or CE2422 (any other parameter), and consuming one is CE2411
> with `.clone()` as the escape.

**A borrow parameter is enforced in both directions.** A parameter is materialized as a private
SHALLOW copy, so the two things a borrow must not do would both corrupt memory:

- **Writing through one.** A write would be lost, or would free an owning field that the caller
  still holds. A write is **CE2421** for the receiver and **CE2422** for any other borrow
  parameter, with the same relational rendering CE2414 uses, and the same three shapes CE2414
  rejects: a mutating method under it (`self.items.push(9)`), a field assignment, and a `poke`
  borrow of it.
- **Handing one to a position that takes ownership.** The value would get a second owner, and
  both would free it. `return self`, `eat(nom self)`, `sink.push(self)` and the same three for
  an explicit parameter are **CE2411**, with no per-sink work: the parameter's provenance is
  BORROWED, and the (BORROWED, MOVE) cell says REJECT.

**There is no `string` carve-out (ruled 2026-08-15).** A `string` parameter is a borrow like
every other owning type, and every consuming use of one is **CE2411**. A returned `string` self
would be a non-owning VIEW of the receiver's buffer, and nothing relates the view's lifetime to
the receiver's, so the `Display` idiom is spelled `return self.clone()`:

```sushi
extend string say_it() string:
    return self.clone()            # `return self` is CE2411

fn make_tag() string:
    let string s = "tag-{1}"
    return s.say_it()
```

The backend clears the owned bit of a method's `string` parameter on entry, so a method body
can never free the caller's buffer; no consuming use compiles, so the bit guards read paths
only. `.clone()` on a `string` copies unconditionally, a view included; the destructor keeps
its owned-bit guard. So `.clone()` is always a deep copy.

The mutable receiver is spelled **`poke self`**, an opt-in first parameter carrying the borrow
vocabulary the language already has —

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
an extension/perk method.

**Reads are unaffected.** A field read, a read-only method under the receiver, `.clone()` of
an owning field, and `.clone()` of the whole receiver are all legal — the last is the escape
CE2411 names. `peek self` and `peek self.field` work. A PLAIN parameter — an i32, a struct of
primitives — is never affected in either direction: it copies, and (BORROWED, PLAIN) adopts.

**Four read-only receivers, one gate.** A `match`/`foreach` binding (CE2414), a `peek`
reference (CE2408), the method receiver (CE2421) and a by-value method parameter (CE2422)
are the same rule with four rationales: a write through any of them cannot reach the value
it appears to write. The checker holds them as a TABLE of kinds behind one dispatcher
(`reject_readonly_write`, `passes/borrow/writes.py`), called from the four write sites, so a
fifth kind is one row rather than a fifth walk; `tests/unit/test_readonly_receiver_matrix.py`
fails if a kind in the table has no code. The codes stay separate for the reason the six
position codes do (§8.5): each carries its own escape. Here the escapes are what separate the
last two — a by-value parameter is redeclared `poke T`, and a receiver `poke self`.

The mechanisms themselves — the six ways a borrow is created, their extents, and the gate
that backs each rule — are `docs/design/borrowing.md`.

## 9. Invariants

**Annotation completeness.** `Provenance` must survive both monomorphization and field
resolution, with the same failure shape as `resolved_scrutinee_type`'s **CE0121**. A missing
stamp is **CE0129** (internal), so a gap surfaces loudly and never silently misclassifies.

**`RETURN` and `CAPTURE` need per-use behaviour** beyond the plain table. `RETURN` routes
through `consume()` with `ConsumingUse.RETURN`; its special case is "emit the value before scope
cleanup runs", which `returns.py` does, and it needs no new table cell. `CAPTURE` reads its
`Provenance` off a `Param`, not an `Expr` (`Lambda.captures` is a list of `Param`, not a list of
expressions) — a plumbing difference, not a rule difference; `classify` answers it exactly like
every other use.

**The seam is not a cleanup-registry lookup.** `consume`/`bind` take `Provenance` computed in
semantics and the resolved target type from the backend; neither queries a cleanup registry to
decide. `resolver_for` (`backend/ownership.py`) exists only to resolve an `UnknownType` name to
its struct/enum table entry before classifying it — a type lookup, not an ownership-registry
lookup.
