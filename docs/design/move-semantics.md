# Move-by-Value Unification for Owning Composites (#134)

*Design doc, 2026-07-19. Status: **implemented** (2026-07-20); **superseded in part** by
`docs/design/ownership-conventions.md` (Phase 9, 2026-08-14), which unified this document's two
ownership predicates, deleted the `COPY` type class this document designed around (a `string` moves
now, like every other heap-owning type — see §2's note below), and closed §3.1's residual-copy
question. Read this document for the #134 move-by-value decision itself (owning structs/enums move,
`.clone()` is the escape) and for its historical record; read `ownership-conventions.md` for the
current predicate and the current answer to "what does a field read do."
Companion to `docs/memory-management.md` (user-facing rules) and `docs/design/string-representation.md`
(the fat-pointer decision, which the string-move change did not touch).*

---

## 1. Decision

**Owning user structs and owning enums switch from copy-by-value to move-by-value**, unifying them
with `T[]`, `List@(T)`, `Own@(T)`, and capturing closures. After this change:

- Every value that *contains an owning resource* **moves** at ownership sinks (call args, rebinds,
  construction, array literals, returns). Reusing the source is **CE2405** (use-after-move).
- `.clone()` becomes the **single, explicit** way to copy an owning value — newly auto-derived for
  structs and enums (the machinery already exists as the implicit `emit_value_clone`).
- Plain-data composites (primitives, strings, and composites of only those) keep **copy**
  semantics. This is Rust's `Copy` tier, derived automatically instead of opted into.

**Why.** The current split is memory-safe but inconsistent: refactoring `f(list)` into
`f(WrapperStruct)` silently turns a move into a hidden O(n) deep copy — exactly the invisible cost
Sushi's move-only + explicit-`.clone()` model exists to surface, and the same rationale used to
justify moving `List`/`Own` in #131/#133. Both #134 and the language's own design philosophy argue
for the flip.

---

## 2. The rule: move-ness is compositional, and it was NOT `needs_cleanup` — until Phase 9 made it so

> **A type moves by value iff it transitively contains an owning resource.**
> Owning resources are what `is_owning_type` recognized at the time: dynamic arrays (`T[]`),
> `List@(T)`, `Own@(T)`, and capturing closures. A struct/enum/fixed-array *inherits* move-ness from
> its fields/variant payloads/elements. Everything else copies.

**Superseded: the string distinction is gone, and the two predicates ARE unified.** Everything in
this section, as originally written, argued the opposite of what shipped a few weeks later. It is
kept for the historical reasoning — the string-owning-heap-but-copies asymmetry really was true at
the time — but `docs/design/ownership-conventions.md` §4.1/§6 is the current answer: **`string` now
moves like every other heap-owning type**, so `needs_cleanup` ("must RAII free it?") and the move
predicate ("does it own a resource?") became the *same question* by construction, and the backend's
`needs_cleanup` is now a thin alias of the single semantics predicate, `owns_heap`
(`sushi_lang/semantics/typesys.py`). The reasoning below explains why unifying them seemed unsound
at the time — the fix was not to keep them apart, but to remove the one type they disagreed about
from the disagreement.

**The string distinction (as it stood in 2026-07-19, before Phase 9).** The backend's `needs_cleanup`
(`backend/destructors.py:668`) answered *"does RAII have to free something?"* — and strings answered
**yes** (heap data behind the `owned` bit). But strings were **copy types**
(`docs/design/string-representation.md`; copies were cheap and safe via the owned-bit protocol). If
the move predicate had been `needs_cleanup`, a struct with only a `string` field would *move* while a
bare `string` *copied* — recreating the wrap-a-value inconsistency this design set out to eliminate.
So at the time, the new predicate had to be a **different question** than `needs_cleanup`:

| Predicate | Question | strings (as of 2026-07-19) | `struct {string}` (as of 2026-07-19) | `struct {i32[]}` |
|---|---|---|---|---|
| `needs_cleanup` (backend, existed) | must RAII free it? | yes | yes | yes |
| `moves_by_value` (semantics, new then) | does it contain an owning resource? | **no** | **no** | **yes** |

At the time, a `struct {string name; i32 id}` stayed a copy type: passing it by value deep-copied
(the string field was cloned), and the source stayed usable. **This is no longer true.** Since
`string` itself moves now (Phase 9), `struct {string name; i32 id}` is a **move** type: passing it by
value moves it, and the source is CE2405 on reuse — unless the struct's string field was never bound
from a literal to begin with, in which case the point is moot, because the option-B literal exception
lives on a *bare `string` binding*, not on a struct field (`ownership-conventions.md` §4.1). A
`struct {i32[] data}` was and remains a move type.

### 2.1 Predicate implementation

Extend `is_owning_type` (`sushi_lang/semantics/typesys.py:275`) — or add a sibling
`type_moves_by_value(t)` that delegates to it for the base cases — with the recursive arms.
(Note: user-facing generic syntax is `@(...)` since #235, but **interned type-identity names
keep the `<...>` form**, so the base-case `.name` match below still tests `Own<`/`List<` — do
not rewrite those name-prefix checks to `@(`.)

- `StructType` → any field's type moves (recurse; **cycle-guarded** with a visited set, mirroring
  `can_struct_be_hashed` in `semantics/generics/hashing.py:52-115`, which is the proven template
  for this exact recursion shape).
- `EnumType` → any variant's `associated_types` entry moves.
- `ArrayType` (fixed `T[N]`) → element type moves (mirrors `needs_cleanup`'s element-driven arm,
  #185).
- Base cases unchanged: `DynamicArrayType`, `GenericTypeRef`/name `Own<`/`List<`, `FunctionType`
  with captures → move; everything else (primitives, `string`, `ForeignPtrType`, non-capturing fn
  values) → copy.

**Placement and timing.** The predicate lives in **semantics** (`typesys.py`, next to
`is_owning_type`) — the borrow checker needs it and `semantics` must never import `backend` (the
Tier 4.1 invariant, enforced by grep). It needs no type table: `StructType.fields` and
`EnumType.variants` carry resolved types inline after Pass 1.7, and the borrow checker runs as
Pass 3, so every type it sees is concrete (monomorphized generics included — `Pair@(i32, List@(i32))`
is an ordinary concrete `StructType` by then). Guard the `UnknownType` case explicitly: treat it as
non-moving and rely on Pass 2 having already rejected unresolved types.

**Sync risk (the one real hazard).** The borrow checker (semantics) decides *what is an error*;
the backend decides *what code is emitted*. If they ever disagree about a type's move-ness, the
result is unsoundness (backend moves what semantics didn't flag → silent use-after-free; or
semantics flags what backend copies → spurious CE2405). Mitigations, both required:

1. The backend imports and uses the **same** semantics predicate at every flip site (`backend`
   importing `semantics` is the allowed direction).
2. A unit test (`tests/unit/test_move_predicate_sync.py`) that builds representative types —
   plain struct, string-only struct, `struct {i32[]}`, nested owning struct, owning enum,
   recursive enum via `Own@(T)`, fixed array of owning structs, `struct {string, List@(i32)}` —
   and asserts the predicate's verdicts against a hand-written expectation table, plus asserts
   that every type where `moves_by_value` is true also satisfies backend `needs_cleanup`
   (move implies something-to-free; the converse is deliberately false — strings).

---

## 3. Context-by-context semantics

The design principle: **ownership sinks move; reads from a continuing owner copy.** The second
half is the "V5 rule" that bit three times during the 4.8 leak cluster (*"a value read out of a
container that keeps owning it must be copied"* — #185, #203, #200) and it mirrors Rust, which
also refuses to move out of an index.

| Context | Today (struct/enum) | After #134 | Notes |
|---|---|---|---|
| by-value call argument, bare `Name` | deep-copy | **move**, CE2405 on reuse | joins T[]/List/Own (#133) |
| `let b = a` rebind, bare `Name` | deep-copy | **move**, CE2405 on reuse | joins arrays/closures |
| construction field value, bare `Name` (`Buffer(data: x)`) | struct/enum cloned; `T[]` already moves | **move** | removes the constructor asymmetry (`structs.py:123-148`) |
| array-literal element, bare `Name` (`from([r1, r2])`) | cloned (#185) | **move** | sink, like Rust's `vec![a, b]` |
| `return x` of a local | moved out (backend) | unchanged | already correct (`returns.py`) |
| closure capture | move (T1.5) | unchanged | already correct |
| **`s.field` / `MemberAccess` source** in any of the above | cloned | **stays cloned** | read from a continuing owner; Sushi has no partial moves |
| **`own.get()` deref source** in any of the above | aliased (#256 bug) | **cloned** | same row as `s.field`: `Own` is a smart pointer, `get()` reaches *through* a live owner. Cloned at the SINK, not at the access site — `get()` returns bare `T` (not `Maybe@(T)`), so `o.get().field` chains, and a copy there would belong to no owner and leak |
| container get-out (`list.get(i)??`, `arr[i]`) | cloned (#203/#185) | **stays cloned** | V5 rule; the guard tests must stay green |
| struct-field read (`let x = s.field`) | cloned | **stays cloned** | same |
| `match` / `foreach` bindings | borrow | unchanged | |
| `peek` / `poke` arguments | borrow | unchanged | the way to *not* move |
| HashMap/List insert of struct/enum values | key+value moved into container (T2.2/N1) | unchanged | already move-shaped |

**Phase 9 moved the "stays cloned" rows again, and made two "borrow" cells true.** Two kinds of claim
in this table did not survive as written:

- The four **"stays cloned"** rows (`s.field`, `own.get()` deref, container get-out, struct-field
  read) no longer clone at all. `docs/design/ownership-conventions.md` §4.2 classifies every one of
  them `BORROWED`; a `let` of one binds without owning (§8) and a genuine consuming use of one is
  **CE2411**, with `.clone()` doing explicitly what these rows used to do silently. This is *not* a
  behavior regression — it is §3.1's upgrade path (below), taken.
- The **"borrow, unchanged"** cells for `match`/`foreach` bindings were optimistic when this table
  was written: at the time, a binding was in fact "a shallow byte-copy of an owner's fat pointer"
  with no type recorded and no diagnosis possible for misuse of it
  (`ownership-conventions.md` §8.1) — so "borrow" described the codegen shape, not a checked
  property. It is now **actually true**: `ownership-conventions.md` §8 makes it a typed, tracked
  borrow, with **CE2408** on mutation and **CE2411** on consumption. The `peek`/`poke`-argument row
  was already backed by real borrow checking at the time and needed no such correction.

### 3.1 The deliberate residual copy, and its implications (historical — resolved)

**Resolved by `docs/design/ownership-conventions.md`.** This section's "upgrade path" asked for one
missing feature — `let`-borrow bindings — before the residual copy below could become a hard error.
That feature landed, but not in the shape this section anticipated: rather than a new *syntax*
(`let peek T x = s.field`), a `let` reading through a live owner now implicitly BORROWS
(`ownership-conventions.md` §8), tracked with a real lifetime (**CE2412** on mutating the owner
while the binding is live) and a real consuming-use check (**CE2411**, `.clone()` as the escape).
The explicit reference-typed-`let` syntax this section imagined was itself proposed later as #252,
assessed against the now-shipped implicit mechanism, and rejected as **CE2413** — it would have been
a second, overlapping way to say the same thing. See `ownership-conventions.md` §8.4 for the
untangled numbering (#242 is this section's issue; #252 is the syntax that was rejected). The
sequencing this section worried about — "gated on the same missing feature" — is therefore unblocked:
the gate opened, just via a different mechanism than the one anticipated below. The rest of this
section is kept for its historical reasoning.

A `MemberAccess` source (`take(s.field)`, `from([s.field])`, `let x = s.field`) used to keep a silent
deep copy, and since #256 an `Own@(T).get()` deref (`take(o.get())`, `let x = o.get()`) is on the
same footing. Be honest about what this means: **the #134 trap survives in field-access and deref
form** — after the flip, `f(list)` moves but `f(wrapper.list)` and `f(owned.get())` still silently
deep-copy. The hidden-cost surface is *narrowed* (to expressions that visibly reach through an
owner), not eliminated. **The two forms — field access and deref — retire together** under the
upgrade path below: one rule, one rationale, and both gated on the same missing feature
(`let`-borrow bindings), so the warning and the hard error should cover both in one change.

**Why copy anyway.** Rust's alternative is a hard error ("cannot move out of a field"), forcing
explicit `.clone()` or a borrow — ergonomic in Rust only because reference *bindings* exist
(`let x = &s.field`). **Sushi has no `let`-borrow bindings** (the grammar has `peek T` types, but
borrows work only as parameter types and call-site expressions; zero tests bind one locally). A
hard error today would therefore force `.clone()` on *every* read of an owning field — including
`let payload = msg.data` in exactly the decoder-shaped code R1 will write — mandatory ceremony
with no escape hatch. Copy is the right call until `let`-borrows exist.

**Why the silent copy cannot leak (accounting argument).** A deep copy leaks only if the clone
ends up with no registered owner. Every copy site in the table hands the clone to an owner
immediately: callee parameter (callee RAII), array element (array RAII), `let` binding
(scope-exit RAII), constructor field (struct RAII) — one allocation, one owner, freed once. This
design also adds **zero new copy sites**; it only deletes some (bare `Name`s become moves). The
historically real hazard is the *adjacent* class — a clone emitted for a consumer that is not an
owner (unowned temporaries: N1's `println(words[0])` print-temp, #159's unowned Result/Maybe
temporaries). The one **new** member of that class this design introduces is a **discarded
`.clone()`** (bare expression statement, or a clone argument orphaned by a later argument's `??`
error path) — pinned by a dedicated leak test in §6. The mirror-image hazard belongs to the
*move* sites: a flip site that stops cloning but forgets to mark the source moved is a
double-free, which `EXPECT_NO_LEAKS` + the interposer catch from both directions (leak = positive
balance; double-free = abort).

**Upgrade path — taken, skipping the intermediate warning stage.** This section proposed a staged
rollout: first a CW warning on owning-`MemberAccess` at by-value sinks, then a Rust-style hard error
once `let`-borrow bindings landed. What shipped went straight to the equivalent of the hard error,
without ever emitting the intermediate warning — because the "hard error" and "let-borrow binding"
turned out to be the same mechanism rather than two staged ones: reading through an owner now BINDS
(no copy, no error) at a `let`, and errors (CE2411) only at a genuine consuming use, which is exactly
the Rust shape this section held up as the target (`match &x { Some(v) => take(v.clone()) }`). No
working program's meaning changed silently — every behavior change here is a new compile error.

---

## 4. Auto-derived `.clone()` for structs and enums

The explicit escape hatch, so `take(buf.clone())` keeps working code working. Mirror the **hash
auto-derivation pipeline** (Pass 1.8) exactly — it is the established pattern for "synthesize a
method on every user type with a lazily-bound backend emitter":

1. **Registration pass** (extend `semantics/passes/hash_registration.py` or a sibling): for every
   struct and enum in the table, `register_builtin_method(type, BuiltinMethod(name="clone",
   return_type=<same type>, arity 0, llvm_emitter=_lazy_clone_emitter(kind, type)))`. Register for
   **all** structs/enums, not only owning ones — a plain-data clone is trivially the value itself,
   and uniform availability keeps generic code simple. (Unlike `.hash()`, there is no
   CE0052-style exclusion: `emit_value_clone` already handles every shape RAII handles, including
   recursive types via out-of-line emission. **Confirmed by what shipped**: `.clone()` is total over
   every type — primitives, `string`, fixed and dynamic arrays, `List@(T)`, `Own@(T)`, and now a
   function value too (the fat pointer's `clone_ptr`, see `docs/design/closures.md`) all have one,
   with `tests/unit/test_clone_totality.py` as the gate. The one gap is `HashMap@(K, V)`, absent
   only because it is absent from `owns_heap`'s callers that ask this question, not because of a
   CE0052-style structural exclusion.)
2. **Lazy backend binding**: mirror `_lazy_hash_emitter` (`semantics/generics/hashing.py:284-296`)
   + `register_clone_emitter_factory` self-registration from the backend (pattern:
   `backend/types/structs.py:207`), with a CE0123-style internal error if the factory is missing.
3. **Backend emitter**: a thin wrapper over the existing `emit_value_clone`
   (`backend/expressions/memory.py:454`) — no new clone logic.
4. **Dispatch**: a `try_emit_struct_clone` / `try_emit_enum_clone` slot in the method dispatch
   chain (`backend/expressions/calls/dispatcher.py:355-363`, next to the hash slots). **Order
   matters** (V3/#199): the dispatcher must gate on the receiver's *type* before the method name.
5. **Semantics validation**: Pass 2 must know `clone()` takes no args and returns the receiver's
   type (extension-ABI style bare value, not Result — matching `.hash()`).

Arrays and `List` keep their existing `.clone()`; `Own@(T)` gains one only if the implementation
finds it free (not required by this design — `Own` is rarely cloned and `.get()` copies out).

---

## 5. Implementation map

Ordered so each step is independently verifiable. One PR, red-first tests per project rails.

### 5.1 Semantics
- `semantics/typesys.py` — the compositional predicate (§2.1). Decide during implementation
  whether to extend `is_owning_type` in place or add `type_moves_by_value` delegating to it;
  extending in place is preferred **iff** an audit of all `is_owning_type` call sites confirms
  every caller wants the new answer (expected: yes — they are all move/ownership sites).
- `semantics/passes/borrow/` — no structural change expected: `_mark_moved_if_applicable`
  (`:847`) and the call-arg loop (`:546-552`) already delegate to the predicate, and CE2405
  already carries the two-location "moved here" note (`_emit_use_after_move`, `:822-832`).
  Verify the branch-join reconciliation (`:427`) behaves for struct moves in `if` arms — the
  T2.1 machinery is type-agnostic but has only ever been exercised by arrays/List/Own.
- Clone registration pass (§4, step 1) + Pass 2 arity/return validation (step 5).

### 5.2 Backend (every site consumes the semantics predicate — §2.1 sync rule)
- `backend/expressions/calls/dispatcher.py` — `_deep_copy_struct_value_args` (`:199`): remove the
  owning struct/enum clone; those params flow into `_move_owning_value_args` (`:219`) instead,
  which marks the source moved (`mark_struct_as_moved` exists). String-only/plain composites keep
  the copy path.
- `backend/statements/variables.py` — `_clone_owning_struct_alias` (`:137-165`): for a bare
  `Name` RHS of a **moving** type, mark moved instead of cloning (mirror the array-rebind path).
  Keep the clone for `MemberAccess` RHS and for copy-type composites (its docstring's "#60/#134
  copy types" contract text gets rewritten to the new rule).
- `backend/expressions/structs.py` — construction (`:123-148`): bare-`Name` owning struct/enum
  field values move (mark source) instead of cloning; `MemberAccess` sources keep the clone;
  the existing `T[]` move branch (`:84-107`) is the template.
- `backend/types/arrays/utils.py` — `emit_array_literal_elements` (`:16-50`): bare-`Name` owning
  elements move instead of cloning; `MemberAccess` elements keep the clone.
- `backend/functions/helpers.py` — callee-side registration (`:329-359`) already frees a by-value
  owning composite exactly once; only the "#60 copy semantics" comments change. Verify the
  moved-in (rather than freshly-cloned) value is registered identically.
- `backend/statements/returns.py` — verify the owning-struct/enum move-out branch (past `:95`)
  covers enums; no semantic change.
- Clone emitter factory + dispatcher slots (§4, steps 2-4).

### 5.3 Move tracking is already sound for this
The backend's move/cleanup registries are **slot-identity keyed** since T2.2 (`789e11c`) — not the
flat name-keyed sets the old code comments describe — so name shadowing and sibling-scope name
reuse cannot poison a moved struct's sibling. The T2.2 guard tests
(`tests/memory/test_run_move_then_reuse_name.sushi`, `test_warn_shadow_owning_*.sushi`) must stay
green and leak-clean; they are the regression net for this claim.

---

## 6. Test plan

Project rails apply: every behavioral change gets a test that **fails red on today's `main`**
first; leak-sensitive tests carry `# EXPECT_NO_LEAKS`; error tests pin the code **and both
locations** of the relational diagnostic; value tests use `EXPECT_STDOUT_EXACT`.

**New tests (red-first):**
- `tests/memory/test_move_struct_param.sushi` + `test_err_move_struct_param_use_after.sushi`
  (CE2405 with "moved here" note) — mirror the existing `test_move_{array,list,own}_param` set.
- Same pair for an owning **enum** param.
- `test_move_struct_rebind.sushi` + err twin (rebind moves).
- `test_move_struct_into_constructor.sushi` + err twin (construction field moves).
- `test_move_struct_into_array_literal.sushi` + err twin.
- `test_move_struct_clone_keeps_source.sushi` — `take(buf.clone())`; source usable; zero leaks
  (mirrors `tests/memory/test_move_clone_keeps_source.sushi`).
- `test_clone_plain_struct.sushi` — `.clone()` on a plain-data struct works and is a no-op copy.
- `test_copy_string_only_struct.sushi` — **the string-distinction pin**: a `struct {string}`
  passed by value still copies; source usable; zero leaks. This is the test that fails if the
  predicate is wrongly built on `needs_cleanup`.
- `test_err_move_struct_in_loop.sushi` — struct moved in a `foreach` body (exercises T2.1's
  fixed-point for the new type class).
- `test_clone_discarded_no_leak.sushi` — `# EXPECT_NO_LEAKS`: a `.clone()` whose result is
  discarded (bare expression statement) and a clone argument orphaned by a later argument's `??`
  error path must both be freed — the unowned-temporary class (§3.1; precedents: N1's print-temp,
  #159).
- `test_memberaccess_copy_keeps_owner.sushi` — `# EXPECT_NO_LEAKS`: `take(s.field)` copies;
  `s` stays fully usable and both the copy and the original free exactly once (§3.1 pin).
- Unit: `tests/unit/test_move_predicate_sync.py` (§2.1).

**Tests that flip (copy → move):**
- `tests/unit/test_struct_raii.py::test_byvalue_struct_param_freed_by_callee` and
  `::test_byvalue_struct_arg_deep_copied_at_call_site` — rewritten to assert move semantics.
- `tests/types/test_struct_nested_deep_copy.sushi` (+ `_stress`) — constructor field values
  become moves; rewrite to use `.clone()` where the test genuinely wants two copies (which also
  exercises the new `.clone()`).
- Any test that reuses a struct after passing it by value (find them by running the suite —
  the failures ARE the inventory; per the F2 lesson, do not trust a grep to find them all).

**Guards that must NOT flip (stay green as-is):**
- Container get-out copies: `tests/memory/test_array_clone_owning_elements.sushi` *for its
  `MemberAccess`/get-out cases*, the #203 `List.get` tests, #200 field-array tests.
- `test_run_string_array_element_binding.sushi` (N1), HashMap owning-value tests (#140/#154/#219).
- The T2.2 shadow/move-reuse set (§5.3).
- All string copy-semantics tests.

**Full verification:** suite `--enhanced` 0 failed, `--leaks-only` 0 failed, pytest unit green,
`grep -rn "from sushi_lang.backend" sushi_lang/semantics/` still empty, ruff/mypy gates green.

---

## 7. Documentation to update (same PR)

- `docs/memory-management.md`, `docs/language-guide.md` — the by-value rules ("a struct is passed
  by copy" prose dies; the new rule table from §3 replaces it).
- `CLAUDE.md` memory section — rewrite the "structs and enums are the two 'copy' owning
  composites" paragraph; strike the #134 tracking sentence; document `.clone()` for structs/enums
  and the string-only-struct copy tier.
- `docs/tutorial/` + examples — PR #133 precedent: every example that reuses a struct after
  passing it needs `peek` or `.clone()`.
- Close #134 with a pointer to this doc.

---

## 8. Explicitly out of scope (as of 2026-07-19 — see the note on each item for current status)

- **Partial moves** (`let x = s.field` consuming `s`) — still out of scope; Sushi has none. But
  `let x = s.field` no longer *copies* either (§3.1's update) — it now BORROWS, which is a third
  option neither "partial move" nor "copy" that this document did not anticipate.
- **Move-out of containers** (`list.get(i)` transferring ownership) — still out of scope; a
  container's `.get()` never transfers ownership. What changed is what the reader gets when it
  doesn't transfer: a borrow (§3.1's update), not a copy.
- **A `Copy`/`Move` perk or user override** — still out of scope; move-ness stays structural and
  automatic. No library has needed an opt-out.
- **CW warning or hard error on `MemberAccess` deep copies** — **done**, and superseded by a better
  answer than either option this bullet considered. See §3.1: neither a warning nor an error was
  needed, because the read stopped copying (or erroring) and started borrowing.
- **`let`-borrow bindings** (`let peek T x = s.field`) — **landed, in a different shape.** Not the
  explicit reference-typed syntax this bullet named (that syntax was assessed later, as #252, and
  rejected as CE2413) — instead, every `let` reading through an owner implicitly borrows. See
  `docs/design/ownership-conventions.md` §8.
- **`Own@(T).clone()`** — shipped. `Own@(T)` has a `.clone()` (`backend/generics/own.py`), the
  explicit escape from CE2411 for an `Own@(T).get()` deref.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Semantics/backend predicate divergence (unsoundness) | single predicate consumed by both + sync unit test (§2.1) |
| Predicate recursion on recursive types (`enum MsgValue: Arr(MsgValue[])`) | visited-set cycle guard; template exists (`can_struct_be_hashed`) |
| Hidden reliance on implicit struct copies in the existing corpus | the flipped suite run is the inventory (F2 lesson); each failure is triaged into `.clone()`, `peek`, or a genuine move |
| Borrow-checker branch/loop paths untested for struct moves | dedicated loop/branch tests (§6); T2.1 machinery is type-agnostic |
| Dispatcher name-before-type ordering repeating #199 for `clone` | gate on receiver type first (V3/V7 rule), noted in §4 |
| `main`'s `string[] args` interplay | unchanged — CE2410 already forbids moving it; it is an array, not a struct |
