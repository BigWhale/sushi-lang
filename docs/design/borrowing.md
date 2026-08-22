# Borrowing

**Status: DECIDED and SHIPPED.** The reference subsystem is part of the ownership model
(PRs #306, #309, #313, #320, #325, #336, #342, #343 — 2026-08-15 and 2026-08-16). Every
mechanism in this document is enforced and has tests.

> **Sections 1 to 5 are SUPERSEDED by `docs/design/borrow-model.md`** (ruled 2026-08-16,
> issue #354). Borrow by default made an unmarked parameter a borrow for every kind of
> callee, so `peek` and `poke` are no longer the only two ways to say "do not take this".
> Read that document for the mode rule and this one for the borrow MECHANISMS — where a
> reference type may appear, the six ways a borrow is created, and the gate behind each
> rule. Two changes ripple through the text below: the `&` is gone from both words, and
> the sixth mechanism (a method parameter) is now simply *a parameter*, in any callable.

`docs/design/ownership-conventions.md` is the normative spec for ownership. This document
describes the borrow half only, and does not repeat it.

## 1. The two words

| spelling | meaning | how many at a time |
|---|---|---|
| `peek T` | read-only borrow | many |
| `poke T` | read-write borrow | one, and no `peek` with it |

A borrow names storage that something else owns. The owner keeps the value and frees it.
Four rules follow from that one sentence, and everything below is one of them:

1. **You cannot write through a read-only borrow.** The write lands on a private copy, or
   on storage the owner no longer holds.
2. **You cannot give a borrow to a position that takes ownership.** The value would get a
   second owner, and both would free it. The escape is `.clone()`.
3. **The owner is frozen while a borrow of it lives.** A mutation, a free, a rebind or a
   move of the owner would leave the borrow pointing at storage the owner no longer holds.
   This is Rust's E0502.
4. **A borrow cannot leave the function that makes it.** Sushi has no lifetimes, so nothing
   can relate a borrow to its owner in the caller.

## 2. Where a reference type may appear

The grammar's `?type` rule is recursive and universal, so `peek T` / `poke T` parses in
every type position. Semantics defines **two**:

- a function **parameter** — `fn f(peek T x)`;
- a **parameter inside a function type** — `fn(peek i32) -> i32`, and the lambda
  `|peek i32 x|` that satisfies it.

Every other position is a registered rejection at the declaration: CE2413 (`let`), CE2415
(struct field), CE2416 (enum payload), CE2417 (return type), CE2418 (nested reference),
CE2419 (generic type argument), CE2420 (extension or perk-impl target), CE5003 (FFI
signature), CE0114 (variadic element). One walk — `contains_reference` in
`semantics/type_predicates.py` — backs the six CE24xx codes. Its carve-out for
`FunctionType.param_types` is load-bearing: it keeps the second supported position legal
wherever a function type appears. The position table and the reason for each rejection are
in `ownership-conventions.md` §8.5.

## 3. The mechanisms

One surface vocabulary, six mechanisms. Each has its own extent and its own diagnostics.

| # | mechanism | spelling | extent | write through it | consume it |
|---|---|---|---|---|---|
| 1 | call-site borrow | `f(peek x)`, `f(poke y)` | one statement | the callee's mode decides | the argument is not consumed |
| 2 | reference parameter | `fn f(poke T x)` | the function body | `peek`: **CE2408**; `poke`: in place | **CE2411** |
| 3 | `let` binding of a read | `let v = c.get(0)??` | the enclosing block | not gated (§8) | **CE2411** |
| 4 | pattern value binding | `E.V(p)`, `foreach(n in ...)` | the arm or the loop body | **CE2414** | **CE2411** |
| 5 | pattern reference binding | `foreach(poke r in xs.iter())`, `Own(poke x)`, `E.V(poke p)` | the arm or the loop body | `poke`: in place; `peek`: **CE2408** | **CE2411** |
| 6 | method parameter, `self` included | `extend T m(H h)` | the method body | **CE2421** (receiver), **CE2422** (by value) | **CE2411** |

**1 — the call-site borrow** is tracked by counters that are cleared at the end of every
statement. Therefore the exclusivity rules have jurisdiction inside one statement only: a
second `poke` is CE2403, and a mixed pair is CE2407. A `poke` of a `poke` parameter —
forwarding it whole (`inner(poke cur)`) or by field (`set_port(poke cfg.port)`) — is
legal and silent: the borrow ends with the statement, and the callee can neither store
the reference nor write outside its declared mode. (CW2409 warned on the whole-parameter
form until 0.11.x; it was retired because it marked the mandated composition idiom while
guarding nothing the errors do not.) A move and a borrow of one owner in one statement is
CE2401, in either argument order. Two statements are two borrows, and are unaffected.

**2 — the reference parameter** is the position the subsystem is built for. It carries its
full `ReferenceType` in the borrow state, which is what makes the write gate answerable
(§5). A borrow is created at a USE site, so a reference parameter is forwarded as
`f(peek v)`, never as bare `f(v)`.

**3 — the `let` binding of a read** inherits BORROWED provenance: a field read
(`h.inner`), an index (`rows[i]`) and a container get-out (`c.get(0)??`, `own.get()`) all
borrow. The owner is frozen until the end of the block that declares the binding
(**CE2412**). A reference-typed `let` (`let peek T x = ...`) is **CE2413** — it parses,
but it would be an alias the checker does not track.

**4 — the pattern value binding** is compiled as a private copy, so a write through it
could never reach the owner. A rebind of the binding itself (`n := 99`) stays legal: it
re-initializes a local, and does not claim to write through. Each binding has a scope of
its own — an arm binding no longer replaces an outer local of the same name (#337).

**5 — the pattern reference binding** (#300) binds a POINTER into the owner's storage, so
`r.n := 5` and `p.push(9)` mutate in place. It registers with its full `ReferenceType`, so
every rule above applies by construction. The match half rests on the enum payload layout
`{i32 tag, [K x i64] data}`, whose naturally aligned payload offsets come from one
authority (`TypeSizing.payload_field_offsets`). Four fences: an iterable whose items have
no address is **CE2423** (a range, `HashMap.entries()`); a reference binding in a NESTED
match pattern is **CE2424** (extraction walks through temporary copies there); a temporary
scrutinee is CE2404; and a `poke` binding out of a `peek` owner is CE2408, out of a
constant CE2400.

**6 — the method parameter.** Every parameter of an extension or perk method is a borrow,
`self` included, and the caller keeps ownership (the #298 ruling). There is no `string`
carve-out (#338). The mutable form is an opt-in FIRST parameter:

```sushi
extend Counter bump(poke self) ~:
    self.n := self.n + 1
    return ~
```

The receiver then arrives by pointer, so the write reaches the caller's value. `peek self`
states the read-only default. A perk declares the mode in its signature, and the
implementation must match it (**CE4004**). A receiver parameter anywhere else is
**CE2425**. The full ruling is in `ownership-conventions.md` §8.6.

## 4. Coercion

`poke T` coerces to `peek T` at a **call site** — a safe downgrade of a borrow that is
passed once. The coercion is a property of the position, not of the type pair, so it does
NOT apply to a stored function type: `fn(peek T)` and `fn(poke T)` are different types
in both directions (CE2002). Without that invariance, one indirection defeats the write
gate (#335). The single coercion site is `semantics/passes/types/compatibility.py`;
`tests/references/test_borrow_coercion_matrix.sushi` and its two rejection companions pin
every position where the coercion does and does not apply.

## 5. One gate for each rule

**A write cannot reach through a read-only receiver.** Six kinds, three write shapes each
(a mutating method under the receiver, a field assignment, a `poke` borrow of it), plus the
indexed assignment, which routes through the same gate:

| kind | code | escape |
|---|---|---|
| `match` / `foreach` value binding | CE2414 | `.clone()`, mutate, store back; or a reference binding |
| `peek` reference | CE2408 | declare the parameter `poke` |
| method receiver | CE2421 | `poke self` |
| by-value method parameter | CE2422 | declare the parameter `poke T` |
| `let`-borrow binding | CE2426 | write to the owner; or `.clone()`, mutate, store back |
| unbound chained borrow (`o.get().items`) | CE2429 | `.clone()`, mutate, rebuild the owner (`o := Own.alloc(h)`); or a nested `Own(poke ...)` binding where the `Own` sits in an enum |

The first five kinds are a TABLE (`READONLY_RECEIVERS`) behind one dispatcher
(`reject_readonly_write`) with four call sites, so a new state-keyed kind is one row and not
a new walk — the fifth one (#344) cost exactly that. The codes stay separate because each
carries its own escape, and rows four and five show why that convention earns its keep: a
`match`/`foreach` binding is a private DEEP copy, so its write is only lost, while a
`let`-borrow shares the owner's DATA, so its write is lost AND a reallocating one frees the
owner's buffer. Same rule, different first answer.

**The sixth kind keys on SHAPE, not on state** (ruled 2026-08-20, #352; the live bug was
#407). The other five are each a NAMED thing carrying a `BorrowState`; an unbound chained
receiver has no name to hold state on. The rule is structural: **a write receiver must
reach its root — a NAME — through member and index steps only**. Whatever else the walk
stops at — a method call, a `??`, a plain call, an inline constructor — yields a
temporary copy, so the write would land on the copy and be lost
(`o.get().items.push(9)` printed the old length, exit 0, leak-clean).
`chain_call_boundary` (`reads.py`) finds the boundary, deliberately INVERTED — "a Name
is fine" rather than a list of boundary kinds — so a new expression kind is rejected,
never silently writable. The same dispatcher checks it BEFORE the state table, because
the boundary answer is the more precise one whatever the root's mode is. The boundary
span is the second location of the relational diagnostic. Three consequences:

- The READ through the same chain stays legal and leak-clean (#280; the receiver is a
  registered scope temporary).
- A FRESH temporary is rejected too (`make().items.push(9)`): the statement discards the
  value, so the write is dead either way. Swift rejects the same shape. One rule, no
  fresh/borrowed split.
- The `poke`-borrow shape of this kind was already gated — CE2404, "expression has no
  stable address" — so the kind covers the mutating method, the field assignment and the
  indexed assignment (which was a CE0000 ICE until this gate).

**A borrow cannot be consumed.** The ownership table's `(BORROWED, MOVE)` cell rejects, and
that is the whole implementation: `type_class_of` derefs a reference to its referent, so
all twelve consuming positions answer the same way with no per-sink work. The code is CE2411
and the escape is `.clone()`, which is total over types.

**An owner is frozen while a borrow of it lives.** CE2412, reported NLL-style: the owner is
invalidated at the change, and the error is reported at the next read of the borrow.

## 6. Where reference-ness lives in the compiler

- **Grammar / AST.** Two spellings of one concept: `ReferenceType` in a type position, and
  a `Borrow` expression at a use site.
- **`typecheck`** registers a reference parameter with its full `ReferenceType`, then UNWRAPS it
  at every mention. That gives borrow transparency — it is why `p.len()` works — at the
  price that no inferred type downstream can answer "is this a borrow?".
- **`borrow`** is therefore the only layer that can ask, because it holds the borrow state.
  All three callable kinds — plain function, extension method, perk method — go through one
  entry point (`_check_callable`), so a relational diagnostic renders its second location in
  a method body like anywhere else.
- **The ownership seam** needs no reference arm: the rejection happens in the borrow pass, before
  codegen. An unstamped consuming use in the backend is CE0129, which is fatal on purpose.
- **The backend** keys every deref on `variable_types`, which is saved and restored per
  function (#332).

## 7. What keeps this total

Each gate turns the next occurrence of its bug class into a red test:

| gate | what it pins |
|---|---|
| `test_borrow_dispatch_is_total.py` | an arm for every `Expr` node (CE0125) |
| `test_scope_dispatch_is_total.py` | the same for the scope pass (CE0130) |
| `test_peek_write_gate_is_total.py` | every member of `_MUTATING_METHODS` |
| `test_readonly_receiver_matrix.py` | every kind x shape cell of §5, the shape-keyed sixth kind included |
| `test_borrow_flag_lifecycle.py` | every `BorrowState` flag x flow event |
| `test_ownership_table.py` | the 3x2 table, reference rows included |
| `test_consuming_use_coverage.py` | nothing bypasses the backend seam |
| `test_method_body_diagnostics.py` | all three callable kinds render the same evidence |
| `test_enum_payload_layout.py` | the payload offsets mechanism 5 depends on |
| `tests/references/` | the behaviour corpus, ~50 programs |

## 8. Two questions about a `let`-borrow, not one

Mechanism 3 — a `let` binding of a read — is the kind that shows the two rules are
complementary rather than alternatives:

- **May I change the OWNER while the binding lives?** CE2412, answered NLL-style.
- **May I write THROUGH the binding?** CE2426, the §5 gate's fifth row.

Until #344 the second question had no answer, because the CE2414 row excluded this kind
by pointing at CE2412. `let i32[] v = h.items` followed by `v.push(9)` compiled: the write
was lost from the owner's view, a push that forced a reallocation freed the owner's buffer
(a double free plus a read of released memory), and `v.destroy()` did the same directly. A
field assignment and a `poke` borrow of the binding were ungated the same way.

The row keys on `is_let_borrow`, not on `borrows_from is not None`. An owner with no
`BorrowState` — a temporary, as in `let v = make()??.items` — records no owner name, and
the `borrows_from` spelling would have handed that case to CE2414, which tells the author
their `let` is a match binding. The temporary's buffer is just as real.

The sixth kind (CE2429) has only ONE of the two questions. A write THROUGH the chain is
the §5 gate's answer; there is no owner-side question, because the chain names no binding
the owner could invalidate — the temporary copy is freed at scope exit like any other
unbound owning temporary.

## 9. Not designed

Each of these is a rejection today, and each is lifted separately when its feature is
designed:

- **Lifetimes.** Nothing relates a borrow to the value it names, which is why a borrow
  cannot be returned (CE2417) or stored in data (CE2415, CE2416, CE2419).
- **A checked reference-typed `let`** (CE2413).
- **A reference binding in a nested match pattern** (CE2424).
- **A scrutinee-side spelling** for a mutable binding (Rust's `match &mut x`). Sushi marks
  the binding instead.
