# The Sushi IR: SHIR and SLIR

**Status: ACCEPTED.** Measured against the tree on 2026-10-11, at `cc4088ca`, and ruled
on the same day. Every design question has a ruling. No SHIR or SLIR code exists in the
tree. This document is the DESIGN: what the two IRs are, why they have this shape, and what
a migration must keep. The phases, the risks and the progress tracking are not part of this
document; a migration plan comes from it. Nothing in the language changes: every behaviour
test passes before and after, unchanged.

Amended on 2026-10-11 by the rulings on the migration plan: 6.3 (initializer bodies), 6.4
(the gate reads the body modules), 7.4 (evaluation order, recorded values, `expect`), 10.3
and 11.9.

## The decisions, in one place

| # | Decision | Where |
|---|---|---|
| **S1** | Two levels: `AST -> SHIR -> SLIR -> LLVM`. The AST stays | 5, 6 |
| **S2** | The AST keeps the smallest possible interface. Four questions, three gates | 6 |
| **S3** | `scope` STAYS on the AST, and it gets one new output: the binding table. Three passes move to SHIR | 7.1 |
| **S4** | `effects` STAYS on the AST. It runs whole-program, before any SHIR exists | 7.1 |
| **S5** | Every answer `typecheck` gives lives in `TypeckResults`. A SHIR node never gets a field that a pass fills in later | 7.3 |
| **S6** | One fact, one writer. A fact that `typecheck` and `borrow` both touch today belongs to `borrow`, in `OwnershipResults` | 7.9 |
| **R1** | Calling conventions from Swift SIL. Sushi's modes map one-for-one | 8.1 |
| **R2** | Value model from Rust MIR: places and locals, NOT SSA | 8.1 |
| **R3** | No unwinding machinery. `Call` is a STATEMENT, not a terminator | 8.1 |
| **Q1** | **SHIR is MONOMORPHIC.** The template check is one more body, at opaque type arguments | 9 |
| **Q2** | The copy fixpoint STAYS | 9 |
| **Q3** | SHIR and SLIR are TWO hierarchies, not one node set with levels | 9 |
| **Q4** | `.slib` does not change | 9 |
| **Q5** | Constants are declaration-level, evaluated on the AST | 9 |
| **Q6** | A printer yes, a parser no. `--dump-shir` and `--dump-slir` | 9 |
| **Q7** | The per-type emitter families stay. SLIR calls them with operands, never with an AST node. A family moves into SLIR only when a named optimization needs it, by its own ruling | 9 |
| **Q8** | `lift` rewrites nothing and writes `LiftResults`. `Lambda` stays in SHIR through `borrow`; SLIR lowering cuts the function and builds the closure | 7.9, 9 |
| **G9** | Every SLIR statement and terminator carries a `Span`. The verifier refuses one without | 2, 8.2 |
| **C8** | No language epic starts while the migration is open | 11 |

---

## 1. The problem

Every number in this section was measured in the tree on 2026-10-11, at `cc4088ca`.

### 1.1 The AST does three jobs and was designed for one

| Job | Fit |
|---|---|
| Represent syntax, carry source spans for diagnostics | Correct. This is its job |
| Substrate for the 19 semantic passes | Adequate, and it strains |
| Input to code generation | Wrong tool |

Job 3 is not a choice. `parser -> AST -> emit` is the natural first shape of a compiler,
and it works until the language has ownership. Sushi has ownership.

### 1.2 There is already an IR. It is implicit.

The semantic passes record analysis facts as fields on AST nodes, and the backend reads
them back. The census:

| Measure | Count |
|---|---|
| Analysis fields on AST nodes (class, field pairs) | 141 |
| Distinct field names | 80 |
| AST classes that carry one | 37 |
| Pairs the backend reads | 103 (57 names) |

Method: `dataclasses.fields` over every class in `semantics/ast.py`, then a search for a
write after the AST builder and for a read under `sushi_lang/backend/`. Markers that only
the AST builder writes (`nom_marked`, `IndexAccess.unchecked`, `borrowed_return`,
`dont_panic`) are records of syntax and are not in the count.

`typecheck` writes most of them: the `resolved_*` / `inferred_*` type family, the
`callee_*` group, `display_type`, `integer_match_type`, `protocol_next`,
`value_iterator`, `is_value`, `inferred_conversion`. `borrow` writes
`ownership_provenance`, `conditional_move_names`, `iterator_state` and `iterator_leaves`.
`scope` writes `Lambda.captures`, `lift` writes `lifted_name` and `env_struct`, and
`monomorphize` writes `home_unit`, `instance_key` and their kin. The call fields are the
largest group: `Call` carries 14 analysis fields, `MethodCall` 20 and `DotCall` 22.

This is an intermediate representation grown in place: a field per fact, spread across
node classes, written by one pass and read by the backend, with no owner and no
completeness check. Three faults follow from that shape, and each one is in the tree:

- **Two writers for one fact.** `Foreach.walks_in_place` is written by `typecheck` (a
  walk through a `poke` root) and by `borrow` (a `let` that holds a lent iterator).
  `iterator_state` is written by `typecheck` (`CURSOR`) and by `borrow` (`LENT`, `OWNED`).
  `Lambda.captures` is written by `scope` (the names) and by `typecheck` (each capture's
  `ty`, `passes/types/visit/helpers.py:72`). No pass can say that the field is final.
  On the old pipeline, ruling S6 now makes `borrow` the one writer of `walks_in_place`
  and of `iterator_state`.
- **One set that is not one set.** `CallLike` says that each call class carries the whole
  set of callee fields. `Call` carries 7 `callee_*` fields, `MethodCall` 11 and `DotCall`
  13.
- **The backend writes too.** `backend/runtime/closures.py:205` writes
  `ownership_provenance` onto a node it makes, and
  `backend/expressions/calls/dispatcher.py:322` writes `resolved_struct_type`.

Every dataclass in `semantics/ast.py` is `slots=True`, every analysis field is declared on
each class that takes it, and `tests/unit/test_ast_nodes_are_slotted.py` keeps it that
way. That CONTAINS the channel: a stray write raises at the site that wrote it. It does
not give a fact an owner, one writer, or a completeness check. Sections 7.3 and 7.9 do.
The declared analysis fields are transitional (6.2): each one goes when the pass that
writes it moves to SHIR and its side table.

### 1.3 The ownership rule runs twice

`backend/ownership.py` calls `classify(provenance, type_class)` **while it emits LLVM IR**,
at three sites: `bind` (`:61`), `consume` (`:88`) and `unwrap` (`:117`). Each site builds
the type class there, from the `Drop` set and a type resolver. The rule runs once in the
`borrow` pass to check it, and again at emission to act on it.

`_provenance_of()` (`:152`) reads `source.ownership_provenance`. A missing value is
[CE0129](../error-catalog.md#ce0129): fatal, with a deliberate no-fallback rule. Three
more backend sites read the field outside the seam (`statements/returns.py:62`,
`expressions/memory.py:265`, `runtime/closures.py:205`), and `_mark_moved` (`:160`) finds
the moved name by a test on the syntax (`Name` or `MemberAccess`).

[CE0129](../error-catalog.md#ce0129) is not a bug. It is the correct response to a
missing note in a design that passes notes. An IR removes the whole category, because the
decision becomes an instruction.

### 1.4 Ownership state is keyed on LLVM objects and on names

```
self._moved: Set['ir.Instruction'] = set()
self._flags: Dict['ir.Instruction', 'ir.AllocaInstr'] = {}
```

`MoveTracker` (`backend/memory/moves.py:13`) keys move state on `llvmlite` objects.
`arm_if_conditional(name, slot)` arms a drop flag when the **name string** is in
`codegen.current_conditional_moves`, a set copied from `Block.conditional_move_names` at
the start of each function. The scope manager keys its cleanup registries by name, and
`_held_by_temporary` keys an owning temporary by `id(expr)`, the identity of an AST node.

RAII is therefore tied to `llvmlite` object lifetimes, to name strings and to AST node
identity.

### 1.5 The backend makes semantic decisions by matching on syntax

`emit_foreach` (`backend/statements/loops.py:58`) picks a loop protocol like this:

1. Test `isinstance(node.iterable, RangeExpr)`
2. Test the `protocol_next` stamp that `typecheck` left for a type with `next()`
3. Test the `value_iterator` stamp that `typecheck` left for an `Iterator@(T)` value
4. For everything else, emit the iterable as a cursor. Then test
   `isinstance(node.iterable, (MethodCall, DotCall))` with `method in ("keys", "values",
   "entries", "pairs")`, infer the receiver type again, and ask
   `is_instance_of(receiver_type, "HashMap")`. The HashMap walk then switches on the
   method name string again

Items 2 and 3 read an answer that `typecheck` wrote down. Items 1 and 4 match on the syntax
of the iterable again. `typecheck` knows those answers too, and has nowhere to put them.

The loop is not alone. The method-call dispatcher tests method names, the LLVM type of a
value (`try_emit_string_method`) and a receiver spelled `string`
(`try_emit_primitive_static`). `emit_rebind` recognises `s := s.concat(x)` by its syntax.
The backend calls a semantic type inference 34 times while it emits.

### 1.6 Surface forms multiply backend code

The AST has 77 classes. 52 of them describe a body: 25 expressions, 14 statements, 9
pattern classes, `MatchArm`, `Block`, `ArrayElement` and `DestructureTarget`. Many are
syntax, not semantics:

`Print` / `PrintLn`, `Call` / `MethodCall` / `DotCall`, `ArrayLiteral` /
`DynamicArrayNew` / `DynamicArrayFrom`, `While` / `Foreach`, `Let` / `Rebind`.

Other forms carry several meanings, and the position decides which (4.8). A `RangeExpr`
is a run in an array literal, a loop bound, a slice index or an `Iterator@(i32)` value. A
`MemberAccess` is a field, a tuple element (`.0`) or another unit's storage (`ns.v`). An
`IndexAccess` is an element or a slice.

Each distinction is backend code that exists because the parser made it, or because the
backend must find the meaning again.

### 1.7 Scale of the coupling

| Measure | Count |
|---|---|
| Backend files / lines | 169 / 26,652 |
| Backend files that import `semantics.ast` | 64 |
| `isinstance` checks on AST classes in the backend | 134, in 25 files |
| `match` statements on AST classes in the backend | 4, with 45 class patterns |
| Semantic type inference calls at emit time | 34 |
| `param_modes` imports in the backend (mode resolution at emit time) | 13 |
| `raise_internal_error` sites in the backend | 250, in 83 files |
| Monomorphization code (`generics/monomorphize/`) | 2,809 lines |

The 250 internal errors each guard a contract that nothing checks earlier.

---

## 2. Goals

**G1. One place where a fact is decided, and one place where it is read.**
A pass decides a semantic fact and records it as data. No consumer derives it again.

**G2. Replace the analysis-fields-on-nodes channel with typed side tables.**
A field per fact, spread across node classes with no owner and no completeness check
(1.2), becomes owned, completeness-checked tables with one writer each. See 7.3 and 7.9.
This is a real change, not a rename.

**G3. Make ownership explicit.**
`move`, `copy`, `borrow`, `clone` and `drop` become instructions that lowering writes
from the `borrow` pass's answers, not decisions the code generator takes.

**G4. Make the compiler testable below end-to-end.**
Assert that a source shape lowers to an expected instruction sequence, with no `cc`. This
is a DEPENDENCY of the migration, not a benefit: for a stretch of the work it is the only
signal there is (section 11).

**G5. Reduce the backend.**
Collapse surface forms so that one lowering serves many syntaxes.

**G6. Enable optimizations LLVM cannot see.**
Clone elision, drop-flag removal, perk devirtualization, bounds-check removal. These need
facts that are gone before LLVM receives the module. Bounds-check removal has a named
consumer: the range analysis of epic #1244, whose first design question is where the
analysis runs. SLIR is that place: a CFG with typed locals, explicit `Len` and explicit
bounds checks.

**G7. Keep diagnostics quality.**
No diagnostic may lose its location or its wording.

**G8. Give the AST the smallest possible interface, and only its own job.**
The AST is a record of syntax. Section 6 is the rule, and gates hold it.

**G9. One source of locations for the emitted code.**
Every SLIR statement and terminator carries a `Span`, and the verifier refuses one without
(8.13). Debug information (`-g`, DWARF) then reads one field at one emission site, not a
location threaded through each emitter.

## 3. Non-goals

**N1. A second backend.** LLVM stays. This work makes one possible later. That is a side
effect and not a reason.

**N2. Deleting the AST.** The AST keeps syntax and source spans. The `ast_builder` does
not change. When somebody proposes to finish the job, the answer is section 6.

**N3. Faster compilation.** An IR is a place to put optimizations. It is not itself one.
Expect a small slowdown until the optimization work starts.

**N4. New language features.** No user-visible change. Every behaviour test passes before
and after, unchanged.

**N5. Changing the pass order.** The names and the order in the
`SemanticAnalyzer.check()` docstring stay.

**N6. Fixing the generic inference gaps.** The inference gaps of the generic call
(a bare-parameter lambda, a type parameter that only the return type names, no explicit
`@(...)` on a method call) are not addressed by this work.

**N7. Rewriting the runtime emitters.** The per-type emitter families (Q7) keep their
job. They lose their AST inputs and nothing else.

---

## 4. Language facts that fix the design

Each fact below is a property of Sushi, not of the implementation. Each one settles a
design choice, and each was verified in the tree.

### 4.1 Parameter modes are CONVENTIONS, not types

This decides which IR to model on. Sushi declares a mode at both ends of a call
([CE2427](../error-catalog.md#ce2427)). Swift SIL declares conventions. Rust derives
everything from the type and has no convention concept at all.

| Sushi | Swift SIL | Rust |
|---|---|---|
| `string x` (the default borrow) | `@guaranteed` | follows from `&T` |
| `nom string x` | `@owned` | follows from `T` by value |
| `peek string x` | `@in_guaranteed` | follows from `&T` |
| `poke string x` | `@inout` | follows from `&mut T` |

Four for four. **On the ownership axis, SIL is the closer model** (ruling R1). MIR offers
Sushi nothing here, because Rust never needed the concept.

### 4.2 Sushi does not unwind

`emit_runtime_error`, `emit_runtime_error_with_values` and
`emit_runtime_error_with_message` (`backend/runtime/errors.py`) write to stderr and call
`exit(1)`. There are no exceptions; an error is a `Result` value, and a trap stops the
program. The backend emits no `invoke` and no `landingpad`.

A large part of Rust MIR's weight comes from unwinding. Every `Call` terminator carries an
unwind successor, cleanup blocks exist for the unwind path, and drop elaboration must be
correct on both paths.

Sushi needs none of it. This is a permanent simplification and it belongs to the language,
not to the implementation. See ruling R3.

### 4.3 A template is checked as one more copy, at opaque type arguments

A generic body is checked where it is written (`checked-generics.md`). Each type parameter
is OPAQUE, and an operation on it is legal only when a constraint promises it: `Eq` gives
`==`, `Ord` gives `<`, `Clone` gives `.clone()`, and arithmetic on `T` is refused.

The mechanism is a COPY. `passes/types/templates.py` deep-copies the template, cuts it
with each type parameter replaced by its opaque form, and runs `typecheck`, `lift` and
`borrow` on that copy, inside an overlay of the program tables. The written template is
then skipped by the ordinary passes.

The real instances are still cut by `monomorphize` and still checked one by one, but each
one reports only the PER-INSTANCE REMAINDER (`checked-generics.md` §8.2): consume or copy
of an owning value, the drop set and the order of `drop()`, the derived methods of the
concrete type, the layout, E3 ([CE2084](../error-catalog.md#ce2084)) and an owning lambda
parameter ([CE2094](../error-catalog.md#ce2094)). Any other error from an instance is
[CE0149](../error-catalog.md#ce0149): a fault that the template check missed.

So the CHECK is per template, and every fact that code generation needs is per instance.
This settles Q1: SHIR is built per body, and the opaque check copy is one more body.

### 4.4 An iterator is a value, and the walk is chosen statically

An `Iterator@(T)` is a value (`iteration.md`, ruling 10). It has the shape of a closure,
`{next_fn, state, drop_fn, clone_fn}`, and each source (a contiguous cursor, each HashMap
view, a range, `Iterator.new`, each lazy adapter) supplies its own three functions.

The loop kind is still chosen statically. `typecheck` decides it once
(`_choose_the_walk`): a range or a built-in producer written as the iterable is a DIRECT
walk with no call through `next_fn` and no allocation, a type with `next()` is a protocol
walk, and every other iterator is a walk of a value through its `next_fn`. `LoopKind`
(7.7) has a variant for each.

Two facts about the iterator belong to `borrow`, not to `typecheck`: whether a producer
over a place makes a LENT iterator (a stack state) or an OWNED one (a heap state), and
whether a lent iterator leaves its function through a `peek` return and so moves its state
to the heap. Whether a `foreach` walks its value in place, through a `poke` root, is a
borrow fact too. Ruling S6 puts all three in `OwnershipResults`.

### 4.5 `expand` dies in `monomorphize`, except in the template check

`monomorphize/unroll.py` rewrites every `Expand` of an instance into ordinary statements.
The collect pass refuses an `expand` in a position that has no pack
([CE0119](../error-catalog.md#ce0119)).

The template check copy of 4.3 keeps its `Expand`: `typecheck` checks the body once, for
every pack element. An `Expand` anywhere else is an internal error
([CE0015](../error-catalog.md#ce0015)). So SHIR has an `Expand` node, legal only in a
template check body, and a template check body never lowers to SLIR.

### 4.6 A rebind frees the OLD value, in a fixed order

`emit_rebind` reads the new value FIRST, then destroys the old value, then stores. The
order is normative: the source may alias the value about to be freed, so any other order
double-frees a string or leaks an array. Drop placement carries this as rule 6 of 8.10.

### 4.7 A constant is read while its unit's AST is built

`ConstantEvaluator` is called from `ast_builder/builder.py`, because a fixed array's size
is read while that unit's AST is built. That path is AST-level by language design and
cannot move. This settles Q5: constants are a declaration-level concern, and no IR level
ever evaluates one.

### 4.8 One spelling, several meanings, chosen by position

| Spelling | Meanings | Who decides |
|---|---|---|
| `a..b` | a run in an array literal; a loop bound; a slice index; an `Iterator@(i32)` value | the syntax for the run and the slice; `typecheck` for the loop bound against the value |
| `x.y` | a field; a tuple element (`.0`); another unit's storage or constant (`ns.v`) | the binding table for a namespace; the syntax for a tuple element |
| `x[i]` | an element; a slice (`x[a..b]`) | the syntax |
| `e as T` | a numeric cast; an error conversion (`extend A as B:`) | `typecheck` |

Lowering splits what the syntax and the binding table already decide (`Slice`, `ArrayRun`,
`StorageRef`). What only `typecheck` knows stays one SHIR node, and the answer goes into
`TypeckResults`.

### 4.9 `dont_panic` is a fact about what the user wrote

The header marker `dont_panic because "..."` makes the AST builder set
`IndexAccess.unchecked` on every non-slice index in the body, and it stops at a lambda
(`semantics/unchecked_index.py`). The flag derives from the source alone. So it is a
legal field of the SHIR `Index` node, and SLIR lowering reads it to omit the bounds check.
A slice keeps its range check ([RE2028](../error-catalog.md#re2028)) in every body.

---

## 5. The end state

```
source
  |
  v
 AST  ......... syntax and source spans. Declarations. NOTHING ELSE.  (section 6)
  |             `monomorphize` is the ONE pass that rewrites it.
  |
  |  lower (bodies only, per copy: an instance, or a template check copy)
  v
 SHIR  ......... typed, names resolved, calls resolved, MONOMORPHIC.   (section 7)
  |             `typecheck`, `lift` and `borrow` run here. `scope` does NOT (S3).
  |
  |  lower (instances only)
  v
 SLIR  ......... CFG, places, explicit ownership and conventions.      (section 8)
  |
  v
LLVM IR
```

Two rules define the shape:

- **SHIR is a tree. SLIR is a graph.** SHIR keeps nested expressions because
  `typecheck` and `borrow` want them. SLIR is basic blocks with explicit control flow.
  This structural gap is also why they are two hierarchies (ruling Q3).
- **Both levels are monomorphic** (ruling Q1). `monomorphize` keeps its position, ahead
  of SHIR construction, and keeps working on the AST.

---

## 6. The AST interface

**The rule: the AST is a record of what the user wrote. It answers questions about
syntax. It answers nothing else, and it carries nothing else.**

This is G8. It is the reason the work is worth doing at all: 1.1 says the AST is
overloaded, not wrong. Take the extra jobs away and it is a good data structure again.

### 6.1 The four legitimate questions

Only these may be asked of the AST once the migration completes:

1. **"What did the user write here?"** Declarations, and bodies before lowering.
2. **"Where in the file is it?"** `loc: Span`, on every node, for every diagnostic.
3. **"What was next to what?"** Lexical adjacency. The `docs` pass needs this and only the
   AST can answer it: a doc block attaches to the declaration on the next line, and a
   blank line or a comment breaks the attachment ([CW7001](../error-catalog.md#cw7001)).
   No IR can answer that, at any level. This question alone makes the AST permanent.
4. **"What does this unit declare, and what does a body NAME?"** The declaration table
   that the whole-program passes from `collect` to `effects` read, and the bodies they
   walk for a name: the generic instantiations a body names (`instantiate`), the
   declarations a body reaches (`unused`), the indexes under a `dont_panic` header and
   the `Result.Ok` rule of a channel body (`collect`), the `poke` parameter a body destroys
   (`effects`). Each runs before any SHIR exists.

### 6.2 What the AST must never do again

- **Carry an analysis result.** No pass may write an analysis fact onto a node. The
  channel of 1.2 is the failure this design exists to end. The 141 declared analysis
  fields that contain it are TRANSITIONAL: each one goes when the pass that writes it
  moves to SHIR and its side table. A marker that the AST builder sets from the source
  (`nom_marked`, `unchecked`, `borrowed_return`, `dont_panic`) is syntax and stays.
- **Be rewritten by more than one pass.** `monomorphize` is the single sanctioned
  rewriter (ruling Q1 keeps it there). The template check works on a deep copy and leaves
  the written template as it is. Every other pass treats the AST as immutable.
- **Be read by the backend.** The new backend reads SLIR and nothing else.
- **Be read by a per-body pass.** Once the passes move, the BODY modules of `typecheck`,
  `lift` and `borrow` read SHIR only. The declaration half of `typecheck` and `borrow`
  stays on the AST (6.4). Every WHOLE-PROGRAM pass, `collect` to `effects`, reads the
  AST, bodies included: each runs before any SHIR exists, and asks question 4. `scope` is
  the one per-body pass that stays on the AST (ruling S3), and 7.1 says why.

### 6.3 The lowering surface

The entire interface between the AST and everything downstream is two entry points:

```
lower_body(ast_block, bindings, ctx) -> ShirBody   # bodies, once scope has run
declarations(program)                -> DeclTable  # declarations, whole-program passes
```

Anything that needs a third entry point is a design smell and must be argued for in this
document first.

**The one third entry point: an initializer body.** Lowering makes a one-expression
`ShirBody`, with an origin of its own, from each constant initializer and each `var`
initializer. The argument: `typecheck` checks an initializer with the same helpers that
check a body expression (the propagation of the declared type, the assignment check), and
for a `var` it decides the element type of an empty container (`List.new()`, `from([])`).
After the move those helpers read SHIR. The other two choices put the same rule in two
places: type checks of its own in the constant evaluator, or AST copies of the helpers. The
VALUE of a constant stays the evaluator's (Q5). The body only gives `typecheck` a SHIR node
to check. Ruled 2026-10-11. The block of the body holds one statement: a `Return` of the
initializer, because the initializer gives the value of the body as a `return` gives the
value of a function.

### 6.4 Enforcement, not convention

Three gates. All are cheap, and a rule with no gate does not survive a deadline.

| Gate | Mechanism | When |
|---|---|---|
| No node may take a stamp | `@dataclass(slots=True)` on all 77 classes. A stray write raises `AttributeError` | **In force**: `tests/unit/test_ast_nodes_are_slotted.py` |
| A per-body pass may not read the AST | the BODY modules of `typecheck`, `lift` and `borrow` import `shir`, not `ast` | when the passes move to SHIR |
| The backend may not read the AST | `grep -rn "semantics.ast" sushi_lang/backend/` is empty (64 files match today) | when the old backend is deleted |

Two sibling gates are in force. The ruff rule `TID251` (`pyproject.toml`) allows no
`llvmlite` import outside `backend/` and `sushi_stdlib/`, so `semantics` names no LLVM
type, which is the precondition for both IRs living there. `tests/unit/test_layering_gate.py`
holds the rule that `semantics` never imports `backend`.

The second gate reads the BODY modules only. The declaration half of `typecheck` and
`borrow` stays on the AST: a module that reads declarations only, and the declaration half
of a driver module. SHIR has no declaration, and question 4 of 6.1 allows a read of a
declaration. Ruled 2026-10-11.

---

## 7. SHIR: the Sushi High-level IR

**Purpose:** the substrate for semantic analysis of function bodies.

**Shape:** a tree. Nested expressions and structured control flow, because `typecheck`
and `borrow` both want them.

**Position:** built after `monomorphize`, from bodies that are already specialized, once
per copy: an instance, a copy that the copy fixpoint cuts later, or a template check copy
at opaque type arguments (ruling Q1).

### 7.1 Two passes stay behind: `scope` and `effects`

SHIR resolves a name to a `LocalRef(LocalId)`. Something must first decide **which**
binding a name refers to, and that is what `scope` does. So lowering needs `scope`'s
answer before it can build a single node.

`scope` also asks purely source-level questions: is this name declared, is it used before
its declaration, does it shadow (CE1xxx). Those are questions about what the user wrote,
like the `docs` pass.

Today `scope` keeps a scope stack that it discards at the end of the body, and it records
one answer on the AST, `Lambda.captures`. Under this design it keeps what it finds:

```
Bindings : one per body, written by `scope`
  name occurrence (the `Name` node)  -> Local(LocalId) | Const(DeclId) | Storage(DeclId)
                                      | Function(DeclId) | Namespace(unit) | Type(DeclId)
  binder (the declaring node)        -> LocalId
  lambda (the `Lambda` node)         -> [Capture(name: str, local: LocalId)]
```

The kinds follow the bare-name ladder that `scope` already walks
(`semantics/name_ladder.py`). `Storage` is a unit-level `var`.

**The key is the NODE, never its span.** A hidden binder has no span by design
(`semantics/hidden_names.py`: a `_` binder, the `??` binder's hidden local), and two
nodes the AST builder synthesizes can share the span of their cause. A table keyed by
the node is valid for the tree it was built from and for no copy of it, so `scope` runs
on EVERY copy before that copy is lowered: the copies of the fixpoint (Q2) get it today,
and the template check copy (4.3), which today inherits the written template's
`Lambda.captures` through its deep copy, gets a `scope` run of its own.

A `Capture` names WHAT is captured and nothing more. Today `Lambda.captures` has two
writers: `scope` writes the names, and `typecheck` fills each capture's type
(`passes/types/visit/helpers.py:72`). Under this design the type of a capture is
`typecheck`'s answer in `TypeckResults` (7.3), and `Bindings` never carries a type.

**Ruling S3: `scope` runs on the AST and produces the binding table. Lowering consumes
it. Three passes move to SHIR: `typecheck`, `lift`, `borrow`.**

`effects` has the same problem from the other side. It is a WHOLE-PROGRAM pass
(`passes/borrow/destroy_effects.py`): it walks every body once, before the per-unit loop,
and computes which functions destroy a `poke` parameter, transitively. At that point no
SHIR exists, because SHIR bodies are built inside the per-unit loop. So `effects` cannot
move without a change to the loop, and it does not need to: it reads bodies and writes
only a summary table that `borrow` reads.

**Ruling S4: `effects` runs on the AST, whole-program, where it is, as every
whole-program pass does.** The line is not "two passes read bodies". It is this: a
whole-program pass runs before any SHIR exists and reads the AST, bodies included
(`collect`, `instantiate`, `unused` and `effects` each walk bodies for a name, 6.1); the
three per-body passes read SHIR; `scope` is the one per-body pass on the AST (S3).

The pass order and every pass name are unchanged (N5).

### 7.2 The unit

```
ShirBody
  fn_id   : DeclId                 # the declaration this body belongs to
  origin  : Instance | TemplateCheck(TemplateId) | Initializer
                                   # Initializer: a constant or `var` initializer (6.3)
  params  : [ShirParam]
  locals  : [ShirLocal]            # from the `scope` pass
  block   : ShirBlock

ShirParam
  local      : LocalId
  ty         : TypeRef                   # the declared type (7.4)
  convention : Convention          # from the declared mode -- section 8.3
  loc        : Span

ShirLocal
  id       : LocalId
  name     : str | None            # the source name; None for a hidden local
  declared : TypeRef | None        # the type as WRITTEN, when the source has one
  loc      : Span | None
```

Every node carries `shir_id` and `loc: Span`. No node and no local carries a computed
type. A local's type is `typecheck`'s answer, in `TypeckResults` keyed by `LocalId` (7.3):
a written type is checked against it, and a binder with none (a `foreach` item, a pattern
binding, a destructuring target, a bare lambda parameter) is inferred into it. `declared`
is syntax, like `ShirParam.ty`: the user wrote it.

A hidden local is a local the AST builder made: the binder of a `foreach(x?? in ...)` or of
a `foreach((a, b) in ...)`, the whole value of a destructuring `let`, the `let` of a
`foreach(_?? in ...)`. It has no source name and no span, and no diagnostic names it. The
`_` binder of a `foreach` with no `??` binds nothing: its source holds `Discard` (7.5).

### 7.3 Everything `typecheck` decides lives in a side table

`typecheck` RUNS on SHIR, so SHIR must exist before any type is known. No SHIR node can
carry a field whose value only `typecheck` knows, and `slots=True` on the SHIR nodes
refuses the late write.

```
ShirNode      : shir_id, loc      # and its own operands. NO type field
TypeckResults : one owner, written by `typecheck`, completeness-checked when it ends
```

**Ruling S5: every answer `typecheck` gives lives in `TypeckResults`.** That covers more
than types:

| Decision | Keyed by |
|---|---|
| the node's type | `shir_id` |
| the type of each local, written or inferred | `LocalId` |
| the type of each capture of a `Lambda` | the `Lambda`'s `shir_id`, per capture |
| the resolved `Callee` of a call (7.6), with its method type arguments and its declaring unit | the call's `shir_id` |
| the resolved `Convention` of each argument | the call's `shir_id`, per position |
| the instance type of a `StructInit`, an `EnumInit` or a `TupleInit` | the node's `shir_id` |
| the field INDEX of a `Field` (the node carries only the name) | the `Field`'s `shir_id` |
| the `LoopKind` of a loop (7.7), with the `next()` callee of a protocol walk | the loop's `shir_id` |
| whether a `Range` is an `Iterator@(i32)` value | the `Range`'s `shir_id` |
| the conversion that a `Cast` or a `Try` calls | the node's `shir_id` |
| the success tag, the payload types and the function's return type of a `Try` | the `Try`'s `shir_id` |
| the display type of each hole of an `Interp`, and of a `print` argument | the node's `shir_id`, per part |
| the scrutinee type of a `Match`, and its integer type for literal and range arms | the `Match`'s `shir_id` |
| the element type of each target of a `Destructure` | the statement's `shir_id`, per target |

**This is not the stamp channel returning.** Four differences, and each one is a reason a
stamp went wrong (1.2):

| The stamp channel | `TypeckResults` |
|---|---|
| a field per fact, on whichever class first needed it | one table |
| declared on one class, a different set on its siblings | one declared value type |
| any pass may write, the backend included | one pass writes it |
| a missing entry is [CE0129](../error-catalog.md#ce0129), at emit time | completeness is checked when `typecheck` ends |

This is Rust's shape: HIR plus `TypeckResults`, read together by MIR building. Method
resolutions and field indices live inside `TypeckResults` there too.

The side tables are PER `ShirBody`. Each copy gets its own body and its own tables, so the
copy fixpoint (Q2) never collides with itself, and `shir_id` needs to be unique only within
one body.

### 7.4 Expressions

| Node | Fields |
|---|---|
| `Lit` | `value: Constant` |
| `LocalRef` | `local: LocalId` |
| `ConstRef` | `const_id: DeclId`; the value is already computed (ruling Q5) |
| `StorageRef` | `decl: DeclId`; a unit-level `var`, a PLACE with an address (`unit-storage.md`) |
| `GlobalRef` | `decl: DeclId`; a function used as a value |
| `Field` | `base: Expr`, `name: str`; a struct field or a tuple element (`"0"`). The index is `typecheck`'s (S5) |
| `Index` | `base: Expr`, `index: Expr`, `unchecked: bool` (4.9) |
| `Slice` | `base: Expr`, `start: Expr`, `end: Expr`, `inclusive: bool`; a borrowed view (`slices.md`) |
| `Call` | `target: CallTarget`, `args: [Arg]`; the resolved `Callee` is `typecheck`'s (S5) |
| `Unary` | `op: UnOp`, `operand: Expr` |
| `Binary` | `op: BinOp`, `lhs: Expr`, `rhs: Expr` |
| `Cast` | `operand: Expr`, `target: TypeRef`; a numeric cast or a conversion (4.8) |
| `Borrow` | `place: Expr`, `mode: BorrowMode` |
| `Take` | `place: Expr`; a marked field take `nom s.out` in a `let` or a `return` |
| `Try` | `inner: Expr`; `??`, kept whole for `borrow` and for G7 |
| `Lambda` | `params`, `body: ShirBlock`; the captures are in `Bindings`. It stays in the tree through `borrow`; `lift` describes it in `LiftResults` (Q8) |
| `StructInit` | `decl: DeclId`, `fields: [Arg]`, `written_order: [int]`; `fields` in the declaration order |
| `EnumInit` | `decl: DeclId`, `variant: str`, `payload: [Arg]` |
| `TupleInit` | `parts: [Expr]` |
| `ArrayInit` | `kind: Fixed \| Dynamic`, `runs: [ArrayRun]` |
| `Range` | `start: Expr`, `end: Expr`, `inclusive: bool`, `reverse: bool` |
| `Interp` | `parts: [Str \| Expr]`; kept whole so `typecheck` reports per part |

```
CallTarget = Named(ref: Expr)                # a scope-resolved name, or any callable expr
           | Dotted(recv: Expr, name: str)   # x.f(...) -- built-in, extension, perk, adapter
           | Static(ty: TypeRef, name: str)  # Vec.at, List.new, Iterator.new, f64.from_bits
           | BuiltinFn(name: str)            # print, println: no declaration, no receiver

Arg      = (value: Expr, marker: Convention | None)
ArrayRun = Single(Expr) | Repeat(value: Expr, count: Expr) | RangeRun(Range)
```

A `Repeat` run `[v; n]` evaluates its value first and then its count, in the written order.
Today the backend evaluates the count first. The value is the first field of `Repeat` and
the count the second, and SHIR-to-SLIR lowering evaluates each into a temp in the field
order, so the old order goes. Ruled 2026-10-11.

A `TypeRef` is a type as WRITTEN: the `typesys.Type` the AST builder made from the
annotation, concrete in an instance and opaque in a template check copy (4.3). It is
syntax, so it may stand on a node (`Cast.target`, `Static.ty`, `ShirLocal.declared`,
`ShirParam.ty`). What a written type RESOLVES to in a position is `typecheck`'s (S5).

`CallTarget` is what the SOURCE says; the closed `Callee` of 7.6 is what `typecheck`
resolves it to (S5). The split exists because lowering runs before `typecheck`: `x.f()`
cannot know whether `f` is a built-in, an extension, a perk method or an iterator adapter
until the type of `x` is known. `Static` covers a call on a type name, user or built-in,
`hm.HashMap.new()` behind an alias included; it has no receiver EXPRESSION at all.

`marker` is the WRITTEN call-site mode (`nom s`, `poke n`), or `None`. The RESOLVED
convention is `typecheck`'s: an unmarked argument in a consuming position (constructor,
container insert, array element) resolves to `Owned` with no marker, and
[CE2427](../error-catalog.md#ce2427) compares `marker` against the declared parameter. A
marked field take in a call argument (`take(nom w.inner)`) is an `Arg` with an `Owned`
marker on a `Field`; in a `let` or a `return` it is `Take`. A struct or a variant
construction is a consuming position, and its arguments are `Arg`s too, so the written
marker of `Result.Ok(nom s)` stays in SHIR. Lowering records the written shape of a
`Take`: that the place is one step off a name that the body owns is a rule of `borrow`. In
the same way, `peek x` and `poke x` are the `marker` of an `Arg` in a call argument, and a
`Borrow` in every other position (a tuple part, an operand). The value of a reference `let`
(`let poke T x = place`) is a `Borrow` too, from the same lowering. A `Borrow` that is not
legal in its position is refused by `typecheck`, not by lowering.

`StructInit` and `EnumInit` name the declaration, because the binding table knows it at
lowering: the written name, with its alias (`geo.Vec`), as a pattern names an enum. The
instance of a generic struct or enum is `typecheck`'s answer (S5). Named struct
construction is all-or-nothing, so lowering puts named fields into declaration order and
`fields` is always positional. It reads the field names from the struct declaration, not
from a type. Reordering must not reorder EVALUATION: `written_order` gives the positions
of `fields` in the WRITTEN order, and SHIR-to-SLIR lowering evaluates each field
expression into a temp in that order, then builds the aggregate in declaration order. SHIR
has no temp, so the rule holds in every expression position: a `while` condition, an
operand of `and`, a lambda body. Today `typecheck` puts the arguments into declaration
order and the backend evaluates them in that order. That order goes at the switch. Ruled
2026-10-11.

`T.m(...)` and the bare `T.m` on a type name are an `EnumInit` when the enum declaration of
`T` has the variant `m`; else `T.m(...)` is a `Static` call (#542). A member `ns.m` behind
an alias is a `ConstRef`, a `StorageRef` or a `GlobalRef`, by what the namespace holds.
`from([...])` and `new()` are an `ArrayInit` of the kind `Dynamic`, never a `Call`.

**A readable value inside a body.** `typecheck` needs the value of some sub-expressions
that the compiler can read: a divisor and a shift count, the operands of an overflow fold,
the count of an array run, a range bound, a constant index. Lowering evaluates each such
sub-expression with the AST constant evaluator and records the value on the SHIR node. The
value is syntax: it comes from the source alone, as `unchecked` does (4.9). `typecheck`
reads the recorded value and does not call the evaluator, so the evaluator has no SHIR
reader (Q5). Ruled 2026-10-11. A sub-expression that holds a call, a field read, a member
behind an alias, a construction other than a fixed array literal, a `Borrow`, a `Take`, a
`Try` or a `Slice` records no value: the evaluator writes on the AST for a member access,
the value of such a construction is never a number that `typecheck` reads, and the
evaluator has no value for an ownership node or a view. A `Cast`, an `Index`, a `Range` and
an `Interp` are read through: the evaluator folds `4 as i32`, `[4, 2][0]` and a hole of
constants with no write on the AST.

**`expect(msg)` keeps its lazy message.** The message is evaluated only when the call
fails, as the message of an `Assert` is. A `Builtin` call with an operand that is
evaluated first cannot keep this. So SHIR-to-SLIR lowering lowers `expect` as it lowers
`Assert` (8.9): `Discriminant`, `SwitchInt`, and the message on the failing edge before the
exit. `expect` is not a row of the `Result`/`Maybe` method family (Q7). Ruled 2026-10-11.

A native variadic call is NOT collapsed here. Which parameter is `...T` is a fact about
the callee, and `typecheck` resolves the callee. So the trailing arguments stay a flat list
in SHIR, and SHIR-to-SLIR lowering folds them into one synthesized owned array (today
`build_variadic_array` does this at emit time, inside the dispatcher). A bloom `arr...`
forwards the array operand itself, as a `Move`.

### 7.5 Statements

| Node | Fields |
|---|---|
| `Assign` | `target: Expr`, `value: Expr`, `is_init: bool`; `Let` and `Rebind` collapse here |
| `Destructure` | `targets: [LocalId \| Discard]`, `value: Expr`; `let (a, b) = v` |
| `ExprStmt` | `value: Expr` |
| `Return` | `value: Expr` |
| `If` | `cond: Expr`, `then: ShirBlock`, `otherwise: ShirBlock \| None` |
| `Loop` | `source: LoopSource`, `body: ShirBlock`; the walk kind is `typecheck`'s (7.7) |
| `Match` | `scrutinee: Expr`, `arms: [Arm]`, `consumes: bool`; `match nom r:` |
| `Assert` | `cond: Expr`, `message: Expr \| None`, `label: str` |
| `Break` / `Continue` | none |
| `Expand` | `binder: LocalId`, `pack: str`, `body: ShirBlock`; a template check body only (4.5) |

```
LoopSource = Cond(cond: Expr)                                              # `while`
           | Items(iterable: Expr, binder: LocalId | Discard, by: Bare | Ref(BorrowMode))
                                                                           # `foreach`
```

`LoopSource` is what the SOURCE says: a condition, or an iterable with its binder and the
binder's written mode. The binder `_` is `Discard`, and `peek _` keeps its mode. A type
written on a plain binder is the `declared` type of its local. A type written on a `??`
binder names the value that the `let` at the top of the body unwraps, so it is the
`declared` type of the local of that `let`. Which WALK the iterable gets is `typecheck`'s answer (7.7), and it
holds no operand of its own.

`is_init` carries the one semantic difference between `Let` and `Rebind`: a rebind
RE-INITIALIZES, which clears a moved flag. A reference binding (`let poke T x = place`) is
an `Assign` with `is_init` whose value is a `Borrow`, and its local has the reference type.
A tuple rebind `(a, b) := v` reaches lowering already split by the AST builder into a hidden
binder and one `Rebind` per place.

`Destructure` stays one statement, because its ownership is not that of a field read: it
OWNS from an owned value and BORROWS from a borrow (`tuples.md`). `borrow` decides which,
and SLIR lowering writes the field moves or the field views.

`Assert` keeps its message as an expression, because the message is evaluated only when
the condition fails (`assert.md`). `label` is the source text of the condition, set by the
AST builder.

### 7.6 `Callee`: a closed set

```
Callee = Free(decl: DeclId)
       | Method(recv: Expr, decl: DeclId)        # extension or perk implementation
       | Static(ty: Type, decl: DeclId)          # a user static: Vec.at(3, 4)
       | Builtin(recv: Expr, method: BuiltinMethod)
       | BuiltinStatic(ty: Type, method: BuiltinMethod)   # List.new, HashMap.new, Iterator.new
       | Conversion(decl: DeclId)                # `e as B`, and `??` across two error types
       | Extern(decl: DeclId)
       | Intrinsic(name: str)
       | Indirect(value: Expr)                   # a closure or a fn-typed local
```

This is the closed `CalleeKind` of `param_modes.py` read as an answer, with two
differences. `Constructor` and `Container` are not callees here: a construction is a
`StructInit` or an `EnumInit`, and a container method is a `Builtin`. `Conversion`,
`Intrinsic` and `BuiltinStatic` are named, because each has a lowering of its own. The
mode table of `param_modes.py` is read per variant. Three AST call nodes collapse
into one, and **which kind of callee it is has already been decided by `typecheck`**,
which records it in `TypeckResults` against the call's `shir_id` (S5). The `Call` node
carries only the syntactic `CallTarget` of 7.4.

`BuiltinMethod` names a row of `METHOD_TYPE_REGISTRY`, so the iterator adapters, `or_err`,
`map_err`, the container methods and the string methods are one variant. `Method` carries
the declaring unit of an extension, because two units' private extensions of one name
link side by side (`extension-visibility.md`, C5).

`backend/expressions/calls/dispatcher.py` is 554 lines, with 12 handlers before the
receiver is emitted and 12 after, that decide this again at emit time. It reads the answer
instead.

### 7.7 `LoopKind`: 1.5, resolved

This is the concrete answer to `emit_foreach`.

```
LoopKind = While
         | CountedRange
         | Cursor
         | HashMapCursor(view: Keys | Values | Entries | Pairs)
         | Protocol(next: Callee)
         | IteratorValue
```

`typecheck` picks the variant, once, with the types it already has, and records it in
`TypeckResults` against the loop's `shir_id` (S5). A `LoopKind` carries NO operand: the
condition, the iterable, the binder and its mode are on the `Loop` node's `LoopSource`
(7.5), because the source says them. The kind says what the iterable IS. Nothing
downstream matches on syntax or asks the receiver type again to find out which walk
applies.

- `While` is the kind of every `Cond` source.
- `CountedRange` is a range written as the iterable; `reverse` is in the `Range`.
- `Cursor` is a direct walk of contiguous elements: `xs.iter()` on `T[]`, `T[N]`,
  `List@(T)`, and a slice. `foreach(poke r in xs.iter())` is `Cursor` over an `Items`
  source with `by: Ref(Poke)`.
- `HashMapCursor` is a direct walk of one HashMap view.
- `Protocol` walks a type that carries `next()` answering `Maybe@(T)`, such as
  `Lines@(R)`. The `next()` callee is in `TypeckResults`.
- `IteratorValue` walks an `Iterator@(T)` value through its `next_fn`.

A `while(true)` loop is a `Cond` source with a constant condition. A `foreach(x?? in ...)` reaches
lowering already desugared by the AST builder: a hidden binder, and a `let` with `??` at
the top of the body.

The iterator of a `Protocol` or an `IteratorValue` walk has no SHIR local. Today the
typecheck pass makes a hidden local for it (`Foreach.protocol_iter_name`) and the `next()`
call on it (`Foreach.protocol_next`), after `scope`, so lowering cannot read either. The
`next()` callee is the `Protocol` answer, keyed by the loop's `shir_id`, and SHIR-to-SLIR
lowering makes the SLIR local that holds the iterator. No pass before it names that local:
`borrow` keys its facts on the loop's `shir_id`.

Whether a `Protocol` or an `IteratorValue` walk takes its iterator or walks it in place,
through a `poke` root, is NOT here: it is a borrow fact (4.4), in `OwnershipResults` (S6).
The item mode, owned or lent, follows from the type (`IteratorType.peeks`).

### 7.8 Patterns

```
Pattern = Wildcard
        | Literal(value: Constant)               # an integer, a byte or a string
        | IntRange(low: Constant, high: Constant, inclusive: bool)   # 0x80..=0x8f
        | Variant(decl: DeclId, variant: str, items: [Pattern | PatBinding])
        | Tuple(elements: [Pattern | PatBinding])
        | Own(inner: Pattern | PatBinding)       # Own(poke h) -- unwraps Own@(T)
        | Or(alternatives: [Pattern | PatBinding])   # 1 | 2 -- two or more, same bindings

PatBinding = (local: LocalId, by: Bare | Ref(BorrowMode) | Nom)

Arm = (pattern: Pattern, body: ShirBlock)
```

A payload binding is required, and `_` discards (it lowers to `Wildcard`). A binding has
three modes (`borrow-model.md` §10b): `Bare` is a shallow read-only view, `Ref` points into
the payload, and `Nom` takes it, which needs a scrutinee the match owns: a
temporary, or `Match.consumes`. `consumes` is the written `match nom r:`; whether the match
owns a temporary scrutinee is an answer of `borrow`. A bare name in a pattern is a
`PatBinding` with the mode `Bare`. The mode of `Own(poke h)` is in the `PatBinding` that
`Own` holds. Two alternatives of `|` that bind one name bind one local. An arm body that
the source writes as one expression is a block of one `ExprStmt`.

An INTEGER scrutinee and a STRING scrutinee use `Literal` arms; the kinds never mix
([CE2076](../error-catalog.md#ce2076)), and that is checked on SHIR. `IntRange` is an
INTEGER pattern only: the AST's `RangePattern` (`a..b` or `a..=b`) lowers to its written
bounds and `inclusive`. Lowering has no types, so `typecheck` reads the bounds as values of
the type at the position: `0..10` matches 0 to 9, and `0x80..=0xff` on an i8 matches -128
to -1. A `Literal` integer is a value of that type in the same way. A bound is a literal and
not an expression, so lowering does not call the constant evaluator for it, and a constant
keeps its spelling (a radix, a byte). `Variant` names its enum by the written name, the
alias included: a name that is not an enum is an error of `typecheck`, not of lowering.
`Tuple` mirrors `TuplePattern`, `Own` mirrors `OwnPattern`, and `Or` mirrors `OrPattern`:
it stands at the top of an arm and in every pattern position. Each alternative binds the
same locals, with the same types and the same mode ([CE2126](../error-catalog.md#ce2126)),
so one binding list serves the arm whatever alternative matched.

### 7.9 What each pass reads and writes

| Pass | Runs on | Reads | Writes |
|---|---|---|---|
| `effects` | **AST**, whole-program | AST bodies | the destroy-effect summary (ruling S4) |
| `scope` | **AST** | AST bodies | `Bindings` (ruling S3) |
| *lowering* | AST + `Bindings` | | `ShirBody` |
| `typecheck` | SHIR | `ShirBody` | `TypeckResults` (S5) |
| `lift` | SHIR | `ShirBody`, `TypeckResults`, `Bindings` | `LiftResults` (Q8) |
| `borrow` | SHIR | `ShirBody`, `TypeckResults`, `LiftResults`, the destroy-effect summary | `OwnershipResults` |

A template check body runs the same three SHIR passes, at opaque type arguments, and stops
there: it never lowers to SLIR (4.3).

`OwnershipResults` is the second typed side table, and it obeys the four rules of 7.3:

| Decision | Keyed by |
|---|---|
| the `Ownership` of each consuming use (move, copy, reject) | `shir_id` |
| the locals that are CONDITIONALLY moved | the body |
| whether a producer makes a LENT or an OWNED iterator | the producer's `shir_id` |
| whether a lent iterator's state goes to the heap, because the iterator leaves through a `peek` return | the producer's or adapter's `shir_id` |
| whether a loop walks its iterator in place | the loop's `shir_id` |

**Ruling S6: one fact, one writer.** Before this ruling, `typecheck` and `borrow` both
wrote the walk in place and the iterator state (1.2). Each of those facts is a question about ownership, and
`borrow` sees every input that `typecheck` sees. So `borrow` writes them, and `typecheck`
writes nothing about them. A `Cursor` walk needs no state, because its `LoopKind` says so.

`LiftResults` is the third typed side table, written by `lift` alone, under the same four
rules:

| Decision | Keyed by |
|---|---|
| the symbol of the lifted function | the `Lambda`'s `shir_id` |
| the environment layout: the captured locals in order, each with its capture mode (copy for a plain value, move for an owning one) | the `Lambda`'s `shir_id` |

**Ruling Q8: `lift` rewrites nothing.** The `Lambda` node stays in the tree, its body is
checked where it stands by `typecheck` (its types are rows of the enclosing body's
`TypeckResults`) and by `borrow` (a move capture is a consuming use of the captured local,
read from `LiftResults`). SLIR lowering cuts one `SlirFunction` per `Lambda` from the
enclosing body's tables and builds the closure `Aggregate` from `LiftResults` (8.9). So no
pass rewrites SHIR after it is built, `TypeckResults` keeps one writer (S5), and a
`shir_id` stays unique within one body.

### 7.10 AST to SHIR, node by node

| AST | SHIR |
|---|---|
| `Name` | `LocalRef` / `ConstRef` / `StorageRef` / `GlobalRef`, from `Bindings` |
| `IntLit`, `FloatLit`, `BoolLit`, `BlankLit`, `StringLit` | `Lit` |
| `Call`, `MethodCall`, `DotCall` | `Call` with a `CallTarget`; a struct name gives `StructInit`, also behind an alias; a variant of an enum gives `EnumInit` |
| `Print`, `PrintLn` | `ExprStmt` of a `Call` with a `BuiltinFn` target |
| `Let`, `Rebind` | `Assign` with `is_init`; a `Let` with targets gives `Destructure` |
| `If` | `If`; each `elif` is an `If`, the one statement of the `otherwise` block of the arm before it |
| `Return`, `ExprStmt`, `Break`, `Continue` | the node of the same name |
| `Assert` | `Assert` |
| `While` | `Loop` with a `Cond` source |
| `Foreach` | `Loop` with an `Items` source; the walk kind is one of five `typecheck` answers. The `let` of a `??` binder and the destructure of a tuple binder stay the first statement of the body, as the AST builder made them |
| `ArrayLiteral`, `DynamicArrayNew`, `DynamicArrayFrom`, `ArrayElement` | `ArrayInit` with runs: `Fixed` for `[...]`, `Dynamic` for `from([...])` and `new()` |
| `EnumConstructor` | `EnumInit` |
| `TupleLiteral` | `TupleInit` |
| `MemberAccess` | `Field`; `ConstRef` / `StorageRef` / `GlobalRef` through a namespace; `EnumInit` for a unit variant (`Sign.Plus`) |
| `IndexAccess` | `Index` with the `unchecked` mark of the builder (4.9), or `Slice` when the index is a range; a reversed range (`data[(0..3).rev()]`, CE2122) is a hole, because `Slice` has no field for it |
| `CastExpr` | `Cast` |
| `TryExpr` | `Try`, **kept** |
| `InterpolatedString` | `Interp`, **kept**: each text part as written, an empty one too, and each hole as an expression |
| `Expand` | `Expand` in a template check body; nothing in an instance (4.5) |
| `Spread` (bloom `arr...`) | the array operand, forwarded whole: an `Arg` with `bloom`; SLIR lowering makes it a `Move` |
| `Borrow` | `Borrow`; in a call argument, the `marker` of the `Arg` |
| a `nom`-marked field read | `Take`, or an `Owned` marker on the `Arg` |
| `RangeExpr` | `Range`, a run of an `ArrayInit`, or a `Slice` bound |
| `UnaryOp`, `BinaryOp` | `Unary`, `Binary` |
| `Lambda` | `Lambda` |
| `Match`, `MatchArm` and the 9 pattern classes | `Match`, `Arm`, `Pattern`, `PatBinding` |

The 52 body classes of the AST become 40 SHIR nodes: 22 expressions, 11 statements and 7
patterns. The count is not the gain. The gain is that each SHIR node has one meaning, and
the meaning that only `typecheck` knows is a table entry and not a test in the backend.

### 7.11 What SHIR does not have

No basic blocks. No SSA. No drop instructions. No type parameters, except the opaque ones
of a template check body. No mangled symbols in the tree: a lifted lambda's symbol is a
`LiftResults` row. Those belong to SLIR, or are already gone.

---

## 8. SLIR: the Sushi Low-level IR

**Purpose:** input to code generation.

**Shape:** a control-flow graph. Basic blocks, explicit terminators, no nesting.

### 8.1 The three model rulings

**R1. Conventions from Swift SIL.** Fact 4.1: Sushi's declared modes map one-for-one onto
SIL's calling conventions, and MIR has no equivalent concept. Every SLIR call argument and
every parameter carries an explicit `Convention`.

**R2. Value model from Rust MIR: places and locals, NOT SSA.** Drop elaboration and clone
elision are questions about *storage*, not about values: "has this slot been moved out of"
is a question about a place. This is why MIR is not SSA, and it holds here. LLVM's
`mem2reg` builds SSA, so constructing phi nodes is duplicated work.

Swift does ownership inside SSA (OSSA), and it is possible. It also took Apple years. If
SLIR ever goes SSA, use **block parameters** (Cranelift, SIL, MLIR) and not phi nodes: no
per-predecessor bookkeeping, and an edit of the CFG does not mean a rewrite of phi lists.

**R3. No unwinding machinery, anywhere.** Fact 4.2. This is where SLIR is smaller than
MIR, and it is the ruling most easily lost by somebody who copies MIR without thinking:

- a call needs no unwind successor, so **`Call` is a statement, not a terminator**
- there are no cleanup blocks and no landing pads
- drop elaboration has to be correct on normal control flow only

With `Call` as a statement, blocks are longer, flatter and far easier to read. In MIR every
call splits a block. In SLIR none do.

### 8.2 The unit

```
SlirFunction
  symbol : str                       # the mangled name; monomorphic (ruling Q1)
  unit   : str                       # the declaring unit; the module it goes into
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
  statements : [SlirStatement]       # each carries a Span (G9)
  terminator : SlirTerminator        # carries a Span
```

`locals[0]` is always the return slot, written `_0`.

A statement that lowering synthesizes (a `Drop`, a flag write, a `StorageDead`, a bounds
check) takes the `Span` of the construct that caused it: the scope exit, the move, the
index. No statement has an empty span (G9).

### 8.3 Conventions

```
Convention = Owned          # `nom`     -- the callee frees
           | Guaranteed     # default   -- caller owns, callee borrows, by value
           | InGuaranteed   # `peek`    -- by address, read-only, many at once
           | Inout          # `poke`    -- by address, read-write, exclusive
```

`param_modes.py` resolves the mode per callee kind today, and the `borrow` pass and the
backend each call it. Under this design `typecheck` resolves it once, per argument, into
`TypeckResults` (7.3), and SLIR lowering writes that answer into the call. That is G1
applied to the call boundary.

### 8.4 Places and operands

```
Place    = Local(id: LocalId)
         | Global(symbol: str)                # a unit-level `var`
         | Field(Place, index: int)
         | Index(Place, Operand)
         | Downcast(Place, variant: str)      # narrow an enum to one variant
         | Deref(Place)

Operand  = Copy(Place)     # the value is PLAIN, or the source is a borrow
         | Move(Place)     # ownership transfers; the source is dead after this
         | Const(Constant)
```

**The `Copy` / `Move` distinction is the point.** `classify()` runs once, in the `borrow`
pass, and lowering writes its answer into the operand. Nothing derives it again. 1.3
disappears and [CE0129](../error-catalog.md#ce0129) has nothing left to guard.

`Downcast` exists because a `match` arm and a `??` both read an enum payload, and reading
one is valid only under a known variant. The worked example in 8.11 is what found it.

`Global` is a place, never a value to move out of: a `var` is never moved out of, and
`borrow` refuses a consuming use of one that owns a resource (`unit-storage.md`). The
declaring unit's module defines the global, and every other module declares it external.

### 8.5 Constants

```
Constant = Int(value: int, ty: Type)
         | Float(value: float, ty: Type)
         | Bool(value: bool)
         | Blank                                  # `~`
         | Str(bytes: bytes)                      # a literal; owns no heap
         | Aggregate(ty: Type, parts: [Constant]) # a const array, struct or enum variant
         | FnRef(symbol: str)
```

Produced by the constant evaluator on the AST (ruling Q5) and carried through unchanged.
Only the backend turns one into an `ir.Constant`.

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
SlirCallee = Direct(symbol: str)                         # free fn, method, extern, conversion
           | Builtin(method: BuiltinMethod, types: [Type])   # an emitter family (Q7)
           | Indirect(Operand)                           # a closure's fn_ptr
```

`DropIfSet` replaces the string-keyed drop-flag table of 1.4. The flag is an ordinary SLIR
local of type `bool`. It is visible, testable and optimizable.

`BeginBorrow` / `EndBorrow` make a borrow REGION explicit.
[CE2412](../error-catalog.md#ce2412), a change of an owner while a `let`-borrow is alive,
is a question about a region, and a lent iterator freezes the owners of its place until
its last use (`iteration.md`, ruling 10). Writing the region down is the same argument as
`Copy` / `Move`: the pass that proved it records it, and nothing works it out twice. A
region can hold a SET of owners, as a `zip` of two lent iterators does: one `BeginBorrow`
per owner, under one region.

`Builtin` is a call to a per-type emitter family (Q7). The backend expands it with the
existing emitter, which receives LLVM values for the operands and no AST node.

### 8.7 Rvalues

```
Rvalue = Use(Operand)
       | Binary(op: BinOp, Operand, Operand)     # never `and`/`or` -- see 8.9
       | Unary(op: UnOp, Operand)
       | Cast(Operand, target: Type)
       | Aggregate(kind: AggKind, parts: [Operand])   # struct, tuple, enum variant, array,
                                                      # closure, iterator
       | Discriminant(Place)                     # an enum's tag, as i32
       | Clone(Operand)                          # the ONE deep copy, still one seam
       | Len(Place)
       | Slice(Place, start: Operand, end: Operand)   # a borrowed `T[]` view
       | Ref(Place, mode: BorrowMode)            # peek / poke
```

`Discriminant` was also found by the worked example: `SwitchInt` needs an integer, and a
`match` or a `??` switches on a tag.

A closure and an iterator have one layout, four words. `AggKind` keeps them apart, because
the backend reads the SEMANTIC type and never the layout to know which one it has
(`iteration.md`, ruling 10).

### 8.8 Terminators

Five, because a call is not one of them (R3):

```
Goto(BlockId)
SwitchInt(Operand, targets: [(int, BlockId)], otherwise: BlockId)
Return
Trap(code: str, args: [Operand])   # run-time traps. Exits. Never unwinds
Unreachable
```

`Trap` carries operands because the registry text is a printf format string and some traps
print values: [RE2020](../error-catalog.md#re2020) prints the index and the size.

### 8.9 SHIR to SLIR, construct by construct

| SHIR | SLIR |
|---|---|
| `If(c, t, e)` | `SwitchInt(c, [(0, else_bb)], otherwise: then_bb)` |
| `Binary(and/or)` | **two blocks.** Short-circuit is control flow, never an rvalue |
| `Try(e)` | evaluate `e`; `Discriminant`; `SwitchInt`; the Err arm calls the conversion if there is one, builds the Err and returns |
| `Match` on an enum | `Discriminant` + `SwitchInt`; one block per arm; payload through `Downcast` + `Field` |
| `Match` on an integer | `SwitchInt` on the value; with a `Range` arm, a chain of compares in source order |
| `Match` on a string, a tuple, or with `Or` arms | a chain of tests in source order, each a `Call` to the value equality and a `SwitchInt` on its bool |
| `Loop`, kind `While` | header block, `SwitchInt`, body, `Goto` header |
| `Loop`, kind `CountedRange` | counter local, header, compare, body, step, `Goto` header |
| `Loop`, kind `Cursor` | `Len` into a local, then the `CountedRange` shape |
| `Loop`, kind `HashMapCursor` | the one probe walk, over the counted `data[0..count)` range |
| `Loop`, kind `Protocol` | the iterator in a hidden owning local (or an `Inout` place, for a walk in place); each round calls `next()`, `Discriminant`, `SwitchInt` |
| `Loop`, kind `IteratorValue` | the same shape, with an `Indirect` call through `next_fn` |
| an iterator producer or a lazy adapter | an `Aggregate(Iterator, ...)` of the source's three functions and its state; the state is a local when the iterator is lent and stays in the function, and a heap block otherwise (`OwnershipResults`) |
| `Range` as a value | an owned iterator over the range formula |
| `Interp(parts)` | a sequence of `Builtin` string-builder calls into a fresh owning local |
| `Lambda` | one `SlirFunction` cut from the body, and an `Aggregate(Closure, [FnRef, env...])` built from `LiftResults` (Q8); each captured operand is `Copy` or `Move` by `OwnershipResults` |
| `ArrayInit(runs)` | `memcpy` for a plain element, one `copy_out` per slot for an owning one |
| `Index` read | bounds compare, `SwitchInt`, `Trap("RE2020")` on the failing edge; no check when `unchecked` |
| `Index` write (`arr[i] := v`) | the same bounds check; the target slot is a consuming position |
| `Slice` | range compare, `SwitchInt`, `Trap("RE2028")` on the failing edge, then `Slice`; never unchecked |
| `Assert` | `SwitchInt` on the condition; the failing edge evaluates the message, then `Trap("RE2026")` |
| `Destructure` | one `Field` place per target: `Move` from an owned value, a view from a borrow |
| `Take` | `Move` of the field; the other owning fields of the receiver get `Drop`, and `drop()` does not run (`borrow-model.md` §10c) |
| a variadic tail | folded into one synthesized owned array argument (7.4) |
| a `string` argument to an `i8*` callee | an explicit conversion into an owning temp local, dropped by the rules of 8.10. The `emit_cstr_arg` seam becomes visible IR, still one lowering site, and `run()` stays the one exception |

Four SHIR nodes vanish at this boundary: `Try`, `Interp`, `Destructure` and `Take`. They
were kept in SHIR only so that `typecheck` and `borrow` could report against the source
(G7). `Expand` never reaches it.

### 8.10 Drop placement

Seven rules. They encode the two drop regimes (a local moved on every path has no drop; a
local moved on some paths has a drop flag), the rebind order of 4.6, and the loop's own
locals, and nothing else:

1. An owning local gets `StorageLive` at its declaration and `StorageDead` after its last
   use in the enclosing block.
2. `Drop(place)` is emitted on every exit path of the owning scope, before `StorageDead`,
   newest declaration first (reverse declaration order).
3. A local that `borrow` proved moved on **every** path gets no drop at all.
4. A local marked CONDITIONALLY moved gets a `bool` flag local: set `true` at the
   declaration (set again on each loop iteration), set `false` at each move site, and its
   drop becomes `DropIfSet`.
5. A borrow never drops. `Guaranteed`, `InGuaranteed` and `Inout` parameters never drop;
   an `Owned` parameter drops in the callee.
6. An `Assign` to an owning place that may hold a live value drops the OLD value, and the
   order is normative (4.6): compute the new value FIRST, then `Drop` the old, then store.
   The source may alias the value about to be freed. A rebind through a `poke` parameter
   follows the same rule, through the pointer.
7. The hidden iterator local of a `Protocol` or an `IteratorValue` loop, and the item that
   the loop owns, are owning locals of the loop scope under rules 1 to 4: the end, a
   `break`, a `return` and the `??` of a binder all reach a `Drop`. A walk in place has no
   hidden local and drops nothing. A lent iterator is still dropped: it borrows its source
   and owns its value, and its `drop_fn` frees only what the state owns.

### 8.11 A worked example

The source:

<!-- docs-sweep: skip (fragment: calls functions the example does not declare) -->
```sushi
fn greet(nom string name, bool loud) ~ | StdError:
    let string msg = decorate(nom name)??
    if (loud):
        shout(nom msg)
    return Result.Ok(~)
```

`msg` is moved on one path and not on the other, so it needs a drop flag. This is the case
that 1.4 handles today with a name string.

**SHIR**: a tree, `Try` intact, no drops, no blocks. The printer shows side-table answers
in place, so `Free(@decorate)` and the conventions below are `TypeckResults` facts (S5)
shown inline:

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

**SLIR**: this is also the `--dump-slir` text format:

```
fn greet(_1: string @owned, _2: bool @guaranteed) -> Result@(~, StdError) {
    let _0: Result@(~, StdError)           // return slot
    let _3: Result@(string, StdError)
    let _4: i32                            // discriminant
    let _5: string                         // msg
    let _6: bool                           // drop flag for _5
    let _7: Result@(~, StdError)           // shout(), unused

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

Read `bb3` and `bb4` together. The whole of 1.4, the LLVM-keyed move set, the name-string
drop-flag lookup, `arm_if_conditional` and `emit_free_unless_moved`, is those three lines.
That is the design in one screen.

### 8.12 What SLIR removes from the backend

| Backend concern today | In SLIR |
|---|---|
| `MoveTracker` and its LLVM-keyed sets | `move` operands and `Drop` statements |
| drop flags by name string | `DropIfSet` with a real local |
| cleanup registries keyed by name and by AST node identity | `Drop`, placed by the rules of 8.10 |
| scope-exit cleanup during emission | `Drop`, placed by the rules of 8.10 |
| `classify()` at three emit sites | `Copy` / `Move`, written at lowering |
| `emit_foreach` protocol matching | `LoopKind`, already resolved in SHIR |
| `dispatcher.py` deciding the callee kind | `Callee`, already resolved in SHIR |
| three call kinds | one `Call` statement |
| `??` control flow at emit time | `Discriminant` + `SwitchInt` |
| mode resolution at emit time (13 `param_modes` imports) | one `Convention` on the call |
| 34 semantic type inferences at emit time | every local is typed |
| borrow-region derivation | `BeginBorrow` / `EndBorrow` |
| iterator state placement read from two stamps | the state local or heap block, written at lowering |

What is NOT in this table: the 2,809 lines of monomorphization. Ruling Q1 leaves them
where they are. Also not in the table: the per-type emitter families. Ruling Q7 keeps them.

### 8.13 The verifier

Runs after lowering, before the backend. It checks:

- every local is typed, and every `Place` type-checks against it
- every `Downcast` names a real variant of the place's enum
- no use of a place after a `Move` of it, on any path
- a `Move` of a sub-place (a `Field`, a `Downcast`) is a `Move` of its root local, and
  no local is ever PARTIALLY moved: lowering drops the sibling owning fields first (8.9,
  `Take`), or the sibling payloads of a pattern take, because an arm takes the variant
  whole (`borrow-model.md` S10b); a `Try` payload has no sibling. So the root is spent
  whole, and the owning-local rule below reads the root
- no `Move` out of a `Global`
- every block ends in exactly one terminator, and every `BlockId` exists
- every `Drop` targets an owning type
- every owning local reaches exactly one of: a `Drop`, a `DropIfSet`, or a `Move` on
  every path
- every `BeginBorrow` has exactly one matching `EndBorrow` on every path
- every argument `Convention` matches the callee's declared parameter, with one
  carve-out: the tail of a `var_arg` extern has no declared parameters, so the rule
  applies to the declared prefix only
- no `Binary` carries `and` or `or` (8.9)
- every `Trap` code is a registered RExxxx, with the operand count its format string needs
- every statement and terminator carries a `Span` (G9)

This is where most of the 250 backend internal errors go. One gate, one message, one source
location, asked once.

---

## 9. Decided questions

**Q1. Is SHIR polymorphic? NO. SHIR is monomorphic.**

Fact 4.3 is the evidence. A template IS checked where it is written, but the check is a
copy at opaque type arguments, run through the same three body passes. So the template
check needs no polymorphic IR: its copy is one more `ShirBody`, with `origin:
TemplateCheck`, and it stops before SLIR.

What code generation needs is per instance: the consume-or-copy decision, the drop set,
the derived methods, the layout and E3 all depend on the concrete type. A polymorphic SHIR
would still need a per-instance pass for all of them, before SLIR. It would add a level and
remove nothing.

Consequences: `monomorphize` keeps its position and keeps working on the AST. SHIR is
built per copy. The 2,809 lines stay. The generic inference gaps are untouched (N6).

**Q2. Does the copy fixpoint stay? YES.**

Follows from Q1. After the per-unit loop, `_check_array_extensions`
(`semantic_analyzer.py:917`) runs a fixpoint bounded by `MAX_NEW_TYPE_ROUNDS`: it drains
the extension copies that a first call cut, places the array-template perk copies and the
late function bodies, and `_check_copies` runs `scope`, `typecheck`, `lift` and `borrow`
over each one with the home unit's passes. Under this design each copy is lowered to SHIR
when it is placed, with its own side tables (7.3), and the fixpoint is otherwise unchanged.

**Q3. One node set with a legality level, or two hierarchies? TWO hierarchies.**

MLIR's dialect trick works because every level shares one shape: regions of blocks of
operations. **SHIR is a tree and SLIR is a control-flow graph.** They do not share a shape.

A shared node set would need every node to be valid in both a tree and a graph. That is
exactly the overloading that 1.1 identifies as the problem, put back one level up. The
duplicated walking and printing code is the smaller cost.

**Q4. Does the `.slib` format change? NO.**

A source `.slib` ships Sushi text, and the consumer compiles it. A binary `.slib` ships
LLVM bitcode and a manifest, and the backend still emits LLVM (non-goal N1). Neither is
touched.

Recorded so that nobody later presents it as new: shipping **SLIR** in a `.slib` would be a
backend-neutral binary format, and the header already has a `KIND` byte. That is a real
option and it is out of scope here.

**Q5. Where does the constant evaluator live? It stays where it is, on the AST.**

Fact 4.7. Constants are a **declaration-level** concern, evaluated on the AST during
`collect`. SHIR carries `ConstRef(id)` and the value is already computed. The evaluator
never needs a SHIR reader, and no second evaluator is built.

**Q6. Do we need a text format? A printer, yes. A parser, no.**

Add `--dump-shir` and `--dump-slir`, in the same family as `--dump-parse`, `--dump-ast`
and `--dump-ll`. The printer renders types in the source spelling (`Result@(T, E)`),
through `display_type`.

The printer is what makes G4 real: a lowering test asserts on printed SLIR text, with no
`cc` and no running binary. It is also the only practical way to review the migration's
diffs.

No parser. Nothing needs to read SLIR back in, and a parser would be a second definition of
the IR to keep in step with the first.

**Q7. What happens to the per-type emitters? They stay, and they take operands.**

A large part of the backend is not tree-to-IR translation. It is a family of emitters per
type: the clone and destroy pair per type kind (`backend/lifecycle.py`), the equality, the
order and the string form (`backend/types/contracts.py`, `backend/types/display.py`), the
hash of a held value (`backend/types/value_hash.py`), the container method tables
(`LIST_EMITTERS`, `HASHMAP_EMITTERS`), the stdlib call table (`STDLIB_EMITTERS`), the
string method rows, the iterator sources and adapters (`backend/runtime/iterators.py`,
`backend/runtime/iterator_adapters.py`) and the runtime traps. Each is already one seam,
with a gate.

These are not lowered from SHIR. SLIR calls them: a `Builtin` callee for a method family,
a `Direct` callee for a generated helper function, `Clone` for the one deep copy, `Drop` for
the destroy half, `Trap` for a run-time error. What changes is their input: an emitter
receives LLVM values for SLIR operands and the semantic types of SLIR locals, and never an
AST node. The migration replaces the statement, expression, ownership and dispatch layer;
the emitter families stay (N7).

Take `List.push` (`generics/list/methods_modify.py`). Its emitter does two jobs: it emits
the AST argument and calls `consume` on it, and then it grows the buffer, stores the element
and adds one to the length. Under this design the first job is gone, because lowering has
already made the argument an operand with `Move` or `Copy`. The second job is the family's,
and it stays.

**The door.** A family moves into SLIR only when a NAMED optimization needs to see inside
it, and only by its own ruling. "SLIR would be more uniform" is not a reason. A move of a
family brings the low-level layer it needs (raw pointers, byte offsets, `memcpy` and
`realloc` intrinsics) into SLIR, and the ruling must accept that cost.

**Q8. What does `lift` produce on SHIR? A side table. It rewrites nothing.**

The alternative was the first draft of 7.9: `lift` replaces the `Lambda` with a reference
and a closure aggregate and cuts a new `ShirBody`. That rewrites SHIR after `typecheck` has
run, and the new nodes need types, so `lift` becomes a second writer of `TypeckResults`,
against S5, and a tree that one pass may rewrite is the overloading of 6.2 one level up.

So `Lambda` stays in the tree through `borrow`, `lift` writes `LiftResults` (7.9), and SLIR
lowering cuts the function and builds the closure (8.9). Ruled 2026-10-11.

---

## 10. What carries over

### 10.1 The assets that carry over unchanged

This is not a rewrite from nothing:

- **`semantics/typesys.py`** is a frozen-dataclass type model with no LLVM in it, the
  opaque type parameter and the `IteratorType` included. Both SHIR and SLIR use it. No new
  type model is needed.
- **`semantics/ownership.py`** already defines `Provenance`, `TypeClass`, `Ownership` and
  `classify()`. The vocabulary for an ownership IR exists.
- **`semantics/param_modes.py`** already has a closed `CalleeKind`. That is the model of
  SHIR's resolved `Callee` (7.6), and its mode result becomes SLIR's `Convention`.
- **`semantics/name_ladder.py`** already orders the kinds of a bare name. That becomes the
  kinds of `Bindings`.
- **`METHOD_TYPE_REGISTRY`** already names every built-in method family. That becomes
  `BuiltinMethod`.
- **`backend/types/core/sizing.py`** (`TypeSizing`) is already free of `llvmlite`.
- **The constant evaluator's values** are already neutral; the backend converts them
  (`backend/constants/llvm_values.py`).
- **The emitter families of Q7**, each behind one seam with its gate.

### 10.2 The behaviour suite

The `.sushi` suite (5,801 fixtures) asserts on behaviour: stdout, exit codes, error codes
and leaks (1,483 fixtures carry `EXPECT_NO_LEAKS`). It does not assert on compiler
internals. A refactor of this size usually fails because the tests encode the old
structure. Here they do not. This is the single largest reason the work is feasible.

One small group is the exception: 16 fixtures assert on the emitted LLVM IR
(`EXPECT_IR_HOLDS`, `EXPECT_IR_LACKS`, `EXPECT_IR_FUNCTION`, `EXPECT_IR_NO_FUNCTION`). Most
assert that a copy is absent or present, which the new backend keeps; each one is read
again when its area moves.

### 10.3 The pytest seam gates: ported, not kept

The Python unit layer is different: the mandated seam gates
(`test_consuming_use_coverage`, `test_borrow_dispatch_is_total`,
`test_callee_mode_matrix`, `test_scope_exit_walk_is_one`,
`test_method_call_dispatch_is_a_table`, ...) assert on the OLD structure by name. Each
guarded seam has a named replacement (8.12), so each gate is ported when its seam moves.
The gates of the emitter families (Q7) stay. This is a real, planned cost.

`test_owning_binding_uses_the_router` is dropped, not ported. Its replacement is the one
drop-placement gate that `test_scope_exit_walk_is_one` ports to, and two ports of one rule
are two gates for one rule. Ruled 2026-10-11.

---

## 11. Constraints on the migration plan

The plan is written elsewhere; these are the design-level invariants it must honour.

1. **The growth rule.** The set of programs that compile through the NEW pipeline only
   grows. No dual-path backend, no escape-hatch node that falls back to the old emitter,
   no bridge that writes SHIR results back onto AST nodes. An unlowered construct fails
   loudly.
2. **Ownership is built into SLIR from its first construct.** `Move` / `Copy` operands,
   `Drop`, `DropIfSet`, `BeginBorrow` / `EndBorrow` and `Convention` are not retrofits; a
   lowering built without them is written twice.
3. **The printers are a dependency, not a convenience** (G4, Q6). Between "the passes
   move" and "the first program runs end to end", golden-file tests on `--dump-shir` /
   `--dump-slir` plus the verifier (8.13) are the only signal. Golden files demand what
   the current backend does not have: **deterministic output**. Ids are assigned in
   traversal order, side tables print in id order, and no printed line depends on the
   iteration order of a dict or on `PYTHONHASHSEED`. A golden file that flakes trains
   people to re-bless it.
4. **Diagnostics return with their constructs, and no tier closes without its own.** A
   `test_err_*` test (exit 2) comes back with the `typecheck` or `borrow` chunk of the
   construct group it belongs to, and a tier's gate is not met while one of its
   diagnostics is missing. A `test_warn_*` test asserts exit 1, which needs a full
   successful compile; those return with the emission chunk of their group.
5. **The leak suite is the net for ownership.** Extend `EXPECT_NO_LEAKS` coverage BEFORE
   lowering the owning types, not during. A missing leak test there is a bug that survives
   to the end undetected. The descriptor check (`EXPECT_NO_OPEN_FDS`) is the same net for
   a `Drop` handle.
6. **The incremental cache is preserved.** A monomorphized instance carries its declaring
   unit, so SLIR functions group into one module per unit exactly as the emitters do
   today, and the per-unit `.o` cache keys on the same fingerprints.
7. **The seam gates travel with their seams** (10.3): a gate is ported in the same change
   that moves what it guards, never later.
8. **No language epic starts while the migration is open (C8).** With no dual path
   (rule 1), a construct that `main` gains during the migration must also be lowered on
   the migration branch before that branch can merge, so each language epic in that window
   would be built twice. Bug fixes still land on `main` and are ported to the branch.
   Standard library work in Sushi source (`src_sushi/`) continues, because it adds
   programs and no construct, and each new program is more corpus for the new pipeline.
   Work that needs SLIR waits for it: debug information (G9) and the range analysis of
   #1244 (G6).
9. **Evaluation order is kept where an emitter evaluates its own argument.** A family
   emitter that evaluates an argument through a callback chooses when the argument runs:
   `List.push` evaluates its element AFTER the buffer grows. Under SLIR every operand is
   evaluated before the call. For each family with such a callback, the plan shows that
   the order is not visible to a program, or it keeps the order, or a ruling changes it.
   Ruled 2026-10-11 for each visible order: where the old order is a defect (a receiver
   that is read before an argument changes it, a receiver or an argument that is evaluated
   two times), SHIR lowering gives the SHIR order by construction and the old order is not
   kept. A named struct construction takes the written order, and a repeat run takes the
   value first (7.4). `expect` keeps its lazy message (7.4).
