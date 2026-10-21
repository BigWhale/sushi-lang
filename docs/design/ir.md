# The Sushi IR: SHIR and SLIR

**Status: ACCEPTED.** Measured against the tree on 2026-10-11, at `cc4088ca`, and ruled
on the same day. Every design question has a ruling. No SHIR or SLIR code exists in the
tree. This document is the DESIGN: what the two IRs are, why they have this shape, and what
a migration must keep. The phases, the risks and the progress tracking are not part of this
document; a migration plan comes from it. Nothing in the language changes: every behaviour
test passes before and after, unchanged.

Amended on 2026-10-11 by the rulings on the migration plan: 6.3 (initializer bodies), 6.4
(the gate reads the body modules), 7.4 (evaluation order, recorded values, `expect`), 10.3
and 11.9. Amended on 2026-10-20 by the rulings at Gate 1: 7.4 (`typecheck` folds a readable
value at the type of its position; an error hole carries its code), 7.5 and 8.9 (a tuple
rebind is one `Destructure` with `Place` targets).

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

Every number in this section was measured in the tree on 2026-10-11, at `cc4088ca`. The
section describes the pipeline before the migration: its backend modules, the AST body
emitter, are deleted (8.12).

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
  `dispatcher.py:322` writes `resolved_struct_type`.

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
CE0129: fatal, with a deliberate no-fallback rule. Three
more backend sites read the field outside the seam (`statements/returns.py:62`,
`expressions/memory.py:265`, `runtime/closures.py:205`), and `_mark_moved` (`:160`) finds
the moved name by a test on the syntax (`Name` or `MemberAccess`).

CE0129 was not a bug. It was the correct response to a
missing note in a design that passes notes. An IR removes the whole category, because the
decision becomes an instruction.

### 1.4 Ownership state is keyed on LLVM objects and on names

```
self._moved: Set['ir.Instruction'] = set()
self._flags: Dict['ir.Instruction', 'ir.AllocaInstr'] = {}
```

`MoveTracker` (`moves.py:13`) keyed move state on `llvmlite` objects.
`arm_if_conditional(name, slot)` armed a drop flag when the **name string** is in
`codegen.current_conditional_moves`, a set copied from `Block.conditional_move_names` at
the start of each function. The scope manager keyed its cleanup registries by name, and
`_held_by_temporary` keyed an owning temporary by `id(expr)`, the identity of an AST node.

RAII was therefore tied to `llvmlite` object lifetimes, to name strings and to AST node
identity.

### 1.5 The backend makes semantic decisions by matching on syntax

`emit_foreach` (`loops.py:58`) picked a loop protocol like this:

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
| The backend may not read the AST | `grep -rn "semantics.ast" sushi_lang/backend/` is empty (37 files match: the declaration round and the families that still take an AST node) | when the old backend is deleted |

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
on EVERY copy before that copy is lowered: the copies of the fixpoint (Q2) get it today.
The body of a template check (4.3) is lowered from the template AS WRITTEN, which `scope`
walks in the per-unit loop, so its `Bindings` exist and the deep copy of the check needs no
`scope` run of its own. Each type parameter stays a type as written in that body, and its
origin `TemplateCheck(TemplateId)` names the template whose opaque parameters `typecheck`
reads it at.

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
  copies  : ShirId -> [CopyRange]  # the copies of an unrolled `expand`, by block (4.5)

ShirParam
  local      : LocalId
  ty         : TypeRef                   # the declared type (7.4)
  convention : Convention          # from the declared mode -- section 8.3
  loc        : Span
  pack       : bool                # the written `...` of a value pack parameter

CopyRange
  start, stop : int                # the statements of one copy; `stop` is not included

ShirLocal
  id       : LocalId
  name     : str | None            # the source name; None for a hidden local
  declared : TypeRef | None        # the type as WRITTEN, when the source has one
  loc      : Span | None
  declared_loc : Span | None       # the span of the written type
```

Every node carries `shir_id` and `loc: Span`. No node and no local carries a computed
type. A local's type is `typecheck`'s answer, in `TypeckResults` keyed by `LocalId` (7.3):
a written type is checked against it, and a binder with none (a `foreach` item, a pattern
binding, a destructuring target, a bare lambda parameter) is inferred into it. `declared`
is syntax, like `ShirParam.ty`: the user wrote it. `declared_loc` is where the user wrote
it, and a type mismatch puts its note there ("declared here").

A hidden local is a local the AST builder made: the binder of a `foreach(x?? in ...)` or of
a `foreach((a, b) in ...)`, the `let` of a `foreach(_?? in ...)`. It has no source name and no span, and no diagnostic names it. The
`_` binder of a `foreach` with no `??` binds nothing: its source holds `Discard` (7.5). The
AST builder also binds the whole value of a destructuring `let` to a hidden name, but no
SHIR node reads it, so it is not a local of the body: `Destructure` holds the value, and
SHIR-to-SLIR lowering makes its place.

`pack` is true for a value pack parameter of a template check body (`...Ts args`); its `ty`
is the pack as written (`Ts`). In an instance, each element of the pack is a parameter of
its own (`#pack_args_0`), and no element is a pack.

`copies` is a side table, and no node holds it. The monomorphize pass puts one copy of the
body of an `expand` in the enclosing block for each element of the pack (4.5), so the
copies are one sequence of statements in SHIR. Three rules need the boundary of a copy: the
dead-statement rule, the scope of each copy and the end of an element at a `break`, a
`continue` or a `return`. Lowering is the one writer: for each block that holds copies, it
records the statement range of each copy, in the order of the start, the outer copy first.
A copy local keeps the name that the unroll gives it (`#line_x0`). The dump prints the
table on a `copies` line, by the path of each block, only for a body that has a copy.

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
| a missing entry was an internal error at emit time (CE0129) | completeness is checked when `typecheck` ends |

This is Rust's shape: HIR plus `TypeckResults`, read together by MIR building. Method
resolutions and field indices live inside `TypeckResults` there too.

The side tables are PER `ShirBody`. Each copy gets its own body and its own tables, so the
copy fixpoint (Q2) never collides with itself, and `shir_id` needs to be unique only within
one body.

**The table in the code.** `TypeckResults` is in `semantics/shir/typeck.py`, with one row
kind for each row of the table above. `LoweredProgram` owns it: it makes the empty table of
a body when lowering lists the body, and keeps it beside the body
(`LoweredProgram.typeck_of`). `typecheck` is the one writer (gate:
`tests/unit/test_typeck_results_have_one_writer.py`), and a second answer for one key is an
internal error. A row for a position takes the node and the position as its key: a call
argument and a `print` argument by the index in the arguments, a part of an `Interp` by
the index in the parts (a text part has no row), a capture by the `LocalId` of the captured
local, and a target of a `Destructure` by its path: the index of the target, then the index
in each `Nested` level (`(1, 0)` is `b` in `let (a, (b, c)) = v`). The callee row holds the
`Callee` of 7.6 with the method type arguments and the declaring unit (`ResolvedCall`). A
`Builtin` and a `BuiltinStatic` name their row of `METHOD_TYPE_REGISTRY` by the family and
the method (`BuiltinMethod`). The conversion row of a `Cast` and of a `Try` holds a
`Conversion` callee, or `NoConversion` for a numeric cast and for a `??` that keeps its
error type. The facts of a `Try` are one record (`TryFacts`), and so are the scrutinee type
and the integer type of a `Match` (`MatchFacts`).

The completeness check is `check_answers` in `semantics/shir/typeck_check.py`. It walks
the body through the one walk of the SHIR tree (`semantics/shir/walk.py`). A missing answer
is the internal error [CE0156](../error-catalog.md#ce0156), which names the answer, the
class of the node, its position and the body. Each expression node needs a type, the
reference of a call target too. The captures of a lambda are in `Bindings` and not in the
body, so the check cannot count them.

The body half of `typecheck` walks a SHIR body through ONE table of arms keyed on the SHIR
class, with a row for each member of `ShirStmt`, `ShirExpr` and `ShirPattern`
(`passes/types/visit/__init__.py`, gate: `tests/unit/test_typecheck_table_is_total.py`).
An arm checks its node and writes the answers of the node; the check and the answer are
one arm, so SHIR has no second inference walk. The walk of a template check reads the body
of the template as written (origin `TemplateCheck`), and the header of its check copy
stays with the declaration half (6.4). An expression arm takes the type of its position,
when the position has one, and answers the type of its node; the one dispatch writes that
answer as the type row of the node. A node that has an error has no answer, and a node that
reads it has none either, so the completeness check runs only when a walk ends with no stop
and with no error in its body. A call is the one exception: its callee decides its type, so
a call of a function that the pass finds answers what the function yields also when the
call has an error of its own: a wrong count, an argument that does not fit, a wrong marker,
or a function that is private to another unit (CE3005, where the pass checks no argument).
A later fault of that value (an unhandled `Result`) is a fault of its own. A call of a name
that no function takes (CE2008) or that two imports bring (CE3012) answers nothing. The
display type of a hole of an `Interp` and of the argument of `print` is the type of the
value with no `peek` or `poke`. A `??` in a body with no channel has no answer and no
error of `typecheck`: the `collect` pass refuses it ([CE0131](../error-catalog.md#ce0131)),
so the body holds an error, and the completeness check does not run.

**The channel rule and `??`.** A `Return` of a body with a channel spells
`Result.Ok(...)` or `Result.Err(...)`, and any other value is
[CE2030](../error-catalog.md#ce2030); the constructor is checked at the `Result` of the
channel, so a payload that does not fit is [CE2049](../error-catalog.md#ce2049) at the
payload and [CE2031](../error-catalog.md#ce2031) or [CE2039](../error-catalog.md#ce2039)
at the constructor, as before the switch. A `Return` of a bare body refuses both
constructors ([CE2091](../error-catalog.md#ce2091)). An `EnumInit` of `Result` takes its
instance from the `Result` type of its position (the channel of a `return`, the written
type of a `let`, the type of a parameter); a variant of an enum that is not generic
(`StdError.Error`) is an instance of that enum. A `Try` takes a `Result` only
([CE2507](../error-catalog.md#ce2507)), and answers the payload of `Ok`. Its facts are
the tag of `Ok`, the two payload types and the `Result` of the channel. Its conversion row
is `NoConversion` when the two error types are one type; with no declared conversion
between two error types, the `??` is [CE2511](../error-catalog.md#ce2511).

While the migration is in progress, the arm of a construct that the pass does not
type yet stops the walk of its body with the stop code; the pass then goes on with the
next body, so every unit is lowered and every earlier error is found.

`--dump-shir` prints three answers in place. The resolved callee prints in place of the
`CallTarget` (`Call(Free(@decorate), ...)`; `println` is `Intrinsic("println")`). Each
argument of a `Call` prints as `(value, Convention)` with its resolved convention. The type
of each local is its `LocalId` row. No other answer prints. Where the table holds no answer
for a node, the dump prints the written form, so a body that `typecheck` refused or did
not reach prints as lowering made it.

### 7.4 Expressions

| Node | Fields |
|---|---|
| `Lit` | `value: Constant` |
| `LocalRef` | `local: LocalId` |
| `ConstRef` | `const_id: DeclId`; the value is already computed (ruling Q5) |
| `StorageRef` | `decl: DeclId`; a unit-level `var`, a PLACE with an address (`unit-storage.md`) |
| `GlobalRef` | `decl: DeclId`; a function used as a value, or a C global of an `unsafe external` block (`libc.environ`) |
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
| `Lambda` | `params: [LambdaParam]`, `ret: TypeRef \| None`, `err: TypeRef \| None`, `body: ShirBlock`, `expr_body: bool`; the captures are in `Bindings`. It stays in the tree through `borrow`; `lift` describes it in `LiftResults` (Q8) |
| `StructInit` | `decl: DeclId`, `fields: [Arg]`, `written_order: [int]`, `names: [str] \| None`; `fields` in the declaration order |
| `EnumInit` | `decl: DeclId`, `variant: str`, `payload: [Arg]` |
| `TupleInit` | `parts: [Expr]` |
| `ArrayInit` | `kind: Fixed \| Dynamic`, `runs: [ArrayRun]` |
| `Range` | `start: Expr`, `end: Expr`, `inclusive: bool`, `reverse: bool` |
| `Interp` | `parts: [Str \| Expr]`; kept whole so `typecheck` reports per part |

```
CallTarget = Named(ref: Expr, type_args: [TypeRef])  # a scope-resolved name, or any callable expr
           | Dotted(recv: Expr, name: str)   # x.f(...) -- built-in, extension, perk, adapter
           | Static(ty: TypeRef, name: str)  # Vec.at, List.new, Iterator.new, f64.from_bits
           | BuiltinFn(name: str)            # print, println: no declaration, no receiver

Arg      = (value: Expr, marker: Convention | None, bloom: bool, marker_loc: Span | None)
LambdaParam = (local: LocalId, convention: Convention)
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

The type arguments that a call writes (`identity@(i32)(5)`) stand on the callee:
`Named.type_args`, each a `TypeRef`, empty when the call writes none. A call through a
namespace (`geo.nothing@(i32)()`) is a `Named` of a `GlobalRef` too, so the same field
holds them. Lowering resolves no type argument; the instance that the call names is
`typecheck`'s (S5). The grammar also parses type arguments after a type or a value and
a dot (`Vec.at@(i32)(1)`, `xs.get@(i32)(0)`), but the language refuses both (CE6102), so
`Static` and `Dotted` have no such field, and that form is a hole. Ruled 2026-10-12.
A struct construction takes no type arguments either (`Pair@(i32, i32)(1, 2)`, also behind
an alias), and the language refuses a named argument outside a struct construction
(CE6104). Each of these forms is an ERROR HOLE: the program always has an error from a
later pass, so the hole never stops the compile under `SUSHI_SHIR_STRICT=1`, as the hole of
a name with no declaration does not. Ruled 2026-10-14. Three more forms are error holes by
the same ruling: a slice with a reversed range (`data[(0..3).rev()]`, CE2122), a call of a
name that the namespace does not hold (`geo.twicee(21)`, CE2008), and a member of a type
that is no variant (`Sign.Nope`, CE2045; a static outside a call is one too). A `poke` of a
constant (`bump(poke STEP)`, CE2400) is an error hole too, because a constant lives in
read-only storage: so `borrow` never meets a `poke` of a constant, and one that reaches it
is an internal error. A `break` or a `continue` outside a loop (CE1003, which the `scope`
pass reports) is an error hole too: lowering counts the loop bodies as the `scope` pass
counts them, so `borrow` never meets a jump with no loop, and one that reaches it is an
internal error.

An error hole CARRIES the code of the error that it stands for (CE6102, CE6104, CE2122,
CE2008, CE2045, CE2400, CE1003; for a name with no declaration, the codes CE1001, CE1002 and CE0144). At
the end of the analysis, an error hole whose code no diagnostic reported is an internal
error, CE0155. An error of the construct around the hole can come first, and the hole
accepts its code too: in a constant or a `var` initializer the constant evaluator refuses
the first node that is not constant (CE0108), and the borrow of a slice in a `let peek` is
refused as a borrow of a temporary (CE2404). A new case of this kind is added with its
fixture (`tests/ir/shir/error_holes/`). So an error hole can never hide a fault in a correct program: when lowering
and `typecheck` disagree, the compile stops loudly. Lowering and `typecheck` also read ONE
answer for a name behind a namespace: lowering resolves it (`DeclLookup.member_kind`), and
`typecheck` reads the SHIR node and does not resolve the name a second time. Ruled
2026-10-20.

`marker` is the WRITTEN call-site mode (`nom s`, `poke n`), or `None`, and `marker_loc` is
the span of the written marker (a `peek x` and a `poke x` from the marker), where a
diagnostic of the marker stands. The RESOLVED convention is `typecheck`'s: an unmarked
argument in a consuming position (constructor, container insert, array element) resolves
to `Owned` with no marker. At a call of a declared parameter, the one argument check
(`passes/types/arguments.py`) resolves the convention of each argument from the declared
mode. A `peek x` or a `poke x` argument is a borrow of the type of `x`, so a wrong or a
missing `peek`/`poke` marker is an argument type mismatch, and a `poke` argument for a
`peek` parameter is a safe downgrade that resolves to `InGuaranteed`. A `nom` changes no
type, and [CE2427](../error-catalog.md#ce2427) compares it against the declared parameter
in `typecheck`. The one argument of `print` and `println` is read, so it is `Guaranteed`. A
marked field take in a call argument (`take(nom w.inner)`) is an `Arg` with an `Owned`
marker on a `Field`; in a `let` or a `return` it is `Take`. A struct or a variant
construction is a consuming position, and its arguments are `Arg`s too, so the written
marker of `Result.Ok(nom s)` stays in SHIR. Lowering records the written shape of a
`Take`: that the place is one step off a name that the body owns is a rule of `borrow`. In
the same way, `peek x` and `poke x` are the `marker` of an `Arg` in a call argument, and a
`Borrow` in every other position (a tuple part, an operand). The value of a reference `let`
(`let poke T x = place`) is a `Borrow` too, from the same lowering. A `Borrow` that is not
legal in its position is refused by `typecheck`, not by lowering: its arm answers the
reference type `peek T` or `poke T`, and the position refuses it as any other type that it
does not take (a `let` of a `T`, an operand). An `Interp` hole takes it and prints the `T`.

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

A named construction whose names are not the declared fields (an unknown, a repeated or a
missing name), or whose name has no struct declaration, has an error. Lowering keeps it as
written: `fields` in the written order, `written_order` the identity, and `names` the
written name of each field. `typecheck` reports the fault (CE2080, CE2081, CE2082) from
`names`. In every other construction `names` is `None`. Ruled 2026-10-12.

`T.m(...)` and the bare `T.m` on a type name are an `EnumInit` when the enum declaration of
`T` has the variant `m`; else `T.m(...)` is a `Static` call (#542). A member `ns.m` behind
an alias is a `ConstRef`, a `StorageRef` or a `GlobalRef`, by what the namespace holds.
A C global of an `unsafe external` block is a `GlobalRef` with the written name and its
alias (`libc.environ`), as a C function is (`libc.abs`). A read and a write of it are the
same node; `typecheck` refuses the write (CE5016). A type and a name that the namespace
does not hold stay holes. Ruled 2026-10-12.
`from([...])` and `new()` are an `ArrayInit` of the kind `Dynamic`, never a `Call`.

**A readable value inside a body.** `typecheck` needs the value of some sub-expressions
that the compiler can read: a divisor and a shift count, the operands of an overflow fold,
the count of an array run, a range bound, a constant index. The value depends on the TYPE
of the position: the overflow nest is read at the type of its left operand, a divisor at
the type of the dividend, an index or a count at `i32`. `~255` is 0 as a `u8` and -256 as
an `i64`; a `>>` of an unsigned value and a `u64` above the `i64` maximum also differ. Only
`typecheck` knows that type, so `typecheck` reads the value, and lowering records none.

`typecheck` FOLDS a readable SHIR sub-expression at the type of its position: a `Lit`, a
`ConstRef` (its value from the constant table, Q5, behind an alias too), a `Unary`, a
`Binary`, a `Cast`, an `Index` of a fixed array literal, a `Range` and an `Interp` of such
parts. Any other node makes the sub-expression unreadable: a local, a `var`, a call, a
field read, any other construction, a `Borrow`, a `Take`, a `Try`, a `Slice`, a `Lambda` or
a hole. The fold is not a second evaluator: it has no arithmetic of its own. The integer
operator semantics have ONE home, a kernel of pure functions over an operator, its values
and a type, which the AST constant evaluator and the fold both call. The fold has its own
totality gate over the SHIR expression nodes, and the kernel keeps the gate of the integer
operator semantics. Ruled 2026-10-20; this replaces the ruling of 2026-10-11 that lowering
records the value with the AST evaluator at `i64`.

The fold is `Folder.read(node, type)` in `passes/types/fold.py`, with the table `FOLDS` of
its arms and the set `UNREADABLE` (gate: `tests/unit/test_typeck_fold_is_total.py`). It
answers the values of the constant evaluator, a `ScalarConstant` or an `AggregateConstant`,
or a `RangePlan` for a `Range`. It answers nothing when the sub-expression is not readable
or has no value, for example a division by zero. A literal takes the type of its position,
else its default (`int_literal_default`). An operation that leaves its type has no value:
the fold records it with its node (`Folder.overflows`), so the check of the position
reports it there. The value of a `ConstRef` comes from a reader that the pass gives: the
constant table, through the AST constant evaluator (Q5). The target of a `Cast` comes from
a reader of written types that the pass gives too, so the check of a template reads `T` at
its opaque type (4.3).

**An operator in `typecheck`.** The arm of an operator takes the type of its position, as
every expression arm does, and gives that type to each operand that has the type of the
operation: both operands of `+ - * / % & | ^`, the value of a shift, and the operand of
`-` and `~`. Only a literal and an operator take it. A number literal that gets no type
from its position takes the type of the other operand, which is checked first. A
comparison, `and`, `or`, `xor` and `not` answer a `bool`. Each operand of `and`, `or`,
`xor` and `not` is a condition at the type `bool`, as the condition of an `If` and of a
`Loop` with a `Cond` source is. A negated literal is one value: the arm of the negation
types the literal with its sign, so `-128` is an `i8`, and the dispatch writes the type
row of the literal and of the negation. Each operation that the overflow rule checks
([CE2077](../error-catalog.md#ce2077)) asks the fold for its own value, at its own type and
after its operands, and reports the overflow that the fold records at its own node. The
fold keeps the value of each node for each type, so a nest is folded one time. A node
whose own rule reports an error answers no type, so the node around it reports nothing
more.

**`expect(msg)` keeps its lazy message.** The message is evaluated only when the call
fails, as the message of an `Assert` is. A `Builtin` call with an operand that is
evaluated first cannot keep this. So SHIR-to-SLIR lowering lowers `expect` as it lowers
`Assert` (8.9): `Discriminant`, `SwitchInt`, and the message on the failing edge before the
exit. `expect` is not a row of the `Result`/`Maybe` method family (Q7). Ruled 2026-10-11.

**A lambda body.** The body of a `Lambda` is a block of the enclosing `ShirBody`, lowered
where the lambda stands: its parameters and its locals take ids from the counter of that
body, as the scope pass gives them (7.1), and the body lists them. An expression body
`|x| e` is a block of one `Return(e)`, and `expr_body` is true. The written shape is syntax
that two answers need: the typecheck pass infers the return type from an expression body
and never from a block body, and a lambda with a channel returns the value of an
expression body in `Result.Ok`, where a block body spells both constructors
(`closures.md`, section 2).

A native variadic call is NOT collapsed here. Which parameter is `...T` is a fact about
the callee, and `typecheck` resolves the callee. So the trailing arguments stay a flat list
in SHIR, and SHIR-to-SLIR lowering folds them into one synthesized owned array (today
`build_variadic_array` does this at emit time, inside the dispatcher). A bloom `arr...`
forwards the array operand itself, as a `Move`.

### 7.5 Statements

| Node | Fields |
|---|---|
| `Assign` | `target: Expr`, `value: Expr`, `is_init: bool`; `Let` and `Rebind` collapse here |
| `Destructure` | `targets: [LocalId \| Place \| Discard \| Nested]`, `value: Expr`; `let (a, b) = v`, `Nested(targets)` for `let ((a, b), c) = v`, and `Place(target: Expr)` for each place of a tuple rebind `(a, b) := v` |
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
A tuple rebind `(a, b) := v` is ONE `Destructure` whose targets are `Place`s:
`Destructure([Place(a), Place(b)], v)`. The AST builder splits it into a destructure of `v`
into hidden binders and one rebind per place; lowering joins the split again, so the
hidden binders are no locals of the body. The type of a `Place` target is the type of its
place, as the type of a `let` target is its written type, so a literal on the right side
takes the type of its place. The order is the rule of the source: the right side first,
then each place from left to right, and each rebind frees the old value of its place
(4.6). SHIR-to-SLIR lowering makes the temps of that order, as it makes the temps of the
written order of a struct construction. Ruled 2026-10-20.

**The path predicate.** Where the path through a statement goes is ONE predicate over
SHIR, `semantics/shir/paths.py`, with a rule for each statement class (gate:
`tests/unit/test_shir_paths_are_total.py`). `typecheck` reads it for a statement after
one that ends the path ([CE0140](../error-catalog.md#ce0140)) and for a body that answers
a value or a `Result` and can reach its end with no `return`
([CE0107](../error-catalog.md#ce0107)); `borrow` reads it at a join. A `Return` leaves the
function. With `leaves_round`, a `Break` and a `Continue` leave the round of their loop,
which is the question of a join in a loop body. An `If` ends the path when it has an
`otherwise` and each of its blocks ends it. A `Loop` can run zero times or break, so it
never ends the path, and an `Assert`, an `Expand` and every other statement go on to the
next. A statement after a `Break` or a `Continue` of its block is dead too. Two statements
of two copies of an unrolled `expand` were not written one after the other, so the one does
not make the other dead (`ExpandCopies.holding`, 7.2). A `Match` has no rule yet, because
its answer needs the exhaustiveness of its arms, an answer of `typecheck` (#886). The two
path rules read no type: `typecheck` runs them on the SHIR body after a walk that ends
with no stop, on a template as it is written and not on a copy of it, and it reads every
block of the body.

`Destructure` stays one statement, because its ownership is not that of a field read: it
OWNS from an owned value and BORROWS from a borrow (`tuples.md`). `borrow` decides which,
and SLIR lowering writes the field moves or the field views.

`Assert` keeps its message as an expression, because the message is evaluated only when
the condition fails (`assert.md`). `label` is the file name that a diagnostic of the unit
shows, set by the AST builder: the program prints it with the line and the column of the
`assert` when the condition fails ([RE2026](../error-catalog.md#re2026)).

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
`map_err`, the container methods and the string methods are one variant. The row of a
call in `TypeckResults` carries the declaring unit of an extension beside the `Method`
callee (`ResolvedCall.unit`, 7.3), because two units' private extensions of one name link
side by side (`extension-visibility.md`, C5). The row of a `Free` callee carries the
declaring unit of the function too, and SLIR lowering makes the symbol of the `Direct`
callee from it (8.6).

The emitter reads the answer: one row of `CALL_EMITTERS` (`backend/slir/emit.py`) for
each callee kind, and no decision at emit time.

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
| *SLIR lowering* | SHIR | `ShirBody`, `TypeckResults`, `OwnershipResults`, the declaration (for the symbol and the return type); the verifier reads the function table (8.13) | `SlirFunction` (8.2) |

A template check body runs the same three SHIR passes, at opaque type arguments, and stops
there: it never lowers to SLIR (4.3).

`OwnershipResults` is the second typed side table, and it obeys the four rules of 7.3:

| Decision | Keyed by |
|---|---|
| the `Ownership` of each consuming use (move, adopt, reject) | `shir_id` |
| the locals that are CONDITIONALLY moved | `LocalId`, one set for the body |
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

**The tables in the code.** `OwnershipResults` is in `semantics/shir/ownership.py`, and
`LiftResults` is in `semantics/shir/lift.py`. `LoweredProgram` owns both, as it owns
`TypeckResults`: when lowering lists a body, it makes the three empty tables of the body
(`BodyTables`) and keeps them beside the body (`ownership_table_of`, `lift_table_of`). The
three tables share their shapes (`semantics/shir/side_table.py`): a second answer for one
key is an internal error, a copy of a table is refused, and a completeness check stops at
its first gap. `borrow` is the one writer of `OwnershipResults` (gate:
`tests/unit/test_ownership_results_have_one_writer.py`), and `lift` is the one writer of
`LiftResults` (gate: `tests/unit/test_lift_results_have_one_writer.py`). The gates of the
three tables use one scan (`tests/unit/side_table_scan.py`).

The rows of `OwnershipResults`:

- The `Ownership` of a consuming use is the enum of `semantics/ownership.py`: `MOVE`,
  `ADOPT` or `REJECT`, the answer of `classify()`. SLIR lowering makes the operand from
  the row and the shape of the operand alone: `MOVE` gives `Move`; `ADOPT` on a place
  gives `Copy`; `ADOPT` on a fresh value gives `Move` of its temporary. By the table of
  `classify()`, a place that gets `ADOPT` holds a PLAIN value, or a `string` that is bound
  directly from a literal: such a string owns no heap and copies freely. Its local keeps
  its drop at each exit, and the emitter guards the drop of a string with the `owned`
  byte of the string, so that drop frees nothing. So the verifier refuses a `Copy` of an
  owning value into an `Owned` argument for every type except `string` (8.13). `REJECT` is
  an error of `borrow`, except at two positions, where it is a borrowed view with no
  error: the value of an `Assign` with `is_init` (a let-borrow of the owner) and the
  operand of a `Try` that reads through a borrowed owning wrapper. SLIR lowering does not
  lower a borrowed view yet: a `REJECT` row stops the body with `slir, borrowed view`
  (8.2), and the operand that a borrowed view gets waits for a ruling. A `REJECT` row at
  any other position of a body that `borrow` ended with no error is the internal error
  CE0157 of the completeness check. `ADOPT` never gives `Copy` of a fresh value: a fresh owning
  temporary would then be dropped twice. (Ruled provisionally 2026-10-21, for a
  confirmation before SHIR-to-SLIR lowering.) The key is the `shir_id`
  of the consumed node. A capture has no node in the body, so its
  row has the key of the capture type in 7.3: the `shir_id` of the `Lambda` and the
  `LocalId` of the captured local.
- A conditionally moved local is a row by its `LocalId`. A local with no row is moved on
  no path, or moved on every path, at each exit of its scope and at each rebind of it.
  SLIR lowering reads no row to tell the two apart: it tracks the state of each owning
  local along its walk, from the `move` rows of the consuming uses, and on the straight
  line that state is exact (8.10, rule 3). A local gets a drop flag only where its state
  differs between the paths that meet at a join, and such a local must have a row (8.10,
  rule 4).
- LENT or OWNED (a bool, true for LENT) and the heap decision of a lent iterator (a bool)
  are rows by the `shir_id` of the producer call or the lazy-adapter call.
- The walk in place is a bool by the `shir_id` of the `Loop`.

`LiftResults` holds the symbol (a string) and the environment layout (`Capture(local,
moves)` for each slot, in slot order), each by the `shir_id` of the `Lambda`.

**What a consuming use is.** A consuming use is a value in a position that takes it over
(`ConsumingUse`, `semantics/ownership.py`). SHIR shows the position, and the rule reads the
node and the answers of `typecheck` (`consuming_uses`, `semantics/shir/ownership_check.py`):

- the value of an `Assign` (a `let`, a rebind, a write of a field or an element) and the
  value of a `Destructure`;
- the value of a `Return`;
- an argument of a `Call` whose convention is `Owned`, and a bloom argument;
- each field of a `StructInit`, each payload of an `EnumInit`, each part of a
  `TupleInit`, and the value of each `Single` run of an `ArrayInit` (a repeated value is a
  borrow, and each slot takes its own copy);
- the scrutinee of a `Match` that consumes (`match nom r:`);
- the operand of a `Try`, and the operand of every `Cast`: a conversion takes it as
  `nom self`, and an identity cast moves it (a plain operand gets `ADOPT`);
- the iterable of a `Loop` over an iterator value or a `next()` type that does not walk
  in place;
- each capture of a `Lambda`, from its environment layout in `LiftResults`.

The receiver of a method call is not on the list yet: `TypeckResults` has no convention row
for a receiver. The first group with method calls (A1, the built-in methods on plain
values) adds that row, and a `nom self` receiver joins the rule.

**The order of the three passes.** Each SHIR pass gives the next one each body that it
finished with no error, with the tables that `typecheck` read the body in: `typecheck`
gives it to `lift`, and `lift` gives it to `borrow`. An error in the header of a function
or a method (the type of a parameter, the return type, the channel) is an error of its
body for `typecheck`, so such a body does not go to `lift` either: a parameter with an
unknown type would reach SLIR as a local with no type. In the same way, `typecheck` checks
the path faults of a function or a method (CE0140 and CE0107) before it gives the body to
`lift`, so a body with one of them meets no later stage. A stop of a pass ends the walk of
its body, and the next pass does not get the body. The body of a template check is read
in the overlay of its check, and an error of `borrow` there refuses the template as an
error of `typecheck` does.

**`lift` on SHIR.** `LiftPass` (`passes/lift.py`) walks the body once (`walk.py`). Until the
closures group, a `Lambda` stops the walk of its body with the stop code (`lift, Lambda`),
and the pass writes no row: a body with a lambda never reaches `borrow`. Today
`typecheck` stops at a `Lambda` first, so no compile meets the stop of `lift`. A body with
no `Lambda` has an empty `LiftResults`, and the dump prints no answer of `lift`. When the
walk ends with no stop, `check_lift` runs, and the body goes to `borrow`, which reads
`LiftResults` for the captures. The AST pass `LambdaLifter` stays in the same module, and
no stage runs it: the closures group takes the symbol and the environment layout from it,
and then deletes it.

**The body half of `borrow`.** The declaration half of `borrow` reads the declaration of the
body (a borrowed return), and the mode of each parameter is its `Convention` (8.3). The
body half walks the body through ONE table of arms keyed on the SHIR class, with a row for
each member of `ShirStmt` and `ShirExpr` (`passes/borrow/arms.py`, gate:
`tests/unit/test_borrow_dispatch_is_total.py`). The lookup of an arm and the stop are the
one dispatch of the SHIR passes (`semantics/shir/dispatch.py`), which `typecheck` reads
too: a class with no row is an internal error at the span of the node, and the arm of a
construct that the pass does not check yet stops the walk of its body with the stop code
(`borrow, <class>`). A statement of a copy of an unrolled `expand` that ends the path,
with more statements after it in its block, stops the walk too (`borrow, Expand`): the
group of the copies decides the statements that no path reaches. The state of the walk
(`BorrowState`, `FlowFacts`) is keyed by `LocalId`, because a hidden local has no source
name, and a diagnostic names a local by the name of its `ShirLocal`. The pass decides the
`Ownership` of each consuming use ONE time, with `classify()`, and one function writes the
row (`consume.decide`, gate: `tests/unit/test_consuming_use_coverage.py`). The convention of
a call argument is the row of `typecheck`, and `borrow` does not derive it again: the
`nom` marker is compared against the declared mode by `typecheck` (CE2427). A `peek x` or a
`poke x` argument borrows its place for the statement with the mode that the source
writes, so a `poke` argument for a `peek` parameter keeps the `InGuaranteed` convention of
the parameter and is still a `poke` borrow of its place (CE2408 for a read-only place). When the walk
of a body ends with no stop and with no error, `check_ownership` runs, and the body goes
to the SLIR lowering (8.2). The pipeline reports the first stop of
`typecheck`, then the first stop of `lift`, then the first stop of `borrow`, then an error
hole, then the first stop of the SLIR lowering; with no stop, the backend emits each body from
its SLIR (8.2).

**Control flow in `borrow`.** The joins read the one path predicate of 7.5. An `If`
checks each block from the state after its condition, and the paths that reach the code
after it JOIN: a block that ends in a `Return`, a `Break` or a `Continue` leaves by its own
exit, and an `If` with no `otherwise` has the path that skips `then`. A `Loop` with a
`Cond` source reads its condition once, at the entry, and checks its body in two rounds.
The first round is a DISCOVERY round: it reports no error, writes no row and records no
exit, and it gives the facts that come round the back edge (the end of the body and each
`Continue`). The second round starts from the entry joined with that back edge, and it
reports and writes each row once. The code after the loop sees that fixed point joined
with each `Break` path of the second round. A `Loop` with an `Items` source is the
iterators group's (C3) and stops the walk. A `While` loop has no iterator, so it has no
walk-in-place row: that row is a fact of a `Protocol` or an `IteratorValue` walk alone.
`walk_place` (`semantics/places.py`) reads a SHIR place through the adapter of its tree,
with the same `Step` and `Walked` as the AST walk of the `scope` pass.

**The conditionally moved locals.** The state of a local holds two facts: moved on SOME
path (the join is a union, and a use after it is CE2405) and moved on EVERY path (the join
is an intersection). An EXIT of a scope is the end of a block that the path reaches, a
`Return`, the error path of a `Try`, and a `Break` or a `Continue` for the blocks of its
loop body. A rebind (an `Assign` with no `is_init`) is an exit of the OLD value of its
local: it drops that value (8.10, rule 6), so it needs to know whether the value is live.
At each exit, `borrow` records each local of the scope that is not moved on every path
there, and at a rebind it records the local when the old value is moved on some paths
and not on others; a local of a `let` is in its scope after its value, so an exit in the
value is no exit of the local. A local that is moved on some path and that an exit sees
not moved on every path is a row of the conditionally moved locals. So a local moved in
each block of an `If` has no row; a local moved in one block, or moved before a `Return`
and not on the path that goes on, or moved after a `Try` whose error path leaves with it,
has a row; a local moved and then rebound has a row only when an exit keeps it, because
that exit sees the new value (moved, rebound and moved again has none; 8.10, rule 3); and
a local moved on some paths before a rebind has a row, although each exit after
it sees the local moved on every path, because the rebind drops the old value only on the
paths that did not move it.

**The completeness checks.** `check_ownership` runs when `borrow` ends a body with no
error. It reads `TypeckResults` and `LiftResults` for the positions, and it needs an
`Ownership` for each consuming use and each capture; LENT or OWNED for each producer over a
container and each lazy adapter, except the iterable of a `Cursor` or a `HashMapCursor`
walk, which makes no value; a heap decision for each lent one; and a walk-in-place answer
for each `Loop` of kind `Protocol` or `IteratorValue`. A `REJECT` row is legal only at
the two positions of a borrowed view above. Two other calls get LENT from
`borrow`, and the check does not ask for them yet, because no spine body holds them: the
clone of a lent iterator (the iterators group, C3) and a borrowed call that answers an
iterator (the `peek` return group, B8). Each group adds its call to the check. The conditionally moved locals have
no check, because no row is an answer. `check_lift` (`semantics/shir/lift_check.py`) runs
when `lift` ends a body with no error, and it needs a symbol and an environment for each
`Lambda`, a lambda in the body of a lambda too. A gap of either check is the internal error
[CE0157](../error-catalog.md#ce0157), which names the pass, the table, the answer, the
class of the node, its position and the body.

**The dump.** `--dump-shir` prints the answers of both tables in place, where a row exists:
the `Ownership` of a consuming use as a word before the node (`move LocalRef(_0)`,
`adopt Lit(2)`, `reject ...`), the name of the answer in lower case (not a SLIR operand,
which is `Copy`, `Move` or `Const`); the conditionally moved locals
on a `cond_moved : [_5]` line after the `locals` line; `iterator: lent` or
`iterator: owned` and `heap: true` or `heap: false` after the arguments of an iterator
call; `in_place: true` or `in_place: false` after the body of a `Loop`; and
`symbol: "<symbol>"` and `env: [(adopt _4, Copy), ...]` after the fields of a `Lambda`,
each capture with its `Ownership` and its mode. With no row, the text does not change: a
body that `borrow` did not reach prints no answer of it, and a body that `borrow` stopped in
prints the rows that the walk wrote before the stop.

### 7.10 AST to SHIR, node by node

| AST | SHIR |
|---|---|
| `Name` | `LocalRef` / `ConstRef` / `StorageRef` / `GlobalRef`, from `Bindings` |
| `IntLit`, `FloatLit`, `BoolLit`, `BlankLit`, `StringLit` | `Lit` |
| `Call`, `MethodCall`, `DotCall` | `Call` with a `CallTarget`, the written type arguments on a `Named` target; a struct name gives `StructInit`, also behind an alias; a variant of an enum gives `EnumInit` |
| `Print`, `PrintLn` | `ExprStmt` of a `Call` with a `BuiltinFn` target |
| `Let`, `Rebind` | `Assign` with `is_init`; a `Let` with targets gives `Destructure`; the split tuple rebind of the AST builder gives one `Destructure` with `Place` targets |
| `If` | `If`; each `elif` is an `If`, the one statement of the `otherwise` block of the arm before it |
| `Return`, `ExprStmt`, `Break`, `Continue` | the node of the same name |
| `Assert` | `Assert` |
| `While` | `Loop` with a `Cond` source |
| `Foreach` | `Loop` with an `Items` source; the walk kind is one of five `typecheck` answers. The `let` of a `??` binder and the destructure of a tuple binder stay the first statement of the body, as the AST builder made them |
| `ArrayLiteral`, `DynamicArrayNew`, `DynamicArrayFrom`, `ArrayElement` | `ArrayInit` with runs: `Fixed` for `[...]`, `Dynamic` for `from([...])` and `new()` |
| `EnumConstructor` | `EnumInit` |
| `TupleLiteral` | `TupleInit` |
| `MemberAccess` | `Field`; `ConstRef` / `StorageRef` / `GlobalRef` through a namespace, a C global of an `unsafe external` block included (`libc.environ`); `EnumInit` for a unit variant (`Sign.Plus`); any other member of a type (`Sign.Nope`, CE2045) is an error hole (7.4) |
| `IndexAccess` | `Index` with the `unchecked` mark of the builder (4.9), or `Slice` when the index is a range; a reversed range (`data[(0..3).rev()]`, CE2122) is an error hole (7.4), because `Slice` has no field for it |
| `CastExpr` | `Cast` |
| `TryExpr` | `Try`, **kept** |
| `InterpolatedString` | `Interp`, **kept**: each text part as written, an empty one too, and each hole as an expression |
| `Expand` | `Expand` in a template check body; nothing in an instance (4.5) |
| `Spread` (bloom `arr...`) | the array operand, forwarded whole: an `Arg` with `bloom`; SLIR lowering makes it a `Move` |
| `Borrow` | `Borrow`; in a call argument, the `marker` of the `Arg` |
| a `nom`-marked field read | `Take`, or an `Owned` marker on the `Arg` |
| `RangeExpr` | `Range`, a run of an `ArrayInit`, or a `Slice` bound |
| `UnaryOp`, `BinaryOp` | `Unary`, `Binary` |
| `Lambda` | `Lambda`; a parameter is a `LambdaParam`, with its name, written type and span on its local. A block body is the body; an expression body is a block of one `Return`, with `expr_body` |
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
  name   : str                       # the declared source name; the printer writes it
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

The printer writes the source name of a function and of a callee (`fn greet`,
`call @decorate`), not the symbol (`test_spine_greet$greet`, `user_main`). The symbol is
for the module and the link step.

A statement that lowering synthesizes (a `Drop`, a flag write, a `StorageDead`, a bounds
check) takes the `Span` of the construct that caused it: the scope exit, the move, the
index. No statement has an empty span (G9).

**Where SLIR lives.** The package is `semantics/slir/`, and it never imports the backend:
the node modules (`ids.py`, `places.py`, `constants.py`, `rvalues.py`, `statements.py`,
`terminators.py`, `function.py`), the lowering (`lower.py`), the drop placement of 8.10
(`drops.py`, the one module that builds a `Drop` or a `DropIfSet`; gate:
`tests/unit/test_slir_drop_placement_is_one.py`), the verifier of 8.13 (`verify.py`) and
the printer of `--dump-slir` (`printer.py`). Every class is a slotted dataclass, and every
statement and terminator has a required `span` (gate:
`tests/unit/test_slir_nodes_are_slotted.py`). The borrow pass gives each body that it
finished, and that is not a template check (Q1), to the SLIR stage of its unit
(`SlirStage`). The stage makes one `SlirFunction` from the body and its three side tables,
and `SlirProgram` keeps the outcome of each body, keyed by the `DeclId` of the body. The
symbol, the unit and the return type of the function are facts of its declaration (6.4).
The unit is the declaring unit. A function that has none (a lifted lambda, the instance of
a template of a binary library) goes into the module of the unit that holds its body.

While the migration is in progress, a construct that the lowering does not handle yet
stops the body with the stop code (`slir, <construct>`). The construct is the name of the
SHIR class, or one of these names: `Initializer` for the body of a constant; `generic
instance`, `conversion`, `perk method` and `method` for the body of a monomorphized generic
instance, of a conversion, of a perk method and of an extension method; `borrowed view`
for a `REJECT` row (7.9), `borrowed rebind` for a rebind of a `Guaranteed` or an
`InGuaranteed` parameter that owns, `borrowed try` for a `Try` over an owning wrapper that
the body only reads (a copy of it would drop its payload twice), `conversion` also for a
`Try` that calls a declared conversion (today `typecheck` stops there first), `library
function` for a free callee whose row names no declaring unit (a function of a binary
library, whose symbol is its `link_symbol`), and `bloom` for a bloom argument. The dump
prints such a function as ONE line in place of its body, `fn <name>: stopped (slir, <construct>)`,
so that a golden file shows the progress of each function. The pipeline reports one stop
after the analysis: a stop of `typecheck`, else of `lift`, else of `borrow`, else an error
hole, else a stop of the lowering. When nothing stopped, the program goes on to the
backend.

**The emitter.** The package is `backend/slir/`, and it follows the backend rule: free
functions, and one state object for each function (`FunctionState`: the codegen, the
storage of each local and the LLVM block of each `BlockId`). The stage entry
(`emit_function`) takes `codegen` first and makes the state; every other function of the
package takes the state first and reads the codegen from it, so one route reaches it. The
declarations of a module still come from the AST. Each body comes from the `SlirFunction`
of the analysis, keyed by the `DeclId` of the body: `codegen_for` hands the SLIR of the
program to the backend (`SlirBodies`), and a body that has no `SlirFunction` there is an
internal error. There is no fallback to the AST body emitter. The emitter defines the LLVM
function that the declaration round made for the symbol. One function derives the symbol
of a body (`function_symbol`, `semantics/unit_symbols.py`): the definition, the callee of
a `Direct` call, the declaration round and the verifier all read it. The body of `main` is
`user_main`, and the C `main` that calls it stays a helper of the backend. Every local is a
slot in the entry block (`entry_alloca`), except an `InGuaranteed` or an `Inout`
parameter, whose local IS the incoming address (8.3); a value parameter is stored into its
slot at the entry, and `return` loads `_0`. A signature that does not agree with the
conventions or with the type of `_0` is an internal error. `StorageLive` and `StorageDead`
emit nothing. An operator calls the value half of its emitter family, which takes LLVM
values (Q7), and a comparison of two strings calls the string relation of that family.
`SwitchInt` with `otherwise: None` goes to a block that holds `unreachable`.

Each family of the backend gets LLVM values and semantic types, never an AST node (Q7);
where a family took an AST node, its value half is cut out. The AST body emitter is
deleted: its statement, expression, call dispatch and ownership layers went (8.12), and
so did the AST entry of each family that has a value half. A family that has no value
half yet keeps its AST argument half, with no caller, until the group of its construct
gives it operands. A `Str` constant comes from the string constant seam and owns
no heap (owned flag 0). `Copy` and `Move` both load the value: a `Move` emits no code of
its own, because lowering placed every drop. `Drop` goes through the one destroy seam
(`emit_value_destructor`): a string frees its buffer only when its owned flag is set, and
an enum goes to the destroy half of its lifecycle handler. `DropIfSet` loads its flag and
drops only while the flag is `true`. An enum is `{i32 tag, [K x i64] data}`: an
`Aggregate` of a variant goes through the enum emitter with one value for each payload,
`Discriminant` reads the tag as an `i32`, and the place `Field(Downcast(p, v), i)` is the
address of payload field `i` at the offset that the one layout authority gives
(`TypeSizing.payload_field_offsets`). The emitter reads the semantic type of the place,
never a layout match, and an `Aggregate` whose payload types do not agree with the variant
is an internal error. A `Builtin` call of an intrinsic goes through one row of
`INTRINSICS` (`intrinsics.py`), which evaluates every operand before the family emits
(11.9): `interp` appends each part to one string builder (a string part as its bytes, any
other part as its string form, `emit_value_fmt`) and answers a fresh owned string, also for
one string part; `print` and `println` of a string, an integer, a float or a `bool` call
the print emitter. The print of a value that prints by its `Display` form, and a
`BuiltinMethod` callee, still stop.

While the migration is in progress, a SLIR shape that the emitter does not handle yet
stops the compile with the stop code (`emit, <class>`, the name of the SLIR class, for
example `emit, Indirect` or `emit, AggStruct`), at the span of its statement or
terminator. The stop
comes before any output: the incremental path builds the module of every unit before it
writes the first object, so no object, no binary and no `.ll` text is written. This order
serves the migration alone: when no shape stops the emitter, the path can again build and
write one unit at a time. A stop of the emitter renders as a stop of the analysis does
(one `except StageStop` in `compile_multi_file`): it is a limit of the compiler, not a bug.

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

An `InGuaranteed` or an `Inout` parameter is by address, and its local is the place of the
caller: lowering reads it and writes it as an ordinary local (`copy _1`, `_1 = ...`), and
the convention tells the emitter that the local is an address. So a rebind through a
`poke` parameter writes `_1` (8.10, rule 6). An argument with one of these conventions is
the operand `copy <place>`, and the emitter passes the address of the place.

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
disappears, and so does CE0129, the internal error that guarded it.

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
SlirCallee = Direct(symbol: str, name: str)              # free fn, method, extern, conversion
           | Builtin(method: BuiltinName, types: [Type])     # an emitter family (Q7)
           | Indirect(Operand)                           # a closure's fn_ptr

BuiltinName = BuiltinMethod                              # a row of METHOD_TYPE_REGISTRY (7.6)
            | Intrinsic(name: str)                       # `println`, `print`, `interp`
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

An `Intrinsic` callee of SHIR (7.6) has no row of `METHOD_TYPE_REGISTRY`, so it lowers to
`Builtin(Intrinsic(name), types)`: `types` holds the type of each operand, and the
printer writes it as `builtin println@(string)`. An interpolation lowers to the intrinsic
`interp` (8.9).

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
SwitchInt(Operand, targets: [(int, BlockId)], otherwise: BlockId | None)
Return
Trap(code: str, args: [Operand])   # run-time traps. Exits. Never unwinds
Unreachable
```

`otherwise` is `None` when no other value can reach the switch, as for the tag of a
`Result`. The printer writes it as `otherwise: unreachable`.

`Trap` carries operands because the registry text is a printf format string and some traps
print values: [RE2020](../error-catalog.md#re2020) prints the index and the size.

### 8.9 SHIR to SLIR, construct by construct

| SHIR | SLIR |
|---|---|
| `If(c, t, e)` | `SwitchInt(c, [(0, else_bb)], otherwise: then_bb)`; with no `else`, the zero edge goes to the join, or to a block of its own that goes to the join when the condition has a local (see below). The blocks are made in the order `then`, `else` or the zero block, join; with no `else` and no condition local, the join is the zero target. With an `else`, the join is made after the two arms, and no join is made when no block reaches it |
| `Binary(and/or)` | **two blocks.** Short-circuit is control flow, never an rvalue. The left operand goes into the place that takes the value; a `SwitchInt` on it goes to the join (`and` on a zero, `or` on any other value) or to the right block, which writes the same place and goes to the join. The temporaries of the right operand die in its block |
| `Try(e)` | evaluate `e` into a temporary; `Discriminant` into a local; `SwitchInt` with `otherwise: None`; the `Ok` block moves the payload (`Downcast` + `Field`) into the place that takes the value; the `Err` block builds the `Err` into `_0`, runs the drops of every scope that it leaves and returns. A `Try` that calls a declared conversion is a stop of the lowering for now (`slir, conversion`) |
| `Match` on an enum | `Discriminant` + `SwitchInt`; one block per arm; payload through `Downcast` + `Field` |
| `Match` on an integer | `SwitchInt` on the value; with a `Range` arm, a chain of compares in source order |
| `Match` on a string, a tuple, or with `Or` arms | a chain of tests in source order, each a `Call` to the value equality and a `SwitchInt` on its bool |
| `Loop`, kind `While` | header block, `SwitchInt`, body, `Goto` header. The blocks are made in the order header, the blocks of the condition (an `and` or an `or` in it), body, the zero block (when the condition has a local, see below), exit |
| `Break`, `Continue` | the drops of each scope of the loop body that it leaves (8.10, rule 2), then `Goto` the exit or the header |
| `Loop`, kind `CountedRange` | counter local, header, compare, body, step, `Goto` header |
| `Loop`, kind `Cursor` | `Len` into a local, then the `CountedRange` shape |
| `Loop`, kind `HashMapCursor` | the one probe walk, over the counted `data[0..count)` range |
| `Loop`, kind `Protocol` | the iterator in a hidden owning local (or an `Inout` place, for a walk in place); each round calls `next()`, `Discriminant`, `SwitchInt` |
| `Loop`, kind `IteratorValue` | the same shape, with an `Indirect` call through `next_fn` |
| an iterator producer or a lazy adapter | an `Aggregate(Iterator, ...)` of the source's three functions and its state; the state is a local when the iterator is lent and stays in the function, and a heap block otherwise (`OwnershipResults`) |
| `Range` as a value | an owned iterator over the range formula |
| `Interp(parts)` | ONE `Builtin(Intrinsic("interp"), types)` call into a fresh owning local, with one `Guaranteed` operand for each part in order (an empty text part gives none). The backend expands it with the string-builder emitters (Q7), so the builder state stays out of SLIR |
| `Lambda` | one `SlirFunction` cut from the body, and an `Aggregate(Closure, [FnRef, env...])` built from `LiftResults` (Q8); each captured operand is `Copy` or `Move` by `OwnershipResults` |
| `ArrayInit(runs)` | `memcpy` for a plain element, one `copy_out` per slot for an owning one |
| `Index` read | bounds compare, `SwitchInt`, `Trap("RE2020")` on the failing edge; no check when `unchecked` |
| `Index` write (`arr[i] := v`) | the same bounds check; the target slot is a consuming position |
| `Slice` | range compare, `SwitchInt`, `Trap("RE2028")` on the failing edge, then `Slice`; never unchecked |
| `Assert` | `SwitchInt` on the condition; the failing edge evaluates the message, then `Trap("RE2026")` |
| `Destructure` | one `Field` place per target: `Move` from an owned value, a view from a borrow. For `Place` targets: the value into a temp first, then each place from left to right, a rebind of each (rule 6 of 8.10) |
| `Take` | `Move` of the field; the other owning fields of the receiver get `Drop`, and `drop()` does not run (`borrow-model.md` §10c) |
| a variadic tail | folded into one synthesized owned array argument (7.4) |
| a `string` argument to an `i8*` callee | an explicit conversion into an owning temp local, dropped by the rules of 8.10. The C-string marshalling becomes visible IR, still one lowering site, and `run()` stays the one exception |

A condition (of an `If` or a `While`) is evaluated in a scope of its own, whose
temporaries die before the `SwitchInt`. A condition that is no local and no constant goes
into a `bool` local: it gets `StorageLive` before its first write, and `StorageDead` at the
top of each block that its `SwitchInt` goes to (the rule of rustc); an exit inside the
condition (the error path of a `Try`) ends it too. So each target of that `SwitchInt` is a
block that only the switch reaches: an `If` with no `else` sends its zero edge to a block
of its own that goes to the join, and a `While` sends its zero edge to a block of its own
that goes to the exit, which a `Break` reaches too. Only the local of a `Discriminant`,
which the terminator of its own block reads, gets no storage marker.

Four SHIR nodes vanish at this boundary: `Try`, `Interp`, `Destructure` and `Take`. They
were kept in SHIR only so that `typecheck` and `borrow` could report against the source
(G7). `Expand` never reaches it.

### 8.10 Drop placement

Seven rules. They encode the two drop regimes (a local moved on every path has no drop; a
local moved on some paths has a drop flag), the rebind order of 4.6, and the loop's own
locals, and nothing else:

1. Each local that is not `_0` or a parameter gets `StorageLive` right before its first
   write, and `StorageDead` at each exit of its scope: a temporary at each exit of its
   statement, and a named local and its flag at each exit of its block, after the drop of
   the local (rule 2). The local of a condition and the local of a `Discriminant` follow
   the rule of 8.9.
2. `Drop(place)` is emitted on every exit path of the owning scope, before `StorageDead`,
   newest declaration first (reverse declaration order). On the path of a `return`, the
   value goes into `_0` FIRST and the drops come after it, because the value can read a
   local that the scope exit frees. A statement is the owning scope of its temporaries: a
   temporary that nothing moves drops at the end of its statement, the newest first, and
   on the path of a `return` its drop comes before the drops of the enclosing scopes.
3. A local that is moved at an exit gets no drop at all. SLIR lowering tracks the state of
   each owning local along its own walk, and the `move` rows of `borrow` are the ONLY
   source of a move: lowering never classifies a use again. A `move` row at a `LocalRef`
   of the local (`move LocalRef(<local>)`) or at a capture of it marks the local moved at
   the statement that consumes the operand (the call or the store), never where the
   operand is built: an exit between the two (the error path of a `Try` in a later
   argument) still drops the local. The same holds for a temporary that is read with
   `Move`, and a move of a projection (`move ((_3 as Ok).0)`) moves its local;
   its declaration and each rebind of it mark it live. At an exit, a live local gets
   `Drop` and a moved local gets nothing; at a rebind, a live old value drops and a moved
   one drops nothing (rule 6). While every path that reaches a point gives a local one
   state, that state decides, and no row is read: on the straight line the state is
   exact, whether `borrow` wrote a `cond_moved` row for the local or not (a local that is
   moved, rebound and kept at an exit has a row, 7.9; a local that is moved, rebound and
   moved again has none). A local whose state differs between the paths that meet at a
   join must have a `cond_moved` row, and rule 4 gives it a flag; after the join its
   state is UNKNOWN. A loop header is a join too: the entry and each path back to the
   header (the end of the body, a `Continue`) meet there. A state that disagrees with the
   rows of `borrow` is an internal error, never a guess: a second move of a moved local, a
   move of a local whose state is unknown, a move of a parameter that the function does
   not own, and a join of two states for a local that has no row.
4. A local marked CONDITIONALLY moved gets a `bool` flag local where its state differs
   between the paths that meet at a join (rule 3); on the straight line it needs none.
   The flag is set `true` at the declaration (set again on each loop iteration and at
   each rebind), set `false` at each move site, and where the state of the local is
   unknown its drop becomes `DropIfSet`, at a scope exit and at the drop of the old value
   of a rebind (rule 6) alike. Where the state is exact, the state decides, as in rule 3:
   a live local gets `Drop`, a moved one nothing. The flag is the local right after its
   local; the flag of a parameter comes after the last parameter, and it is set `true` at
   the entry. The flag is set `true` right after the statement that declares its local,
   after the temporaries of the statement die, and right after the store of a rebind. It
   is set `false` right after the statement that moves its local, before the temporaries
   of the statement die, and before each jump to another block, so that each path into a
   join carries the flag of its own state. At each exit the flag gets `StorageDead` after
   the drop of its local and before the `StorageDead` of the local. The walk finds a local
   that needs a flag only at the join, after its declaration and its moves, so lowering
   records the local and lowers the body again, until a walk finds no new local; a local
   whose state at a loop header differs from the state that a path back brings is
   recorded with the loop, and the next walk starts the header with that state unknown.
   The row is the answer of `borrow` at the exits of the scope of the local and at each
   rebind of it (7.9). A local with no row is moved on no path, or each exit and each
   rebind see it moved on every path, and rule 3 applies to it.
5. A borrow never drops. `Guaranteed`, `InGuaranteed` and `Inout` parameters never drop;
   an `Owned` parameter drops in the callee.
6. An `Assign` to an owning place that may hold a live value drops the OLD value, and the
   order is normative (4.6): compute the new value FIRST, then `Drop` the old, then store.
   The source may alias the value about to be freed. A rebind through a `poke` parameter
   follows the same rule, through the pointer. The state of rule 3 decides whether the old
   value is live: a live one drops and a moved one drops nothing, and the store makes the
   local live. When the old value is live, the new value goes into a temporary first,
   unless it is a constant, and the store moves the temporary (`_2 = ...`, `Drop(_1)`,
   `_1 = move _2`). A new value that is `and` or `or` always goes into a temporary first,
   because its left operand is written before its right operand reads the old value. A
   rebind of a `Guaranteed` or an `InGuaranteed` parameter that owns is a stop of the
   lowering (`slir, borrowed rebind`) until a group rules what the callee owns after it.
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
  params : [(_0 "name": string, Owned), (_1 "loud": bool, Guaranteed)]
  locals : [_2 "msg": string]
  block  :
    Assign(LocalRef(_2), Try(Call(Free(@decorate), [(LocalRef(_0), Owned)])), is_init: true)
    If(LocalRef(_1), then: [ExprStmt(Call(Free(@shout), [(LocalRef(_2), Owned)]))])
    Return(EnumInit(@Result, "Ok", [Lit(Blank)]))
```

This is the `--dump-shir` text: one line for each statement, and a declaration keeps its
`@`. SHIR counts its locals from `_0`. SLIR gives `_0` to the return slot and gives ids to
its temporaries, so the ids of the two levels differ (`msg` is `_2` in SHIR and `_5` in
SLIR).

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
    _0 = Result::Ok(const ~)
    DropIfSet(_5, _6)
    StorageDead(_6)
    StorageDead(_5)
    return
}
```

The comments are notes for this example. The printer writes one comment only: the source
name of a local (`// msg`). In `bb4` the `return` writes `_0` first, and then the drops of
the scope exit run (8.10, rule 2).

Read `bb3` and `bb4` together. The whole of 1.4, the LLVM-keyed move set, the name-string
drop-flag lookup, `arm_if_conditional` and `emit_free_unless_moved`, is those three lines.
That is the design in one screen.

### 8.12 What SLIR removes from the backend

| Backend concern before the migration | In SLIR |
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

The backend has none of the concerns of the left column: each one went with the AST body
emitter.

What is NOT in this table: the 2,809 lines of monomorphization. Ruling Q1 leaves them
where they are. Also not in the table: the per-type emitter families. Ruling Q7 keeps them.

### 8.13 The verifier

Runs after lowering, before the backend. It checks:

- every local is typed, and every `Place` type-checks against it
- every `Downcast` names a real variant of the place's enum
- no use of a place after a `Move` of it, on any path
- no read and no `Move` of a local that holds no value on a path: before its first write,
  or after its `StorageLive` or its `StorageDead`
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
- `_0` holds a value on every path to a `return`
- every `BeginBorrow` has exactly one matching `EndBorrow` on every path
- every argument `Convention` matches the callee's declared parameter, with one
  carve-out: the tail of a `var_arg` extern has no declared parameters, so the rule
  applies to the declared prefix only
- no `Copy` of an owning value into an `Owned` argument, except a `string` (7.9)
- no `Binary` carries `and` or `or` (8.9)
- every `Trap` code is a registered RExxxx, with the operand count its format string needs
- every statement and terminator carries a `Span` (G9)

**As built.** The verifier is `semantics/slir/verify.py`. The SLIR stage gives it each
function that the lowering finished, on every compile, before the function meets the next
stage. A failed rule is the internal error CE0158, at the span of the statement, with the
name of the function and of the rule. The structure comes first, statement by statement;
then a dataflow over the CFG gives each local, at each point, the set of states that the
paths to the point give it (no value, live, moved, dropped), with the value of its drop
flag, and a join takes the union. The rule of the drops reads: on each path from the write
of an owning local (the entry, for an `Owned` parameter) to the end of its storage, to a
write over it, and to a `return`, the value is moved or dropped exactly one time. A
`DropIfSet` is the drop of the paths where its flag is true, and the paths where its flag
is false must have moved the local; so a flagged local can reach a plain `Drop` where its
state is exact, and a `DropIfSet` where it is unknown (8.10, rule 4). A borrowed parameter
never drops and never moves, and an `Inout` parameter holds a value again at the `return`.
A `Trap` ends the program and needs no drop. A read or a `Move` of a local in the state
"no value" is refused, and so is a `return` on a path where `_0` holds no value. The
declared parameters of a `Direct` callee come from the function table (`funcs.by_unit`),
which the verifier reads at each call, so a generic instance that a later body declares is
found too. A `Direct` callee with no row there is CE0158. Today only a free function of the
build has a row: the group that first emits another `Direct` callee (an extern, a method, a
conversion, a function of a library) adds its row, with `var_arg` for a variadic extern.
The comparison of the declared prefix alone is in the code already, for that day. The
verifier does not check a shape that no lowering makes yet, and it refuses such a shape
with the internal error CE0000 (the growth rule): a `BeginBorrow` or an `EndBorrow`, a
projection of a `Global` (a `Global` has no type in SLIR), and a `Drop` or a `DropIfSet`
of a place that is no local. The negative case of each rule is a pytest test over a
hand-built function (`tests/unit/test_slir_verifier.py`), because a correct lowering makes
none.

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
The point is in `_check_copies`: after the `scope` run of each copy and before its
`typecheck` run, with the passes of the home unit, as the per-unit loop lowers a unit
after `scope`. A copy is a body of its home unit, and a unit lists its copies in the order
of their names, which hold the type arguments, never in the order of the fixpoint.

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
never needs a SHIR reader, and no second evaluator is built. The fold of a readable value
inside a body (7.4) reads SHIR, but it is no evaluator: it reads a constant from the table
and calls the one integer operator kernel that the evaluator calls.

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
