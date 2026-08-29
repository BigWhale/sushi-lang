# The Sushi IR: SHIR and SLIR

**Status: PROPOSED.** Extracted from the working doc `IR.md` on 2026-08-29, after two
verification passes against the tree. Every design question is answered; the plan is not
yet approved. This document is the DESIGN: what the two IRs are, why they have the shape
they have, and what any migration must preserve. The phases, the risks and the progress
tracking stay in `IR.md`, and a detailed migration plan will be derived from this
document. Nothing in the language changes: every behaviour test passes before and after,
unchanged.

## The decisions, in one place

| # | Decision | Where |
|---|---|---|
| **S1** | Two levels: `AST -> SHIR -> SLIR -> LLVM`. The AST is NOT deleted | 5, 6 |
| **S2** | The AST keeps the smallest possible interface. Four questions, three gates | 6 |
| **S3** | `scope` STAYS on the AST. Three passes move to SHIR, not four | 7.1 |
| **S4** | `effects` STAYS on the AST. It runs whole-program, before any SHIR exists | 7.1 |
| **S5** | Every answer `typecheck` produces lives in `TypeckResults`. A SHIR node never gets a field filled in later | 7.3 |
| **R1** | Calling conventions from Swift SIL. Sushi's modes map one-for-one | 8.1 |
| **R2** | Value model from Rust MIR: places and locals, NOT SSA | 8.1 |
| **R3** | No unwinding machinery. `Call` is a STATEMENT, not a terminator | 8.1 |
| **Q1** | **SHIR is MONOMORPHIC.** Definition-site checking is a language change | 9 |
| **Q2** | The per-instantiation repeat loop STAYS | 9 |
| **Q3** | SHIR and SLIR are TWO hierarchies, not one node set with levels | 9 |
| **Q4** | `.slib` does not change | 9 |
| **Q5** | Constants are declaration-level, evaluated on the AST | 9 |
| **Q6** | A printer yes, a parser no. `--dump-shir` and `--dump-slir` | 9 |

---

## 1. The problem

Every number in this section was measured in the tree on 2026-08-29.

### 1.1 The AST does three jobs and was designed for one

| Job | Fit |
|---|---|
| Represent syntax, carry source spans for diagnostics | Correct. This is its job |
| Substrate for the 18 semantic passes | Adequate, and it strains |
| Input to code generation | Wrong tool |

Job 3 was never chosen. `parser -> AST -> emit` is the natural first shape of a compiler.
It works until the language grows ownership. Sushi passed that point.

### 1.2 There is already an IR. It is implicit.

The semantic passes record analysis facts as fields on AST nodes, and the backend reads
them back. The full set (census of 2026-08-29, run by turning on `slots=True`): the
`resolved_*` / `inferred_*` type family, `ownership_provenance`,
`conditional_move_names`, `range_checked`, `in_cast_context`, `integer_match_type`,
`variadic_arg_types`, `nom_marked`, `nom_span`, `is_synthesized`, `home_unit`,
`expected_type`, and the `callee_param_modes` / `callee_param_names` /
`callee_param_types` group.

This is an intermediate representation grown in place: a field per fact, scattered
across node classes, written by one pass and read by the backend, with no owner and no
completeness check. Before the census, most of these were declared on ONE class and
written onto ANOTHER — `callee_fn_type` declared on `Call` and stamped on `MethodCall`
and `DotCall`, `expected_type` declared on `Lambda` and stamped on `Name` — because an
open object cannot tell a declared field from a typo, so the other classes worked by
accident.

Today every dataclass in `semantics/ast.py` is `slots=True`, every analysis field is
declared on every class that takes it, and `tests/unit/test_ast_nodes_are_slotted.py`
keeps it that way. That CONTAINS the channel — a stray write raises at the site that
wrote it. It does not give any fact an owner, a single writer, or a completeness check.
Sections 7.3 and 7.9 do. The declared analysis fields are transitional (6.2): each one
is deleted when the pass that writes it moves to SHIR and its side table.

### 1.3 The ownership rule runs twice

`backend/ownership.py` calls `classify(provenance, type_class)` **while it emits LLVM
IR**. The rule runs once in the `borrow` pass to check it, and again at emission to act
on it.

`_provenance_of()` reads `source.ownership_provenance`. A missing value is CE0129:
fatal, with a deliberate no-fallback rule.

CE0129 is not a bug. It is the correct response to a missing note in a design that
passes notes. An IR removes the whole category, because the decision becomes an
instruction.

### 1.4 Ownership state is keyed on LLVM object identity

```
self._moved: Set['ir.Instruction'] = set()
self._flags: Dict['ir.Instruction', 'ir.AllocaInstr'] = {}
```

`MoveTracker` keys move state on `llvmlite` objects. Drop flags are armed by looking up
a **string name** in `codegen.current_conditional_moves`.

RAII is therefore entangled with `llvmlite` object lifetimes and with name strings.

### 1.5 The backend makes semantic decisions by matching on syntax

`emit_foreach` picks a loop protocol like this:

1. Test `isinstance(node.iterable, RangeExpr)`
2. Test `isinstance(node.iterable, DotCall)` and check `method in ("keys","values","entries")`
3. String-match the receiver type against `"HashMap<"`
4. Emit a **run-time** `icmp` against `-1` to tell a stdin iterator from an array iterator

The `typecheck` pass already knows the answer to all four. It has nowhere to write it
down.

### 1.6 Surface forms multiply backend code

The AST has 67 node classes. Many are syntax, not semantics:

`Print` / `PrintLn`, `Call` / `MethodCall` / `DotCall`, `ArrayLiteral` /
`DynamicArrayNew` / `DynamicArrayFrom`, `While` / `Foreach`, `Let` / `Rebind`,
`TryExpr`, `InterpolatedString`, `RangeExpr`.

Each distinction is backend code that exists only because the parser made it.

### 1.7 Scale of the coupling

| Measure | Count |
|---|---|
| Backend files / lines | 148 / ~24,000 |
| `isinstance` or `match` on AST nodes in the backend | 57 |
| `raise_internal_error` sites in the backend | 323 |
| Monomorphization code (AST subtree duplication) | 2,452 lines |

The 323 internal errors each guard a contract that nothing checks earlier.

---

## 2. Goals

**G1. One place where a fact is decided, and one place where it is read.**
A semantic decision is made by a pass and recorded as data. No consumer re-derives it.

**G2. Replace the analysis-fields-on-nodes channel with typed side tables.**
A field per fact, scattered across node classes with no owner and no completeness check
(1.2), becomes schema'd, owned, completeness-checked maps. See 7.3 — this is a real
change, not a rename.

**G3. Make ownership explicit.**
`move`, `copy`, `borrow`, `clone` and `drop` become instructions the `borrow` pass
emits, not decisions the code generator takes.

**G4. Make the compiler testable below end-to-end.**
Assert that a source shape lowers to an expected instruction sequence, with no `cc`.
This is a DEPENDENCY of the migration, not a benefit: for a stretch of the work it is
the only signal there is (section 11).

**G5. Reduce the backend.**
Collapse surface forms so that one lowering serves many syntaxes.

**G6. Enable optimizations LLVM cannot see.**
Clone elision, drop-flag removal, perk devirtualization, bounds-check removal. These
need facts that are erased before LLVM receives the module.

**G7. Keep diagnostics quality.**
No diagnostic may lose its location or its wording.

**G8. Give the AST the smallest possible interface, and only its own job.**
The AST is a record of syntax. Section 6 is the rule, and it is enforced mechanically.

## 3. Non-goals

**N1. A second backend.** LLVM stays. This work makes one possible later. That is a
side effect and not a reason.

**N2. Deleting the AST.** The AST keeps syntax and source spans. The `ast_builder` is
unchanged. Somebody will propose finishing the job in month five; the answer is
section 6.

**N3. Faster compilation.** An IR is a place to put optimizations. It is not itself
one. Expect a small slowdown until the optimization work starts.

**N4. New language features.** No user-visible change. Every behaviour test passes
before and after, unchanged.

**N5. Changing the pass order.** The names and the order in the
`SemanticAnalyzer.check()` docstring stay.

**N6. Fixing the generic inference gaps.** Ruling Q1 means Known Limitations 7 and 8
are NOT addressed by this work. They need definition-site checking, which is a language
change. Recorded so that nobody expects them to fall out.

---

## 4. Language facts that fix the design

Each fact below is a property of Sushi, not of the implementation. Each one settles a
design choice, and each was verified in the tree.

### 4.1 Parameter modes are CONVENTIONS, not types

This decides which IR to model on. Sushi declares a mode at both ends of a call
(CE2427). Swift SIL declares conventions. Rust derives everything from the type and has
no convention concept at all.

| Sushi | Swift SIL | Rust |
|---|---|---|
| `string x` (the default borrow) | `@guaranteed` | follows from `&T` |
| `nom string x` | `@owned` | follows from `T` by value |
| `peek string x` | `@in_guaranteed` | follows from `&T` |
| `poke string x` | `@inout` | follows from `&mut T` |

Four for four. **On the ownership axis, SIL is the closer model** (ruling R1). MIR
offers Sushi nothing here, because Rust never needed the concept.

### 4.2 Sushi does not unwind

`RuntimeErrors.emit_runtime_error` writes to stderr and **exits the program**. There
are no exceptions; an error is a `Result` value, and a trap aborts.

A large part of Rust MIR's weight comes from unwinding. Every `Call` terminator carries
an unwind successor, cleanup blocks exist for the unwind path, and drop elaboration
must be correct on both paths.

Sushi needs none of it. This is a permanent simplification and it belongs to the
language, not to the implementation. See ruling R3.

### 4.3 Sushi's generics are TEMPLATES, not bounded generics

A perk constraint is checked against the **concrete** type at the call site:

```
if not validator.perk_impl_table.implements(type_name, perk_name):
```

`type_name` comes from the resolved argument type. Nothing anywhere checks a generic
**body** against its declared bounds. A search for definition-site checking returns
nothing.

This is C++ template semantics, not Rust generic semantics, and it is the fact that
settles Q1. It also explains CE2061, which is registered as `Category.INTERNAL`: a
monomorphized function that is missing is a compiler-internal failure, because nothing
verified the generic body on its own.

### 4.4 An iterator is not a value

`let Iterator@(i32) it = arr.iter()` fails with CE2001: `Iterator` is not a nameable
type. An iterator exists only as the iterable of a `foreach`. So `typecheck` always
sees the source expression that produced it, and the static `LoopKind` pick of 7.7 is
safe. This is a language fact worth guarding: the day an iterator becomes bindable,
`LoopKind` needs a dynamic variant.

### 4.5 `expand` dies in `monomorphize`

`monomorphize/unroll.py` rewrites every `Expand` into ordinary statements, and
`passes/types/visitor.py` rejects a survivor as CE0119. SHIR is built after
`monomorphize`, so SHIR needs no `Expand` node.

### 4.6 A rebind frees the OLD value, in a fixed order

`emit_rebind` reads the new value FIRST, then destroys the old value, then stores. The
order is normative: the source may alias the value about to be freed (#303 double-freed
a string, #304 leaked an array). Drop placement carries this as rule 6 of 8.10.

### 4.7 A constant is read while its unit's AST is built

`const_eval` is called from `ast_builder/builder.py`, because a fixed array's size is
read while that unit's AST is built (Known Limitation 14). That path is AST-level by
language design and cannot move. This settles Q5: constants are a declaration-level
concern, and no IR level ever evaluates one.

---

## 5. The end state

```
source
  |
  v
 AST  ......... syntax and source spans. Declarations. NOTHING ELSE.  (section 6)
  |             `monomorphize` is the ONE pass that rewrites it.
  |
  |  lower (bodies only, per instantiation)
  v
 SHIR  ......... typed, names resolved, calls resolved, MONOMORPHIC.   (section 7)
  |             `typecheck`, `lift` and `borrow` run here. `scope` does NOT (S3).
  |
  |  lower
  v
 SLIR  ......... CFG, places, explicit ownership and conventions.      (section 8)
  |
  v
LLVM IR
```

Two rules define the shape:

- **SHIR is a tree. SLIR is a graph.** SHIR keeps nested expressions because `scope`,
  `typecheck` and `borrow` want them. SLIR is basic blocks with explicit control flow.
  This structural gap is also why they are two hierarchies (ruling Q3).
- **Both levels are monomorphic** (ruling Q1). `monomorphize` keeps its position, ahead
  of SHIR construction, and keeps working on the AST.

---

## 6. The AST interface

**The rule: the AST is a record of what the user wrote. It answers questions about
syntax. It answers nothing else, and it carries nothing else.**

This is G8. It is the reason the work is worth doing at all — 1.1 says the AST is
overloaded, not wrong. Take the extra jobs away and it is a good data structure again.

### 6.1 The four legitimate questions

Only these may be asked of the AST once the migration completes:

1. **"What did the user write here?"** — declarations, and bodies before lowering.
2. **"Where in the file is it?"** — `loc: Span`, on every node, for every diagnostic.
3. **"What was next to what?"** — lexical adjacency. The `docs` pass needs this and
   only the AST can answer it. CW7001 says *"a blank line or a comment between the two
   breaks the attachment"*. No IR can answer that, at any level, ever. This question
   alone makes the AST permanent.
4. **"What does this unit declare?"** — the declaration table the whole-program passes
   from `collect` to `effects` read.

### 6.2 What the AST must never do again

- **Carry an analysis result.** No pass may write an analysis fact onto a node. The
  channel of 1.2 is the failure this design exists to end. The DECLARED analysis
  fields that contain it today (`ownership_provenance` on `Node`,
  `conditional_move_names` on `Block`, one shared `callee_*` set on the three call
  classes) are TRANSITIONAL: each is deleted when the pass that writes it moves to
  SHIR and its side table.
- **Be rewritten by more than one pass.** `monomorphize` is the single sanctioned
  rewriter (ruling Q1 keeps it there). Every other pass treats the AST as immutable.
- **Be read by the backend.** The new backend reads SLIR and nothing else.
- **Be read by a body-level pass.** Once the passes move, `typecheck`, `lift` and
  `borrow` read SHIR only. `scope` and `effects` are the exceptions (rulings S3 and
  S4), and 7.1 says why for both.

### 6.3 The lowering surface

The entire interface between the AST and everything downstream is meant to be two entry
points:

```
lower_body(ast_block, ctx)  -> ShirBody      # bodies, once scope has run
declarations(program)       -> DeclTable     # declarations, whole-program passes
```

Anything that needs a third entry point is a design smell and should be argued for in
this document first.

### 6.4 Enforcement, not convention

Three gates. All are cheap, and a rule with no gate does not survive contact with a
deadline.

| Gate | Mechanism | When |
|---|---|---|
| No node may take a stamp | `@dataclass(slots=True)` on all 67 classes. A stray write raises `AttributeError` | **In force** — `tests/unit/test_ast_nodes_are_slotted.py` |
| A body-level pass may not read the AST | `typecheck`, `lift` and `borrow` import `shir`, not `ast` | when the passes move to SHIR |
| The backend may not read the AST | `grep -rn "semantics.ast" sushi_lang/backend/` is empty | when the old backend is deleted |

A sibling gate is also already in force: `tests/unit/test_llvmlite_containment.py` — no
llvmlite IMPORT outside `backend/` and `sushi_stdlib/`. `semantics` no longer names an
LLVM type, which is the precondition for both IRs living there.

---

## 7. SHIR — the Sushi High-level IR

**Purpose:** the substrate for semantic analysis of function bodies.

**Shape:** a tree. Nested expressions and structured control flow, because `typecheck`
and `borrow` both want them.

**Position:** built after `monomorphize`, from already-specialized AST bodies, once per
instantiation (ruling Q1).

### 7.1 Two passes stay behind: `scope` and `effects`

SHIR resolves a name to a `LocalRef(LocalId)`. Something must first decide **which**
binding a name refers to, and that is what `scope` does. So the lowering already needs
`scope`'s answer before it can build a single node.

`scope` also asks purely source-level questions — is this name declared, is it used
before its declaration, does it shadow (CE1xxx). Those are questions about what the
user wrote, like the `docs` pass.

**Ruling S3: `scope` runs on the AST and produces the binding table. AST-to-SHIR
lowering consumes it. Three passes move to SHIR: `typecheck`, `lift`, `borrow`.**

`effects` has the same shape of problem from the other side. It is a WHOLE-PROGRAM pass
(`passes/borrow/destroy_effects.py`): it walks every body once, before the per-unit
loop, and computes which functions destroy a `poke` parameter, transitively (#168). At
that point in the pipeline no SHIR exists — SHIR bodies are built inside the per-unit
loop. So `effects` cannot move without restructuring the loop, and it does not need to:
it reads bodies and writes only a summary table that `borrow` consumes.

**Ruling S4: `effects` runs on the AST, whole-program, exactly where it is. It is the
second and last sanctioned body-reading AST pass, beside `scope`.**

The pass order and every pass name are unchanged (N5).

### 7.2 The unit

```
ShirBody
  fn_id   : DeclId                 # the declaration this body belongs to
  params  : [ShirParam]
  locals  : [ShirLocal]            # from the `scope` pass
  block   : ShirBlock

ShirParam
  local      : LocalId
  ty         : Type
  convention : Convention          # from the declared mode -- section 8.3
  loc        : Span

ShirLocal
  id   : LocalId
  name : str                       # the source name, for diagnostics
  ty   : Type | None               # None until `typecheck`; see 7.3
  loc  : Span
```

Every node carries `shir_id` and `loc: Span`. No expression node carries a type.

### 7.3 Everything `typecheck` decides lives in a side table

`typecheck` RUNS on SHIR, so SHIR must exist before any type is known — no SHIR node
can carry a field whose value only `typecheck` knows, and `slots=True` on the SHIR
nodes refuses the late write.

```
ShirNode      : shir_id, loc      # and its own operands. NO type field
TypeckResults : one owner, written by `typecheck`, completeness-checked when it ends
```

**Ruling S5: every answer `typecheck` produces lives in `TypeckResults`.** That covers
more than types:

| Decision | Keyed by |
|---|---|
| the node's type | `shir_id` |
| the resolved `Callee` of a call (7.6) | the call's `shir_id` |
| the `LoopKind` of a loop (7.7) | the loop's `shir_id` |
| the field INDEX of a `Field` (the node carries only the name) | the `Field`'s `shir_id` |
| the resolved `Convention` of each argument | the call's `shir_id`, per position |

**This is not the stamp channel returning.** Four differences, and each one is the
reason a stamp went wrong:

| The old stamp channel | `TypeckResults` |
|---|---|
| a field per fact, on whichever class first needed it | one table |
| declared on one class, written onto its siblings (1.2) | one declared value type |
| any pass may write | one pass writes it |
| a missing entry is CE0129, at emit time | completeness is checked when `typecheck` ends |

This is exactly Rust's shape: HIR plus `TypeckResults`, consumed together by MIR
building — method resolutions and field indices live inside `TypeckResults` there too.
The census of 1.2 is the concrete case: the `callee_*` analysis set was declared on ONE
call class and written onto the other two for years, and nothing noticed.
Fields-on-nodes drift toward exactly that; one owned table cannot.

One more consequence of ruling Q2: the side tables are PER `ShirBody`. Each
instantiation of a generic-target extension gets its own body and its own tables, so
the repeat loop never collides with itself and `shir_id` only needs to be unique within
one body.

### 7.4 Expressions

| Node | Fields |
|---|---|
| `Lit` | `value: Constant` |
| `LocalRef` | `local: LocalId` |
| `ConstRef` | `const_id: DeclId` — the value is already computed (ruling Q5) |
| `GlobalRef` | `decl: DeclId` — a function used as a value |
| `Field` | `base: Expr`, `name: str` — the index is `typecheck`'s answer (S5) |
| `Index` | `base: Expr`, `index: Expr` |
| `Call` | `target: CallTarget`, `args: [Arg]` — the resolved `Callee` is `typecheck`'s (S5) |
| `Unary` | `op: UnOp`, `operand: Expr` |
| `Binary` | `op: BinOp`, `lhs: Expr`, `rhs: Expr` |
| `Cast` | `operand: Expr`, `target: Type` |
| `Borrow` | `place: Expr`, `mode: BorrowMode` |
| `Try` | `inner: Expr` — `??`, kept whole for `borrow` and for G7 |
| `Lambda` | `params`, `body: ShirBlock`, `captures: [Capture]` — until `lift` |
| `StructInit` | `ty: StructType`, `fields: [Expr]` — positional after lowering |
| `EnumInit` | `ty: EnumType`, `variant: str`, `payload: [Expr]` |
| `ArrayInit` | `kind: Fixed \| Dynamic`, `runs: [ArrayRun]` |
| `Range` | `start: Expr`, `end: Expr`, `inclusive: bool` |
| `Interp` | `parts: [Str \| Expr]` — kept whole so `typecheck` reports per part |

```
CallTarget = Named(ref: Expr)                # a scope-resolved name, or any callable expr
           | Dotted(recv: Expr, name: str)   # x.f(...) -- builtin, extension, perk, view
           | Static(ty: Type, name: str)     # List.new, HashMap.new, f64.from_bits

Arg = (value: Expr, marker: Convention | None)
```

`CallTarget` is what the SOURCE says; the closed `Callee` of 7.6 is what `typecheck`
resolves it to (S5). The split exists because lowering runs before `typecheck`: `x.f()`
cannot know whether `f` is a builtin, an extension or a perk method until the type of
`x` is known. `Static` covers a call on a type name — `List.new()`, `hm.HashMap.new()`
behind an alias (#506), `f64.from_bits(b)` — which has no receiver EXPRESSION at all.

`marker` is the WRITTEN call-site mode (`nom s`, `poke n`), or `None`. The RESOLVED
convention is `typecheck`'s: an unmarked argument in a consuming position (constructor,
container insert, array element) resolves to `Owned` with no marker, and CE2427 is
checked by comparing `marker` against the declared parameter.

Named struct construction is all-or-nothing (Known Limitation 4), so lowering reorders
named fields into declaration order and `StructInit` is always positional. Reordering
must not reorder EVALUATION: lowering evaluates each field expression into a temp in
the WRITTEN order, then aggregates in declaration order.

A native variadic call is NOT collapsed here. Which parameter is `...T` is a fact about
the callee, and the callee is resolved by `typecheck` — so the trailing arguments stay
a flat list in SHIR, and the SHIR-to-SLIR lowering folds them into one synthesized
owned array (today `build_variadic_array` does this at emit time, inside the
dispatcher). A bloom `arr...` forwards the array operand itself, as a `Move`.

### 7.5 Statements

| Node | Fields |
|---|---|
| `Assign` | `target: Expr`, `value: Expr`, `is_init: bool` — `Let` and `Rebind` collapse here |
| `ExprStmt` | `value: Expr` |
| `Return` | `value: Expr` |
| `If` | `cond: Expr`, `then: ShirBlock`, `otherwise: ShirBlock \| None` |
| `Loop` | `kind: LoopKind`, `body: ShirBlock` |
| `Match` | `scrutinee: Expr`, `arms: [Arm]` |
| `Break` / `Continue` | — |

There is no `Expand` node (4.5): `monomorphize` unrolls every pack expansion before
SHIR is built, and `typecheck` already rejects a survivor as CE0119.

`is_init` carries the one semantic difference between `Let` and `Rebind`: a rebind
RE-INITIALIZES, which clears a moved flag.

### 7.6 `Callee` — a closed set

```
Callee = Free(decl: DeclId)
       | Method(recv: Expr, decl: DeclId)        # extension or perk implementation
       | Builtin(recv: Expr, method: BuiltinMethod)
       | Static(ty: Type, method: BuiltinMethod) # List.new, HashMap.new, f64.from_bits
       | Extern(decl: DeclId)
       | Intrinsic(name: str)
       | Indirect(value: Expr)                   # a closure or fn-typed local
```

Reuses the closed `CalleeKind` of `param_modes.py`. Three AST call nodes collapse into
one, and **which kind of callee it is has already been decided by `typecheck`**, which
records it in `TypeckResults` against the call's `shir_id` (S5) — the `Call` node
itself carries only the syntactic `CallTarget` of 7.4.
`backend/expressions/calls/dispatcher.py` is 562 lines that decide this again at emit
time. It reads the answer instead.

### 7.7 `LoopKind` — 1.5, resolved

This is the concrete answer to `emit_foreach`.

```
LoopKind = While(cond: Expr)
         | CountedRange(binding: LocalId, start: Expr, end: Expr, inclusive: bool)
         | ArrayIter(binding: LocalId, array: Expr, by: Value | Ref(BorrowMode))
         | StringChars(binding: LocalId, s: Expr)
         | HashMapIter(binding: LocalId, map: Expr, view: Keys | Values | Entries)
         | StdinLines(binding: LocalId)
         | Infinite
```

`typecheck` picks the variant, once, with the types it already has, and records it in
`TypeckResults` (S5). Nothing downstream matches on syntax, string-matches
`"HashMap<"`, or emits a run-time `icmp` against `-1` to find out which protocol
applies.

The static pick is SAFE because an iterator is not a value (4.4): `Iterator` is not a
nameable type, so the iterable of every `foreach` is a source expression `typecheck`
can see whole. `file.lines()` rides the same descriptor as `stdin.lines()` and is
`StdinLines`'s sibling — the variant covers both, or gains a `source` field; the
lowering decides which spelling, not the design.

`foreach(poke r in ...)` is `ArrayIter` with `by: Ref(Poke)`.

### 7.8 Patterns

```
Pattern = Wildcard
        | Literal(value: Constant)
        | Variant(ty: EnumType, variant: str, bindings: [PatBinding])
        | Own(inner: Pattern | PatBinding)       # Own(poke h) -- auto-unwraps Own@(T)

PatBinding = (local: LocalId, by: Value | Ref(BorrowMode))

Arm = (pattern: Pattern, body: ShirBlock)
```

A payload binding is required and `_` discards (it lowers to `Wildcard`). An INTEGER
scrutinee uses `Literal` arms; the kinds never mix (CE2076), and that is checked on
SHIR. `Own` mirrors the AST's `OwnPattern`; it is nested-only today (Known Limitation
13) and the binding carries its own `by` mode.

### 7.9 What each pass reads and writes

| Pass | Runs on | Reads | Writes |
|---|---|---|---|
| `effects` | **AST**, whole-program | AST bodies | the destroy-effect summary (ruling S4) |
| `scope` | **AST** | AST bodies | the binding table (ruling S3) |
| *lowering* | AST + bindings | | `ShirBody` |
| `typecheck` | SHIR | `ShirBody` | `TypeckResults` — types, `Callee`, `LoopKind`, field indices, conventions (S5) |
| `lift` | SHIR | `ShirBody`, `TypeckResults` | `Lambda` becomes `GlobalRef` + a closure aggregate; the lifted body joins the body list and is lowered like any other |
| `borrow` | SHIR | `ShirBody`, `TypeckResults` | `OwnershipResults` — consumed by SLIR lowering |

`OwnershipResults` is the second typed side table: `shir_id -> Ownership`, plus the set
of locals that are CONDITIONALLY moved. It replaces the transitional
`ownership_provenance` and `conditional_move_names` fields (1.2) and obeys the same
four rules as 7.3.

### 7.10 AST to SHIR, node by node

| AST | SHIR |
|---|---|
| `Name` | `LocalRef` / `ConstRef` / `GlobalRef` — resolved by `scope`'s table |
| `Call`, `MethodCall`, `DotCall` | `Call` with a `CallTarget` |
| `Print`, `PrintLn` | `Call(Builtin(...))` |
| `Let`, `Rebind` | `Assign` with `is_init` |
| `While` | `Loop(While)` |
| `Foreach` | `Loop(...)` — one of five iterator variants |
| `ArrayLiteral`, `DynamicArrayNew`, `DynamicArrayFrom` | `ArrayInit` |
| `EnumConstructor` | `EnumInit` |
| `MemberAccess` | `Field` |
| `IndexAccess` | `Index` |
| `TryExpr` | `Try` — **kept** |
| `InterpolatedString` | `Interp` — **kept** |
| `Expand` | nothing — already unrolled by `monomorphize` (4.5) |
| `Spread` (bloom `arr...`) | the array operand, forwarded whole; SLIR lowering makes it a `Move` |
| `Borrow` | `Borrow` |
| `RangeExpr` | `Range`, or folded into `Loop(CountedRange)` |

67 AST node classes become 28.

### 7.11 What SHIR does not have

No basic blocks. No SSA. No drop instructions. No type parameters. No mangled symbols.
Those belong to SLIR, or are already gone.

---

## 8. SLIR — the Sushi Low-level IR

**Purpose:** input to code generation.

**Shape:** a control-flow graph. Basic blocks, explicit terminators, no nesting.

### 8.1 The three model rulings

**R1. Conventions from Swift SIL.** Fact 4.1: Sushi's declared modes map one-for-one
onto SIL's calling conventions, and MIR has no equivalent concept. Every SLIR call
argument and every parameter carries an explicit `Convention`.

**R2. Value model from Rust MIR — places and locals, NOT SSA.** Drop elaboration and
clone elision are questions about *storage*, not about values: "has this slot been
moved out of" is a question about a place. This is why MIR is not SSA, and it still
holds. LLVM's `mem2reg` builds SSA for us, so constructing phi nodes is duplicated
work.

Swift does ownership inside SSA (OSSA) and it is provably possible. It also took Apple
years. If SLIR ever goes SSA, use **block parameters** (Cranelift, SIL, MLIR) and not
phi nodes: no per-predecessor bookkeeping, and editing the CFG does not mean rewriting
phi lists.

**R3. No unwinding machinery, anywhere.** Fact 4.2. This is where SLIR is smaller than
MIR, and it is the ruling most easily lost by somebody copying MIR without thinking:

- a call needs no unwind successor, so **`Call` is a statement, not a terminator**
- there are no cleanup blocks and no landing pads
- drop elaboration only has to be correct on normal control flow

Demoting `Call` from a terminator makes blocks longer, flatter and far easier to read.
In MIR every call splits a block. In SLIR none do.

### 8.2 The unit

```
SlirFunction
  symbol : str                       # the mangled name; monomorphic (ruling Q1)
  params : [(LocalId, Convention)]
  locals : [SlirLocal]
  blocks : [SlirBlock]
  entry  : BlockId

SlirLocal
  id   : LocalId
  ty   : Type
  name : str | None                  # the source name where there is one, for diagnostics
  loc  : Span | None

SlirBlock
  id         : BlockId
  statements : [SlirStatement]
  terminator : SlirTerminator
```

`locals[0]` is always the return slot, written `_0`.

### 8.3 Conventions

```
Convention = Owned          # `nom`     -- the callee frees
           | Guaranteed     # default   -- caller owns, callee borrows, by value
           | InGuaranteed   # `peek`    -- by address, read-only, many at once
           | Inout          # `poke`    -- by address, read-write, exclusive
```

`param_modes.py` resolves the mode per callee kind today, and both the `borrow` pass
and the backend call it separately. In SLIR it is resolved once, at lowering, and
written into the call. That is G1 applied to the call boundary.

### 8.4 Places and operands

```
Place    = Local(id: LocalId)
         | Field(Place, index: int)
         | Index(Place, Operand)
         | Downcast(Place, variant: str)      # narrow an enum to one variant
         | Deref(Place)

Operand  = Copy(Place)     # the value is PLAIN, or the source is a borrow
         | Move(Place)     # ownership transfers; the source is dead after this
         | Const(Constant)
```

**The `Copy` / `Move` distinction is the point.** `classify()` runs once, during
lowering, and writes its answer into the operand. Nothing re-derives it. 1.3 disappears
and CE0129 has nothing left to guard.

`Downcast` exists because a `match` arm and a `??` both read an enum payload, and
reading one is only valid under a known variant. The worked example in 8.11 is what
found it.

### 8.5 Constants

```
Constant = Int(value: int, ty: Type)
         | Float(value: float, ty: Type)
         | Bool(value: bool)
         | Blank                                  # `~`
         | Str(bytes: bytes)                      # a literal; owns no heap
         | Aggregate(ty: Type, parts: [Constant]) # a const array or struct
         | FnRef(symbol: str)
```

Produced by `const_eval` on the AST (ruling Q5) and carried through unchanged. Only the
backend turns one into an `ir.Constant`.

### 8.6 Statements

```
Assign(Place, Rvalue)
Call(dest: Place, callee: SlirCallee, args: [(Operand, Convention)])   # R3: a STATEMENT
Drop(Place)                          # RAII, unconditional
DropIfSet(Place, flag: LocalId)      # the conditional move, with a REAL bool local
BeginBorrow(id: BorrowId, Place, mode: BorrowMode)
EndBorrow(id: BorrowId)
StorageLive(LocalId)
StorageDead(LocalId)
```

```
SlirCallee = Direct(symbol: str)      # free fn, method, extern, intrinsic -- all mangled
           | Indirect(Operand)        # a closure's fn_ptr
```

`DropIfSet` replaces the string-keyed drop-flag table of 1.4. The flag is an ordinary
SLIR local of type `bool`. It is visible, testable and optimizable.

`BeginBorrow` / `EndBorrow` make a borrow REGION explicit. CE2412 — mutating an owner
while a `let`-borrow is alive — is a question about a region, and today the backend
re-derives it. Writing the region down is the same argument as `Copy` / `Move`: the
pass that proved it records it, and nothing works it out twice.

### 8.7 Rvalues

```
Rvalue = Use(Operand)
       | Binary(op: BinOp, Operand, Operand)     # never `and`/`or` -- see 8.9
       | Unary(op: UnOp, Operand)
       | Cast(Operand, target: Type)
       | Aggregate(kind: AggKind, parts: [Operand])   # struct, enum variant, array, closure
       | Discriminant(Place)                     # an enum's tag, as i32
       | Clone(Operand)                          # the ONE deep copy, still one seam
       | Len(Place)
       | Ref(Place, mode: BorrowMode)            # peek / poke
```

`Discriminant` was also found by the worked example: `SwitchInt` needs an integer, and
a `match` or a `??` switches on a tag.

### 8.8 Terminators

Five, because a call is not one of them (R3):

```
Goto(BlockId)
SwitchInt(Operand, targets: [(int, BlockId)], otherwise: BlockId)
Return
Trap(code: str, args: [Operand])   # run-time traps. Exits. Never unwinds
Unreachable
```

`Trap` carries operands because the registry text is a printf format string and some
traps print values — RE2020 prints the index and the length
(`emit_runtime_error_with_values`).

### 8.9 SHIR to SLIR, construct by construct

| SHIR | SLIR |
|---|---|
| `If(c, t, e)` | `SwitchInt(c, [(0, else_bb)], otherwise: then_bb)` |
| `Binary(and/or)` | **two blocks.** Short-circuit is control flow, never an rvalue |
| `Try(e)` | evaluate `e`; `Discriminant`; `SwitchInt`; the Err arm rebuilds and returns |
| `Match` | `Discriminant` + `SwitchInt`; one block per arm; payload via `Downcast` + `Field` |
| `Loop(While)` | header block, `SwitchInt`, body, `Goto` header |
| `Loop(CountedRange)` | counter local, header, compare, body, increment, `Goto` header |
| `Loop(ArrayIter)` | `Len` into a local, then exactly `CountedRange` |
| `Loop(HashMapIter)` | the one probe walk, over the counted `data[0..count)` range |
| `Interp(parts)` | a sequence of concat `Call`s into a fresh owning local |
| `Lambda` | already gone — `lift` turned it into `GlobalRef` plus an `Aggregate` |
| `ArrayInit(runs)` | `memcpy` for a plain element, one `copy_out` per slot for an owning one |
| `Index` read | bounds compare, `SwitchInt`, `Trap("RE2020")` on the failing edge |
| `Index` write (`arr[i] := v`) | the same bounds check; the target slot is a consuming position |
| a variadic tail | folded into one synthesized owned array argument (7.4) |
| a `string` arg to an `i8*` callee | an explicit conversion into an owning temp local, dropped by the rules of 8.10 — the `emit_cstr_arg` seam becomes visible IR, still one lowering site, and `run()` stays the one exception |

Two SHIR nodes vanish entirely at this boundary: `Try` and `Interp`. Both were kept in
SHIR only so that `typecheck` and `borrow` could report against the source (G7).

### 8.10 Drop placement

Six rules. They encode the two regimes of #414, plus the rebind order of #303/#304, and
nothing else:

1. An owning local gets `StorageLive` at its declaration and `StorageDead` after its
   last use in the enclosing block.
2. `Drop(place)` is emitted on every exit path of the owning scope, before
   `StorageDead`.
3. A local the `borrow` pass proved is moved on **every** path gets no drop at all.
4. A local marked CONDITIONALLY moved gets a `bool` flag local: set `true` at the
   declaration (re-set on each loop iteration), set `false` at each move site, and its
   drop becomes `DropIfSet`.
5. A borrow never drops. `Guaranteed`, `InGuaranteed` and `Inout` parameters never
   drop; an `Owned` parameter drops in the callee.
6. An `Assign` to an owning place that may hold a live value drops the OLD value — and
   the order is normative (4.6): compute the new value FIRST, then `Drop` the old, then
   store. The source may alias the value about to be freed (#303, #304). A rebind
   through a `poke` parameter follows the same rule, through the pointer.

### 8.11 A worked example

The source:

<!-- docs-sweep: skip (fragment: calls functions the example does not declare) -->
```sushi
fn greet(nom string name, bool loud) ~:
    let string msg = decorate(nom name)??
    if (loud):
        shout(nom msg)
    return Result.Ok(~)
```

`msg` is moved on one path and not the other, so it needs a drop flag. This is the case
that 1.4 handles today with a name string.

**SHIR** — a tree, `Try` intact, no drops, no blocks. The printer renders side-table
answers in place, so `Free(@decorate)` and the conventions below are `TypeckResults`
facts (S5) shown inline:

```
ShirBody greet
  params : [(_1 "name": string, Owned), (_2 "loud": bool, Guaranteed)]
  locals : [_5 "msg": string]
  block  :
    Assign(LocalRef(_5),
           Try(Call(Free(@decorate), [(LocalRef(_1), Owned)])),
           is_init: true)
    If(LocalRef(_2),
       then: [ ExprStmt(Call(Free(@shout), [(LocalRef(_5), Owned)])) ])
    Return(EnumInit(Result, "Ok", [Lit(Blank)]))
```

**SLIR** — this is also the `--dump-slir` text format:

```
fn greet(_1: string @owned, _2: bool @guaranteed) -> Result<~, StdError> {
    let _0: Result<~, StdError>            // return slot
    let _3: Result<string, StdError>
    let _4: i32                            // discriminant
    let _5: string                         // msg
    let _6: bool                           // drop flag for _5
    let _7: Result<~, StdError>            // shout(), unused

  bb0:
    StorageLive(_3)
    _3 = call @decorate(move _1 @owned)
    _4 = discriminant(_3)
    switchInt(copy _4) -> [0: bb1, 1: bb2, otherwise: unreachable]

  bb1:                                     // Ok
    StorageLive(_5)
    _5 = move ((_3 as Ok).0)
    StorageDead(_3)
    StorageLive(_6)
    _6 = const true
    switchInt(copy _2) -> [0: bb4, otherwise: bb3]

  bb2:                                     // Err -- `??` propagates
    _0 = Result::Err(move ((_3 as Err).0))
    StorageDead(_3)
    return

  bb3:                                     // loud
    StorageLive(_7)
    _7 = call @shout(move _5 @owned)
    _6 = const false                       // msg was moved on this path
    StorageDead(_7)
    goto -> bb4

  bb4:                                     // join
    DropIfSet(_5, _6)
    StorageDead(_6)
    StorageDead(_5)
    _0 = Result::Ok(const ~)
    return
}
```

Read `bb3` and `bb4` together. The whole of 1.4 — the LLVM-keyed move set, the
string-name drop-flag lookup, `arm_if_conditional`, `emit_free_unless_moved` — is those
three lines. That is the design in one screen.

### 8.12 What SLIR removes from the backend

| Backend concern today | In SLIR |
|---|---|
| `MoveTracker` and its LLVM-keyed sets | `move` operands and `Drop` statements |
| drop flags by name string | `DropIfSet` with a real local |
| scope-exit cleanup during emission | `Drop`, placed by the rules of 8.10 |
| `emit_foreach` protocol matching | `LoopKind`, already resolved in SHIR |
| `dispatcher.py` deciding callee kind | `Callee`, already resolved in SHIR |
| three call kinds | one `Call` statement |
| `??` control flow at emit time | `Discriminant` + `SwitchInt` |
| mode resolution at two separate sites | one `Convention` on the call |
| borrow-region re-derivation | `BeginBorrow` / `EndBorrow` |

Note what is NOT in this table: the 2,452 lines of monomorphization. Ruling Q1 leaves
them where they are.

### 8.13 The verifier

Runs after lowering, before the backend. It checks:

- every local is typed, and every `Place` type-checks against it
- every `Downcast` names a real variant of the place's enum
- no use of a place after a `Move` of it, on any path
- every block ends in exactly one terminator, and every `BlockId` exists
- every `Drop` targets an owning type
- every owning local reaches exactly one of: a `Drop`, a `DropIfSet`, or a `Move` on
  every path
- every `BeginBorrow` has exactly one matching `EndBorrow` on every path
- every argument `Convention` matches the callee's declared parameter — with one
  carve-out: the tail of a `var_arg` extern has no declared parameters, so the rule
  applies to the declared prefix only
- no `Binary` carries `and` or `or` (8.9)
- every `Trap` code is a registered RExxxx, with the operand count its format string
  needs

This is where most of the 323 backend internal errors go. One gate, one message, one
source location, asked once.

---

## 9. Decided questions

**Q1. Is SHIR polymorphic? — NO. SHIR is monomorphic.**

Fact 4.3 is the evidence. Sushi checks a perk constraint against the **concrete** type
at the call site, and nothing checks a generic body against its declared bounds.
Sushi's generics are templates, not bounded generics.

A polymorphic SHIR needs definition-site checking. Definition-site checking needs a
constraint language strong enough to describe everything a body does, and Sushi's perks
have **no type parameters (CE4010), no inheritance and no default implementations**.
The constraint language is too weak today.

That makes a polymorphic SHIR a **language change**, not a refactor, and it does not
belong inside this work.

Consequences: `monomorphize` keeps its position and keeps working on the AST. SHIR is
built per instantiation. The 2,452 lines stay. Known Limitations 7 and 8 are untouched
(N6).

**Q2. Do generic-target extensions keep their repeat loop? — YES.**

Follows from Q1. `_check_monomorphized_extensions` re-runs the per-unit passes for each
instantiation, and under template semantics that is correct, not a workaround. It
re-runs them over SHIR instead of the AST, with per-body side tables (7.3).

**Q3. One node set with a legality level, or two hierarchies? — TWO hierarchies.**

MLIR's dialect trick works because every level shares one shape: regions of blocks of
operations. **SHIR is a tree and SLIR is a control-flow graph.** They do not share a
shape.

A shared node set would need every node to be valid in both a tree and a graph. That is
exactly the overloading that 1.1 identifies as the problem, re-introduced one level up.
The duplicated walking and printing code is the smaller cost.

**Q4. Does the `.slib` format change? — NO.**

A source `.slib` ships Sushi text, and the consumer compiles it. A binary `.slib` ships
LLVM bitcode, and the backend still emits LLVM (non-goal N1). Neither is touched.

Recorded so that nobody later presents it as new: shipping **SLIR** in a `.slib` would
be a backend-neutral binary format, and the header already has a `KIND` byte. That is a
real option and it is firmly out of scope here.

**Q5. Where does `const_eval` live? — It stays where it is, on the AST.**

Fact 4.7. Constants are a **declaration-level** concern, evaluated on the AST during
`collect`. SHIR carries `ConstRef(id)` and the value is already computed. `const_eval`
never needs an SHIR reader, and no second evaluator is built.

**Q6. Do we need a text format? — A printer, yes. A parser, no.**

Add `--dump-shir` and `--dump-slir`, in the same family as the existing `--dump-parse`
and `--dump-ast`.

The printer is what makes G4 real: a lowering test asserts on printed SLIR text, with
no `cc` and no running binary. It is also the only practical way to review the
migration's diffs.

No parser. Nothing needs to read SLIR back in, and a parser would be a second
definition of the IR to keep in step with the first.

---

## 10. What carries over

### 10.1 Five assets, unchanged

This is not a rewrite from nothing:

- **`semantics/typesys.py`** is a clean frozen-dataclass type model with no LLVM in it.
  Both SHIR and SLIR use it. No new type model is needed.
- **`semantics/ownership.py`** already defines `Provenance`, `TypeClass`, `Ownership`
  and `classify()`. The vocabulary for an ownership IR exists.
- **`semantics/param_modes.py`** already has a closed `CalleeKind`. That becomes SHIR's
  resolved callee, and its mode result becomes SLIR's `Convention`.
- **`backend/types/core/sizing.py`** (`TypeSizing`) is already free of `llvmlite`.
- **`const_eval.ConstantValue`** is already a neutral value; the backend converts it
  (`backend/constants/llvm_values.py`).

### 10.2 The behaviour suite

The `.sushi` suite asserts on behaviour: stdout, exit codes, error codes and leaks. It
does not assert on compiler internals. A refactor of this size usually fails because
the tests encode the old structure. Here they do not. This is the single largest reason
the work is feasible.

### 10.3 The pytest seam gates — ported, not kept

The Python unit layer is different: the mandated seam gates
(`test_consuming_use_coverage`, `test_borrow_dispatch_is_total`,
`test_callee_mode_matrix`, `test_owning_value_registry_is_total`, ...) assert on the
OLD structure by name. Each guarded seam has a named replacement (8.12), so each gate
is ported when its seam moves. This is a real, planned cost.

---

## 11. Constraints on the migration plan

The plan is written elsewhere; these are the design-level invariants it must honor.

1. **The growth rule.** The set of programs that compile through the NEW pipeline only
   grows. No dual-path backend, no escape-hatch node that falls back to the old
   emitter, no bridge that writes SHIR results back onto AST nodes. An unlowered
   construct fails loudly.
2. **Ownership is built into SLIR from its first construct.** `Move` / `Copy`
   operands, `Drop`, `DropIfSet`, `BeginBorrow` / `EndBorrow` and `Convention` are not
   retrofits; a lowering built without them is written twice.
3. **The printers are a dependency, not a convenience** (G4, Q6). Between "the passes
   move" and "the first program runs end to end", golden-file tests on `--dump-shir` /
   `--dump-slir` plus the verifier (8.13) are the only signal. And golden files demand
   what the current backend does not have: **deterministic output**. Ids are assigned
   in traversal order, side tables print in id order, and no printed line depends on a
   dict's iteration order. A golden file that flakes trains people to re-bless it.
4. **Diagnostics never go dark.** `test_err_*` tests (exit 2) run throughout, because
   diagnostics come from the passes. A `test_warn_*` test asserts exit 1, which needs a
   full successful compile — those return with the new backend, area by area.
5. **The leak suite is the net for ownership.** Extend `EXPECT_NO_LEAKS` coverage
   BEFORE lowering the owning types, not during. A missing leak test there is a bug
   that survives to the end undetected.
6. **The incremental cache is preserved.** A monomorphized instance carries its
   declaring unit (PR #510), so SLIR functions group into one module per unit exactly
   as the emitters do today, and the per-unit `.o` cache keys on the same fingerprints.
7. **The seam gates travel with their seams** (10.3): a gate is ported in the same
   change that moves what it guards, never later.
