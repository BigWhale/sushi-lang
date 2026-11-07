# The Sushi IR: SHIR and SLIR

**Status: ACCEPTED, and BUILT in part.** The design was measured against the tree on
2026-10-11, at `cc4088ca`, and ruled on the same day. Every design question has a ruling.
SHIR and SLIR exist on the branch `feat/ir-shir-slir`. Phases 0 to 4 of the migration are
done: the spine, Tier A (plain values) and Tier B (ownership), closed at Gate 4. Phase 5
(Tier C: generics, closures, iterators) is next. A construct that a stage does not handle
yet stops the compile with CE0155. The numbers in sections 1 to 5 describe the tree at
`cc4088ca`. This document is the DESIGN: what the two IRs are, why they have this shape, and what
a migration must keep. The phases, the risks and the progress tracking are not part of this
document; a migration plan comes from it. Nothing in the language changes: every behaviour
test passes before and after, unchanged. There is ONE exception: the failure text of
`expect` is `Runtime Error RE2029: expect failed at <file:line:col>: <msg>`, with exit code 1
(8.8, 11).

Amended on 2026-10-11 by the rulings on the migration plan: 6.3 (initializer bodies), 6.4
(the gate reads the body modules), 7.4 (evaluation order, recorded values, `expect`), 10.3
and 11.9. Amended on 2026-10-20 by the rulings at Gate 1: 7.4 (`typecheck` folds a readable
value at the type of its position; an error hole carries its code), 7.5 and 8.9 (a tuple
rebind is one `Destructure` with `Place` targets). Amended on 2026-10-26 at the start of Phase 3:
the status above, 8.12 (the concerns that are not built yet), and 10.2 and 10.3 (the counts and
the gate names). Amended on 2026-10-28 by ruling R-W: 7.3 and 8.5 (`typecheck` writes the value
that an integer literal holds, and the value of a constant, as a row of `TypeckResults`).
Amended on 2026-10-28 by group A8: 7.3, 7.4 and 7.9 (`borrow` walks an initializer body, and
refuses a `poke` of a constant with CE2400; a `poke` of a constant is no error hole), and 8.2
and 8.5 (SLIR lowers an initializer body and a `ConstRef` from the value rows of `typecheck`;
the next amendment takes back the initializer body).
Amended on 2026-10-28 by group A8 (emit): 7.9, 8.2 and 8.5 (an initializer body does not go
to SLIR; the global of a constant or a `var` takes the value of the constant evaluator, in
the declaration round of the backend, and that is the one path).
Amended on 2026-10-28 by ruling R-U (group A11): 8.8, 8.9 and 8.13 (a `Trap` gives one
operand of a fixed type to each printf conversion of its registry text; the trap of an
`assert` carries its position and its message as two `string` operands).
Amended on 2026-10-28 by group A3: 7.8 (an `IntRange` keeps the span of each bound, where a
diagnostic of that bound points), and 7.5 (the path rule of a `Match`). Amended on 2026-10-28
by rulings R17 and R18 (group A3): 7.3 (a pattern has its own table of arms, and its dispatch
writes no type row) and 7.8 (a `Literal` and each bound of an `IntRange` keep the source
text, which a diagnostic quotes). Amended on 2026-10-28 by group A3 (borrow): 7.9 (the
arms of a `Match` join as the blocks of an `If` do, and its scrutinee is a read, or a
consuming use with `consumes`). Amended on 2026-10-28 by group A3 (SLIR): 8.6 (the
intrinsic `eq`, the value equality of a test of a match), 8.8 (the trap RE2023 after the last
arm of a match carries the source text of that arm as a `string` operand), 8.9 (the lowering
of a `Match` on an integer or a string; the `bool` local of a test gets no storage marker)
and 8.13 (the rule of a switch on an integer). Amended on 2026-10-28 by group A4: 7.3 (a
`Maybe` or a `Result` variant in a comparison takes the type of the other operand) and 7.8 (a
`Variant` keeps the spans of its enum name and of its variant name; the typecheck rule of a
match on an enum or a tuple, and the type row of a binder), and 7.3 again (the arms of the
`result` and `maybe` method families). Amended on 2026-10-29 by ruling R-V (group A4,
borrow): 8.3 (the receiver of a built-in method: each family is in one set of a total
partition, and the `result` and `maybe` families give a per-method answer), 7.9 and 8.6
(the `borrow` pass and SLIR lowering read that answer), and 7.9 again (the bare binders of
an enum match in `borrow`). Amended on 2026-10-29 by group A4 (SLIR): 8.5 (a constant
`Aggregate` of an enum names its variant), 8.6 (`expect` is no call), 8.8 (the trap RE2029 of
`expect`), 8.9 (the lowering of a `Match` on an enum: one switch on the tag, or a chain of
tests in source order, and the binders of each path; the lowering of `expect`), and 8.13 (the
rules of an enum value and of a switch on a tag). Amended on 2026-10-29 by group A4 (emit):
8.5 (the backend turns a constant `Aggregate` of a variant into an `ir.Constant` through the
one converter that the global of a `const` uses too) and the emitter paragraph of 8.2 (one
check of a variant against the semantic types of its payload, for the rvalue and for the
constant). Amended on 2026-10-29 by group A4 (emit): the emitter paragraph of 8.2 (the
`result` and `maybe` families have a row of `METHOD_FAMILIES`; every operand of such a call is
`Guaranteed`, so the family copies an owning value that it answers).
Amended on 2026-10-29 by the final review of run 2 of Phase 3: 4.2 (the trap emitter of
SLIR), 6.3 (`borrow` checks an initializer body too), 7.3 (`typecheck` writes no value row
for an initializer), 7.5 and 8.8 (an `Assert` holds no file name; the position of a trap
names the file of the body), 7.9 (an initializer body is the one place where an owning value
gets `ADOPT`), and 8.2, 8.5 and 8.13 (the declaration round of a global stops the compile
where it cannot make a global, and never drops one; the one check of a variant runs for each
constant `Aggregate`, on both paths; the emit stops `aggregate constant` and `constant with
no value`; a `Trap` takes no `Move` operand). Amended on 2026-11-03 by rulings R-AD and
R-AE: the header, 7.3, 7.8, 8.8, 8.9 and 11 (the failure text of `expect` is `Runtime Error
RE2029: expect failed at <file:line:col>: <msg>` with exit code 1, the one user-visible
change; a pattern value has no `TypeckResults` row). Amended on 2026-11-04 by group A5
(SLIR): 8.2 (the modules of the lowering), 8.5 (a constant `Aggregate` of a struct), 8.9 (the
lowering of a struct and a tuple construction in the written order, of a field read and a
field write, and of a destructure of plain values) and 8.13 (the rule of a struct value, and
the part types of an enum value). Amended on 2026-11-04 by group A5 (the match on a tuple,
and emit): 8.9 (a match on a plain tuple is a chain of tests over the `Field` paths of each
tuple pattern; a tuple that owns stops), 8.2 and 8.5 (the emitter builds a struct and a
tuple from the value of each field and reads a field through a GEP of its index; a struct
and a tuple constant go through the one converter of an aggregate constant, which the
global of a `const` uses too; only an array value stops). Amended on 2026-11-04 by group A6:
7.3 (the typecheck of an array, an index, a slice, an element write and the array method
family; a negated byte literal in an `i32` position), 7.9 (an array in `borrow`), 8.6 (the
array family takes the address of its receiver) and 8.9 (the lowering of an array value, an
index, an element write, a slice and an array method). Amended on 2026-11-04 by ruling R-AS
(the shapes of the ownership tier): 7.9, 8.2, 8.7, 8.9, 8.10 and 8.13 (a borrowed view is a
view local); 7.9, 8.6, 8.12 and 8.13 (the borrow region is a row of `OwnershipResults`);
7.3, 7.9, 8.9, 8.10 and 8.13 (a `peek` return copies its value into `_0`, and the caller
holds a view local); 8.2, 8.3, 8.4, 8.6, 8.7, 8.9, 8.10 and 8.13 (a reference binding is an
address local that a `Ref` writes one time); 8.2, 8.3, 8.6, 8.7 and 8.9 (`Clone` is the one
deep copy, and `realise` and `err` lower as `expect` does); 8.3 and 8.13 (the rows of
`METHOD_EFFECTS` give the receiver and the argument conventions of each container method);
7.9 and 8.9 (a container get-out answers a view, by a row of `answers_view`); 7.9, 8.2,
8.3, 8.10 and 8.13 (a `Guaranteed` parameter starts not owned, and a rebind makes it owned);
8.2 (the body of a `Drop` implementation is a method body, and the destroy seam calls it);
7.9, 8.3 and 8.9 (`or_err` and `??` over a borrowed `Maybe` lower as one named shape).
Amended on 2026-11-05 (the owning structs, enums and tuples, and the one deep copy): 7.3
(the arm of the derived `clone`), 7.9 (`.clone()` reads its receiver), 8.2 (a row of the
`result` and the `maybe` family answers a `bool`), 8.9 (a construction moves each owning
part; the read and the write of an owning field; `x.clone()` of a temporary is the
temporary; `realise` and `err` drop a temporary on the path that does not take it) and 8.10
(rule 3: the drop on such a path). Amended on 2026-11-05 by group B7 (the takes): 7.9 (a
marked field take, a `nom` binder with CE2432 and CE2433, a destructure of an owning value,
and the CE2411 of each owning place of a rebind form over a borrowed value), 8.9 (the
lowering of a `Take`, of a destructure of an owning value, and of a `nom` binder), 8.10
(rule 3: the drop of a temporary on an arm that takes nothing) and 8.13 (one take moves
two or more disjoint parts of one root in one block). Amended on 2026-11-05 by group B8.2:
7.9, 8.2, 8.6 and 8.9 (a reference `let` has a region row to its last use, as a binder
of a pattern; a `Ref` of a constant reads a hidden local) and 8.13 (the `Ref` of an
address local comes before each use, and a region is open at each use). Amended on
2026-11-05 by group B8.3 (the `peek` return): 7.9 (a `REJECT` row at the operand of a `??`
over the answer of a call with a `peek` return; `ADOPT` on a view gives `Copy`; the dump
prints the source row), 8.2 and 8.13 (the `_0` of a `peek` return is a view local, written
one time on each path to a `return`). Amended on 2026-11-05 by group B9 (the rebind of an
owning place): 8.3, 8.9 and 8.13 (the concat peephole is the `concat_in_place` row of the
string family, which takes its receiver `Inout`), 8.13 (the `Drop` of the old value at a
rebind through a `poke` address local; a `Guaranteed` parameter that owns holds the value of
the caller until a write). Amended on 2026-11-06 by ruling R-AW (the shapes of generics,
closures and iterators, ruled before they are built): 8.2 and 8.13 (the home, the symbol and
the order of the body of a generic instance; no template check body reaches SLIR); 8.4, 8.7,
8.9 and 8.13 (the closure value, its environment and its glue, and the capture place); 8.7,
8.9, 8.12 and 8.13 (the iterator value `AggIterator(ty, source, heap)`, and a stack-state
iterator never leaves); 8.6, 8.9 and 8.13 (the `HashMapCursor` walk is the `Protocol`
shape with the step `cursor_next` of the `hashmap` family); 7.9 and 8.9 (the capture mode
is the `Ownership` row of the capture alone, and `LiftResults` holds the captured locals in
slot order); 7.6, 8.6 and 8.13 (the `Indirect` call takes its conventions from the
function type, and the emitter passes the environment first); 7.3, 7.6, 8.2, 8.3 and 8.6
(a contract call is an intrinsic over the one contract seam; a perk implementation method
is a `Method` callee, and its body lowers as the body of `drop`); 8.2, 8.9 and 8.13 (a
view local and an address local have one write on each path, so an alternative binds an
owning payload or a reference); 7.3 and 7.9 (the arm of a generic call makes a late request
directly, and the callee row records the instance); 8.6 and 8.13 (a `BeginBorrow` names its view, and the
view-region check reads the view from the statement). Amended on 2026-11-06 by group X2:
7.3, 7.6, 7.9, 8.6, 8.9 and 8.13 (an `Extern` callee carries the link name of its C
function; it lowers to a `Direct` callee with no module, whose row the verifier finds in
the external table; the emitter makes the C copy of a `string` argument at the call and
frees it after the call, so no SLIR local holds it).

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
  `ownership_provenance` onto a node it makes, and `dispatcher.py:322` of
  `backend/expressions/calls/` (deleted, 8.12) writes `resolved_struct_type`.

Every dataclass in `semantics/ast.py` is `slots=True`, every analysis field is declared on
each class that takes it, and `tests/unit/test_ast_nodes_are_slotted.py` keeps it that
way. That CONTAINS the channel: a stray write raises at the site that wrote it. It does
not give a fact an owner, one writer, or a completeness check. Sections 7.3 and 7.9 do.
The declared analysis fields are transitional (6.2): each one goes when the pass that
writes it moves to SHIR and its side table.

### 1.3 The ownership rule ran twice

`backend/ownership.py` called `classify(provenance, type_class)` **while it emitted LLVM
IR**, at three sites: `bind` (`:61`), `consume` (`:88`) and `unwrap` (`:117`). Each site
built the type class there, from the `Drop` set and a type resolver. The rule ran once in
the `borrow` pass to check it, and again at emission to act on it.

`_provenance_of()` (`:152`) read `source.ownership_provenance`. A missing value was
CE0129: fatal, with a deliberate no-fallback rule. Three
more backend sites read the field outside the seam (`statements/returns.py:62`,
`expressions/memory.py:265`, `runtime/closures.py:205`), and `_mark_moved` (`:160`) found
the moved name by a test on the syntax (`Name` or `MemberAccess`).

CE0129 was not a bug. It was the correct response to a
missing note in a design that passed notes. An IR removes the whole category, because the
decision becomes an instruction.

### 1.4 Ownership state was keyed on LLVM objects and on names

```
self._moved: Set['ir.Instruction'] = set()
self._flags: Dict['ir.Instruction', 'ir.AllocaInstr'] = {}
```

`MoveTracker` (`moves.py:13`) keyed move state on `llvmlite` objects.
`arm_if_conditional(name, slot)` armed a drop flag when the **name string** was in
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
and after, unchanged. There is ONE exception: the failure text of `expect` is `Runtime Error
RE2029: expect failed at <file:line:col>: <msg>`, with exit code 1 (8.8, 11).

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

`emit_runtime_error` and `emit_runtime_error_with_values` (`backend/runtime/errors.py`),
and the emitter of a SLIR `Trap` (`backend/slir/emit.py`), write to stderr and call
`exit(1)`. There are no exceptions; an error is a `Result` value, and a trap stops the
program. The backend emits no `invoke` and no `landingpad`.

A large part of Rust MIR's weight comes from unwinding. Every `Call` terminator carries an
unwind successor, cleanup blocks exist for the unwind path, and drop elaboration must be
correct on both paths.

Sushi needs none of it. This is a permanent simplification and it belongs to the language,
not to the implementation. See ruling R3.

### 4.3 A template is checked as one more body, at opaque type arguments

A generic body is checked where it is written (`checked-generics.md`). Each type parameter
is OPAQUE, and an operation on it is legal only when a constraint promises it: `Eq` gives
`==`, `Ord` gives `<`, `Clone` gives `.clone()`, and arithmetic on `T` is refused.

The mechanism is one more BODY. `passes/types/templates.py` makes a header of the template,
with each type parameter replaced by its opaque form and with no body, inside an overlay of
the program tables. `typecheck` checks that header and walks the SHIR body of the template
as written, whose origin is `TemplateCheck`; each type that the body writes is read at the
opaque parameters (`TemplateValidator.written_type`). `lift` and `borrow` read that body in
the overlay. No pass copies or stamps the body of a template. The written template is
then skipped by the ordinary passes.

The real instances are still cut by `monomorphize` and still checked one by one, but each
one reports only the PER-INSTANCE REMAINDER (`checked-generics.md` §8.2): consume or copy
of an owning value, the drop set and the order of `drop()`, the derived methods of the
concrete type, the layout, E3 ([CE2084](../error-catalog.md#ce2084)) and an owning lambda
parameter ([CE2094](../error-catalog.md#ce2094)). Any other error from an instance is
[CE0149](../error-catalog.md#ce0149): a fault that the template check missed.

So the CHECK is per template, and every fact that code generation needs is per instance.
This settles Q1: SHIR is built per body, and the template check is one more body.

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

The body of the template check of 4.3 keeps its `Expand`: `typecheck` checks the body once,
for every pack element. An `Expand` anywhere else is an internal error
([CE0015](../error-catalog.md#ce0015)). So SHIR has an `Expand` node, legal only in a
template check body, and a template check body never lowers to SLIR.

Each `Expand` binds its own opaque element type (`PackElements`), which has the constraints
of the pack. The value pack is no value: a parameter with `pack` has no type row, and a read
of it, also as a bloom, is [CE0144](../error-catalog.md#ce0144) at the name. `borrow` checks
the body of an `Expand` as a loop body, to its fixed point, because it runs zero or more
times, so a move of an outer value in it is a move in a loop. It is no loop of its own: a
`break` and a `continue` in it go to the frames of the enclosing loop. The binder is a borrow
of the value of the caller, so a consuming use of it is
[CE2411](../error-catalog.md#ce2411). In an instance, no path reaches a statement after a copy
that ends every path (`reached_statements`, 7.2).

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
  |  lower (bodies only, per copy: an instance, or a template check body)
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
  rewriter (ruling Q1 keeps it there). The template check writes nothing on the AST: it reads
  the SHIR body of the written template, and makes a header with no body. Every other pass treats the AST as immutable.
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
VALUE of a constant stays the evaluator's (Q5). The body only gives `typecheck` and
`borrow` a SHIR node to check; it does not go to SLIR (8.2). Ruled 2026-10-11. The block of
the body holds one statement: a `Return` of the initializer, because the initializer gives
the value of the body as a `return` gives the value of a function.

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
per copy: an instance, a copy that the copy fixpoint cuts later, or a template check body
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
walks in the per-unit loop, so its `Bindings` exist and the check needs no `scope` run of
its own. Each type parameter stays a type as written in that body, and its
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
  loc        : Span                # of the receiver: the type that the method extends
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
One function answers which statements of a block a path reaches (`reached_statements`,
`semantics/shir/paths.py`): it stops after a statement of a copy that ends every path, also
at a `break` or a `continue`. `borrow`, its completeness check (`walk` with `reached_in`) and SLIR
lowering read it, so they do not read the statements of the later copies; `typecheck` reads
every statement (gate: `tests/unit/test_unrolled_run_end_is_one.py`).
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

**Ruling S5: every answer `typecheck` gives lives in `TypeckResults`.** The one exception is
the value of a pattern (7.8, ruling R-AE). That covers more than types:

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
| the value that an integer literal holds (8.5), and the value of the constant that a `ConstRef` names | the `Lit`'s or the `ConstRef`'s `shir_id` |
| the symbol of the global of the `var` that a `StorageRef` names (`global_symbol`) | the `StorageRef`'s `shir_id` |
| the source positions of a call of a function or a method with a `peek` return: the arguments whose owners the answer borrows (`callee_borrows`) | the call's `shir_id` |

A pattern value is not a row: a `Literal` or an `IntRange` has no row of its own (7.8).

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
`Callee` of 7.6 with its type arguments and the declaring unit (`ResolvedCall`). A
`Builtin` and a `BuiltinStatic` name their row of `METHOD_TYPE_REGISTRY` by the family and
the method (`BuiltinMethod`). The conversion row of a `Cast` and of a `Try` holds a
`Conversion` callee, or `NoConversion` for a numeric cast and for a `??` that keeps its
error type. The facts of a `Try` are one record (`TryFacts`), and so are the scrutinee type
and the integer type of a `Match` (`MatchFacts`). The value row holds a value of the constant
evaluator (`ConstantValue`, `semantics/const_eval.py`), one shape for the two keys: the value
that an integer literal holds at its type, and the value of the constant that a `ConstRef`
names (from the constant table, Q5). A `ConstRef` of a constant whose initializer has an
error has no value row: the walk goes on (`refused_before`, 7.9). With no error in the
program, a constant with no value stops (`ConstRef with no value`). An initializer has no
value row: no stage reads one,
because the backend makes the global of a constant from the evaluator (8.2). The global row
of a `StorageRef` holds the symbol of the global of its `var`, from the one derivation of a
global symbol (`global_symbol`, `semantics/unit_symbols.py`): SLIR lowering names the
`Global` place with it, and the backend names the global with it. The source row of a call
of a function or a method with a `peek` return (`borrow-model.md` S10h) holds the position
of each argument in the source set of the declaration, and the receiver as the position
`-1` (`RECEIVER_POSITION`, `semantics/borrowed_return.py`). It is an answer of `typecheck`,
so it is a row and not the AST stamp `callee_borrows` (S5). `borrow` reads it: the answer of
the call is a view of the owner of each source argument (7.9).

The completeness check is `check_answers` in `semantics/shir/typeck_check.py`. It walks the
body through the one walk of the SHIR tree (`semantics/shir/walk.py`). A missing answer is
the internal error [CE0156](../error-catalog.md#ce0156), which names the answer, the class
of the node, its position and the body. Each expression node needs a type, the reference of
a call target too. An integer literal and a `ConstRef` need a value too. A `ConstRef` of a
constant that has no value stops the walk of its body with `typecheck, ConstRef with no
value`, and an initializer with no value that is no empty container stops with `typecheck,
Return with no value`: a function-typed constant (`const fn() -> i32 F = area`) has no value
and no error yet. When the initializer has an error, that error is the diagnostic, and the
stop does not show. A `var` whose initializer is an empty container has no value, and it
does not stop: its global is zero. The captures of a lambda are in `Bindings` and not in the
body, so the check cannot count them.

The body half of `typecheck` walks a SHIR body through ONE table of arms keyed on the SHIR
class, with a row for each member of `ShirStmt` and `ShirExpr` (`ARMS`), and a pattern
through its own table, with a row for each member of `ShirPattern` (`PATTERN_ARMS`, ruling
R17), both in `passes/types/visit/__init__.py` (gate:
`tests/unit/test_typecheck_table_is_total.py`). A pattern arm takes the type of its
position and answers the place of the pattern in the matrix of the one checker; its
dispatch (`check_pattern`) writes no row, because a pattern has no type.
An arm checks its node and writes the answers of the node; the check and the answer are
one arm, so SHIR has no second inference walk. The walk of a template check reads the body
of the template as written (origin `TemplateCheck`), and the header of the check stays with
the declaration half (6.4). An expression arm takes the type of its position,
when the position has one, and answers the type of its node; the one dispatch writes that
answer as the type row of the node. A node that has an error has no answer, and a node that
reads it has none either, so the completeness check runs only when a walk ends with no stop
and with no error in its body. A call is the one exception: its callee decides its type, so
a call of a function that the pass finds answers what the function yields also when the
call has an error of its own: a wrong count, an argument that does not fit, a wrong marker,
or a function that is private to another unit (CE3005, where the pass checks no argument).
A later fault of that value (an unhandled `Result`) is a fault of its own. Three operators
answer after an error of their own too, because the error does not change the type of the
value: a `??` of a `Result` answers its success value after CE0131 and CE2511, and a bitwise
operator answers the type of its left operand after CE2004, CE2510 and CE2512. So `let string
s = f()??` in a body with no channel is CE0131 and CE2002, as on main. An arithmetic operand
whose type names no type (CE2001) is no second fault (CE2518). A call of a name
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
constructors ([CE2091](../error-catalog.md#ce2091)). An `EnumInit` of `Result` or of
`Maybe` takes its instance from the `Result` or the `Maybe` type of its position (the
channel of a `return`, the written type of a `let`, the type of a parameter, and the type of
the other operand of a comparison, `m == Maybe.None`); a variant of
an enum that is not generic (`StdError.Error`) is an instance of that enum, also behind an
alias (`geo.Sign.Plus`): the enum is the one that the namespace holds
(`NamespaceTable.lookup`). A variant of a private enum of another unit is
[CE3005](../error-catalog.md#ce3005). A `Static` call on an enum whose dot holds no
variant of the name, where no unit declares a method of the name, is
[CE2045](../error-catalog.md#ce2045); the diagnostic names the instance that the position
gives to a generic enum. A `Try` takes a `Result` only
([CE2507](../error-catalog.md#ce2507)), and answers the payload of `Ok`. Its facts are
the tag of `Ok`, the two payload types and the `Result` of the channel. Its conversion row
is `NoConversion` when the two error types are one type; with no declared conversion
between two error types, the `??` is [CE2511](../error-catalog.md#ce2511).

**A struct construction.** A `StructInit` of a struct that is not generic is an instance
of that struct, also behind an alias: the struct is the one that the namespace holds. Each
field goes through the one argument check (`arguments.check_arguments`) at the type of its
field, in the declaration order. A struct is never variadic: a bloom argument (`xs...`)
is [CE0120](../error-catalog.md#ce0120) at the argument, and the fields are then not checked,
as on a method call. Two parts of the old output stay different: the caret covers the value
(`xs`) and not `xs...`, as on a function call, because an `Arg` keeps no span of the spread;
and the refused construction has no instance, so it is unknown to `borrow` (7.9): `borrow`
reads `xs` and does not take it, and the CE2405 that the old pipeline adds at a later use of
`xs` does not come. A positional construction with a wrong count is
[CE2027](../error-catalog.md#ce2027) at `decl_loc`, and a field that does not fit is
CE2028, or CE2083 in a named construction, at the field. A named construction with
`names` is [CE2080](../error-catalog.md#ce2080) (a name that the struct does not
declare), else [CE2081](../error-catalog.md#ce2081) (a repeated name), else
[CE2082](../error-catalog.md#ce2082) (a missing field), at `decl_loc`, and each field is
then checked with no type of its position. A private struct of another unit behind an
alias is [CE3005](../error-catalog.md#ce3005) at `decl_loc`, and its fields are not
checked; a named one is then CE6104 at the construction, as the old pipeline reports it. A
`StructInit` of a type name that is no struct (`Sign(1)`) is
[CE2008](../error-catalog.md#ce2008) at `decl_loc`, and a named one (`Sign(x: 1)`) is
[CE6104](../error-catalog.md#ce6104) at the construction too; a name whose function the
`collect` pass refused ([CE1005](../error-catalog.md#ce1005)) gives no second error, and
the body holds that error. A construction gives its fields no
convention: a field takes its value by position (7.9). A node that has an error has no
answer. A struct whose name the unit lost is not typed yet, and stops the walk of its body.

**A construction of a generic type** (`visit/constructions.py`). A `StructInit`, an `EnumInit`
and a static of a generic type take the instance from the position when the position holds an
instance of the type. Else the arguments solve the type parameters through the one leading
solver (`solve_leading_type_args`), and each argument is checked one time. An argument that
takes its type from its position (a `Maybe.None`, an empty `from([])`) waits for the other
arguments. A construction that nothing solves is [CE2112](../error-catalog.md#ce2112), or
[CE2065](../error-catalog.md#ce2065) when two arguments disagree. The instance is looked up or
interned, never rebuilt (`intern_constructed_instance`), and it is the instance row of the node.

**A field read.** A `Field` checks its base at no type of its position. The type of the
base with no `peek` or `poke` decides the field. A field of a struct, and an element of a
tuple (`t.0`), answers the type that the struct declares for it, and its index is the
field index row. A field of a constant is a field read too, also behind an alias
(`geo.ORIGIN.y`, ruling R-J). A name that the type does not declare is
[CE2106](../error-catalog.md#ce2106) at `member_loc`. A struct, a tuple, an enum (a
`Maybe` and a `Result` too), an array, a primitive, a `string`, a function type, a `ptr`
and an opaque type parameter can have this fault. A note says when the name is a method of
the type, and a help names the field that is near, or the fields of the type. A base of a
type whose name the unit lost, and a base of a type that has no rule here yet, stop
the walk with `typecheck, Field`.

**A tuple.** A `TupleInit` checks each part at the element type of its position, when the
type of its position is a tuple with as many elements, else at no type. It answers the
tuple of the part types with no `peek` or `poke` (`intern_tuple`), and that tuple is its
instance row too. A tuple that does not fit its position is a fault of the position, which
compares the whole tuple: `let (string, i32) t = (1, 1)` is CE2002 at `(1, 1)`. A `Borrow`
in a tuple part is CE2002 at the part (ruling R-I). A tuple holds values, so a part does
not take a `peek T` or a `poke T`: `(peek s, 1)` is "cannot assign peek string to
string", also where the position gives no type. A tuple with an error in a part has no
answer.

**A field write.** An `Assign` whose target is a `Field` is a field write. The target is
checked as a field read, and the value at the type of the field: a value that does not
fit is CE2002 at the statement. A write into a field of a constant, at any depth, is
[CE2096](../error-catalog.md#ce2096) at the statement, and the value is then checked at no
type. The rules of a write through a borrow and of a read-only place are rules of
`borrow`. An `Assign` of an `Index` is an element write (see "An array" below), and an
`Assign` of a `StorageRef` is a rebind of a `var` (see "A `var`" below). An `Assign` of any
other place that is not a `LocalRef`, a `Field` or an `Index` is not typed yet.

**A destructure.** A `Destructure` first finds what each target wants (ruling R-D). A
binder with a written type wants that type, and the written type is checked as the type of
a `let` is checked ([CE2001](../error-catalog.md#ce2001)). A `Place` wants the type of its
place. The place is a `LocalRef`, a `StorageRef` or a `Field`, and it is checked as the
target of a rebind or of a field write, and an `Index` as an element write: a field or an element of a constant
is CE2096 at the place. A `Place` of any other place is not typed yet. A bare binder and a
`_` want no type, and a `Nested` target wants
what its targets want. Then the value is checked. A `TupleInit` with one part for each
target checks each part at what its target wants, also at a nested level, so a literal takes
the type of its binder or of its place: `let (u8 a, b) = (300, 1)` is
[CE2073](../error-catalog.md#ce2073), and `(small, big) := (200, 5000000000)` types each
literal as `small := 200` does. Any other value is checked at no type. A `Result` value is
[CE2505](../error-catalog.md#ce2505) at the value. Then each target takes its element, and
the path of the target keys its element type row. The value has one element for each
target, else it is [CE2117](../error-catalog.md#ce2117) (no tuple) or
[CE2120](../error-catalog.md#ce2120) (the count), at the value for the top level and at
`Nested.loc` for a nested level. A bare binder takes the type of its element. A binder with
a written type takes that type, and a place keeps its own type: an element that does not fit
is CE2002 at the written type or at the place, with no note, and the binder is refused as
the local of a `let` is refused. After an error of the value or of a level, a bare binder of
that level has no type, so a later use of it adds no diagnostic. Whether a target owns or
borrows its element is a rule of `borrow`.

**An array.** The arm of an `ArrayInit` (`passes/types/visit/arrays.py`) gives the element
type of its position (the `T` of a `T[N]` or a `T[]`, also under `peek` or `poke`) to each
run. The value of a `Single` and of a `Repeat` is checked at that type, a range run is
checked by the arm of a range, and a count is an `i32` position. ONE reader of the runs
(`plan_runs`, `semantics/array_runs.py`) serves the arm and the AST constant evaluator: it
reads each count and each range with the fold, and it reports
[CE2017](../error-catalog.md#ce2017), [CE2019](../error-catalog.md#ce2019),
[CE2020](../error-catalog.md#ce2020) and [CE2125](../error-catalog.md#ce2125). An element of
another type than the first is [CE2013](../error-catalog.md#ce2013). A fixed literal answers
`T[N]`, also after CE2013, so its position adds its own error as on `main`. A dynamic
literal answers `T[]` only when its elements agree. An empty one answers the `T[]` of its
position, else it is CE2111. A declared size that the literal does not fill is
[CE2011](../error-catalog.md#ce2011) in a `let` and in a constant initializer. An `Index`
answers the element type of its base, or `u8` for a `string`. A base that is no array is
[CE2114](../error-catalog.md#ce2114), and a readable index out of the range of a `T[N]` is
[CE2012](../error-catalog.md#ce2012) or [CE2056](../error-catalog.md#ce2056). A `Slice`
answers `T[]`: a `string` base is [CE2122](../error-catalog.md#ce2122), and readable bounds
that go down are CE2125. An element write (`a[i] := v`) checks the value at the element
type. A byte of a `string` is [CE2113](../error-catalog.md#ce2113), and an element of a
constant is CE2096. Every index, count and range bound goes through ONE seam of an `i32`
position (`check_i32_operand` and `reject_non_i32`, `passes/types/arrays.py`). A bare byte
literal, also under a `~` or a negation, takes `i32` from the position, and a negated byte
literal below zero is [CE2073](../error-catalog.md#ce2073) for `u8` at the negation, as on
`main`. Any other value gets no type from the position, and a type that is not `i32` is
[CE2121](../error-catalog.md#ce2121), or [CE2006](../error-catalog.md#ce2006) for a method
argument. The array method family has its arm in `FAMILY_ARMS` (`check_array_method`). It
refuses, in this order: a method that changes a constant (CE2096, also behind an alias), a
receiver of the wrong kind ([CE2023](../error-catalog.md#ce2023)), a wrong count
([CE2009](../error-catalog.md#ce2009)), and then each argument by the rule of the row of the
method in `_ARRAY_METHODS`. The call answers the type of its row after each fault. The slot
argument of `push` and `insert` is `Owned`, because the slot takes it by position. Every
other argument is `Guaranteed`, and a `nom` on it is [CE2427](../error-catalog.md#ce2427).

**An initializer body.** Before the walk of the initializer body of a constant or a `var`
(6.3), the constant evaluator reads the initializer at the declared type, with the reporter
of the pass (Q5). It reports what is not constant ([CE0108](../error-catalog.md#ce0108)), an
overflow ([CE2077](../error-catalog.md#ce2077)), a division by zero and a cycle
([CE0109](../error-catalog.md#ce0109)), and the walk does not run after an error of the
evaluator. An empty container (`List.new()`, `from([])`) allocates nothing: the evaluator
does not read it. The `Return` of the body checks its value at the declared type, as a `let`
does: a value that does not fit is [CE2002](../error-catalog.md#ce2002) at `name = value`,
with a note at the declared type. The value that the evaluator gave only decides the stop
above; the pass writes no row for it. The `borrow` pass walks an initializer body as any
other body. The constant evaluator makes each value of an initializer, so each value there
is a new value: the `Return` adopts its value, and a constant that the initializer names is
read, not moved (7.9).

**A `var`.** A `StorageRef` answers the declared type of its `var`, bare or behind an alias
(`t.count`), from the record that the namespace table of the body holds
(`NamespaceTable.constant_record`, the one lookup of a constant and a `var`). The pass
writes the global row of the node: the symbol of the global of the `var`, from
`global_symbol` (`semantics/unit_symbols.py`). Another unit's private `var` is
[CE3005](../error-catalog.md#ce3005), bare or behind an alias, and a bare name that two
imports bring is [CE3012](../error-catalog.md#ce3012), as for a constant. A rebind of a
`var` checks its value at the type of the `var`, as a rebind of a local does: a value that
does not fit is CE2002 at the statement. A `var` has an address, so a field write and an
element write of it are checked as those of a local, with no CE2096.

**A cast.** The arm of `as` (`check_cast`, `passes/types/expressions.py`) answers the
target type. A cast between two numeric types, and a cast to the type of its operand, call
nothing: the conversion row is `NoConversion`. Any other pair is
[CE2014](../error-catalog.md#ce2014). In the body of a conversion, a cast of the pair of
that conversion is [CE2523](../error-catalog.md#ce2523), with a note at the conversion. A
refused cast still answers its target type and has the row `NoConversion`, so the node that
reads it and `borrow` check it too. An
opaque type parameter has no other rule: `v as T` with `v` of the type `T` calls nothing, and
each other cast to or from `T` is CE2014 with a note at the parameter. Two
casts are not typed yet, and each one stops the walk of the body with `typecheck, Cast`: a
cast to or from `ptr`, and a cast between two error types that a declared conversion joins.
An integer literal directly under a cast to an integer type, also under a negation there,
has the target type, and the pass does not check its range: the cast keeps the bits that
the type holds (`300 as u8` is 44). The fold reads that operand at the target type too
(`fold._cast`). Any other operand of a cast gets no type from its position.

**A method call.** A `Call` with a `Dotted` target types its receiver first, at no expected
type. Then the ladder of the call (`resolve_dotted`, `passes/types/calls/dotcall.py`) finds
the callee in one order: the family of `METHOD_TYPE_REGISTRY` that claims the receiver and
the name (the claim puts a perk implementation before a family that yields to it), then a
perk implementation, then an extension method of the name in any unit, and else nothing. A
call that nothing answers is [CE2008](../error-catalog.md#ce2008), except when the collect
pass refused an extension of that name on that type. A family answer is the callee row
`Builtin(recv, BuiltinMethod)`, and only `MethodFamily.callee` makes the `BuiltinMethod`,
so its family is always a row of the table (gate:
`tests/unit/test_dotcall_ladder_is_one.py`). The AST ladder put its answer in a set of
stamps on the call node. The SHIR ladder puts each fact in a row: the callee, its type
arguments and the declaring unit in `ResolvedCall`, the mode of each parameter in a
convention row, the family in `BuiltinMethod`, and the type of the receiver in the type row
of the receiver. The convention of the receiver is a fact of the callee: the answer of the
receiver partition for a `Builtin`, and the declared mode of `self` for a `Method` (8.3,
7.6). The ladder runs for a receiver of every type. A family answer goes to the arm of its
family in ONE table, `FAMILY_ARMS` (`passes/types/visit/calls.py`), keyed by the name of
the family in `METHOD_TYPE_REGISTRY`, and no arm compares a family name of its own (gate:
`tests/unit/test_typecheck_family_arms_are_registry_rows.py`). While the migration is in
progress, a family with no arm in the table stops the walk with `typecheck, Call(method
<family>)`, on a receiver of any type (`Call(method iterator)`), so the stops of each
family are counted apart. A receiver that is not of a built-in type and that no family
claims, and no extension method of a plain type answers (see below), stops the walk with
the label of the call target (`Call(method)`, `Call(stdlib method)`). A perk method that is
no contract method is a `Method` callee (7.6). An opaque type parameter that a
constraint gives `Clone` has the family of the derived `clone`, and a contract constraint the
family of the contract (`promised_contract_family`). In an instance, the same call is the
family of the concrete type.
The primitive family has its arm: a primitive method takes no argument (a wrong count is
[CE2009](../error-catalog.md#ce2009) from the arity row of the family), and the call yields
the type of its row in `PRIMITIVE_METHOD_RETURNS`. A wrong count does not stop the call
from yielding that type. The `string` family has its arm too. A string method has the
arguments of its row in `METHOD_SPECS` (a wrong count is
[CE2009](../error-catalog.md#ce2009) from the arity row of the family, and a wrong argument
type is [CE2006](../error-catalog.md#ce2006) from the one argument check), and the call
yields the return type of its row. A wrong count skips the argument types. The `result` and `maybe` families have their arms too. `is_ok`,
`is_err`, `is_some` and `is_none` yield a `bool`, `realise` and `expect` yield the payload
`T`, and `err` yields a `Maybe@(E)`. A wrong count is [CE2009](../error-catalog.md#ce2009)
from the arity row of the family, and it skips the argument types. The one argument check
reads the default of `realise` at the type `T` and the message of `expect` at `string`. A
value of another type is [CE2503](../error-catalog.md#ce2503) at the argument, and each
argument borrows. The call yields its type after either fault. `realise` on a `~` payload
is [CE2506](../error-catalog.md#ce2506) at the call, and the pass does not check its
default. The call still yields `~`, so a binding of another type is a mismatch too, as on
`main`. One table row holds the argument types and the return type of each method
(`WrapperMethodRow`, `semantics/generics/maybe.py` and `results.py`). The derived `clone`
family has its arm too: `x.clone()` of a struct or an enum takes no argument and yields the
type of `x`. A type that holds a resource has no copy
([CE2431](../error-catalog.md#ce2431), with the `.share()` help;
[CE4018](../error-catalog.md#ce4018) for an opaque type parameter), and a wrong count is
[CE2009](../error-catalog.md#ce2009). The call yields its type after either fault. The arms of
the `List`, the `HashMap` and the `Own` family ask the same rule (`reject_uncloneable`). The
arm of the `Own` family (`check_own_method`, `passes/types/visit/own_methods.py`) checks a
`clone` of an `Own` whose payload holds a resource first, then the count, then the value
of `alloc` called on an `Own` value at the type `T` ([CE2006](../error-catalog.md#ce2006)),
and the call yields the type of its row (`OwnMethodInferrer`): `T` for `get`, the
`Own@(T)` for `clone` and `alloc`, `~` for `destroy`. The arm of the
`HashMap` family (`check_hashmap_method`, `passes/types/visit/hashmap_methods.py`) checks
the count first, then the key of `insert`, `get`, `contains_key` and `remove` at the type
`K` and the value of `insert` at the type `V` ([CE2006](../error-catalog.md#ce2006)), and
the call yields the type of its row (`HashMapMethodInferrer`) after each fault. The key
rules of a map ([CE2054](../error-catalog.md#ce2054),
[CE2055](../error-catalog.md#ce2055), [CE2058](../error-catalog.md#ce2058)) are a rule of
its written type (`reject_unusable_key`), and never of a call or of `HashMap.new()`. A method with a
signature row (`map_err`, `or_err`) has a method-level type parameter, and one arm checks it
from that row (`check_signature_method`, `passes/types/visit/signature_methods.py`), in the
order of `main`: a wrong count is [CE2009](../error-catalog.md#ce2009) and the call answers
nothing more; then the argument is typed with no type from its position, and its type
solves the instance through the solver that the AST half shares
(`settle_builtin_instance`): an unsolved type parameter is
[CE2063](../error-catalog.md#ce2063), and a solved one in an `E` position that is no error
type is [CE2084](../error-catalog.md#ce2084); then the argument is compared with the
parameter of the instance ([CE2006](../error-catalog.md#ce2006), `compare_argument`) and its
`nom` marker with the declared mode ([CE2427](../error-catalog.md#ce2427)). The call answers
the `Result` of the instance. The receiver of each is `Owned` (8.3), and the read-through
of `or_err` over a borrowed `Maybe` is a rule of `borrow` (8.3). The `array` family has its
arm too (see "An array" above). The hash of an array is the `array_hash` family, which a
`Hashable` implementation overrides. Its call is a contract call, which SLIR lowering makes
the intrinsic `hash` (7.6, 8.6). The arm of the family is in `visit/contract_calls.py`,
with the arms of the `derived_hash` and the `contract` families.

**A reference `let`.** The local of `let peek T x = <place>` and of `let poke T x = <place>`
gets the type of its place (8.2), and the `Borrow` value is checked at that type: a place
of another type is [CE2002](../error-catalog.md#ce2002). A value that is no place (a call,
a literal, a slice, a `get()` of no `Own`) is [CE2404](../error-catalog.md#ce2404). A place
is a local, a `var` or a constant, a field or an element of one, or the pointee of an `Own`
(`o.get()`, `places.is_get_call`, which `typecheck`, `borrow` and SLIR lowering read).

**An extension method and a static.** A type is PLAIN when it is a primitive, or a struct
or an enum that is no instance of a generic type and whose name the unit did not lose
(`_is_plain`, `passes/types/visit/calls.py`). The extension rung of the ladder on a plain
receiver resolves through `resolve_extension_method` with the span of the call, and the one
choice of the visible declaration (`_choose_visible`) reports
[CE3005](../error-catalog.md#ce3005), [CE3022](../error-catalog.md#ce3022) and
[CE3023](../error-catalog.md#ce3023) at the call (R4 to R7, C3 to C5). The callee row is
`Method(recv, decl, receiver)`, and `ResolvedCall.unit` is the declaring unit of the
method. A plain struct or enum with no method of the name is CE2008 at the call; when the
collect pass refused an extension of that name (`declaration_was_refused`), the call
reports nothing more and the body holds that error. A `Static` target `T.m(...)` names a
plain type, also behind an alias, where the type is the one that the namespace holds and a
private type of another unit is CE3005. Its static resolves through
`resolve_extension_method(static=True)`: instance against static is still the one filter
there. A type with no static of the name is [CE2102](../error-catalog.md#ce2102), with the
help that names an instance method of the name, and an enum is
[CE2045](../error-catalog.md#ce2045). The callee row is `Static(ty, decl)`. Both kinds
check their arguments with the one argument check, at the declared mode of each parameter
(`CalleeKind.METHOD`, `CalleeKind.STATIC_METHOD`), and the call yields the `Result` of the
channel of the method, or its bare return value, also after an error of an argument. A
`self` in the body of a static is the error hole [CE0134](../error-catalog.md#ce0134) of
lowering. An array of a plain element that owns nothing (`i32[]`, `u8[3]`) takes the
extension methods of a concrete target (`extend i32[] total()`) as a plain type does
(`_takes_user_methods`). A method of a `T[]` that no concrete target declares is the copy
of a template (`extend T[]`), and so is a method of a generic target and a method with a
method-level type parameter: `resolve_user_method` (`visit/method_templates.py`) asks the
extension rungs, then solves a method-level parameter from the arguments with the one leading
solver. The copy is cut for the call, and it is one body of the copy fixpoint. A copy with
solved method arguments has its own row (`ExtensionTable.method_generic_copies`), because it
is no answer of (type, name), and `ResolvedCall.type_args` gives them to the symbol. A payload method on a `Result` or a `Maybe` that no method of
the wrapper answers (`xs.first_big().doubled()`) is [CE2515](../error-catalog.md#ce2515)
at the call, with a note at the receiver, through the one emitter
(`reject_unhandled_channel_chain`, `calls/methods.py`). A static of a primitive type name
(`f64.from_bits`, `string.from_bytes`) is the callee row `BuiltinStatic` (7.6), and its
one argument goes through the one argument check at the type and the mode of its row of
`PRIMITIVE_STATICS`. A static of a built-in container (`List.new`, `List.with_capacity`,
`HashMap.new`, `Own.alloc`) is the callee row `BuiltinStatic` too: its instance is the one
that its position gives, and each argument goes through the one argument check at the
convention of `argument_conventions` (8.3). A perk method is the `Method` of 7.6
(`passes/types/visit/perk_calls.py`): a written call of the `drop` of the predefined `Drop`
perk is [CE4020](../error-catalog.md#ce4020), a wrong count says no more about the
arguments, and an opaque receiver has the method that a constraint promises. These calls
still stop the walk with their label: a receiver of a kind that takes no user method (an
iterator), and a call through a field of a function type that no method of the struct names.

**A `peek` return.** A free function, an extension method and a static with a `peek T`
return (`borrow-model.md` S10h) are typed as any other callee of their kind: the type of
the call is `T`, or the `Result` of `peek T | E`. The arm also writes the source row of the
call (`record_call_sources`, `passes/types/borrowed_returns.py`): the position of each
argument in the source set of the declaration, and -1 for the receiver. The source set is
the one answer of `semantics/borrowed_return.py` (`sources_in`): a borrowed `self`, and
each parameter with no `nom` whose type owns a resource, with each type parameter as a
type that owns. The faults of a declaration are reported at the declaration, one time for a
template: an empty source set is [CE2438](../error-catalog.md#ce2438), and an `E` that owns
a resource is [CE2442](../error-catalog.md#ce2442). The AST builder refuses a `peek` return
on a lambda and on a perk method (CE2439) and on a `nom self` method (CE2440). A function
with a `peek` return named as a value is [CE2441](../error-catalog.md#ce2441); a function
value stops the walk (`typecheck, GlobalRef`), so that check comes with the function values.

**A stdlib function.** A bare name that no function of the unit's scope takes is the
function of a registry stdlib module that the unit imports, when one has it
(`lookup_stdlib_by_name`), so the unit's own function of the name wins. Behind an alias
it is what the namespace of the module holds. Both are the callee row `Stdlib(decl,
module)` (`check_stdlib_call`, `passes/types/calls/user_defined.py`). A row of the
signature table goes through the one argument check at the types of its row, and each
argument borrows (a `nom` marker is [CE2427](../error-catalog.md#ce2427), and it names
the parameter by the member of its type, `F64`, as `main` does). A member of a `<math>`
family (`abs`, `min`, `max`, `divmod`) checks its two arguments as a pair, as the
operands of a binary operator are checked (`check_operand_pair`,
`passes/types/visit/operators.py`), so a lone literal takes the type of the other
argument ([CE2073](../error-catalog.md#ce2073) when it does not fit). Then a wrong count
is [CE2009](../error-catalog.md#ce2009) at the name and skips the argument types. An
argument of a type outside the family is [CE2006](../error-catalog.md#ce2006), with the
name of the family types in the text, and a second argument of another type than the
first is CE2006 too. The call yields the return type of its row (`stdlib_row`,
`semantics/stdlib_registry.py`, with the `Result`, the `Maybe` or the tuple that the row
names interned), also after an error of an argument. A member of a family whose first
argument has no row yields nothing, so the `let` that takes it says nothing more. The
reference of the target answers the function type of the row, with the `Result` of the
row as its channel. Only `<math>` and `<time>` are typed: a call of a function of any
other registry module stops the walk (`Call(stdlib)`).

**A late instance.** The `instantiate` and `monomorphize` passes collect the instances that
the early passes can see. An instance that they do not collect is a late instance. The arm of
a generic call (`visit/generic_calls.py`) makes the late request directly: the analyzer cuts
the copy at once, and the arm reads the signature of the copy, because the arguments of the
call are checked against it. The callee row of the call (`ResolvedCall`) records the instance,
its type arguments and its declaring unit. The copy fixpoint (Q2) places the body of the copy
and reads no row, and no pass reads an AST stamp for a late instance. The request gives the key
of the copy whose body makes the call, and the growth rule reads that chain (CE0151). The
identity of a late copy keys on its unit, its name and its type arguments, as the identity of
each instance does (8.2). A late copy of a perk implementation for a generic struct or enum
target comes from the miss hook of the perk-implementation table, which also cuts the copy of
an array target (`on_perk_miss`, `PerkCopies`, `semantics/generics/perk_copies.py`): a hook of
a table, and no shape of the IR. The first lookup that misses cuts the copy for that perk or
that method, and the copy fixpoint places its body in the home unit of its template and
checks it. `typecheck` keeps the path of `infer_lambda_type` that writes no stamp
(`stamp=False`, `passes/types/visit/helpers.py`). This is not built yet: a generic function as
a value, whose type row names the instance that its position gives (the SHIR walk stops at the
`GlobalRef`).

While the migration is in progress, the arm of a construct that the pass does not
type yet stops the walk of its body with the stop code; the pass then goes on with the
next body, so every unit is lowered and every earlier error is found.

**The label of a stop at a call.** Only `typecheck` stops on a `Call` with the kind of its
target. One function makes the label, `construct_of` (`semantics/shir/dispatch.py`), from
the SHIR node and from the call facts that the stop site gives it
(`passes/types/call_facts.py`). A stage that stops on a `Call` with no facts is an internal
error, so no stage prints a label that another stage would print in another way. The labels
are `Call(<kind>)`: `method` (a method that no built-in family claims and that no
extension and no perk method of any unit names, for example a method of a type parameter), `extension` (a user extension), `stdlib method` (a `public extend`
of a bundled Sushi-source stdlib unit), `static` (a user static),
`builtin static` (a row of `BUILTIN_STATICS`), `stdlib` (a function of a registry stdlib
module other than `<math>` and `<time>`, also behind an alias), `extern` (a C function that `typecheck` does not type yet, 7.6, and `errno`), `alias` (a function of a
user unit behind a `use ... as` alias), `function` (a user function), `generic` (a generic
function, or written type arguments), `fn value` (a call through a function value) and
`builtin` (`print`, `println`). There is no
`Call(construction)`: a construction lowers to `StructInit` or `EnumInit`. A method call
whose family the ladder found names the family instead and needs no facts:
`Call(method <family>)`, made by the one helper `dispatch.stop_at_family` for the typecheck
pass and for the borrow pass.
The stop-code census of the runner (`tests/stop_census.py`) counts a stop inside a bundled
stdlib unit in a row of its own, `<stage>, <construct> [<module>]`, from the note that names
the module. The census reads the labels and decides nothing.

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
| `Field` | `base: Expr`, `name: str`, `member_loc: Span`; a struct field or a tuple element (`"0"`). `member_loc` is the span of `.name`, where a diagnostic of the member points. The index is `typecheck`'s (S5) |
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
| `StructInit` | `decl: DeclId`, `fields: [Arg]`, `written_order: [int]`, `names: [str] \| None`, `named: bool`, `decl_loc: Span`; `fields` in the declaration order; `named` is true for a named construction; `decl_loc` is where a diagnostic of the written type points |
| `EnumInit` | `decl: DeclId`, `variant: str`, `payload: [Arg]`, `decl_loc: Span`; `decl_loc` is the span of the written type path (`geo.Sign`) |
| `TupleInit` | `parts: [Expr]` |
| `ArrayInit` | `kind: Fixed \| Dynamic(new: bool)`, `runs: [ArrayRun]`; `new` is true for `new()` and false for `from([...])`, so a diagnostic names the form that the source writes |
| `Range` | `start: Expr`, `end: Expr`, `inclusive: bool`, `reverse: bool` |
| `Interp` | `parts: [Str \| Expr]`; kept whole so `typecheck` reports per part |

```
CallTarget = Named(ref: Expr, type_args: [TypeRef], type_args_loc: Span | None)
                                             # a scope-resolved name, or any callable expr
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
annotation, concrete in an instance and as written in a template check, which reads it at
the opaque parameters (4.3). It is
syntax, so it may stand on a node (`Cast.target`, `Static.ty`, `ShirLocal.declared`,
`ShirParam.ty`). What a written type RESOLVES to in a position is `typecheck`'s (S5).

`CallTarget` is what the SOURCE says; the closed `Callee` of 7.6 is what `typecheck`
resolves it to (S5). The split exists because lowering runs before `typecheck`: `x.f()`
cannot know whether `f` is a built-in, an extension, a perk method or an iterator adapter
until the type of `x` is known. `Static` covers a call on a type name, user or built-in,
`hm.HashMap.new()` behind an alias included; it has no receiver EXPRESSION at all.

The type arguments that a call writes (`identity@(i32)(5)`) stand on the callee:
`Named.type_args`, each a `TypeRef`, empty when the call writes none, and `type_args_loc` is
the span of the list, where a wrong count is reported (CE2062). A call through a
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
constant (`bump(poke STEP)`) is NOT an error hole: it is a `Borrow` of a `ConstRef`, and
`borrow` refuses it (CE2400, 7.9). A `break` or a `continue` outside a loop (CE1003, which
the `scope` pass reports) is an error hole: lowering counts the loop bodies as the `scope`
pass counts them, so `borrow` never meets a jump with no loop, and one that reaches it is an
internal error.

An error hole CARRIES the code of the error that it stands for (CE6102, CE6104, CE2122,
CE2008, CE2045, CE1003; for a name with no declaration, the codes CE1001, CE1002 and CE0144).
The error hole of a member of a type that is no variant (`Sign.Nope`, `geo.Sign.Nope`) also
carries the data of its message: `member: UnknownMember(written, name, name_loc)`, the
written type with its alias, the member, and the span of `.member`. Lowering sets it from
the source; no pass fills it (S5). The error hole of a call of a name that the namespace
does not hold (`m.nope(1)`, CE2008) carries a `member` too: the alias, the name and the span
of the call. The error hole of a named argument (CE6104) carries `named`, the first name
that the call writes (`dy` in `p.shifted(dy: 5, dx: 0)`). Every other hole has
`member: None` and `named: None`. `typecheck` reads a `member`:
when the written type names an enum, the hole is [CE2045](../error-catalog.md#ce2045) at
`name_loc`, and the diagnostic names the instance that the position gives to a generic
enum, as for a `Static` call (7.3). A static of the name does not change it, because a
static is a value only in a call. On a type that is no enum, the error is the error of
another pass (CE2105, CE2007, CE2008), and the hole stops the walk of its body. The dump
prints the member (`<hole: MemberAccess, error: CE2045 | ..., member: Sign.Nope>`). Ruled
2026-10-28 (R20). The error hole of a bare call with written type arguments on a type name
(`Pair@(i32, i32)(1, 2)`, the `Call` hole of CE6102) is [CE6102](../error-catalog.md#ce6102)
at the hole: `typecheck` reports it (note 108). Its help names the construction as the
source writes it (`let Pair@(i32, i32) v = Pair(1, 2)`), from the text of the hole. The hole
of written type arguments after a type or a value and a dot (`xs.get@(i32)(0)`,
`Vec.at@(i32)(1)`, `Sign.Plus@(i32)(1)`) carries `call`, the call or the construction with no
type arguments, and `type_args_loc`. Lowering sets both. `typecheck` reports CE6102 at
`type_args_loc` and then types `call`, so the later faults of the call are reported too, and
the hole answers what `call` answers. A struct behind an alias with written
type arguments (`geo.Box@(i32)(1)`) has no `call`, and it stops the walk of its body. At
the end of the analysis, an error hole whose code no diagnostic reported is an internal
error, CE0155. The error hole of a name with no declaration, of a jump outside a loop and of
a `self` in a static is the error of a pass before `typecheck` (`REPORTED_BEFORE`,
`visit/results.py`): when that error is reported, the hole has no answer and the walk of the
body goes on, so the later faults of the body are reported too (a CE2002 after a CE1001);
the body goes on to `lift` and `borrow` as a body with an error (7.9), and the hole is unknown
there. The error hole of a named argument is
[CE6104](../error-catalog.md#ce6104) at the hole, with its `named`, and the hole of a name
that a namespace does not hold is [CE2008](../error-catalog.md#ce2008) at the call, with
the closest name of the namespace as a help (`reject_unknown_namespace_call`). When that
call names an argument too (`m.missing(a: 2)`), the hole has both codes, the `member` and
the `named`, and `typecheck` reports CE2008 and then CE6104, the order of `main`. The
collect pass reports CE0134 to the program and not to the reporter of the unit, so the hole
of a `self` in a static walks on with no count of the unit (`REPORTED_TO_THE_PROGRAM`);
the check of the error holes at the end of the analysis still holds it. An error of the construct around the hole can come first, and the hole
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

`decl_loc` is where a diagnostic of the written type points: the written type name of a
bare construction (`Vec`), and the whole construction behind an alias (`geo.Vec(1, 2)`),
as the old pipeline points. Ruled 2026-11-03 (R25, R29). `named` is true for every named
construction, also when `names` is `None`: a field that does not fit is
[CE2083](../error-catalog.md#ce2083) in a named construction and
[CE2028](../error-catalog.md#ce2028) in a positional one, and `written_order` cannot tell
the two apart when the source writes the fields in the declaration order. Lowering sets
both fields from the source; no pass fills them (S5).

A bare call `S(...)` whose name the `scope` pass resolves to a function is a
`StructInit` too when `S` is a struct, the unit declares no concrete function `S`, and no
generic function `S` is in the scope of the unit, its own or an imported one: the struct
takes the call only before a CONCRETE function of another unit (#1076). The rungs are the
rungs of the old call ladder, in its order: a concrete function of the unit, then a
generic function of the scope (`GenericFunctionTable.lookup`), then the struct. Lowering
asks `DeclLookup.constructs_struct`, which reads the generic function table and
`name_ladder.call_constructs_struct`, the answer that the early passes also read. Each
other call stays a `Call`. Ruled 2026-11-03 (R30).

`T.m(...)` and the bare `T.m` on a type name are an `EnumInit` when the enum declaration of
`T` has the variant `m`; else `T.m(...)` is a `Static` call (#542). `decl_loc` is the span
of the written type path before the dot (`Sign`, `geo.Hidden`), where a diagnostic of the
type points (the CE3005 of a private enum behind an alias). Lowering sets it from the
source; no pass fills it (S5). Ruled 2026-10-28 (R19). A member `ns.m` behind
an alias is a `ConstRef`, a `StorageRef` or a `GlobalRef`, by what the namespace holds.
A C global of an `unsafe external` block is a `GlobalRef` with the written name and its
alias (`libc.environ`), as a C function is (`libc.abs`). A read and a write of it are the
same node; `typecheck` refuses the write (CE5016). A type and a name that the namespace
does not hold stay holes. Ruled 2026-10-12.
`from([...])` and `new()` are an `ArrayInit` of the kind `Dynamic`, never a `Call`. The two
have the same runs when `from([])` is empty, so `Dynamic.new` keeps the written form:
the error of an empty array whose position gives no type names it
([CE2111](../error-catalog.md#ce2111)). The element type of an empty array is the type
row of the `ArrayInit` (#544): no other row holds it.

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
| `Destructure` | `targets: [LocalId \| Place \| Discard \| Nested]`, `value: Expr`; `let (a, b) = v`, `Nested(targets, loc: Span)` for `let ((a, b), c) = v`, and `Place(target: Expr)` for each place of a tuple rebind `(a, b) := v`. `Nested.loc` is the span of the nested target, where a diagnostic of its level points |
| `ExprStmt` | `value: Expr` |
| `Return` | `value: Expr` |
| `If` | `cond: Expr`, `then: ShirBlock`, `otherwise: ShirBlock \| None` |
| `Loop` | `source: LoopSource`, `body: ShirBlock`; the walk kind is `typecheck`'s (7.7) |
| `Match` | `scrutinee: Expr`, `arms: [Arm]`, `consumes: bool`; `match nom r:` |
| `Assert` | `cond: Expr`, `message: Expr \| None` |
| `Break` / `Continue` | none |
| `Expand` | `binder: LocalId`, `pack: str`, `body: ShirBlock`; a template check body only (4.5) |

```
LoopSource = Cond(cond: Expr)                                              # `while`
           | Items(iterable: Expr, binder: LocalId | Discard, by: Bare | Ref(BorrowMode, loc))
                                                                           # `foreach`
```

`typecheck` reports CE2423 for a `Ref` binder over a range after the check of the range, and
does not walk the body of that loop, as on main.

`LoopSource` is what the SOURCE says: a condition, or an iterable with its binder and the
binder's written mode. The binder `_` is `Discard`, and `peek _` keeps its mode. The
`loc` of a `Ref` is the span of the written marker (`poke` in `foreach(poke q in ...)`),
where [CE2423](../error-catalog.md#ce2423) points for a range, whose values have no
storage; a `Ref` of a pattern binding has no `loc` (`None`). A type
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
next. A statement after a `Break` or a `Continue` of its block is dead too, and so is a
statement after the error hole of a `break` or a `continue` outside a loop (CE1003, then
CE0140, as `main` reports them). Two statements
of two copies of an unrolled `expand` were not written one after the other, so the one does
not make the other dead (`ExpandCopies.holding`, 7.2). A `Match` ends the path when it has
an arm and each arm ends it. A match whose arms miss a value
([CE2040](../error-catalog.md#ce2040), [CE2074](../error-catalog.md#ce2074)) has no answer,
because the arm that the author adds decides its path (#886): the statement after it is not
dead, and it gives the body no CE0107. So the dead-statement rule gives the predicate the set
of these matches of the body (`uncovered`), and a match of the set does not end the path. The
fall-off rule and `borrow` give no set: the fall-off rule takes the match as one that ends the
path, and `borrow`, which walks a body with an error too (7.9), joins the arms that the match
has. The two path rules read no type:
`typecheck` runs them on the SHIR body after a walk that ends with no stop, on a template as
it is written and not on a copy of it, and it reads every block of the body.

`Destructure` stays one statement, because its ownership is not that of a field read: it
OWNS from an owned value and BORROWS from a borrow (`tuples.md`). `borrow` decides which,
and SLIR lowering writes the field moves or the field views.

`Assert` keeps its message as an expression, because the message is evaluated only when
the condition fails (`assert.md`). It holds no file name: SLIR lowering names the file of
the body in the position of the trap (8.8).

### 7.6 `Callee`: a closed set

```
Callee = Free(decl: DeclId)
       | Method(recv: Expr, decl: DeclId, receiver: Convention)  # extension or perk impl
       | Static(ty: Type, decl: DeclId)          # a user static: Vec.at(3, 4)
       | Builtin(recv: Expr, method: BuiltinMethod)
       | BuiltinStatic(ty: Type, method: BuiltinMethod)   # List.new, f64.from_bits, string.from_bytes
       | Conversion(decl: DeclId)                # `e as B`, and `??` across two error types
       | Extern(decl: DeclId, symbol: str)    # a C function: libc.snprintf
       | Stdlib(decl: DeclId, module: str)      # a function of a registry stdlib module
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

A `BuiltinStatic` callee is a static of `BUILTIN_STATICS` (`semantics/statics.py`). The
statics of a primitive type name (`f64.from_bits`, `f32.from_bits`, `string.from_bytes`)
are the rows of `PRIMITIVE_STATICS` (`calls/dotcall.py`): one parameter each, with its mode
(`from_bytes` takes its `u8[]` with `nom`), and the type that it answers. `method` is the
row of the family of `ty` (`primitive` for a float, `string` for `string`). A static of a
built-in container (`List.new`, `List.with_capacity`, `HashMap.new`, `Own.alloc`) is the
same row: `ty` is the instance that the call builds, which its position gives, and
`method` is the row of the family of the container (`CONTAINER_STATIC_FAMILIES`).
`Iterator.new` is not typed yet.

A `Stdlib` callee is a function of a registry stdlib module (`<math>`, `<time>`): a row of
the signature table of the module (`stdlib_signature`, `semantics/stdlib_registry.py`), or
a member of a `<math>` family (`abs`, `min`, `max`, `divmod`). `decl` names the written
name, bare or behind an alias, and `module` is the `use` path of the module (`math`). It is
the `CalleeKind.STDLIB` of `param_modes.py`: a registry row declares no mode, so each
argument is a borrow (`Guaranteed`). The row of a `Stdlib` callee has no declaring unit.

An `Extern` callee is a C function of an `unsafe external` block, which a call names behind
the alias of its block (`libc.snprintf`). `decl` names the written name, and `symbol` is
the link name of the declaration (`ExternalSig.link_name`). It is the
`CalleeKind.FFI_EXTERN` of `param_modes.py`: a declared parameter has the convention of its
declared mode (a `poke u8[]` is `Inout`), and each value of the variadic tail of a
`var_arg` extern is `Guaranteed`, as each argument of a `Stdlib` callee is. A `nom` marker
on an argument is not compared with the mode: C takes nothing from Sushi. A value in the
tail is a number, a `bool` or a `string`; a value that C cannot hold as itself is
[CE5005](../error-catalog.md#ce5005) at the argument. The call yields the declared return
type, with no channel, and the row carries the declaring unit of the block. `typecheck`
types an extern whose declared parameters are numbers, `string` and byte buffers (`u8[]`,
bare, `peek` or `poke`), and whose return is a number or `~`. The call of any other extern
(a `ptr`, a nullable pointer, a `string` return) stops at `Call(extern)`, and so does a
`ptr` value in the tail.

`BuiltinMethod` names a row of `METHOD_TYPE_REGISTRY`, so the iterator adapters, `or_err`,
`map_err`, the container methods and the string methods are one variant. The row of a
call in `TypeckResults` carries the declaring unit of an extension beside the `Method`
callee (`ResolvedCall.unit`, 7.3), because two units' private extensions of one name link
side by side (`extension-visibility.md`, C5). The row of a `Free` callee carries the
declaring unit of the function too, and SLIR lowering makes the symbol of the `Direct`
callee from it (8.6).

The `decl` of a `Method` and of a `Static` callee is the id of the written name
`<type>.<method>` (`DeclNames.refer`), with the type as `display_type` spells it: the name
that lowering gives the body of the method. The type of the receiver (its type row, or
`Static.ty`), the method of the written name and `ResolvedCall.unit` give the symbol of the
method (`method_symbol`, 8.6). The `receiver` of a `Method` is the
convention of its receiver: the declared mode of `self` (no mode is `Guaranteed`, `peek
self` is `InGuaranteed`, `poke self` is `Inout`, `nom self` is `Owned`). It is a fact of
the callee row and not a row of `TypeckResults`, as `BuiltinMethod.nom_self` is (8.3). A
`Static` has no receiver.

An `Indirect` callee takes the conventions of its arguments from the parameter modes of the
function type of `value` (`CalleeKind.INDIRECT` of `param_modes.py`), and `typecheck`
writes them as convention rows, as for a `Free` callee (8.6). This is not built yet: the
walk stops at a call of a function value (`typecheck, Call(fn value)`).

A call of a perk implementation method is a `Method` callee too: there is no callee kind of
its own. Its symbol is the perk symbol of `extension_symbol`
(`semantics/generics/name_mangling.py`). In an instance the type of the receiver is
concrete (Q1), so the call names one implementation. A contract call (`x.hash()`,
`x.eq(y)`, `x.compare(y)`, `x.to_str()`) is the `Builtin` callee of its family
(`derived_hash`, `array_hash`, `contract`), and SLIR lowering makes it a contract
intrinsic (8.6), as it makes `x.clone()` the rvalue `Clone`. An override of `Hashable`,
`Eq`, `Ord` or `Display` is a perk rung answer at a written call, and each contract seam
takes the implementation first (terminal).

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
- `CountedRange` is a range written as the iterable, with a bare binder or `_`; `reverse`
  is in the `Range`. The binder is an `i32` local. The `Range` has the type
  `Iterator@(i32)` and the row "no value". Each bound is an i32 position: a bound that is
  no number is CE2072, and a number of another type is CE2121. Readable bounds that go down
  are CE2125 (`semantics/ranges.py`). A `peek` or `poke` binder over a range stops the walk
  (`typecheck, Loop`). A `Range` in any other position is CE2122 and its bounds are checked
  too, except in a position that expects an `Iterator@(T)`: there it is a value, which
  stops the walk (`typecheck, Range`).
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
        | Literal(value: Constant, written: str)            # an integer, a byte or a string
        | IntRange(low: RangeBound, high: RangeBound, inclusive: bool)   # 0x80..=0x8f
        | Variant(decl: DeclId, variant: str, items: [Pattern | PatBinding],
                  enum_loc: Span, variant_loc: Span)
        | Tuple(elements: [Pattern | PatBinding])
        | Own(inner: Pattern | PatBinding)       # Own(poke h) -- unwraps Own@(T)
        | Or(alternatives: [Pattern | PatBinding])   # 1 | 2 -- two or more, same bindings

PatBinding = (local: LocalId, by: Bare | Ref(BorrowMode, loc) | Nom)

RangeBound = (value: Constant, written: str, loc: Span)

Arm = (pattern: Pattern, body: ShirBlock)
```

A payload binding is required, and `_` discards (it lowers to `Wildcard`). A binding has
three modes (`borrow-model.md` §10b): `Bare` is a shallow read-only view, `Ref` points into
the payload, and `Nom` takes it, which needs a scrutinee the match owns: a
temporary, or `Match.consumes`. `consumes` is the written `match nom r:`; whether the match
owns a temporary scrutinee is an answer of `borrow`. A bare name in a pattern is a
`PatBinding` with the mode `Bare`. The mode of `Own(poke h)` is in the `PatBinding` that
`Own` holds, and the `loc` of its `Ref` is the span of `poke h`, where a diagnostic about
the reference points; the `Ref` of any other binder of a pattern has no `loc`. Two
alternatives of `|` that bind one name bind one local. An arm body that the source writes
as one expression is a block of one `ExprStmt`.

An INTEGER scrutinee and a STRING scrutinee use `Literal` arms; the kinds never mix
([CE2076](../error-catalog.md#ce2076)), and that is checked on SHIR. `IntRange` is an
INTEGER pattern only: the AST's `RangePattern` (`a..b` or `a..=b`) lowers to its written
bounds and `inclusive`. Each bound is a `RangeBound` with its span: a bound that does not fit
the type ([CE2073](../error-catalog.md#ce2073)) and a string bound
([CE2072](../error-catalog.md#ce2072)) are errors at that bound, as `marker_loc` (7.4) gives
the span of a written marker. A `Literal` and a `RangeBound` keep the source text (`written`:
`0x1FF`, `0x1_00`, `'a'`, `a'/'`), and every diagnostic and note that quotes a pattern quotes
that text (ruling R18). Lowering fills it from the source, and no pass fills it later, so it
is no answer of a pass (S5). The value comes from `value`, never from the text; the dump
prints the value. Lowering has no types, so `typecheck` reads the bounds as values
of the type at the position: `0..10` matches 0 to 9, and `0x80..=0xff` on an i8 matches -128
to -1. A `Literal` integer is a value of that type in the same way.
**A pattern value has no `TypeckResults` row** (ruling R-AE). `typecheck` and SLIR lowering
both ask the one pure function in `semantics/integer_patterns.py` (`literal_value`,
`pattern_interval`). It reads the written constant of the SHIR pattern and the type of the
scrutinee, which is a row already. Ruling R-W does not apply here: its reason is a context
that lowering does not know, and the scrutinee type is known to both readers. A bound is a
literal and not an expression, so lowering does not call the constant evaluator for it, and a constant
keeps its spelling (a radix, a byte). `Variant` names its enum by the written name, the
alias included: a name that is not an enum is an error of `typecheck`, not of lowering.
`Tuple` mirrors `TuplePattern`, `Own` mirrors `OwnPattern`, and `Or` mirrors `OrPattern`:
it stands at the top of an arm and in every pattern position. Each alternative binds the
same locals, with the same types and the same mode ([CE2126](../error-catalog.md#ce2126)),
so one binding list serves the arm whatever alternative matched. `Variant` keeps the span
of the written enum name (`enum_loc`) and of the written variant name (`variant_loc`), where
a diagnostic of that name points: [CE2107](../error-catalog.md#ce2107),
[CE2001](../error-catalog.md#ce2001) and [CE3005](../error-catalog.md#ce3005) at the enum,
[CE2045](../error-catalog.md#ce2045) at the variant. Lowering fills them, and no pass fills
them later (S5).

In `typecheck`, a match on an enum or a tuple writes `MatchFacts` with no integer type, and
checks each arm in the written order. Each alternative of the arm is a pattern at the type
of the scrutinee, and each item of a `Variant`, a `Tuple` and an `Own` is a pattern at the
type of its payload position. A `PatBinding` matches every value of its position. The
type row of the local of a binder is the type of its position with no `peek` or `poke`:
the mode is in the `PatBinding`, as the convention of a parameter holds its mode (4.1). The
alternatives of `|` that bind one name share one local, so the local gets one row, from the
first alternative, after CE2126 finds no difference. An arm whose pattern has an error
does not walk its body. After the last arm, the one checker reads the rows of the arms. A
scrutinee with an error (its own, CE2048, or an enum name that the unit lost) gives the
match no facts and no arm is read: the walk goes on after the match, so the faults of the
later statements are reported, and the body does not go on to the next stage.

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

The arm of a generic call makes a late request directly, and the analyzer cuts the copy at
once (7.3). The callee row records the instance. The copy fixpoint (Q2) reads no row, and no
pass reads an AST stamp for a late instance. A function value is not built yet (7.3).

`typecheck` keeps one flag for the body that it walks, `refused_before`. A node whose error a
pass before `typecheck` reported, or whose answer that error takes away, sets the flag, gets
no answer, and the walk goes on: a `??` in a bare body (CE0131 of `collect`), a call of a
method whose declaration `collect` refused, a construction of a name whose second
declaration `collect` refused (CE1005, CE0006), an error hole of `REPORTED_BEFORE` (7.4), a `match` whose scrutinee has an error
(7.8), and a name of a constant whose initializer has an error (7.3). A body with the flag
gets no completeness check (`check_answers`). It goes on to `lift` and `borrow` as a body with
an error of `typecheck` (see "A body with an error of `typecheck`" below). The flag is a
state of the walk of one body, not a row of `TypeckResults`: no later stage reads it.

`OwnershipResults` is the second typed side table, and it obeys the four rules of 7.3:

| Decision | Keyed by |
|---|---|
| the `Ownership` of each consuming use (move, adopt, reject) | `shir_id` |
| the locals that are CONDITIONALLY moved | `LocalId`, one set for the body |
| whether a producer makes a LENT or an OWNED iterator | the producer's `shir_id` |
| whether a lent iterator's state goes to the heap, because the iterator leaves through a `peek` return | the producer's or adapter's `shir_id` |
| whether a loop walks its iterator in place | the loop's `shir_id` |
| the borrow region of a view: its owner set, the `shir_id` of its last use on each path, and the leaving ends (below) | the `LocalId` of the local that holds the view |

**A leaving end of a region.** An arm of an `if` or a `match` that always leaves the scope of
a view (a `return`, a `break`, a `continue`) has no later use of the view on its path, also when
the path that skips the arm uses the view again. So the region row has a third field, `leaving`.
It holds the block of each such arm with no use of the view, where the region ends at the top of
the arm, and each last use of the view in such an arm. `until` holds the last uses in `leaving`
too, so the statement after a last use reads one field. A change of the owner in such an arm,
after the end of the region, is legal, and a use after that change is CE2412.

**Ruling S6: one fact, one writer.** Before this ruling, `typecheck` and `borrow` both
wrote the walk in place and the iterator state (1.2). Each of those facts is a question about ownership, and
`borrow` sees every input that `typecheck` sees. So `borrow` writes them, and `typecheck`
writes nothing about them. A `Cursor` walk needs no state, because its `LoopKind` says so.

**A named exception: the declared type of a constant.** The record of a constant or a `var`
in the constant table (`ConstSig.const_type`) has two writers. `collect` writes the type as
the source writes it, and `typecheck` writes the resolved type there when it checks the
declaration (`_validate_constant`, `passes/types/constants.py`). The backend, the library
manifest and every pass after `typecheck` read the resolved type. The resolve step is in
`typecheck` at the end of Phase 4. When a later phase moves it into `collect`, `collect`
is the one writer.

`LiftResults` is the third typed side table, written by `lift` alone, under the same four
rules:

| Decision | Keyed by |
|---|---|
| the symbol of the lifted function | the `Lambda`'s `shir_id` |
| the environment layout: the captured locals, in slot order | the `Lambda`'s `shir_id` |

**Ruling Q8: `lift` rewrites nothing.** The `Lambda` node stays in the tree, its body is
checked where it stands by `typecheck` (its types are rows of the enclosing body's
`TypeckResults`) and by `borrow` (each capture is a consuming use of the captured local, at
the slots that `LiftResults` lists). SLIR lowering cuts one `SlirFunction` per `Lambda`
from the
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
  owning value into an `Owned` argument for every type except `string` (8.13). An
  initializer body is the one exception: there each value is new, so a `ConstRef` of an
  owning constant gets `ADOPT` too, and no rule above applies, because an initializer body
  never goes to SLIR (8.2). `REJECT` is
  an error of `borrow`, except at three positions. There it is a borrowed view with no
  error:
  - the value of an `Assign` with `is_init`: a let-borrow of the owner;
  - the value of a `Destructure`: in the `let` form each binder borrows its element (see
    "A destructure in `borrow`" below); a rebind form refuses each place of an owning
    element (CE2411), so with no error it copies plain elements alone;
  - the operand of a `Try` that reads through a borrowed owning wrapper. The wrapper can be
    read directly, or through an `or_err`. With an `or_err`, the `REJECT` row is on the
    receiver of the `or_err` (8.3). The answer of a call with a `peek` return is a
    borrowed wrapper too, so the operand of a `Try` over it (at any depth of `??`) has a
    `REJECT` row.

  SLIR lowering gives a borrowed view a `Copy` of its place into a VIEW local (8.2). A view
  local owns nothing: no drop, no move and no `Owned` argument. The copy is shallow. It is
  safe, because `borrow` freezes each owner while the view lives (CE2412). It is the copy
  that the AST backend makes. A `REJECT` row at any other position of a body that `borrow`
  ended with no error is the internal error CE0157 of the completeness check. `ADOPT` never
  gives `Copy` of a fresh value: a fresh owning temporary would then be dropped twice.
  `ADOPT` on a value that is a VIEW (the answer of a call with a `peek` return, the
  payload of a `??` that reads through) gives `Copy` of its view temporary: a view local
  owns nothing, so it never moves.
  (Ruled provisionally 2026-10-21. Ruling R-AS of 2026-11-04 confirms the mapping and gives
  a borrowed view its view local.) The key is the `shir_id`
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
- The borrow REGION of a view is a row by the `LocalId` of the local that holds the view.
  The row holds the owner set of the view, and the `shir_id` of the last use of the view
  on each path. `borrow` is its one writer (S6). It keeps the owner set of a let-borrow
  already (`record_owners`). A let-borrow freezes each owner to the LAST use of the view,
  so the region ends at the last use, and not at the exit of the scope of the view. When a
  later part of the statement of the last use changes an owner (the `Drop` of the old part
  of a write, which comes after its new value by rule 6 of 8.10; a `poke` argument after
  the argument that reads the view), the region ends right AFTER the evaluation of the last
  use, before that change, and not at the end of the statement. A
  change of an owner after the last use and before the end of the scope is legal. A region
  that ends at the scope exit would refuse a correct program. SLIR lowering writes the
  `BeginBorrow` and the `EndBorrow` statements of the region from the row (8.6). The
  writer is `passes/borrow/regions.py`. The pass keeps each `let` with a `REJECT` row at
  its value, with its owner set and its statement, and it writes the rows when the walk of
  the body ends with no error. A last use is a statement that uses the view, after which no
  path uses it again. The pass reads the statements backwards from the end of the block of
  the view. A use in a loop that does not hold the `let` is a use of the whole loop, because
  the next round can use the view again. A use in an arm of an `if` or a `match` gives the
  last uses of that arm, and a use in the condition alone gives the `if` or the `match`
  itself. A view that no statement uses has its `let` as its last use. A bare binder of
  an owning payload is a view too (see "Control flow in `borrow`"): its row holds the owners
  of its root, and its last uses are read in the statements of its arm. A binder that no
  statement of its arm uses has NO last use, and SLIR lowering gives it no region.
- The answer of a call of a function or a method with a `peek` return is a view too. Its
  owner set is the owner of each argument at a position of the source row of the call
  (7.3).
- A container get-out is a borrow, and it answers a view BY NAME: `get`, `first` and
  `last` of a `List`, `get` of a `HashMap` and `get` of an `Own`. The fact is ONE table
  row for each such method, `answers_view`, beside `METHOD_EFFECTS` in
  `semantics/method_effects.py`. `borrow` reads it: the answer of the call is a view, and
  its owner set is the receiver. SLIR lowering reads it: the destination of the call is a
  view local (8.2) in a region (8.6). The answer of a plain element is a plain copy, and it
  has no region. So a call answer that borrows is one rule with two sources of the owner
  set: the receiver of a get-out, and the source arguments of a `peek` return.
- A `peek` or `poke` binder of a pattern is an address local (8.2) with a region row, as
  a bare binder of an owning payload has: its owners are the owners of the scrutinee, and
  its last uses are read in the statements of its arm. It freezes its owners to its last
  use whatever the type of its payload, as on `main` (a change of the scrutinee after the
  last use in the arm is legal). The address local of a reference `let` has a region row
  too: its owner is the root of its place, and its last uses are read after the `let`, as
  the last uses of a view of a `let` are. So a change of the owner after the last use of the
  reference and before the end of its scope is legal, as on `main`. A reference into a
  constant has no owner: its row has an empty owner set.

`LiftResults` holds the symbol (a string) and the environment layout (the captured local
of each slot, in slot order), each by the `shir_id` of the `Lambda`. The capture mode is ONE
fact with one writer (S6): the `Ownership` row of the capture in `OwnershipResults`, which
`borrow` writes. A copy for a plain value and a move for an owning one is the answer of
`owns_resource`, an ownership fact, so `lift` writes no mode. SLIR lowering reads that row
for the part of the closure: `MOVE` gives a `Move` of the captured local, and `ADOPT` a
`Copy` (8.9). This is not built yet: `Capture` (`semantics/shir/lift.py`) still holds a mode
of its own, `moves`.

**What a consuming use is.** A consuming use is a value in a position that takes it over
(the closed set of `ownership-conventions.md` 3.1). SHIR shows the position, and the rule reads the
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

The receiver of a built-in method that declares `nom self` is a consuming use too (`or_err`
and `map_err`, 8.3). Its convention is the answer of the receiver partition (8.3), and not a
row of `TypeckResults`: the answer is a fact of the callee row (`BuiltinMethod.nom_self`).
The `borrow` pass and SLIR lowering both read it through `receiver_convention`. The
receiver of a `Method` callee whose `receiver` is `Owned` (a `nom self` extension method) is
a consuming use, by the same fact of the callee row (7.6).

**The order of the three passes.** Each SHIR pass gives the next one each body that it
finished with no error, with the tables that `typecheck` read the body in: `typecheck`
gives it to `lift`, and `lift` gives it to `borrow`. An error in the header of a function
or a method (the type of a parameter, the return type, the channel) is an error of its
body for `typecheck`, so such a body does not go to `lift` either: a parameter with an
unknown type would reach SLIR as a local with no type. `typecheck` checks the path faults
of a function or a method (CE0140 and CE0107) before it gives the body to `lift`. A body
with a path fault goes on, marked as the next paragraph tells, so that `borrow` reports its
errors too. A body whose walk in `typecheck` reported an error goes on in the same way. A stop of a pass ends the walk of
its body, and the next pass does not get the body. The body of a template check is read
in the overlay of its check, and an error of `borrow` there refuses the template as an
error of `typecheck` does.

**A body with an error of `typecheck` (ruling R-AR).** `typecheck` gives `lift` a body whose
walk or path faults reported an error, or whose walk set the flag `refused_before`, when its
header has no error. It marks the body `typecheck_failed`, and `lift` gives it to `borrow`
with the mark. `borrow` walks such a body with one more rule: a node with no answer is
UNKNOWN. An error hole is unknown, and so is a node that misses an answer that the
completeness check asks for: an expression with no type, a `Destructure` with no element
type, and the other rows of `NEEDS`. A `Call` with a callee is known: an argument of it with
no convention is unknown on its own. A `Match` and a `Loop` are known, because `borrow` reads
no answer of their own: their scrutinee and their condition are unknown on their own, and
each arm and each body is walked (`misses_an_answer` and `KNOWN_WITH`,
`shir/typeck_check.py`). An unknown node has no arm:

- an unknown expression is a read of each expression that it holds (`walk.parts`), each
  through its own arm. A function name that it holds reads nothing. The expression itself
  borrows nothing and moves nothing;
- an unknown statement is not walked. The `let` form of an unknown `Destructure` declares
  each binder that has a type, so a later statement can name it;
- an unknown argument borrows its place as its `peek` or `poke` marker is written, and it
  takes no convention. A bloom argument (`xs...`) is unknown too: in such a body
  `typecheck` refused it;
- at a consuming use, an unknown value gets `ADOPT` (`consume.decide`): it moves no local and
  it is no error.

A check that the arm of a known node makes on the SHAPE of an unknown part still runs:
CE2404 for a `poke` of a field of a temporary, and CE2437 for a `let` of a slice of a
temporary. A write of a constant is a known statement too: its error is CE2096 of
`typecheck`, and the write gate takes the constant as storage, so it gives no CE2429
(`reads.chain_call_boundary`). So `borrow` gives the diagnostic of each statement and each expression that has
its answers, also in the statement of the error of `typecheck`, and never a diagnostic that
reads an answer that `typecheck` did not give. What `borrow` assumes in an unknown part: no
local moves there, and no view starts there. So a use of a name after an unknown call that
names it with `nom` gives no CE2405. A local with no type is PLAIN for `classify()`, so a use
of it never moves it. Two divergences from the old pipeline follow. `typecheck` types no arm
of a `match` whose scrutinee has an error (7.8), so each node of its arms is unknown, and
`borrow` gives no diagnostic in them. A stop of `borrow` ends the walk of such a body too
(for example a construct that the pass does not check yet), so a diagnostic in the construct of the stop or after it does not
come; the pipeline never reports the stop, because the program has an error. The pass writes no row of such a body,
as in the discovery round of a loop: the `OwnershipResults` of the body stay empty, the pass
does not check them, and the body never goes to the SLIR lowering.

**`lift` on SHIR.** `LiftPass` (`passes/lift.py`) walks the body once (`walk.py`). A `Lambda` stops the walk of its body with the stop code (`lift, Lambda`),
and the pass writes no row: a body with a lambda never reaches `borrow`. Today
`typecheck` stops at a `Lambda` first, so no compile meets the stop of `lift`. A body with
no `Lambda` has an empty `LiftResults`, and the dump prints no answer of `lift`. When the
walk ends with no stop, `check_lift` runs, and the body goes to `borrow`, which reads
`LiftResults` for the captures. The AST pass `LambdaLifter` stays in the same module, and
no stage runs it. The SHIR lowering of a lambda takes the symbol and the environment layout
from it, and then it is deleted.

**The body half of `borrow`.** The declaration half of `borrow` reads the declaration of
the body (a borrowed return), and the mode of each parameter is its `Convention` (8.3). The
body half walks the body through ONE table of arms keyed on the SHIR class, with a row for
each member of `ShirStmt` and `ShirExpr` (`passes/borrow/arms.py`, gate:
`tests/unit/test_borrow_dispatch_is_total.py`). The lookup of an arm and the stop are the
one dispatch of the SHIR passes (`semantics/shir/dispatch.py`), which `typecheck` reads
too: a class with no row is an internal error at the span of the node, and the arm of a
construct that the pass does not check yet stops the walk of its body with the stop code
(`borrow, <class>`). The walk of a block reads the statements that a path reaches
(`reached_statements`, 7.2). A call of a built-in method has its
arm when `receiver_convention` (8.3) gives its receiver the convention `Guaranteed`: each
method of the primitive family and of the `string` family, the derived `clone` of a struct
or an enum, and each method of the `result` and `maybe` families that does not declare
`nom self`. The arm reads the receiver (a use
after a move is CE2405), and the receiver stays with its owner. Then it reads each argument
by its convention row, as a call of a free function does. A call of a method of another
family, and a call with an `Owned` receiver, have no arm in the pass yet: each stops the
walk with the family of its callee row (`borrow, Call(method <family>)`). Both stages make
this stop with the one helper `dispatch.stop_at_family`. A `Static` callee is a call of its
arguments. A `Method` callee reads its receiver by the convention of its `self`
(`check_method_receiver`, `passes/borrow/calls.py`), after the entry of the call:
`Guaranteed` is a read and the implicit shared borrow of the receiver; `InGuaranteed` is a
read and a `peek` of its owner; `Inout` is the write gate and then a `poke` of its owner
(`borrow_of_constant` refuses a constant, [CE2400](../error-catalog.md#ce2400), and
`reject_readonly_write` refuses a read-only place and a temporary,
[CE2429](../error-catalog.md#ce2429)), and after the arguments the change of the owner, so
a view that the same call reads is [CE2412](../error-catalog.md#ce2412), "read by the call
that changes it"; `Owned` takes the receiver after the arguments, a consuming use that
names the method in [CE2435](../error-catalog.md#ce2435), with the help of
[CE2411](../error-catalog.md#ce2411) to copy. A `Stdlib` callee is a call of its
arguments, and each is `Guaranteed`, a read. A `BuiltinStatic` callee is a call of its
arguments with the convention of each row: `string.from_bytes` consumes its array. An
`Extern` callee is a call of its arguments with the convention of each declared mode: a
`poke u8[]` is the exclusive borrow of its place for the statement, and each other argument
is a read. The typecheck pass records only the callees
`Free`, `Stdlib`, `Extern`, `Intrinsic`, `Builtin`, `BuiltinStatic`, `Method` and `Static`, so a call with another
callee in this pass is an internal error. The state of the walk (`BorrowState`, `FlowFacts`) is keyed
by `LocalId`, because a hidden local has no source name, and a diagnostic names a local by
the name of its `ShirLocal`. The pass decides the `Ownership` of each consuming use ONE
time, with `classify()`, and one function writes the row (`consume.decide`, gate:
`tests/unit/test_consuming_use_coverage.py`). The convention of a call argument is the row
of `typecheck`, and `borrow` does not derive it again: the `nom` marker is compared against
the declared mode by `typecheck` (CE2427). A `peek x` or a `poke x` argument borrows its
place for the statement with the mode that the source writes, so a `poke` argument for a
`peek` parameter keeps the `InGuaranteed` convention of the parameter and is still a `poke`
borrow of its place (CE2408 for a read-only place).

**A constant in `borrow`.** A constant is read-only storage that the program keeps: a
`ConstRef` has an address and no state of the walk. A `peek` of it, a `poke` of it and the
value of a reference `let` (`let poke T x = STEP`) reach the one arm of a borrowed place
(`borrows.check_borrow`). The arm reads the record of the constant in the scope of the
body, bare or behind an alias (`NamespaceTable.constant_record`), and asks the one gate
`reject_borrow_of_constant` (`semantics/constant_borrow.py`): a `peek` reads the constant,
and a `poke` is [CE2400](../error-catalog.md#ce2400) at the place. A stdlib constant
(`PI`) has no record and no storage, so both modes are CE2400 there. The `scope` pass asks
the gate only for a name that SHIR keeps as no `ConstRef`: a top-level function and a
namespace. A take of a constant is a consuming use (CE2436, `consume.reject_move_of_storage`).
In an initializer body, the constant evaluator makes each value, so the provenance of each
consuming use there is FRESH: the `Return` adopts its value, and `const string ALIAS =
GREETING` is no take of `GREETING`.

When the walk of a body ends with no stop and with no error, `check_ownership` runs, and
the body goes to the SLIR lowering (8.2). The SLIR stage takes no initializer body
(8.2), and it lowers no body while the program has an error: the program reporter or the
reporter of the unit holds one (`SlirStage.failed`). So no body with an error of
`typecheck` or of `borrow` reaches the SLIR lowering, and neither does a body of a program
whose declaration has an error (CE2416 of an enum payload): the program gets no internal
error of the verifier after its real error. The pipeline reports the first stop of
`typecheck`, then the first stop of `lift`, then the first stop of `borrow`, then an error
hole, then the first stop of the SLIR lowering; with no stop, the backend emits each body from
its SLIR (8.2).

**Control flow in `borrow`.** The joins read the one path predicate of 7.5. An `If`
checks each block from the state after its condition, and the paths that reach the code
after it JOIN: a block that ends in a `Return`, a `Break` or a `Continue` leaves by its own
exit, and an `If` with no `otherwise` has the path that skips `then`. A `Match` reads its
scrutinee, and a `Match` with `consumes` takes it over, a consuming use. Its arms are
exclusive paths: each arm starts from the state after the scrutinee, and the paths that
reach the code after the match join as the blocks of an `If` do. The arms cover each value
(`typecheck` refuses a match that misses one), so no path skips the arms. A literal arm
(`Wildcard`, `Literal`, `IntRange`, and an `Or` of them) binds no name. A `Variant` and a
`Tuple` bind the local of each `PatBinding` in them, at any depth. Each arm declares its
binders before its body: a bare binder of a plain payload or of a plain tuple part is a
read-only view of it (CE2414 for a write or a rebind, with a note at the name of the
variant, or at the tuple pattern, that binds it), and a position that takes it takes a
copy (`ADOPT`). The alternatives of `|` share one local, and the first alternative gives
the span of the note. A tuple literal as the scrutinee is checked as any `TupleInit`: each
part of plain values is a copy. A bare binder of a payload or a part that OWNS is a
borrowed view of it: a let-borrow of the owner of the scrutinee, with a region row (see
the rows above), and SLIR lowering gives it a view local (8.9). A `peek` or a `poke` binder
is a reference binding: it borrows its payload place with its mode for the whole arm, and
SLIR lowering gives it an address local (8.2). An `Own(...)` pattern binds the binders of
its pointee in the same way: a bare binder is a view (or a copy of a plain pointee) with
the note at the name of the variant or the tuple pattern that holds the `Own`, and
`Own(peek h)` or `Own(poke h)` is a reference binding of the pointee, which keeps its
reference type: a write through a `peek` one is [CE2408](../error-catalog.md#ce2408), and
it freezes the owners of the scrutinee to its last use whatever the type of the pointee
([CE2412](../error-catalog.md#ce2412)). A `nom` binder TAKES its payload
(`passes/borrow/pattern_takes.py`): it is an owned local of the arm, with no region, so a
later use after a move of it is CE2405 and a move of it on some paths of the arm gives it a
`cond_moved` row. The match must own the scrutinee: a temporary (a FRESH value), or
`match nom r:`; a `nom` binder under any other match is
[CE2432](../error-catalog.md#ce2432) at the binder, with a note at the owner of the
scrutinee, and a help that names `match nom` (or a `.clone()`, when no name owns the
scrutinee, a get-out of a temporary). An arm that takes one owning payload takes the variant
whole, so a path that takes one and leaves another owning position (a bare binder, a
reference binder, an `Own(...)` or a `_`) is [CE2433](../error-catalog.md#ce2433) at the
position that it leaves, with a note at the taken binder. Each part of an in-place tuple
literal is a scrutinee of its own for both rules. `Own(nom x)` is refused by the AST
builder ([CE2434](../error-catalog.md#ce2434)). A `peek` or `poke` binder of a variant
payload or of a tuple part is a reference binding of its payload place (see "A reference in
`borrow`" below). The owners that the binders of an arm freeze are released at the end of
the arm, so a second match on the same owner is no second borrow. A tuple
literal that owns, as the scrutinee, builds NO tuple (ruling 3 of `tuples.md`): each part
is a root of its own, which the match reads; a part is a consuming use only when the match
consumes (`match nom (a, b):` takes each part), and a binder of a part views that part, so
its owners are the owners of the part. A tuple literal of plain parts is checked as above. A `Loop` with a
`Cond` source reads its condition once, at the entry, and checks its body in two rounds.
The first round is a DISCOVERY round: it reports no error, writes no row and records no
exit, and it gives the facts that come round the back edge (the end of the body and each
`Continue`). The second round starts from the entry joined with that back edge, and it
reports and writes each row once. The code after the loop sees that fixed point joined
with each `Break` path of the second round. A `CountedRange` loop reads the bounds of its
range once, at the entry, and declares its binder as a read-only view, as a bare binder of
a match is: a write or a rebind of it is CE2414, with a note at the binder. Then it checks
its body as a `Cond` loop does. A `Loop` with any other `Items` source walks an iterator,
and it stops the walk (`borrow, Loop`). A `While` loop has no iterator, so it has no
walk-in-place row: that row is a fact of a `Protocol` or an `IteratorValue` walk alone.
`walk_place` (`semantics/places.py`) reads a SHIR place through the adapter of its tree,
with the same `Step` and `Walked` as the AST walk of the `scope` pass.

**A field in `borrow`.** The pass tracks whole locals, not the places in them, so each rule
of a field place is a rule of the ROOT of the place, which `walk_place` finds through the
field steps (`reads.borrow_owner`). A `Field` reads its base: a read of a field of a moved
local is CE2405 at the local. A field is storage that the owner of its base keeps, so a
consuming use of it is a read through that owner (`Provenance.BORROWED`): a plain field
gets `ADOPT`, also off a constant and off a call, and an owning field gets `REJECT`, which
is CE2411 at the field and a let-borrow of the root at a `let`. So a field of a
constant is never taken: `K.name` of a `string` field is CE2411 in a `return` and a
borrowed view in a `let`, and the program does not drop the constant. An unmarked argument
that reads an owning field is a shared borrow of the root for the call (CE2407 beside a
`poke` of the root). A `peek` or a `poke` of a field borrows its root, with the rules of a
borrow of a local (CE2403, CE2407, CE2408, CE2412); a field of a constant goes to
`reject_borrow_of_constant` at the name of the constant, and a field of a temporary is
CE2404. Its text spells each field after a root that a call of a function or a construction
gives as `<expression>` (`make().x` is `<expression>.x`), the text of `main`
(`temporary_spelled`). A field write goes through `reject_readonly_write` at the root, at the statement:
CE2408, CE2414, CE2421, CE2422, CE2426, and CE2429 for a root past a call boundary, with a
note at the boundary. Then the field is read, the value is checked, a change of the root
invalidates each let-borrow of it (`check_owner_not_borrowed`, CE2412 at a later use), and
the value is a consuming use. Each field of a `StructInit`, in the written order (R1), and
each part of a `TupleInit` is a consuming use by its position, as each payload of an
`EnumInit` is. A construction reads each field (each payload) first and then takes
each, as a call does with its arguments, so two takes of one local in one construction
are one take (8.9, `Take`). A root that the pass does not check yet (a `GlobalRef`) stops
the walk with its class.

**A marked field take in `borrow`.** A `Take` (`nom s.out` in a `let`, a `return` or the
value of a destructure) and a `nom` argument on a field (`f(nom s.out)`, a `nom` field of a
construction) are a take when the field is ONE step off a local that the body OWNS and the
field owns (`passes/borrow/takes.py`). The take has a `move` row (on the `Take`, or on the
`Field` of the argument) and spends the WHOLE receiver: a consuming use of the local with
no row of its own, so a later mention is CE2405, a live borrow of the local in the
statement is CE2401, and a take on some paths gives the local a `cond_moved` row. Any other
`Take` is its place, and the marker is inert: the `Take` gets the decision of its place,
which a position decides as for the place alone (a chain `nom a.b.c` and a take through a
borrow read through an owner: a let-borrow in a `let`, CE2411 at a consuming use).

**An array in `borrow`.** Each `Single` element of an `ArrayInit` is a consuming use by its
position. The value of a `Repeat` is read, then its count (R2), and a range run reads its
bounds. An `Index` reads its base, then its index, and a `Slice` reads its base, then its
bounds. A plain element is a copy (`ADOPT`). A slice is a BORROWED `T[]` view of the owner of
its array: its row is `REJECT`, so a consuming use is [CE2411](../error-catalog.md#ce2411),
and a `let` of it is a `let`-borrow of the owner. A `let` of a slice of a temporary is
[CE2437](../error-catalog.md#ce2437). A `peek` of a slice is a shared borrow of the owner, a
`poke` of it goes through the gate of a write ([CE2426](../error-catalog.md#ce2426)), and a
`peek` or a `poke` of an element is [CE2404](../error-catalog.md#ce2404). An unmarked
argument that reads a slice, or an element that owns, is a shared borrow of its root for the
call, as an owning field is ([CE2407](../error-catalog.md#ce2407) beside a `poke` of the
root). An element write has the gates of a field write. The receiver of an array method is
read first. The pass takes no borrow for it. An `Inout` receiver is written after the
arguments, through the one gate of a write and `check_owner_not_borrowed`, and a bulk copy
that reads the array that it grows is [CE2430](../error-catalog.md#ce2430): the check sees
through a slice to its array. A `destroy()` call as a statement marks the local destroyed,
and a later use is [CE2406](../error-catalog.md#ce2406). An array whose element owns has
the same arms. A `Single` element moves a local and adopts a new value, and an element or a
field of another owner there is [CE2411](../error-catalog.md#ce2411). A repeated value is a
borrow, so a later use of it is legal. An element of an owning array is a read through its
root, as a slice is: a position that takes it refuses it (CE2411), a `let` of it is a
`let`-borrow of the root with its region row, and a change of the root while the binding
lives is [CE2412](../error-catalog.md#ce2412). The element of `push` and the value of
`insert` are consuming uses (`argument_conventions`). `get`, `first` and `last` of an array
answer a view of the receiver (`answers_view`), as those of a List do. A refill (`fill`)
whose argument reads a slot of the owning array that it refills is CE2430
(`reject_refill_from_own_slot`): an index and a get-out read as any slot, so `xs.fill(xs[0])`
is refused. `iter()` on an array stops the walk (`Call(method array) iterator`), because it
makes a lent iterator.

**A `var` in `borrow`.** A `var` is storage that the program keeps for its whole run, with
an address and no owner that can hand its value away (`unit-storage.md`). A read of a
`StorageRef` reads nothing that the body owns: a `var` always holds its value. A consuming
use of a `var` has the provenance `OWNED`: a plain `var` copies out (`ADOPT`), and a take
of a `var` that owns is [CE2436](../error-catalog.md#ce2436) at the name, "a unit
variable" (`consume.reject_move_of_storage`, the one rule of a constant and a `var`). A
rebind of a `var` is legal in every body, and its value is a consuming use. A `peek` or a
`poke` of a `var` or of its field, and a `peek self` or a `poke self` receiver in it, are
counted for the statement on the state of the `var` (`borrows.storage_state`, keyed by the
symbol of its global, made at its first borrow): two `poke` borrows of one `var` in one
call are [CE2403](../error-catalog.md#ce2403), and a `peek` beside a `poke`
[CE2407](../error-catalog.md#ce2407). An element of a `var` has no stable address (CE2404),
as an element of a local. A write of a field or of an element of a `var` passes the gate of
a write: the `var` is the root of the place, so no call boundary cuts it
(`reads.chain_call_boundary`). A plain `var` has no `let`-borrow. The pass checks a `var`
that owns too, and SLIR lowering stops at it.

**A reference in `borrow`.** A reference `let` and a `peek` or `poke` binder of a pattern
point INTO a place (`passes/borrow/references.py`). The state of the local keeps the
reference type: a write through a `peek` one is [CE2408](../error-catalog.md#ce2408), and a
consuming use of either is [CE2411](../error-catalog.md#ce2411). The place is read, and a
constant root goes to `reject_borrow_of_constant` (a `peek` binds, a `poke` is
[CE2400](../error-catalog.md#ce2400)). The gates come before the freeze of the owner: a
`poke` through a `peek` root is CE2408, with a note at the root; a `poke` passes the write
gate of the root (`reject_readonly_write`, with the receiver `o` for `o.get()`); a second
reference of one owner is [CE2403](../error-catalog.md#ce2403) or
[CE2407](../error-catalog.md#ce2407), with a note at the other binding; and a `poke`
changes each let-borrow of the owner (CE2412 at a later use of it). Then the owner is frozen
(`record_owners`), and the local has a region row to its last use. A binder of a match that
only borrows a root that is no place is CE2404: a root that a name keeps through a `??`, a
get-out or an `or_err` is borrowed. The binders of one pattern are siblings, and their
region is over the arm. A `poke` of a `var` changes the `var` for each let-borrow of it. An
extension receiver is read before the arguments and takes its borrow after them, so a
`poke` receiver beside a `peek` argument of the same owner is CE2407 at the receiver, with
the note at the argument. A `while` loop reads each local of its condition again at the
back edge of the round that reports, so a view that the condition reads and whose owner
the body changes is CE2412 there.

**A `peek` return in `borrow`.** The declaration half reads the borrowed return of the body
and its source set by local (`passes/borrow/borrowed_returns.py`). A `return` in the body
hands a VIEW to the caller: the value, or the payload of `Result.Ok(...)`, must be a borrow
whose owner is a source, else it is [CE2443](../error-catalog.md#ce2443) with a note at the
`peek` of the declaration. The owner of a place is its root; a `let` of a view is judged by
its owners, and the answer of another call with a `peek` return by the owners of its
sources. The refused shapes are a borrow of a local, of a parameter that is not a source, of
a temporary, and an owned value. The value gets an `ADOPT` row, and nothing moves.
`Result.Err(...)` is an ordinary return. A monomorphized copy of a template does not judge a
value that owns nothing: the template check judged it. At a CALL, the answer is a view:
its provenance is `BORROWED`, a `??` over it reads through, and a position that takes it
over is CE2411. A `let` of it is a let-borrow of the owner of each source argument (the
owners of a source that is the answer of a call with a `peek` return are the owners of its
sources; a literal has none), with a region row. A `let` of a plain answer is a copy, and it
still freezes those owners. A bare binder of a match over the answer views the same owners,
and a `nom` binder there is CE2432. A source argument that is a temporary is legal in one
statement, and a `let` of the answer is [CE2444](../error-catalog.md#ce2444), with a note at
the temporary. A call with a `peek` return that answers an iterator makes a lent iterator;
the lowering of a lent iterator adds it.

**A destructure in `borrow`.** The value is checked first, as a consuming use. A value of
plain elements is a copy (`ADOPT`). A value that owns is OWNED or BORROWED, by its
provenance: a `Destructure` owns from an owned value and borrows from a borrow
(`tuples.md`). An owned value (a new value, or a local that the body owns) gets `MOVE` (a
new value `ADOPT`): the destructure takes each part, and each binder owns its element. A
borrowed value (a parameter, a field, a binder, a let-borrow) gets `REJECT`, with no error:
in the `let` form each binder is a borrowed view of its element, which freezes the owners
of the value while it lives (CE2412, CE2411 for a consuming use of it, CE2414 for a write),
and a binder of an owning element has a region row (its last uses are read after the
destructure). SLIR lowering reads that one row (8.9). A `let` declares each binder, at any
nesting level, as a local of its own. A rebind form then
rebinds each place from left to right, at the span of the place: a `LocalRef` through the
gates of a rebind of a local (CE2408, CE2414, CE2426 for a view of another value's storage;
a by-value parameter is storage of its own and is rebindable), a `StorageRef` with no gate
(a `var` is storage of its own), and a `Field` through the gates of a field write. Each
place takes its part, so a place of an owning element of a borrowed value is the consuming
use of a borrow ([CE2411](../error-catalog.md#ce2411)), right after the gates of that
place: one diagnostic for each such place, at the place, which names the element as the
value and its path (`p.0`), with a note at the destructure, as for `a := p.0` (the text of
`main`). A place of a plain element copies it. So a rebind of a view is its error also when
the value owns.

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
paths that did not move it. A `Guaranteed` parameter that owns starts as a local that is
moved on every path, because the callee does not own the value of the caller (8.10, rule
5): a rebind on some paths and not on others gives it a row. As built, the state of such a
parameter holds two more facts, apart from the moves that a use reads (CE2405): it holds
the value of the caller on SOME path (the join is a union) and on EVERY path (the join is
an intersection); a rebind clears both (`holds_caller_value`, `passes/borrow/state.py`). An
exit and a rebind that see the first fact and not the second write the row
(`passes/borrow/flow.py`). A use of the parameter stays a borrow after a rebind: a `nom`
and a `return` of it are [CE2411](../error-catalog.md#ce2411), as on `main`.

**The completeness checks.** `check_ownership` runs when `borrow` ends a body with no
error. It reads `TypeckResults` and `LiftResults` for the positions, and it needs an
`Ownership` for each consuming use and each capture; LENT or OWNED for each producer over a
container and each lazy adapter, except the iterable of a `Cursor` or a `HashMapCursor`
walk, which makes no value; a heap decision for each lent one; and a walk-in-place answer
for each `Loop` of kind `Protocol` or `IteratorValue`. A `REJECT` row is legal only at
the three positions of a borrowed view above. Two other calls get LENT from
`borrow`, and the check does not ask for them yet, because no spine body holds them: the
clone of a lent iterator and a borrowed call that answers an iterator (a call with a source
row, 7.3, whose answer is an iterator). The check asks for each of them with the lowering
of a lent iterator, which adds the call. The conditionally moved locals have
no check, because no row is an answer. `check_lift` (`semantics/shir/lift_check.py`) runs
when `lift` ends a body with no error, and it needs a symbol and an environment for each
`Lambda`, a lambda in the body of a lambda too. A gap of either check is the internal error
[CE0157](../error-catalog.md#ce0157), which names the pass, the table, the answer, the
class of the node, its position and the body.

**The dump.** `--dump-shir` prints the answers of both tables in place, where a row exists:
the `Ownership` of a consuming use as a word before the node (`move LocalRef(_0)`,
`adopt Lit(2)`, `reject ...`), the name of the answer in lower case (not a SLIR operand,
which is `Copy`, `Move` or `Const`); the conditionally moved locals
on a `cond_moved : [_5]` line after the `locals` line; the borrow regions on a
`regions : [_4: {_1, _2} until #17]` line after it, each view with its owner set and the
`shir_id` of its last use on each path (`until #17, #23` for two paths); `iterator: lent` or
`iterator: owned` and `heap: true` or `heap: false` after the arguments of an iterator
call; `in_place: true` or `in_place: false` after the body of a `Loop`; and
`symbol: "<symbol>"` and `env: [(move _4), (adopt _5)]` after the fields of a `Lambda`,
each capture with its `Ownership` row (the printer does not print this form yet: it prints
the mode of `Capture.moves` beside the row, `(adopt _4, Copy)`). The source row of
`typecheck` prints as
`borrows: [0, -1]` after the arguments of a call with a `peek` return. With no row, the
text does not change: a
body that `borrow` did not reach prints no answer of it, and a body that `borrow` stopped in
prints the rows that the walk wrote before the stop.

### 7.10 AST to SHIR, node by node

| AST | SHIR |
|---|---|
| `Name` | `LocalRef` / `ConstRef` / `StorageRef` / `GlobalRef`, from `Bindings` |
| `IntLit`, `FloatLit`, `BoolLit`, `BlankLit`, `StringLit` | `Lit` |
| `Call`, `MethodCall`, `DotCall` | `Call` with a `CallTarget`, the written type arguments on a `Named` target; a struct name gives `StructInit`, also behind an alias and also when the name is a function of another unit (#1076); a variant of an enum gives `EnumInit` |
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
  view : bool                        # a borrowed view: no drop, no move (8.10, rule 8)
  name : str | None                  # the source name where there is one, for diagnostics
  loc  : Span | None

SlirBlock
  id         : BlockId
  statements : [SlirStatement]       # each carries a Span (G9)
  terminator : SlirTerminator        # carries a Span
```

`locals[0]` is always the return slot, written `_0`.

**A view local.** A local with `view` holds a borrowed VIEW: a shallow copy of a value that
an owner keeps. Lowering writes it with a `Copy` of the place that it views, ONE time on
each path, and nothing writes it again on that path: a binder of an alternative of a
pattern is written one time on each path of its arm (8.9). Four sources make a view local:
a `REJECT` row at a position of a borrowed
view (7.9), a bare binder over an owning payload (8.9), the answer of a call that is a view
(7.9), and the answer of a call of a function with a `peek` return (8.9). A binder of the
`let` form of a `Destructure` that borrows (7.9, 8.9) is a view local too. The local that a
`Slice` writes is a view by the rule of 8.7. A view of a `let` whose owner is a
TEMPORARY (`let string s = make().name`, `let Maybe@(string) m = make().get(0)`) keeps the
temporary: the root of the place that it reads (through the receiver of each get-out) goes
into a HIDDEN OWNER local of the block of the `let`, a local with no source name that owns
the value and drops at each exit of the block (8.10, rule 2). The region of the view is
over that local. A view local owns nothing: drop placement
gives it no `Drop` (8.10, rule 8), and the verifier refuses a `Drop` of it, a `Move` of it
and an `Owned` argument of it (8.13). The shallow copy is safe, because `borrow` freezes
each owner of the view while the view lives (CE2412). The printer writes `view` after the
type: `let _5: string view` (and the source name after it, `// s`). The other option, a
`Ref` into a pointer local and a `Deref` at each read, gives the same answer with one more
indirection at each read, and the value of a `string` or a `T[]` crosses by value in every
other position.

**The `_0` of a `peek` return.** A function or a method with a `peek` return whose answer
owns gives its return slot `view`: `_0` holds a view of a source (8.9), and the callee
never drops it. More than one statement writes it, one time on each path to a `return`,
by a `Copy` of a place, by the aggregate of
the `Ok` or the `Err` of a `peek T | E`, or by a call whose answer is a view. A `peek`
return of a plain type gives an ordinary `_0`: the answer is a plain copy.

**An address local.** A reference binding points INTO a place: `let poke T x = <place>`,
`let peek T x = <place>`, a `peek` or `poke` binder of a pattern, and `Own(poke x)`. Its
local is an ADDRESS local. Its one write on each path is `Ref(place, mode)` (8.7), before
its first use (a `peek` or `poke` binder of an alternative is written one time on each path
of its arm, 8.9), and every later use reads and writes it as an ordinary local (`copy _5`,
`_5 = ...`): the
emitter gives it the address of the place as its storage. Its type is the type of the
place, as the type of the local of an `InGuaranteed` or an `Inout` parameter is. This is
the rule of a `peek` or `poke` parameter (8.3): one rule for a parameter and a reference
local. An address local never drops and never moves (8.10, rule 8), and its place stands in
a region (8.6), to the last uses of its region row (7.9). `SlirLocal` has no field for it:
the address locals of a function are the locals that a `Ref` writes (`address_locals`,
`semantics/slir/function.py`),
and drop placement, the verifier and the emitter read that one function. The emitter gives
an address local an entry-block slot of a pointer, the `Ref` stores the address of its
place there, and each use reads the address from the slot.

The printer writes the source name of a function and of a callee (`fn greet`,
`call @decorate`), not the symbol (`test_spine_greet$greet`, `user_main`). The symbol is
for the module and the link step.

A statement that lowering synthesizes (a `Drop`, a flag write, a `StorageDead`, a bounds
check) takes the `Span` of the construct that caused it: the scope exit, the move, the
index. No statement has an empty span (G9).

**Where SLIR lives.** The package is `semantics/slir/`, and it never imports the backend:
the node modules (`ids.py`, `places.py`, `constants.py`, `rvalues.py`, `statements.py`,
`terminators.py`, `function.py`), the lowering (`lower.py`, with the control-flow arms in
`lower_control.py`, the arm of a `match` in `lower_match.py`, the arms of a struct and a
tuple in `lower_aggregate.py`, the arms of an array in `lower_arrays.py`, the arms of a
call and of an interpolation in `lower_calls.py`, a value before its place in
`lower_values.py`, the view locals and their regions in `lower_views.py` and the facts of a
declaration in `lower_signature.py`), the drop
placement of 8.10 (`drops.py`, the one module that builds a `Drop` or a `DropIfSet`, the
drop of the old part of a field write or an element write included; gate:
`tests/unit/test_slir_drop_placement_is_one.py`), the verifier of 8.13 (`verify.py`,
`verify_calls.py`, `verify_arrays.py`, `verify_flow.py`, `verify_regions.py` and
`verify_rules.py`) and the
printer of `--dump-slir`
(`printer.py`). Every class is a slotted dataclass, and every statement and terminator has
a required `span` (gate: `tests/unit/test_slir_nodes_are_slotted.py`). The borrow pass
gives each body that it finished, and that is not a template check (Q1), to the SLIR stage
of its unit (`SlirStage`). The stage makes one `SlirFunction` from the body and its three
side tables, and `SlirProgram` keeps the outcome of each body, keyed by the `DeclId` of the
body. The symbol, the unit and the return type of the function are facts of its declaration
(6.4). The unit is the declaring unit. A function that has none (a lifted lambda, the
instance of a template of a binary library) goes into the module of the unit that holds its
body. The body of an extension method or of a static is a function like any other: its
receiver is `_1`, with the convention of its `self` (8.3), its declared parameters come
after it, and a static has no receiver. Its symbol is `extension_decl_symbol` (8.6), and
its name is the written name `<type>.<method>`.

**The body of a generic instance.** An instance is one body (Q1). It comes from
`monomorphize`, from a late request of `typecheck` (7.3), or from the copy fixpoint (Q2).
SLIR lowers it as it lowers each other body: one time, at the end of the stage chain of its
copy (`SlirStage.lower_body`). So an instance that the fixpoint cuts lowers after the
per-unit loop, in the order of the fixpoint. No stage keeps an instance body back to lower
it later. The function goes into the module of its declaring unit: the unit that declares
the template (`home_unit`, which `declaring_unit` reads). Its symbol is `function_symbol`
of the instance name and that unit. So the identity of an instance is its unit, its name
and its type arguments, and the instances of two units are two symbols. The row of the
instance in the function table (`funcs.by_unit`) is written when the copy is placed
(`generics/synthesis.py`), before any caller lowers: the `Direct` callee of a caller and the
verifier find it (8.13). The printer and the emitter list the instances of a module by
symbol: the copies of a unit are in the order of their names (Q2), never in the order of
the fixpoint. Three more rules apply:

- a lambda in an instance body is cut one time for each instance, and the symbol of its
  function has the symbol of the instance as its prefix;
- a copy that a pass refused, and a copy of a template that a pass refused, never lower;
- an `Expand` never reaches SLIR: `monomorphize/unroll.py` unrolls it in an instance (4.5).

The dump lists the copies of a unit by name (`LoweredUnit.copies`), and the emitter defines the
written functions of a unit in source order, then the instances by name. The rule of a lambda
has no code yet: a lambda stops the walk of its body. The body of a pack instance has no
`Expand`, and a block ends at a copy that ends every path (`reached_statements`, 7.2).

**The body of a `Drop` implementation.** `fn drop(poke self) ~` lowers as a method body: its
receiver is `_1` with the convention `Inout`, its symbol is the perk symbol of
`extension_symbol` (`semantics/generics/name_mangling.py`), and it has a `Direct` row in
the function table (`funcs.by_unit`). No SLIR statement calls it. A `Drop(place)` of a type
of the `Drop` set goes to the destroy seam, which calls that symbol FIRST and then drops the
owning fields (`drops_of`, the one reader of the `Drop` set in the backend). The verifier
needs no new rule for it. The body of every other perk method lowers in the same way (see
below).
As built: the stage finds the implementation of the body in the perk table
(`perk_impl_of`, `semantics/slir/lower_signature.py`), and the symbol is
`method_symbol` of the target with no unit, the symbol that the declaration round of the
backend gives the method. The SLIR of a body that holds a value of
the type has a `Drop(_n)` of the value at each point where a value of any other owning type
drops: the scope exit in reverse declaration order, the path that does not move it, the old
value of a rebind. A value that a call takes drops in the callee, and a field take spends
its receiver whole, so `drop()` of the receiver does not run.

**The body of a perk method.** The body of every method of a perk implementation lowers as
the body of `drop` does: a method body, its receiver `_1` with the convention of its `self`
(8.3), its declared parameters after it, and the perk symbol of `extension_symbol` as its
symbol. A call of it is a `Method` callee (7.6), which lowers to `Direct` with that symbol
(8.6). The declaration says that a body is a perk method: the stage reads the perk
implementation that holds the method (`perk_impl_of`). It never reads the shape of the body.
The verifier finds the declared parameters of the callee in the same table, by the perk
symbol, with the receiver first (`declared_params`).

While the migration is in progress, a construct that the lowering does not handle yet stops
the body with the stop code (`slir, <construct>`). The construct is the name of the SHIR
class, or one of these names: `conversion` for the
body of a conversion, and also for a
`Try` that calls a declared conversion (today `typecheck` stops there first), `library
function` for a free, a method or a static callee whose row names no declaring unit (a
function of a binary library, whose symbol is its `link_symbol`), `bloom` for a bloom
argument, `Match owning alternatives` for an arm with two or more paths that write a
binder of an owning payload or a `peek` or `poke` binder (the shape of 8.9, not built
yet), and `ConstRef out of range` for a `ConstRef` whose value its type cannot
hold (8.5). The dump prints such a function as ONE line in place of
its body, `fn <name>: stopped (slir, <construct>)`, so that a golden file shows the
progress of each function. The pipeline reports one stop after the analysis: a stop of
`typecheck`, else of `lift`, else of `borrow`, else an error hole, else a stop of the
lowering. When nothing stopped, the program goes on to the backend.

**An initializer body.** The SLIR stage does not lower the initializer body of a constant or
a `var` (6.3). The body gives `typecheck` and `borrow` a SHIR node to check, and nothing
more. The value of the constant is the value of the constant evaluator (Q5). The backend
makes the global of each constant and each `var` from that value, in its declaration round
(`codegen_llvm.py`, `_emit_global_constant`), and names it with `global_symbol`
(`unit_symbols.py`). This is the one path of a global: no SLIR function makes the value of a
global, and the dump shows no function for an initializer. A constant is an `internal`
`.rodata` global in each module. A `var` is defined in the module of its unit, and
declared in each other module, so a body of any module reads and writes it by address: the
emitter finds the global of a `Global` place by its symbol (`values.global_address`), and a
symbol that the module does not hold is an internal error. A body reads a constant as a `Const` operand (8.5), and
never reads its global. The round makes a global for each constant and each `var`, or it
stops the compile; it never drops one. A struct value and an enum value go through the
converter of 8.5, and so does an array value, also in a payload or a field. A constant
that the evaluator gives no value (a function-typed constant) stops with
`emit, constant with no value`.
Every other fault of the round is an internal error: a constant with no type, a scalar of
a type that has no LLVM constant, and a value that the converter of 8.5 cannot write.

**The emitter.** The package is `backend/slir/`, and it follows the backend rule: free
functions, and one state object for each function (`FunctionState`: the codegen, the
storage of each local and the LLVM block of each `BlockId`). The stage entry
(`emit_function`) takes `codegen` first and makes the state; every other function of the
package takes the state first and reads the codegen from it, so one route reaches it. The
declarations of a module still come from the AST. Each body comes from the `SlirFunction`
of the analysis, keyed by the `DeclId` of the body: `codegen_for` hands the SLIR of the
program to the backend (`SlirBodies`), and a body that has no `SlirFunction` there is an
internal error. There is no fallback to the AST body emitter. The emitter defines the LLVM
function that the declaration round made for the symbol. One function derives the symbol of
a body (`function_symbol`, `semantics/unit_symbols.py`): the definition, the callee of a
`Direct` call, the declaration round and the verifier all read it. The body of an extension
method or of a static has its own one function, `extension_decl_symbol`
(`semantics/generics/name_mangling.py`), over `method_symbol`, which the callee of a call
and the verifier read too. The body of `main` is `user_main`, and the C `main` that calls
it stays a helper of the backend. Every local is a slot in the entry block
(`entry_alloca`), except an `InGuaranteed` or an `Inout` parameter, whose local IS the
incoming address (8.3); a value parameter is stored into its slot at the entry, and
`return` loads `_0`. A signature that does not agree with the conventions or with the type
of `_0` is an internal error. `StorageLive` and `StorageDead` emit nothing. An operator
calls the value half of its emitter family, which takes LLVM values (Q7), and a comparison
of two strings calls the string relation of that family. A comparison of two enums or two
structs of one type (a tuple is a struct) calls the contract seam (`emit_contract_relation`,
`backend/expressions/operators.py`): `==` and `!=` read the `Eq` contract, the order
operators read the `Ord` contract, an implementation of the perk first and else the derived
one (`emit_value_eq`, `emit_value_compare`). The operand of any other operator is an
integer, a float or a `bool`, and any other operand type stops the emitter. `SwitchInt`
with `otherwise: None` goes to a block that holds `unreachable`.

Each family of the backend gets LLVM values and semantic types, never an AST node (Q7);
where a family took an AST node, its value half is cut out. The AST body emitter is deleted:
its statement, expression, call dispatch and ownership layers went (8.12), and so did the
AST entry of each family that has a value half. A family that has no value half yet keeps
its AST argument half, with no caller, until its construct gives it operands. A
`Str` constant comes from the string constant seam and owns no heap (owned flag 0). `Copy`
and `Move` both load the value: a `Move` emits no code of its own, because lowering placed
every drop. `Drop` goes through the one destroy seam (`emit_value_destructor`). A string
frees its buffer only when its owned flag is set. An enum goes to the destroy half of its
lifecycle handler, and so do a `List@(T)`, a `HashMap@(K, V)` and an `Own@(T)`, which free
what they hold and then their buffer. A type of the `Drop` set calls the symbol of its
`drop()` first, and then drops its owning fields (see the body of a `Drop` implementation above). `DropIfSet`
loads its flag and drops only while the flag is `true`.
An enum is `{i32 tag, [K x i64] data}`: an `Aggregate` of a variant goes through the enum
emitter with one value for each payload, which the emitter writes at the offsets of the
layout authority (a constant `Aggregate` goes through the converter of 8.5), `Discriminant`
reads the tag as an `i32`, and the place `Field(Downcast(p, v), i)` is the address of
payload field `i` at the offset that the one layout authority gives
(`TypeSizing.payload_field_offsets`). A struct and a tuple are an LLVM struct of their
fields in the declaration order: an `Aggregate(AggStruct)` or an `Aggregate(AggTuple)` goes
through the struct emitter (`emit_struct_value`, `backend/types/structs.py`) with one value
for each field, and the place `Field(p, i)` of a struct or a tuple is the GEP of field `i`.
The emitter reads the semantic type of the place, never a layout match. One check holds
each `Aggregate`: a variant is a variant of its enum, a struct value is of a struct type
with one part for each field, and the semantic type of each part is the type of its
payload field or its field (not only the LLVM type); at a field of a reference type it is
the referenced type, because the part is a place (8.9). A part that does not agree is an
internal error. The check is one function for each kind (`checked_variant`,
`checked_struct`, `backend/constants/enum_initializers.py`): the rvalue calls it, and the
converter of 8.5 calls it for each variant and each struct of a constant, at every depth,
so a constant in a body and the global of a `const` go through the same check. A `Builtin`
call of an intrinsic goes through one row of `INTRINSICS` (`intrinsics.py`), which evaluates
every operand before the family emits (11.9): `interp` appends each part to one string
builder (a string part as its bytes, any other part as its string form, `emit_value_fmt`)
and answers a fresh owned string, also for one string part; `print` and `println` of a
string, an integer, a float or a `bool` call the print emitter. The print of a value that
prints by its `Display` form calls `emit_value_to_str` (8.6), writes the string and frees it
through the destroy seam. A `Builtin` call of a `BuiltinMethod` goes to one
row of `METHOD_FAMILIES` (`backend/slir/methods.py`), by the family of the method. The
receiver is the first operand, and every operand is evaluated before the family emits. The
family keeps its emitters and gives them the operand values (Q7). The primitive family, the
`string` family, the `result` family and the `maybe` family have a row. A family with no row
stops. Each operand of a call of the `result` or the `maybe` family is `Guaranteed` (8.3);
any other convention is an internal error. Each method that the row of the family emits
(`is_ok`, `is_err`, `is_some`, `is_none`) takes only the receiver and answers a `bool`. `realise` and `err` reach no family: they lower
as `expect` does, to a switch on the tag (8.9), and the copy of an owning value is a `Clone`
on its own path. The `Clone` rvalue has ONE emit arm, over `copy_out` (the one deep-clone
entry of `backend/ownership.py`), and no family calls `copy_out` for `realise` or `err`.

While the migration is in progress, a SLIR shape that the emitter does not handle yet
stops the compile with the stop code (`emit, <class>`, the name of the SLIR class, for
example `emit, Indirect` or `emit, AggArray`), at the span of its statement or
terminator. Where one class holds two shapes, the stop names the shape, so that the
stop-code census counts each one: `emit, intrinsic <name>` for an intrinsic with no row
of `INTRINSICS`, and `emit, <family> method` for a `BuiltinMethod` whose family has no row
of `METHOD_FAMILIES`. The declaration round of a global stops with one name, at the span of
the declaration: `emit, constant with no value` (see the initializer body above). Every stop
of the stage goes through one function, `stop` (`backend/slir/state.py`). The stop
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
the operand `copy <place>`, and the emitter passes the address of the place. An address
local (8.2) follows the same rule: a `let peek` or a `peek` binder is read as an
`InGuaranteed` parameter is, and a `let poke` or a `poke` binder is read and written as an
`Inout` parameter is.

A `Guaranteed` parameter is by value, and the emitter stores the incoming value into the
entry slot of its local unchanged (`_open`, `backend/slir/emit.py`). So a borrowed `string`
parameter keeps the `owned` bit of the caller's value, and the callee does not clear it.
No code reads that bit in the callee: SLIR never drops the value of the caller (8.10,
rule 5).

**The rebind of a borrowed parameter.** A `Guaranteed` parameter is storage of its own, and
it is rebindable (`s := v`). It starts NOT OWNED: the callee owns nothing of the value of
the caller. A rebind does not drop the old value when the callee does not own it, stores
the new value, and makes the local owned. At each exit, an owned local drops. On the
straight line the state of rule 3 (8.10) is exact; where a path that rebound the parameter
and a path that did not meet at a join, the parameter gets a flag (rule 4). So the callee
never drops the value of the caller and never copies it at the entry. An `InGuaranteed`
parameter is a view, and `borrow` refuses its rebind. An `Inout` parameter keeps rule 6
through the pointer: the callee writes the place of the caller.

**The receiver of a built-in method** (ruling R-V). The receiver is the first operand of the
call (8.6). Its convention comes from ONE partition of the families of
`METHOD_TYPE_REGISTRY`, in `semantics/method_effects.py`. Each family that a `Builtin` call
can name is in exactly one of four sets (gate:
`tests/unit/test_method_mutation_is_one_fact.py`); `derived_clone` is not such a family
(see below):

- **Only reads** (`RECEIVER_READ_FAMILIES`: `primitive`, `string`). No method of the family
  changes or consumes its receiver. The receiver is `Guaranteed`. One row of the string
  family is the exception: `concat_in_place` (`CONCAT_IN_PLACE`), the call of the concat
  peephole (8.9). Only SLIR lowering makes it, and no program can name it: it is no method
  of `METHOD_TYPE_REGISTRY`. It writes its receiver (`Inout`) and reads its one argument
  (`Guaranteed`).
- **Read or write** (`EFFECT_FAMILIES`: `array`, `list`, `hashmap`, `own`). The rows of
  `METHOD_EFFECTS` tell what a method does to its receiver and to its arguments, and they
  give both conventions, per method, with ONE rule for the four families. The receiver of a
  method whose row `mutates` (`push`, `insert`, `pop`, `truncate`, `fill`, `swap`, ...) is
  `Inout`, a write of its place. The receiver of each other method (`len`, `get`,
  `contains`, `s`, `clone`, ...) is `Guaranteed`: a read. An argument of a method whose row
  `consumes_args` (a container insert: `push`, `insert`) is `Owned`, because the container
  takes it by position with no marker. The index of `insert` on an array and on a `List`
  only gives a position, so it is `Guaranteed`; only the value is `Owned`
  (`POSITION_ARGUMENTS`). `insert` on a `HashMap` takes its key and its value `Owned`,
  because the map stores both. Each other argument is `Guaranteed`, the key of a lookup
  too. `alloc` of an `Own` takes its value `Owned`, and `destroy` writes its receiver.
  A family of `RULED_EFFECT_FAMILIES` has a read-or-write partition of its methods. All four families are in it (`array`, `list`, `hashmap`,
  `own`), so no call of an effect family stops for want of a receiver rule.
- **Consumes** (`CONSUMING_RECEIVER_FAMILIES`: `result`, `maybe`). The family has a
  `nom self` method, so the answer is per method. The receiver of a method that declares
  `nom self` (`map_err`, `or_err`) is `Owned`: a consuming use (7.9). The receiver of each
  other method of the family (`is_ok`, `is_err`, `err`, `is_some`, `is_none`, `realise`,
  `expect`) is `Guaranteed`: a read. The source of the answer is the `self_mode` of the
  signature row of the method. `BuiltinModes` reads it, and `MethodFamily.callee` writes it
  into the callee (`BuiltinMethod.nom_self`). So `realise` and `expect` read a named
  wrapper, as on `main`: the wrapper stays usable after the call, and the lowering copies an
  owning payload out of it with a `Clone` (8.9).
- **No rule yet** (`NO_RECEIVER_RULE_FAMILIES`): each other family. The set can only
  shrink: the gate holds a pinned copy. A call of a method of such a family stops in
  `typecheck` or in `borrow`.

The `derived_clone` family is in no set of the partition (it leaves
`NO_RECEIVER_RULE_FAMILIES`) and has no row of `METHOD_FAMILIES`: `x.clone()` is never a
`Builtin` call in SLIR, because it lowers to the rvalue `Clone` (8.7). One deep
copy has one seam. The refusal of `.clone()` on a type that declares a resource stays in
`typecheck`. The families of the contract calls, `derived_hash`, `array_hash` and
`contract`, follow the same rule: they leave `NO_RECEIVER_RULE_FAMILIES`, each operand of
their call is `Guaranteed`, and they have no row of `METHOD_FAMILIES`, because SLIR lowering
makes each call an intrinsic (8.6).

One function gives the convention of a receiver: `receiver_convention`
(`semantics/method_effects.py`). One function beside it gives the convention of each
argument of a built-in method or of a container static: `argument_conventions(family,
method, count)`, from the same row of `METHOD_EFFECTS`. `typecheck` writes the argument
rows of a `Builtin` call from it, as it writes them for a free call (7.3), and the verifier
reads the same function for a `Builtin` callee (8.13). A container static of
`BUILTIN_STATICS` has the mode of its argument in its row: `Own.alloc` takes its value
`Owned`, and `List.with_capacity` takes its count `Guaranteed`. `receiver_convention`
answers None for a family of the no-rule set, and a family in no set is an internal error.
There is no default convention: the `borrow` pass stops at such a call (`borrow,
Call(method <family>)`), and in SLIR lowering such a call is an internal error. The
`borrow` pass reads a `Guaranteed` receiver. An `Owned` receiver is a consuming use (7.9).
SLIR lowering writes the answer as the convention of the first operand.

**`or_err` and `map_err`.** Over an owned receiver, `or_err` is an ordinary `Builtin` call
with an `Owned` receiver, and the `??` after it is the ordinary `Try`. Over a borrowed
`Maybe` whose payload owns, `m.or_err(nom e)??` reads THROUGH the `Maybe`: `borrow` takes
the receiver as a borrowed view with no error only as the operand of a `??` (7.9), and
refuses it in any other position ([CE2522](../error-catalog.md#ce2522)). A `Result` built
from a borrowed payload would hold a view in an owning aggregate, so the pair `or_err` and
`??` over a borrowed receiver lowers as ONE named shape with no call and no `Result` value
(8.9). `map_err` always takes an `Owned` receiver: on a borrowed owning `Result` it is
[CE2411](../error-catalog.md#ce2411), with the help `r.clone().map_err(f)`.

As built. `borrow` reads the `Owned` receiver of a built-in method first, and takes it
after the arguments (`_take_builtin_receiver`, `passes/borrow/calls.py`), as it takes the
`nom self` receiver of an extension method (`take_receiver`): a named owning receiver
moves, and a later mention is [CE2435](../error-catalog.md#ce2435), which names the method;
a plain one is copied (`ADOPT`). The read-through is a fact of the shape of the call and of
its receiver: `read_through_call` (`passes/borrow/reads.py`) answers the receiver of a call
whose signature row `reads_borrow_through` and whose receiver type owns, and
`read_through_receiver` (`passes/borrow/consume.py`) adds that the receiver is `BORROWED`.
Such a receiver gets its `REJECT` row with no error. The operand of each `??` that the walk
entered is in `try_operands`, and a read-through call outside that set is CE2522. A `??`
over a read-through `or_err`, and a `??` over a `??` that reads through, have the
provenance `BORROWED` (`reads_through`), so the `let` of the payload is a view, and the
walk to its owner crosses the `or_err` (`borrow_owner`). The completeness check takes the
`Owned` receiver of a built-in method as a consuming use, and the receiver of a
read-through `or_err` under a `??` as a borrowed view. In SLIR, an owned `or_err` or
`map_err` is a `Builtin` call with an `@owned` receiver, and the emitter row of the
`result` and the `maybe` family (`backend/slir/methods.py`) gives the value and the type of
the receiver and of the argument, and the type of the destination, to
`emit_maybe_signature_method` and `emit_result_signature_method`. On the `Some` path of an
`or_err`, the emitter destroys the error value. A `map_err` whose argument is a function
value stops in `typecheck` (`typecheck, GlobalRef`, `typecheck, Lambda`).

### 8.4 Places and operands

```
Place    = Local(id: LocalId)
         | Global(symbol: str, ty: Type)      # a unit-level `var`, and its type
         | Field(Place, index: int)
         | Index(Place, Operand)              # an element of a `T[N]` or a `T[]`; a byte
                                              # (`u8`) of a `string`
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

ONE lowering makes a `Deref`: the pointee of an `Own(...)` pattern is the place
`Deref(Field(place, 0))`, the pointer field of the `Own@(T)` read through (8.9). A
by-address parameter (8.3) and an address local (8.2) are read and written as ordinary
locals, and the emitter knows that their storage is an address. So a capture in the body of
a lifted lambda is the place `Field(_1, k)`: the environment `_1` is an `InGuaranteed`
parameter (8.7, 8.9), and that lowering is not built yet.

`Index` is a place, and its operand is an `i32`. An element never moves: a `Move` of a
place that holds an `Index` is refused (8.13), because the array would be left PARTIALLY
moved. An element of an owning type is dropped where it is: the write of an owning element
drops the old element with `Drop(Index(place, i))` (8.10, rule 6), and the write of an
owning field drops the old field with `Drop(Field(place, i))`. A `Drop` of a PART (a place
whose outer projection is a `Field` or an `Index`) is the one `Drop` of a place that is no
local. It reads the root local of the place, and it does not change the state of that
local: the struct still holds a value in each field, the array in each slot.

`Global` is a place, never a value to move out of: a `var` is never moved out of, and
`borrow` refuses a consuming use of one that owns a resource (`unit-storage.md`). The
declaring unit's module defines the global, and every other module declares it external.
A `Global` carries the declared type of its `var`, because no local of the function holds
it: a projection of a `Global` is typed from it, as a projection of a local is typed from
the type of the local.

### 8.5 Constants

```
Constant = Int(value: int, ty: Type)
         | Float(value: float, ty: Type)
         | Bool(value: bool)
         | Blank                                  # `~`
         | Str(bytes: bytes)                      # a literal; owns no heap
         | Aggregate(ty: Type, parts: [Constant], variant: str | None)
                                                  # a const array, struct or enum variant
         | FnRef(symbol: str)
```

Produced by the constant evaluator on the AST (ruling Q5) and carried through unchanged.
Only the backend turns one into an LLVM value. A scalar becomes an `ir.Constant`
(`scalar_initializer`), and a `Str` comes from the string constant seam. An `Aggregate`
becomes an `ir.Constant` through one converter (`constant_initializer`): the declaration
round of the global of a `const` calls it too, with the `Constant` of the evaluator value
(`of_value`). The converter first runs the one check of a variant and of a struct (8.2)
over the value, at every depth. A struct or a tuple is one constant for each field, in the
declaration order; a string field is a constant string of the module. A variant writes each
payload value at the offset of the layout authority, so a constant and a run-time value of
an enum have one layout. A value that the converter cannot write after the check is an
internal error: the converter always gives an `ir.Constant`.

**The value that an integer literal holds.** `typecheck` decides it one time, and writes it
as the value row of the literal (7.3, ruling R-W), beside the type of the literal: the one
writer of a type row (`visit.answer`) writes both, through one function (`held_value`,
`passes/types/visit/expressions.py`). At three positions the literal holds the value that its
type holds, through the one integer kernel (`integer_ops.cast`, which the fold calls too): the
operand of a `Cast` (`300 as u8` holds 44), the operand of a negation (`-128` as an `i8`: the
operand `128` holds -128), and a literal with a radix other than 10, which spells bits (`0xFF`
as an `i8` holds -1). At those positions `typecheck` checks the range by its own rule: none
under a cast, the negated value under a negation, the unsigned range of the width for a bit
pattern. At every other position `typecheck` checked that the value is in the range of the
type, so the value is carried unchanged. SLIR lowering makes the `Int` of a `Lit` from the
value row and decides nothing. The verifier rule "a constant in range" (8.13) holds the
result.

**The value of a `ConstRef`.** A `ConstRef` is a `Const` operand with any row. Lowering
reads the value row of the `ConstRef` (7.3), which holds the value of the constant (Q5), and
makes the `Constant` of that value. A string constant is a `Str`: a literal, which owns no
heap, as a literal-bound string. A `move` row on a `ConstRef` does not reach SLIR: `borrow`
refuses it with CE2436. A `Const` argument of an `InGuaranteed` parameter (`show(peek STEP)`)
has no place: the emitter stores the value in a slot of the entry block and passes the
address of the slot. An enum value is an `Aggregate` of its
enum type that names its variant (`variant`), with one part for each value of the payload, in
order; a payload value is made by the same rule. The printer writes it as the construction of
the variant: `Sign::Plus`, `Shape::Circle(5_i32)`. A struct value is an `Aggregate` of its
struct type with no variant, with one part for each field in the declaration order, and a
field value is made by the same rule. The printer writes it as the construction of the
struct: `Point(3_i32, 4_i32)`. An array value is an `Aggregate` of its `T[N]` type with no
variant, with one part for each element in order, also as a payload or a field value. The
printer writes it as an array literal: `[2_i32, 3_i32]`. The converter writes an array as
one LLVM array constant of its elements. The converter of an enum constant writes a struct
value and an array value in a payload element by element, at the offsets of the layout
authority. The constant evaluator does not wrap
a literal that spells bits (`const i8 B = 0xFF` holds 255), and it gives a value to an
initializer with a range error. A value that its type cannot hold stops the lowering
(`ConstRef out of range`), so lowering decides no value and the verifier rule holds.

### 8.6 Statements

```
Assign(Place, Rvalue)
Call(dest: Place, callee: SlirCallee, args: [(Operand, Convention)])   # R3: a STATEMENT
Drop(Place)                          # RAII, unconditional
DropIfSet(Place, flag: LocalId)      # the conditional move, with a REAL bool local
BeginBorrow(id: BorrowId, Place, mode: BorrowMode, view: LocalId | None)
EndBorrow(id: BorrowId)
StorageLive(LocalId)
StorageDead(LocalId)
```

```
SlirCallee = Direct(symbol: str, name: str, module: Optional[str])  # free fn, method, extern,
                                                         # conversion, stdlib function
           | Builtin(method: BuiltinName, types: [Type])     # an emitter family (Q7)
           | Indirect(Operand)                           # a function value: a closure

BuiltinName = BuiltinMethod                              # a row of METHOD_TYPE_REGISTRY (7.6)
            | Intrinsic(name: str)                       # `println`, `print`, `interp`, `eq`,
                                                         # `hash`, `compare`, `to_str`,
                                                         # a static `<type>.<name>`
```

`DropIfSet` replaces the string-keyed drop-flag table of 1.4. The flag is an ordinary SLIR
local of type `bool`. It is visible, testable and optimizable.

The place of a `Drop` is a local, or an element of an owning type (8.4). The place of a
`DropIfSet` is a local.

`BeginBorrow` / `EndBorrow` make a borrow REGION explicit.
[CE2412](../error-catalog.md#ce2412), a change of an owner while a `let`-borrow is alive,
is a question about a region, and a lent iterator freezes the owners of its place until
its last use (`iteration.md`, ruling 10). Writing the region down is the same argument as
`Copy` / `Move`: the pass that proved it records it, and nothing works it out twice. A
region can hold a SET of owners, as a `zip` of two lent iterators does: one `BeginBorrow`
per owner, under one region.

**Where the statements of a region stand.** The region row of `OwnershipResults` (7.9)
decides them, and lowering derives nothing. At the statement that writes the view, lowering
writes one `BeginBorrow` for each owner of the set, with the mode `peek`. Each `BeginBorrow`
names the view local that its region lends to (`view`), and lowering writes it where it
opens the region. A region with no view local, for example the region of a `peek` or `poke`
argument, has no view (`None`). The printer writes the view as a fourth part,
`BeginBorrow(b3, _2, peek, for _7)`; a region with no view keeps three parts,
`BeginBorrow(b3, _2, peek)`. Lowering writes one
`EndBorrow` for each of them after the statement of the last use of the view on each path,
and at each exit that the region crosses: a `return`, a `break` or a `continue` out of the
region, and the error path of a `Try`. When a later part of the statement of the last use
changes an owner (the `Drop` of the old part of a write, which comes after the new value by
rule 6 of 8.10; a `poke` argument, an `Inout` receiver or a `Move` after the argument that
reads the view), the `EndBorrow` comes right after the evaluation of the last use, before
the SLIR statement that makes that change, and not after the statement. A region does not
end at the exit of the scope of the view: an owner can change after the last use and
before the end of the scope. The
`BeginBorrow` of the region of an address local (8.2) has the mode of its `Ref`, and no
view: an address local is no view local. The
region of a binder of a pattern and of a reference `let` is its region row, as the region
of a view, over the root of the place of its `Ref`. A `Ref` of a constant reads a hidden
local of the block that holds the value, and its region is over that local. The
emitter emits nothing for `BeginBorrow` and `EndBorrow`, as for `StorageLive`. The verifier
reads them (8.13).

**How lowering places them.** The open regions are a part of the state of a path in the
drop placement (`drops.py`): a branch restores them with the state of the locals, and the
paths that meet at a join have the same open regions (else an internal error). After the
statement of a last use, lowering ends the region (`lower_views.py`). At the top of an arm of
an `if` or a `match`, after the binders of the arm, lowering ends each region whose last use
is in the branch (in its condition, its scrutinee or another arm) and not in the arm; the
zero edge of an `if` with no `else` goes to a block of its own for that end. The exit of a
scope ends the region of each view local of the scope before its drops, so a `return`, a
`break`, a `continue` and the error path of a `??` end each region that they cross. A
view that goes into a TEMPORARY (the answer of a get-out that is no `let`, the payload of a
`??` over a borrowed wrapper in an argument) is a view local of the statement. Its region
is over the root local of the place that it reads (the receiver, or the wrapper), and it
ends with the storage of the temporary: at the end of the statement, at the end of the
scope of a condition, before the switch, or at the top of each arm of a `match` whose
scrutinee it is. The view of a get-out of a temporary receiver (`make().get(0)`) has that
temporary as its owner, and its region ends before the temporary drops. A view of a
constant has no owner and no region: a constant never changes.

`Builtin` is a call to a per-type emitter family (Q7). The backend expands it with the
existing emitter, which receives LLVM values for the operands and no AST node.

An `Intrinsic` callee of SHIR (7.6) has no row of `METHOD_TYPE_REGISTRY`, so it lowers to
`Builtin(Intrinsic(name), types)`: `types` holds the type of each operand, and the
printer writes it as `builtin println@(string)`. An interpolation lowers to the intrinsic
`interp` (8.9).

A `BuiltinStatic` callee of a primitive type name lowers to the intrinsic of its written
name, `Builtin(Intrinsic("f64.from_bits"), types)`, and its one operand has the convention
of its row: `f64.from_bits` and `f32.from_bits` read a `Guaranteed` integer and answer its
bits as a float; `string.from_bytes` takes an `Owned` `u8[]`, and the buffer of the array
becomes the buffer of the string, with no copy (#1091), so the array has no drop after the
call. A `BuiltinStatic` callee of a built-in container lowers the same way
(`Builtin(Intrinsic("List.new"), types)`), each operand with the convention of
`argument_conventions`, and its destination is a fresh owning local that the drop
placement drops (8.10). The emitter reads the instance that the static builds from the type
of the destination. `List.new` and `List.with_capacity` go to the rows of the `List` table
that are statics, with no receiver; `List.new` builds the empty container `{0, 0, null}`,
`HashMap.new` allocates its first buckets, and `Own.alloc` takes its value `Owned`.

The test of a string arm of a `match` lowers to the intrinsic `eq`, the value equality: two
`Guaranteed` operands of one type, `types` holds that type for each operand, and the
destination is a `bool` local. The backend expands it with the contract seam `emit_value_eq`
(Q7), so the equality of a type has one home.

**A contract call.** `x.hash()`, `x.eq(y)`, `x.compare(y)` and `x.to_str()` of a struct, an
enum or an array lower to `Builtin(Intrinsic("hash" | "eq" | "compare" | "to_str"), types)`:
`types` holds the type of each operand, and each operand is `Guaranteed` (the receiver, and
the other value of `eq` and `compare`). `eq` is the intrinsic of the test of a string arm
too. The print of a value that prints by its `Display` form is a call of the intrinsic
`println` or `print`. The emitter calls the one seam of each: `emit_value_hash`,
`emit_value_eq`, `emit_value_compare` and `emit_value_to_str` (the print calls
`emit_value_to_str`). Each seam takes an implementation of the perk first (terminal), then
the derived function, then the primitive rule. So no contract call is a `Builtin` call of a
`BuiltinMethod` in SLIR (8.3).

A `Builtin(recv, BuiltinMethod)` callee of SHIR lowers to `Builtin(BuiltinMethod, [])`: a
method has no operand types in the callee, because each operand is a place or a constant
that has its own type. The receiver is the FIRST operand of the call, and it comes before
the arguments in the order of evaluation. Its convention is the answer of the receiver
partition (`receiver_convention`, 8.3). A family with no answer stops in `typecheck` or in
`borrow` first, so its call in lowering is the internal error CE0000, never a `Guaranteed`
receiver by default. The printer writes the call as
`builtin string.len(copy _1 @guaranteed)`. A method that answers a `string` writes it into a
fresh owning local like any other call, and the drop placement drops it (8.10). The emitter
gives each family the value of each operand, in order (Q7). The `array` family takes the
ADDRESS of its receiver instead, for an `Inout` receiver and a `Guaranteed` one alike: the
storage of the place, or a new slot that holds a constant (`operand_address`). It reads
the length, the capacity and the data of the array when it runs, after the last argument,
so a receiver is read after an argument that changes it, and an element receiver is
evaluated one time (11, rule 9). `get`, `first` and `last` of an array answer a view
(`answers_view`): over an owning element the answer goes into a view local of the receiver,
as for a List. `pop` and `remove` move the element out into a fresh `Maybe`, which owns it.
The bulk copies (`extend`, `extend_range`, `s`, `ss`), `fill` and `resize` borrow their
source and write a copy of an owning element into each slot over `copy_out`; `truncate` and
`clear` drop each slot that they remove. Four methods are the exceptions, and each lowers to no
call: `expect`, `realise` and `err` of a `Result` or a `Maybe` lower to a switch on the tag,
as an `assert` does (8.9), and `clone` of any type lowers to the rvalue `Clone` (8.7).

The `list` family takes the ADDRESS of its receiver in the same way. Its rows are
`LIST_EMITTERS` (`backend/generics/list/`): each row takes the address of the List, the type
of the List and the value of each argument (`emit_with_operands`,
`backend/generics/container_table.py`), so `push`, `get`, `reserve`, `contains`, `index_of`
and `index_of_from` read the List after an argument that changes it. No row evaluates an
argument: the element of `push` and of `insert` is an `Owned` operand, an index is an `i32`
value, and a needle is a `Guaranteed` operand. `first` and `last` hold their index rule in
the emitter (`0`, and `len - 1`, which the bounds check of an empty List refuses). `get`,
`first` and `last` answer a view (`answers_view`); over a plain element the destination is
an ordinary local and the answer is a copy (8.9), and over an owning element the answer is
a view (8.2) of the receiver. The name of a static called on a
List value (`xs.new()`) answers `~` and builds nothing. The `iter()` producer is a stop of
`borrow`.

The `hashmap` family takes the ADDRESS of its receiver in the same way. Its rows are
`HASHMAP_EMITTERS` (`backend/generics/hashmap/`), over operands (`emit_with_operands`): the
key and the value of `insert` are `Owned` operands that the map stores as they are, and the
key of `get`, `contains_key` and `remove` is a `Guaranteed` value. Each lookup and each
insert walks the buckets through the one probe loop (`emit_probe_loop`,
`backend/generics/hashmap/probe.py`) after the last argument, so an argument that changes
the map comes first (11, rule 9). An insert of a key that the map holds drops the old key
and the old value of the entry, and then stores the two new ones. `get` answers a view
(`answers_view`): the entry stays in the map, and over an owning value the destination is
a view local of the map. `remove` moves the value out into a fresh `Maybe`, which owns it,
and drops the key of the entry. `free` drops each entry and keeps an empty map, `destroy`
drops each entry and keeps no buckets, and `rehash` moves each entry into new buckets.
`HashMap.new` goes to the static row of the table. The producers `keys`, `values`,
`entries` and `pairs` are a stop of `borrow`.

Four rows of the `hashmap` family are the step of a `HashMapCursor` walk (8.9), one for
each view: `cursor_next.keys`, `cursor_next.values`, `cursor_next.entries` and
`cursor_next.pairs`. This text calls each of them `cursor_next`. Only SLIR lowering makes
them, and no program can name them: they are no methods of
`METHOD_TYPE_REGISTRY`, like `concat_in_place` of the string family (8.3). A step reads
the map (`InGuaranteed`) and writes the cursor (`Inout`), a hidden `i32` slot index. It
goes from the cursor to the next slot that holds an entry, writes the index after that slot
into the cursor, and answers `Some` of the item of the view (the key, the value, the entry
or the pair), or `None` after the last slot. The destination of the call is a view local of
the type `Maybe@(item)`. For a `peek` or a `poke` binder, which only `keys` and `values`
have, its type is `Maybe@(item*)`: the payload is a `PointerType`, the address of the key or
the value in its slot, and the binder is `Ref(Deref(payload), mode)`. The emitter reads the
form from the type of the destination. SLIR lowering interns this `Maybe` (the one wrapper that
lowering interns), because no written type and no call of typecheck names it. It records the
root of the map against the local of the answer (`Lowering.walk_owners`), so that the region of
a binder is over the map. Its emitter keeps the one probe loop
(`backend/generics/hashmap/probe.py`) and the one counted walk
(`backend/generics/container_walk.py`), as a `linkonce_odr` function that LLVM inlines.
So SLIR does not see the bucket array, its state per slot or its layout (Q7, the door).
This row is not built yet.

The `own` family takes the ADDRESS of its receiver too. Its rows are `OWN_EMITTERS`
(`backend/generics/own.py`). `get` loads the payload out of the heap block, a shallow copy
that SLIR holds in a view local of the `Own` when the payload owns (`answers_view`).
`destroy` destroys the payload, frees the block and writes a null pointer into the
receiver, so the drop at the exit of its scope, or at a rebind, finds nothing to free (the
destroy half of the lifecycle handler skips a null pointer). `alloc` called on an `Own`
value makes a new block, as `Own.alloc` does. `.clone()` is the rvalue `Clone` (8.7), whose
lifecycle handler copies the payload into a new block. `destroy` on a temporary is refused
in `borrow` ([CE2429](../error-catalog.md#ce2429)), because the write would land on a copy.

A `Stdlib(decl, module)` callee of SHIR lowers to `Direct(symbol, name, module)`. `name` is
the name of the function with no alias, and `module` is the `use` path of its module.
`symbol` is the generated function of its row (`stdlib_symbol`,
`semantics/stdlib_registry.py`, the one derivation): `sushi_<name>`, and
`sushi_<name>_<type>` for a member of a `<math>` family, at the type of its first argument
(#817). Every other `Direct` callee has no module. The printer writes the call as
`call @<math>.sqrt(copy _1 @guaranteed)`. The emitter gives a `Direct` callee with a
module to the row of its module in `STDLIB_EMITTERS` with the value and the type of each
operand (Q7); every operand is evaluated first. The `<math>` and `<time>` rows read the
row of the call (`stdlib_row`), declare the generated function of the symbol from that
row and call it (`emit_stdlib_row`, `backend/expressions/calls/stdlib/signatures.py`). A
`<time>` sleep answers a bare status, and the call builds its `Result` from it
(`status_result.py`: a negative status is `Result.Err`). A `cstr` parameter takes no
operand yet: the row of another module keeps its AST arguments, until the C-string argument
is ported with `emit_cstr_arg` at the call site.

An `Extern(decl, symbol)` callee of SHIR lowers to `Direct(symbol, name)`: `symbol` is the
link name, `name` is the name of the function with no alias, and it has no module. The
printer writes the call as `call @snprintf(copy _1 @inout, const 32_i64 @guaranteed, ...)`.
The declaration round declares each C function of the external table in each module, with
`var_arg` for a variadic one (`declare_user_externs`). The emitter finds the declaration of a
`Direct` callee with no module in the external table by its link name and its name
(`ExternalTable.by_link_name`), and it marshals each operand at the C boundary
(`backend/slir/externs.py`): a `string` crosses as a NUL-terminated copy that
`emit_cstr_arg` makes (`backend/expressions/calls/utils.py`, the one C-string seam), and the
emitter frees each copy right after the call; a byte buffer crosses as the pointer to its
first byte, read from the descriptor at its address (`Inout`, `InGuaranteed`) or from its
value (`Guaranteed`); a value of the variadic tail gets the C promotion (a narrow integer and
a `bool` to `i32`, an `f32` to `f64`). A C function that answers `~` writes `~` into the
place of the call.

A `Method(recv, decl, receiver)` callee of SHIR lowers to `Direct(symbol, name)`, and a
`Static(ty, decl)` callee too. `name` is the written name `<type>.<method>` of `decl`.
`symbol` is `method_symbol` of the type of the receiver (the type row of `recv` with no
`peek` or `poke`, or `ty`), the method and `ResolvedCall.unit`, so two units' private
methods of one name are two symbols (C5). A row with no declaring unit stops the lowering
(`slir, library function`). The receiver of a `Method` is the FIRST operand of the call,
with the convention `receiver` of the callee row, and the arguments come after it with the
conventions of their rows. A `Static` has no receiver. The printer writes the call as `call
@Counter.bump(copy _1 @inout, const 4_i32 @guaranteed)`. The emitter calls the function
that the declaration round declared for the symbol (`emit_extension_method_decl`): the
receiver of a `peek self` and a `poke self` method crosses by address, and every other
receiver by value, as the convention says.

An `Indirect(value)` callee of SHIR, a call of a function value, lowers to
`Indirect(Operand)`. The operand is the closure value: a `Copy` of its place, or a temporary
of the statement. The convention of each argument comes from the parameter modes of its
`FunctionType`, because the modes are part of the type: `typecheck` reads them through
`semantics/param_modes.py` (`CalleeKind.INDIRECT`) and writes the convention row of each
argument, as for a free call (7.3). The emitter loads `fn_ptr` and `env_ptr` from the value,
and passes the environment first, then each argument by its convention. SLIR does not see
the layout: the emitter reads the semantic type of the operand (8.7). The verifier checks
each `Convention` against the `FunctionType` of the operand (8.13). A named function as a
value is `Aggregate(AggClosure(ty), [FnRef(symbol)])`, with no environment (8.7), and
`materialize_function_ref` (`backend/runtime/closures.py`) becomes the emitter of that
case. A generic function as a value names the instance that its position gives (7.3). The
`Indirect` call of an `IteratorValue` walk has an iterator value as its operand (8.9). This
is not built yet: `typecheck` stops at a call of a function value (`typecheck, Call(fn
value)`) and at a function value (`typecheck, GlobalRef`), and the emitter stops at
`Indirect` (`emit, Indirect`).

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

**An array value.** The kind of an array is `AggArray(ty, runs)`. `ty` is the `T[N]` or the
`T[]` of the value, and `runs` holds one run for each run of the `ArrayInit`, in order:

```
ArrayRun = RunOne                 # one part: the value of one slot
         | RunRepeat              # two parts: the value, then the count
         | RunSequence(step: int) # two parts: the first value, then the count;
                                  # slot k holds first + step * k (an `i32` element)
```

The parts of the `Aggregate` are the parts of each run, in the order of the runs. A count is
an `i32`. A count below zero fills no slot: the emitter reads it as zero. Each count of a
`T[N]` is a constant, and the counts add up to `N`. A `T[]` with no run is the empty array,
`{0, 0, null}`. A plain element copies, so the emitter fills the slots with stores, a walk
or one `memset`, and a `T[N]` of constant parts with one store of its constant. Over an
owning element, the part of a `RunOne` is a `Move` or a constant: the slot takes the value.
The value part of a `RunRepeat` is a `Copy` or a constant, never a `Move`: the run BORROWS
its value, and the emitter writes a deep copy of it into each slot, over `copy_out` (the
seam of the `Clone` arm). A value that is a new value is a temporary of the statement, which
drops it at the end of the statement.

`Len(place)` is the length of a `T[N]`, a `T[]` or a `string`, as an `i32`. `Slice(place,
start, end)` reads a `T[N]` or a `T[]`: two `i32` operands, and `end` is exclusive. Its value
is a `T[]` descriptor that views the storage of `place`. **A VIEW** is a local that a `Slice`
writes, or a view local that a `Copy` writes (8.2): it owns nothing, so drop placement gives
it no drop, nothing moves it, no other statement writes it, and no statement copies it into
an owning local (8.13).

**`Clone(operand)`** is the ONE deep copy (ruling R-AC). Every copy of an owning value that
a read receiver answers is a `Clone`, and `x.clone()` of every type lowers to
`Clone(copy x)`. Its operand comes from the shape of the receiver: a receiver that is a
place gives `Clone(copy <projection>)`, a copy of the projection of the place. A receiver
that is a temporary gives no `Clone`: the value is a `Move` of the projection of the
temporary, because the temporary is spent whole (an enum payload has no sibling): the move
of the projection moves the temporary on that path (8.10, rule 3), so it gets no drop
there. The emitter has one arm for `Clone`, over `copy_out` (8.2).

`Ref(place, mode)` writes an address local (8.2), and nothing else: the address of `place`
with the mode `peek` or `poke`. It is the one write of that local.

A closure and an iterator have one layout, four words. `AggKind` keeps them apart, because
the backend reads the SEMANTIC type and never the layout to know which one it has
(`iteration.md`, ruling 10).

**A closure value.** The kind of a closure is `AggClosure(ty)`, and `ty` is the function
type of the value. The first part is the constant `FnRef` of the lifted function (8.5), and
one part follows for each capture, in slot order (8.9). The emitter allocates ONE heap block
for the environment, with a checked `malloc`, stores each part after the first into it in
slot order, and fills the four words `{fn_ptr, env_ptr, drop_ptr, clone_ptr}`. `drop_fn`
and `clone_fn` are the lifecycle pair of the environment struct type (`backend/lifecycle.py`,
the seam "Clone/destroy pair per type kind"): one `linkonce_odr` pair for each layout. SLIR
writes no glue. A closure with no capture has a null `env_ptr` and glue that does nothing.
The destroy of a closure value is an ordinary `Drop(place)`, which reaches that pair through
the one destroy seam. The lifted function reads a capture through the place
`Field(_1, k)` (8.9). This shape is not built yet: no lowering makes an `AggClosure`,
and the emitter has no arm for it.

**An iterator value.** The kind of an iterator producer or a lazy adapter is
`AggIterator(ty, source, heap)`. `ty` is the `Iterator@(T)` of the value. `source` names one
row of a CLOSED table of the producers and the adapters: a contiguous cursor, each view of a
`HashMap`, a range, `Iterator.new`, and each lazy adapter. Its parts are the operands that
the row reads (the source place, the bounds, the inner iterator, the function of an
adapter). `heap` is the answer of `OwnershipResults` (7.9), and the kind carries it, so the
emitter decides nothing: a LENT iterator that no `peek` return takes out of the function has
its state on the stack (`heap: false`), and an OWNED iterator, or a LENT iterator that
leaves through a `peek` return, has its state in a heap block (`heap: true`). The emitter of
the row builds the state and supplies `next_fn`, `drop_fn` and `clone_fn`. The producers of
`backend/runtime/iterators.py` (`iterator_value`, `emit_cursor_iterator`, `emit_next`) and
the adapters of `backend/runtime/iterator_adapters.py` are that table, over operands (Q7):
SLIR writes no glue. The `peek` return of a lent iterator (4.4, `borrow-model.md` S10h)
moves the WHOLE chain to the heap: each adapter of the chain reads the same `heap` answer.
The drop of an iterator value is a `Drop(place)` that calls its `drop_fn` (8.10, rule 7).
This shape is not built yet: `AggIterator` holds only `ty`, and no lowering makes it.

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
print values: [RE2020](../error-catalog.md#re2020) prints the index and the size. Each printf
conversion of the text takes one operand, in order, and the conversion gives the type of
that operand: `%d` takes an `i32`, and `%.*s` takes a `string`. The trap reads each operand
as a `Guaranteed` argument, so an operand is a `Copy` or a `Const` and never a `Move`, and
it drops nothing, because the program ends there. The trap
emitter prints a `string` operand with `%.*s` from its fields, the size and the data,
because a Sushi string has no NUL at its end. A text holds no conversion of another kind.

The trap of an `assert` is [RE2026](../error-catalog.md#re2026) (ruling R-U), and its text
is `assertion failed at %.*s%.*s`. The first operand is the position of the `assert`, a
`Str` constant: the file of the body as a diagnostic names it (`display_filename`, which
SLIR lowering reads one time for each body), the line and the column (`file:line:col`). The
second operand is the message. With a message, the position ends in `: `, and the operand of
the message is the value of the message expression. With no message, the second operand is
the empty string, and the position has no `: `. So the two forms print the text of the AST
backend: `assertion failed at f.sushi:3:5: the message`, and `assertion failed at
f.sushi:3:5` for a bare `assert`. A value that the compiler knows, such as the position, is
a constant operand: the text of RE2026 holds no `{name}` slot, because the backend would put
the file name in a printf format string.

The trap of `expect` is [RE2029](../error-catalog.md#re2029), and its text is
`expect failed at %.*s: %.*s`. The first operand is the position of the call, a `Str`
constant (`file:line:col`, with the file named as for an `assert`). The second operand is the
message, which the failing block evaluates (7.4). The full line is
`Runtime Error RE2029: expect failed at <file:line:col>: <msg>`, and the exit code is 1. The
trap prints its code and the position, as the trap of an `assert` does. This text is final.
It is the one user-visible change of the migration (11, rule 10).

The trap after the last arm of a `match` is [RE2023](../error-catalog.md#re2023), and its
text is `no match arm matched the value (expected %.*s)`. Its one operand is the source text
of that arm, a `Str` constant, as a diagnostic quotes it (7.8). The typecheck pass refuses a
match that misses a value (CE2040, CE2074), so no program reaches this trap: it is the
backstop of the checker, as on the AST backend.

### 8.9 SHIR to SLIR, construct by construct

| SHIR | SLIR |
|---|---|
| `If(c, t, e)` | `SwitchInt(c, [(0, else_bb)], otherwise: then_bb)`; with no `else`, the zero edge goes to the join, or to a block of its own that goes to the join when the condition has a local (see below). The blocks are made in the order `then`, `else` or the zero block, join; with no `else` and no condition local, the join is the zero target. With an `else`, the join is made after the two arms, and no join is made when no block reaches it |
| `Binary(and/or)` | **two blocks.** Short-circuit is control flow, never an rvalue. The left operand goes into the place that takes the value; a `SwitchInt` on it goes to the join (`and` on a zero, `or` on any other value) or to the right block, which writes the same place and goes to the join. The temporaries of the right operand die in its block |
| `Try(e)` | evaluate `e` into a temporary; `Discriminant` into a local; `SwitchInt` with `otherwise: None`; the `Ok` block moves the payload (`Downcast` + `Field`) into the place that takes the value; the `Err` block builds the `Err` into `_0`, runs the drops of every scope that it leaves and returns. A `Try` that calls a declared conversion is a stop of the lowering for now (`slir, conversion`) |
| `Try(e)` that reads through a borrowed owning wrapper (a `REJECT` row, 7.9) | `e` is a place, and nothing takes it: `Discriminant` of the place into a local and `SwitchInt` as above. The `Ok` block writes a view local (8.2) with a `Copy` of `Field(Downcast(place, Ok), 0)`. The `Err` block copies the error into the `Err` of `_0` with `Copy`, runs the drops of every scope that it leaves and returns: `borrow` refuses an owning error there (CE2411, `tests/borrow/try_on_a_borrowed_owning_result/`), so the error is plain |
| a let-borrow (an `Assign` with `is_init` and a `REJECT` row, 7.9) | a view local (8.2), written with a `Copy` of the place |
| `Try(Call(or_err, [e]))` over a borrowed `Maybe` (a `REJECT` row on the receiver, 7.9) | ONE named shape, with no call and no `Result` value: `Discriminant` of the `Maybe` place into a local and `SwitchInt` on it. The `Some` block writes a view local (8.2) with a `Copy` of the payload projection `Field(Downcast(place, Some), 0)`. The `None` block builds `Err(e)` into `_0` with the `Move` of `e`, runs the drops of every scope that it leaves and returns. The `Maybe` place is read first (`wrapper`: a get-out receiver goes into a view temporary), then `e`, before the switch; a named `e` and a field take go into a temporary of the statement, so the `None` path moves the temporary and the `Some` path drops it at the top of its block, the point where the call of `or_err` drops it on `main`. When the `Maybe` is a get-out of a temporary container, the `let` of the payload keeps the container in a hidden owner local of the block: the walk to it crosses each `??`, each get-out and each read-through `or_err`. Over an owned receiver, `or_err` is an ordinary `Builtin` call with an `Owned` receiver, and `??` is the ordinary `Try` (8.3) |
| a reference `let` (an `Assign` with `is_init` whose value is a `Borrow`, 7.5) | an address local (8.2): `_5 = ref poke <place>` (or `ref peek`), after a `BeginBorrow` of the root of the place with the same mode, to the last uses of its region row (7.9). A constant is first copied into a hidden local of the block that drops nothing, and the `Ref` reads that local |
| a `peek` or `poke` binder of a pattern, `Own(poke x)` included | an address local of the scope of the arm, written with the `Ref` of its payload place (for a variant, `Field(Downcast(place, variant), index)`; for the pointee of an `Own(...)`, `Deref(Field(place, 0))`), in a region over the root of that place, with the mode of the `Ref`, to the last uses of its region row (7.9) |
| `Return` in a function with a `peek` return | `_0 = copy <place>`: the value goes to the caller as it is (`borrow-model.md` S10h): it crosses by value, as a plain borrow does (the 16-byte descriptor of a `string` or a `T[]`), and not as an address. The callee never drops `_0` |
| a call of a function or a method with a `peek` return | the destination of the `Call` is a view local (8.2), in a region over the owner of each source argument (7.3, 7.9): a `let` reads the owners of its region row, and a temporary the root of the operand of each source argument. A source that is a constant has no owner, so a `let` of the answer of a call whose sources are literals has no `BeginBorrow`. One rule serves this answer and the view answer of a container call: the owner set has two sources. A `peek` return of a plain type answers an ordinary local |
| a call of a method of `answers_view` (`get`, `first`, `last` of a `List`; `get` of a `HashMap` or an `Own`) | a `Call` of `Builtin(BuiltinMethod)`; its destination is a view local (8.2) in a region over the owner of the receiver (7.9). When the element is plain, the destination is an ordinary local, and the answer is a plain copy |
| `Match` on an enum | when each arm is `_`, or a variant (or an `Or` of variants) whose items are binders or `_`: ONE `Discriminant` of the scrutinee into a local and ONE `SwitchInt` on it. Each variant of an arm is one case of that arm, at its tag, and `otherwise` is the `_` arm, or `None` when the arms cover the enum. Else a chain of tests in source order (see below). A binder reads its payload value through `Downcast` + `Field`. An `Own(...)` item tests nothing: its pointee is at `Deref(Field(place, 0))`, and a test or a binder inside it reads that place |
| `Match` on an integer | with only literal arms: ONE `SwitchInt` on the value. Each literal alternative of an arm is one case of that arm, at the value that the type of the scrutinee reads (`semantics/integer_patterns.py`: lowering calls `literal_value` and `pattern_interval` over the written constant and the scrutinee type, and reads no row), and `otherwise` is the `_` arm, or `None` when the arms cover the type. With a range arm: a chain of tests in source order (see below). A literal is a `SwitchInt` on the value with one case; a range is two compares, `>=` its low bound and `<=` its high bound, each into a `bool` local and a `SwitchInt` on it |
| `Match` on a string | a chain of tests in source order (see below): each a `Call` of the intrinsic `eq` (8.6) into a `bool` local, and a `SwitchInt` on it |
| `Match` on a tuple | always a chain of tests in source order (see below). The parts of a tuple pattern are at the places `Field(place, index)` of the scrutinee, and each part is tested by the rule of its own kind: a literal, a range, a variant, a nested tuple; a binder and `_` test nothing. A place that owns is read where it is, and a new value is a temporary of the match statement, which drops it at its end. A tuple LITERAL that owns builds no tuple (ruling 3 of `tuples.md`): each part is evaluated once, from left to right, as a scrutinee of its own (a place is read where it is, a new value and a part that the match takes go into a temporary of the statement), and the parts of a tuple pattern are at the operand of each part |
| `Loop`, kind `While` | header block, `SwitchInt`, body, `Goto` header. The blocks are made in the order header, the blocks of the condition (an `and` or an `or` in it), body, the zero block (when the condition has a local, see below), exit |
| `Break`, `Continue` | the drops of each scope of the loop body that it leaves (8.10, rule 2), then `Goto` the exit, or for a `Continue` the next round: the header of a `While`, the step of a `CountedRange` |
| `Loop`, kind `CountedRange` | in the scope of the loop statement: each bound one time, the start first, into an `i32` local (a constant stays a constant), the count `end - start` into a local (plus one when inclusive), and a counter local at 0. The header compares `counter < count` into a `bool` local and switches; a count below one walks nothing, so the count needs no clamp. The body writes the binder first: `start + counter`, or with `.rev()` `last - counter`, where `last` is `end` (less one, into a local, when exclusive). The step block adds one to the counter and goes to the header; the end of the body and a `Continue` go to the step. Two constant bounds take the count and the first value of `plan_range` (`semantics/ranges.py`) as constants. The zero edge goes to a block of its own as for a `While`. The blocks are made in the order header, body, step, the zero block, exit |
| `Loop`, kind `Cursor` | `Len` into a local, then the `CountedRange` shape |
| `Loop`, kind `HashMapCursor` | the `Protocol` shape with a built-in step: a hidden cursor local (an `i32` slot index, `0` at the entry), and each round a `Call` of `Builtin(hashmap cursor_next)` (8.6, the row of the view) with the map `InGuaranteed` and the cursor `Inout`, into a view local of the type `Maybe@(item)`, or `Maybe@(item*)` for a `peek` or a `poke` binder; then `Discriminant` and `SwitchInt`. The `Some` block writes the item into a view local (8.2), in a region over the map (7.9), so a change of the map in the body is CE2412, as for a `Cursor`. A `peek` or a `poke` binder is an address local, with one `BeginBorrow` over the map and one over the `Maybe` local, because the rule of an address local (8.13) reads the root of the place of its `Ref`. The walk has the block shape of the other walks, and it has no bucket layout and no second CFG |
| `Loop`, kind `Protocol` | the iterator in a hidden owning local (or an `Inout` place, for a walk in place); each round calls `next()`, `Discriminant`, `SwitchInt` |
| `Loop`, kind `IteratorValue` | the same shape: each round an `Indirect` call whose operand is the iterator value. The emitter reads the semantic type of the operand, an `Iterator@(T)`, and calls its `next_fn` with its state (`emit_next`). This is not built yet: the walk of an iterator stops before SLIR (`typecheck, Loop` or `borrow, Loop`) |
| an iterator producer or a lazy adapter | ONE `Aggregate(AggIterator(ty, source, heap))` (8.7) into a fresh owning local: `source` is the row of the producer or the adapter, and `heap` is the heap decision of `OwnershipResults` for the `shir_id` of the call. The emitter of the row builds the state and supplies the three functions. This is not built yet |
| `Range` as a value | an owned iterator: an `AggIterator` of the range row, with its bounds as parts and `heap: true`. This is not built yet: `typecheck` stops at it (`typecheck, Range`) |
| `Interp(parts)` | ONE `Builtin(Intrinsic("interp"), types)` call into a fresh owning local, with one `Guaranteed` operand for each part in order (an empty text part gives none). The backend expands it with the string-builder emitters (Q7), so the builder state stays out of SLIR |
| `Lambda` | one `SlirFunction` cut from the body (Q8), with the symbol of `LiftResults`: its environment is `_1`, with the convention `InGuaranteed`, and its declared parameters come after it. A capture is the place `Field(_1, k)`, where `k` is the slot of the capture. Then ONE `Aggregate(AggClosure(ty))` (8.7) into a fresh owning local: the first part is the constant `FnRef` of the symbol, and one part follows for each capture of `LiftResults`, in slot order, a `Copy` or a `Move` of the captured local by the `Ownership` row of the capture (7.9). A lambda with no capture has the `FnRef` alone. This is not built yet: `typecheck` stops at a `Lambda` (`typecheck, Lambda`) |
| `ArrayInit(runs)` | each value in the written order, as for a `StructInit` (a place that a later call can change goes into a temporary first), then ONE `Aggregate(AggArray)` with its runs (8.7): a run-time count is a part; a range with two constant bounds takes the count and the first value of `plan_range`. The emitter fills the slots: unrolled stores, a constant walk, a run-time walk, one `memset` for a repeated constant byte, and one constant for a `T[N]` of constant parts. A count below zero fills nothing (`semantics/slir/lower_arrays.py`, `backend/types/arrays/runs.py`). Over an owning element, a `Single` value is a `Move` or a constant, and a repeated value is a `Copy`: a place of an owning value stays where it is (a temporary would drop a copy that it does not own), and a new value is a temporary of the statement, which drops it at its end. The emitter writes a copy of the repeated value into each slot over `copy_out` |
| `Index` read | the index into a local of the statement, `Len` (or the constant `N` of a `T[N]`), the `u32` casts of the index and of the length, `<`, `SwitchInt`, `Trap("RE2020", [index, length])` on the failing edge; then the place `Index`. No check when `unchecked`, nor for a constant index of a `T[N]` that is in range. An owning element in a `let` is a view local (8.2) in a region over the array; in a borrowed position (a `Guaranteed` argument, a part of an interpolation) it is a `Copy` of the place |
| the concat peephole: a rebind `s := s.concat(x)` of a local whose old value the function holds, where the receiver of `concat` is the local itself | `Call(Builtin(string.concat_in_place), [(copy s @inout), (x @guaranteed)])` into a `~` temporary, and NO `Drop` of the old value: the emitter grows the buffer of `s` in place when `s` owns it, and copies it when `s` does not (`string_append`). The argument is evaluated first, and the call reads `s` after it: when the argument rebinds `s` through a `poke`, the append reads the new value of `s`. The local must hold a value of its own on the straight line: a live local, an `Inout` parameter or a `poke` address local. A local whose state is unknown or moved (a `Guaranteed` parameter before its first rebind) takes the general path (rule 6 of 8.10), and so does a local over which a borrow region is open at the rebind, and a field or an element target (`semantics/slir/lower_rebind.py`) |
| `Index` write (`arr[i] := v`) | the value first (a value that is no constant goes into a temporary), then the index and the same bounds check, then the store into the place `Index`. Over an owning element, rule 6 of 8.10: then `Drop(Index(place, i))` of the old element before the store, which moves the temporary (`part_write`, the drop from `drops.py`) |
| `Slice` | the bounds into locals of the statement (an inclusive end plus one), `end <= len` and `start <= end` as `u32` compares, `&`, `SwitchInt`, `Trap("RE2028", [start, end, length])` on the failing edge, then the `Slice` into a view local; never unchecked. A check that the constant bounds prove is left out |
| a method of an array | a `Call` of `Builtin(BuiltinMethod)`: the receiver first (a `Copy` of its place, a constant, or a temporary of the statement), then each argument with the convention of its row (8.3). The emitter gives the family the address of the receiver (8.6), and one emitter of the family serves a `T[N]` and a `T[]` (`backend/types/arrays/dispatcher.py`) |
| `Assert` | `SwitchInt(c, [(0, fail_bb)], otherwise: next_bb)`. The failing block evaluates the message in a scope of its own and ends in `Trap("RE2026", [position, message])` (8.8); its temporaries get no drop. The next block goes on with the state of the locals before the failing block. The blocks are made in the order fail, next. A condition local ends at the top of the two blocks, as for an `If` |
| `expect(msg)` on a `Result` or a `Maybe` | the receiver into a place (a constant into a temporary), `Discriminant` into a local, and `SwitchInt(tag, [(success tag, next_bb)], otherwise: fail_bb)`. The failing block evaluates the message in a scope of its own and ends in `Trap("RE2029", [position, message])` (8.8); its temporaries get no drop. The next block goes on with the state before the failing block, and reads the payload through `Downcast` + `Field`: a `Copy` of a plain payload. An owning payload of a receiver that is a place is a `Clone(copy <projection>)`, because the receiver is a read (8.3); of a receiver that is a temporary it is a `Move` of the projection, which moves the temporary (8.7). A new value as the receiver is a temporary of its statement, which drops it where it is not moved. The blocks are made in the order fail, next |
| `realise(default)` on a `Result` or a `Maybe`, and `err()` on a `Result` | lowered as `expect` is: the receiver into a place, `Discriminant` into a local and `SwitchInt` on the tag; no call. `realise` writes the payload on the success path (`Downcast` + `Field`) and the default on the other path; `err` writes `Some` of the error on the `Err` path and `None` on the other path. A plain value is a `Copy`. An owning value of a receiver that is a place is a `Clone(copy <projection>)`, and of a receiver that is a temporary a `Move` of the projection, which moves the temporary on that path (8.7). The default of `realise` is a `Move` of its temporary, or a `Clone` of a place. Each copy stands on its own path, because on the other path the payload field holds the bytes of the other variant. A temporary that one path takes (the receiver on the success path of `realise` and on the `Err` path of `err`, the default on the other path of `realise`) drops on the other path, and a temporary receiver that owns drops at the read on each path that moves nothing out of it, as the receiver of a `nom self` method does, so the paths meet with it moved (8.10, rule 3). This point is visible for an error type or a payload type with a user `Drop`. Each path writes one temporary of the statement, which is the value of the call. A copy of an error goes into a temporary of its path, which the `Some` takes |
| `x.clone()`, of any type | the rvalue `Clone(copy x)` (8.7) of a place, of a constant and of a view (a get-out goes into a view local first); no call. A receiver that is a temporary is the copy itself: `make().clone()` is the value of `make()`, and a chain `a.clone().clone()` makes one `Clone` (`semantics/slir/lower_copies.py`) |
| `StructInit`, `TupleInit` | each field (each part) in the WRITTEN order (R1, 7.4), then ONE `Aggregate(AggStruct)` (`Aggregate(AggTuple)`) of the instance type, with the parts in the declaration order. A new value goes into a temporary of the statement in that order. A place, or a constant, is read at the `Aggregate`. A place is copied into a temporary first when a later field in the written order can call a function, because the call can change the place through a `poke`; a literal, a name of a constant, a place, an operator and a construction over such values call none. A literal and a name of a constant never change, so they get no temporary. A part that owns is a `Move`: of a local by the `move` row of `borrow`, or of the temporary of a new value; a constant stays a constant. A place of an owning part is never copied into a temporary. The operands of a binary operator, of a call (a receiver by value, then each argument by value), of an enum construction and of an interpolation take the same rule: a place of a plain value goes into a temporary first when a later operand can call (`settled`, `semantics/slir/lower_aggregate.py`) |
| `Field` read | a place: `Copy` of `Field(place, index)`, with the index of the typecheck row; a field of a field is a projection of the projection. A base that is no place (a new value, a constant) goes into a temporary of the statement first, and the field is a `Copy` of the projection of the temporary; the statement drops the temporary at its end (rule 2). An owning field in a `let` is a view local (8.2) in a region over the root of the place, and a view of a temporary keeps the temporary in a hidden owner local of the block; in a borrowed position (a `Guaranteed` argument, a part of an interpolation) an owning field is a `Copy` of the place |
| a field write (`p.x := v`) | the new value first, then the store into `Field(place, index)`. A new value that is `and` or `or` goes into a temporary first, because its left operand is written before its right operand reads the place (rule 6). Over an owning field, rule 6 of 8.10: the new value goes into a temporary unless it is a constant, then `Drop(Field(place, index))` of the old field before the store, which moves the temporary (`part_write`) |
| `Destructure` | one `Field` place per target, at its path: a binder is a new local of the block, written with the `Copy` of its element. A `let` form reads a place of the value where it is; any other value goes into a temporary of the statement first. For `Place` targets the value always goes into a temporary first, then each place from left to right is rebound (rule 6 of 8.10): a local, or a field, as a field write. `_` writes nothing. Over a value that owns, the row of `borrow` (7.9) decides (`semantics/slir/lower_takes.py`). An OWNED value (`MOVE` or `ADOPT`): the place of each `Place` target is evaluated first, from left to right (its indexes and their checks), then each binder and each place of a plain element takes the `Copy` of its `Field` place, then each `_` over an owning part drops that part, then each binder and each place of an owning element takes the `Move` of its `Field` place: a local drops its old value first (rule 6), and a field or an element drops its old part. So the moves of the parts of the one root stand last in one block (8.13), and the value is spent whole and gets no drop (8.13, "no local is ever partially moved"). A destructure that moves no part keeps the value whole in its temporary, which drops at the end of the statement. An owning binder gets its flag after its write. A BORROWED value (`REJECT`) is read where it is (a view of a temporary keeps the temporary in a hidden owner local of the block, as a `let` does), and each binder of an owning element is a view local (8.2), written with the `Copy` of its `Field` place, in a region over the owners of its region row, or over that hidden owner. A plain element is a `Copy` in both cases. A `Place` target of an owning element that is a `Guaranteed` parameter drops its old value only when the callee owns it (8.10, rule 5), and one that is a `poke` address local drops the old value at its address (rule 8), as its rebind does |
| `StorageRef` (a `var`) | the place `Global(symbol, ty)`: the symbol of the global row of typecheck, and the declared type of the `var`. A read by value goes into a temporary of the statement first, because any call of the statement can change a `var` (`counter + bump()` reads `counter` before the call); a by-address argument, a receiver and a value that its statement stores at once read the `Global` where it is. A slice of a `var` is a view (8.7), which owns nothing: its read gets no temporary. A rebind is an `Assign` into the `Global`, and a field write and an element write an `Assign` into its projection; a plain `var` drops nothing. A `var` that owns stops the lowering (`slir, StorageRef owning`) |
| `Take` | with a `move` row (on the `Take`, or on the `Field` of a `nom` argument): the operand is the `Move` of `Field(local, index)`, and the `Drop(Field(local, i))` of each other owning field of the receiver stands right before the statement that consumes the operand (the call or the store, 8.10 rule 3), after every other operand of that statement: an exit before it (the error path of a `Try` in a later argument) finds the local whole and drops it whole. Two or more takes of one local in one statement (`f(nom w.a, nom w.b)`, `Pair(nom w.a, nom w.b)`) are one take: the local moves one time, and a field drops only when no take of the statement moves it. The `Move` spends the whole local (8.13): it gets no drop, so `drop()` does not run (`borrow-model.md` §10c; `semantics/slir/lower_takes.py`). A `Take` with any other row is its place |
| a variadic tail | folded into one synthesized owned array argument (7.4) |
| a `string` argument to an `i8*` callee | a `Guaranteed` operand of the call. The emitter makes the C copy at the call (`emit_cstr_arg`) and frees it right after the call (8.6): the copy lives for the call alone, so no SLIR local holds it and no drop of 8.10 reads it. `run()` stays the one exception |

A condition (of an `If` or a `While`) is evaluated in a scope of its own, whose
temporaries die before the `SwitchInt`. A condition that is no local and no constant goes
into a `bool` local: it gets `StorageLive` before its first write, and `StorageDead` at the
top of each block that its `SwitchInt` goes to (the rule of rustc); an exit inside the
condition (the error path of a `Try`) ends it too. So each target of that `SwitchInt` is a
block that only the switch reaches: an `If` with no `else` sends its zero edge to a block
of its own that goes to the join, and a `While` sends its zero edge to a block of its own
that goes to the exit, which a `Break` reaches too. Only a local that its own block writes
and that the terminator of that block alone reads gets no storage marker: the local of a
`Discriminant`, and the `bool` local of a test of a `match`. The locals of a bounds compare
follow the same rule: the `u32` casts of the index and of the length, and the `bool` that
the switch reads, are written and read in their own block alone.

A declaration cannot write a field of a reference type
([CE2415](../error-catalog.md#ce2415)), and a generic instance over a borrow has one:
`Box(peek x)` gives `T` the type `peek i32`, and `Maybe.Some(peek x)` gives a payload that
type. A marked part is no consuming position: `borrow` borrows its place for the statement,
and a part that is no place is [CE2404](../error-catalog.md#ce2404). In SLIR the part of an
`Aggregate` at such a field or payload is the operand of the place, at the type of the place,
and the emitter stores the address of the place (`_part`, `backend/slir/values.py`). No
address local holds it, because a `Copy` of an address local reads the value behind it. A
read of the field through a method or an operator has no arm, as on `main`.

A `Match` evaluates its scrutinee in the scope of the match statement. A place (a local, a
field, an element) is read where it is, with `Copy` in each test. A new value, and a scrutinee that the match takes (`consumes`), go into a
temporary of the statement: each test reads the temporary, and the statement drops it at its
end, or at the exit of an arm (a `return`, a `break`, a `continue`). A constant of an enum
goes into a temporary too, because a `Discriminant` reads a place.

The pattern of an arm gives one or more PATHS. A path is the list of the tests of the
pattern, depth first and from left to right, and the binders that it writes, each with its
place. An `Or`, at any depth, gives one path for each alternative. The test of a variant is a
`Discriminant` of its place into a local and a `SwitchInt` on that local with one case, the
tag of the variant; the tests of its items follow, each at the place
`Field(Downcast(place, variant), index)`. A tuple pattern tests nothing itself; the tests of
its parts follow, each at the place `Field(place, index)`. The test of a literal or of a
range is the test above, on a `Copy` of its place, at the type of that place. A binder and
`_` test nothing.
In a chain, a failed test goes to the first test of the next path of its arm, else to the
first test of the next arm; after the last arm it goes to the trap RE2023 (8.8). An arm with
a path of no test (`_`, or an arm with a `_` alternative) has no test, and it is the last arm
(CE2041, CE2118).

The binders of an arm are locals of the scope of its body. A bare binder gets `StorageLive`
and the `Copy` of its place: over a plain payload it is an ordinary local, and over an
owning payload it is a view local (8.2), which gets no drop: its region is over the root of
the place of the scrutinee (the temporary of a new value, or the part of a tuple literal),
from the write of the binder to the last uses of the region row of `borrow`, and a binder
with no last use has no region. A `nom` binder of an owning payload is an owning local of
the scope of the arm, written with the `Move` of its place, and its flag follows its write;
the arm takes the variant whole, so its root (a temporary of the match statement) is moved
one time for all the `nom` binders of the path (8.13). A path writes its other binders first
and its `nom` binders last, so no binder reads a part of the root after the take (8.13). A
`nom` binder of a plain payload is a `Copy`. An arm that takes nothing out of a temporary
that another arm takes drops that temporary at its end, after the exit of its scope
(`drop_on_path`), so the arms that reach the join leave it moved; an arm that leaves by a
`return`, a `break` or a `continue` drops it by its state. A view local, an address local
and the owning local of a `nom` binder have ONE write on each path (8.13). So in an arm whose
two or more paths write a binder of an owning payload, or a `peek` or `poke` binder, each
path writes the binder one time, in the block of that path, and the paths join at the arm
body, which is lowered one time. The region row of the binder (7.9) has the same root, the
scrutinee, on each path: each path writes the `BeginBorrow` of each owner of the region, with
ONE id for the region on every path, before its write, so the paths that meet at the arm
body bring the same open regions (8.6). A `nom` binder in an alternative takes the variant
whole on its path, as a plain arm does. This is not built yet: such an arm stops (`Match
owning alternatives`). With one path, the block of the body writes the binders first.
With two or more paths that write a binder, each path ends in a
block of its own that writes the binders of that path and goes to the body. Each arm body is
a scope of its own, and each arm starts from the state of the locals after the scrutinee.
The arms that reach their end meet at the join, as the blocks of an `If` do. The first test
is in the block of the scrutinee. The blocks are made in source order: for each arm of a
chain, the blocks of its tests after the first, the blocks of its paths that write binders,
the block of its body, then the block where its last test fails (the first test of the next
arm, or the trap); the blocks inside the body of an arm come after these. With one switch,
for each arm in source order, the blocks of its paths that write binders, then the block of
the arm. The join is made after the arms, and no join is made when no arm reaches it.

Four SHIR nodes vanish at this boundary: `Try`, `Interp`, `Destructure` and `Take`. They
were kept in SHIR only so that `typecheck` and `borrow` could report against the source
(G7). `Expand` never reaches it.

### 8.10 Drop placement

Eight rules. They encode the two drop regimes (a local moved on every path has no drop; a
local moved on some paths has a drop flag), the rebind order of 4.6, the loop's own
locals, and the locals that own nothing, and nothing else:

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
   This is a stated change from the AST backend (ruling R-AU) in three cases. The AST
   backend kept a discarded call result (`xs.pop()` as a statement) to the exit of the
   scope. It dropped the temporary receiver of a method call in an interpolation with one
   hole (`println("{mk().is_some()}")`) before the print. It kept the temporary receiver
   of a method call in a `let` initializer (`let i32 x = mk(3).ident() + mk(4).ident()`),
   and in an interpolation with two holes or more, to the exit of the scope. SLIR lowering
   drops each of them at the end of the statement. The receiver of `realise` and of `err`
   is no such temporary: the call takes it, so it drops at the read on each path that moves
   nothing out of it (8.9), before the rest of the statement runs.
3. A local that is moved at an exit gets no drop at all. SLIR lowering tracks the state of
   each owning local along its own walk, and the `move` rows of `borrow` are the ONLY
   source of a move: lowering never classifies a use again. A `move` row at a `LocalRef`
   of the local (`move LocalRef(<local>)`) or at a capture of it marks the local moved at
   the statement that consumes the operand (the call or the store), never where the
   operand is built: an exit between the two (the error path of a `Try` in a later
   argument) still drops the local. The same holds for a temporary that is read with
   `Move`, and a move of a projection (`move ((_3 as Ok).0)`, a field take `move (_3.0)`)
   moves its local. Where one path of `realise` or `err` moves a projection of a temporary,
   the other path drops the temporary and marks it moved (`drop_on_path`), so the paths
   meet with one state, and so does an arm of a match that takes nothing out of a
   temporary that a `nom` binder of another arm takes (8.9). The
   declaration of a local and each rebind of it mark it live. At an exit, a live local gets
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
   the entry for an `Owned` parameter and `false` for a `Guaranteed` one (rule 5). The flag is set `true` right after the statement that declares its local,
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
5. A borrow never drops. `Guaranteed`, `InGuaranteed` and `Inout` parameters never drop
   the value of the caller; an `Owned` parameter drops in the callee. A `Guaranteed`
   parameter starts NOT OWNED, in the state "moved" of rule 3: a rebind of it (rule 6)
   makes it owned, and from there it drops at each exit as a local does. Where a path that
   rebound it meets a path that did not, it has a `cond_moved` row and a flag (rule 4),
   which is set `false` at the entry.
6. An `Assign` to an owning place that may hold a live value drops the OLD value, and the
   order is normative (4.6): compute the new value FIRST, then `Drop` the old, then store.
   The source may alias the value about to be freed. A rebind through a `poke` parameter
   follows the same rule, through the pointer. The state of rule 3 decides whether the old
   value is live: a live one drops and a moved one drops nothing, and the store makes the
   local live. When the old value is live, the new value goes into a temporary first,
   unless it is a constant, and the store moves the temporary (`_2 = ...`, `Drop(_1)`,
   `_1 = move _2`). A new value that is `and` or `or` always goes into a temporary first,
   because its left operand is written before its right operand reads the old value. A
   rebind of a `Guaranteed` parameter drops the old value only when the callee owns it
   (rule 5): the first rebind drops nothing, because the old value is the caller's. A
   rebind of an `InGuaranteed` parameter does not reach SLIR: `borrow` refuses it. The
   concat peephole (8.9) is the one rebind with no `Drop` of the old value: the append of
   the string family takes the old value in place. The
   write of an owning field or element (`p.x := v`, `a[i] := v`) follows the same order:
   the new value into a temporary, unless it is a constant; then each index of the place
   and its bounds check; then `Drop(Field(p, i))` or `Drop(Index(a, i))`; then the store,
   which moves the temporary. A field and an element always hold a value, so the old part
   always drops.
7. The hidden iterator local of a `Protocol` or an `IteratorValue` loop, and the item that
   the loop owns, are owning locals of the loop scope under rules 1 to 4: the end, a
   `break`, a `return` and the `??` of a binder all reach a `Drop`. A walk in place has no
   hidden local and drops nothing. A lent iterator is still dropped: it borrows its source
   and owns its value, and its `drop_fn` frees only what the state owns.
8. A view local (8.2) never drops, at an exit and at a rebind alike: it owns nothing, and
   its owner drops the value. It gets `StorageLive` and `StorageDead` by rule 1, and no
   flag. The `_0` of a function with a `peek` return holds a view too, and the callee never
   drops it. An address local (8.2) never drops either: its storage is the place of
   another value. A write through a `poke` address local follows rule 6 through the
   address, as a rebind through a `poke` parameter does.

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
| iterator state placement read from two stamps | the `heap` field of `AggIterator`, written at lowering |

The backend has none of the concerns of the left column: each one went with the AST body
emitter.

The borrow-region row comes from the region row of `OwnershipResults` (7.9): `borrow`
decides the owner set and the last use of each view, and SLIR lowering writes the
`BeginBorrow` and the `EndBorrow` statements from that row (8.6). The iterator-state row
comes from the heap decision of `OwnershipResults` (7.9): SLIR lowering writes it into the
`heap` field of `AggIterator` (8.7), and the emitter of the row of the source builds the
state on the stack or in a heap block. This row is not built yet: no lowering makes an
`AggIterator`.

What is NOT in this table: the 2,809 lines of monomorphization. Ruling Q1 leaves them
where they are. Also not in the table: the per-type emitter families. Ruling Q7 keeps them.

### 8.13 The verifier

Runs after lowering, before the backend. It checks:

- every local is typed, and every `Place` type-checks against it
- every `Downcast` names a real variant of the place's enum
- no use of a place after a `Move` of it, on any path
- no read and no `Move` of a local that holds no value on a path: before its first write,
  or after its `StorageLive` or its `StorageDead`
- no `Move` of a place that holds an `Index`: an element never moves (8.4)
- a `Drop` of a part (a place whose outer projection is a `Field` or an `Index`): its root
  is a local that is no view and that holds a value on every path, and the type of the part
  owns. It does not change the state of the root. A `Drop` of any other place that is no
  local, a `Drop` of a part of a global, and a `DropIfSet` of a place that is no local, are
  not checked yet (CE0000)
- a `Move` of a sub-place (a `Field`, a `Downcast`) is a `Move` of its root local, and
  no local is ever PARTIALLY moved: lowering drops the sibling owning fields first (8.9,
  `Take`), or the sibling payloads of a pattern take, because an arm takes the variant
  whole (`borrow-model.md` S10b); a `Try` payload has no sibling. So the root is spent
  whole, and the owning-local rule below reads the root. One take can move two or more
  parts of one root: the binders of a `Destructure` of an owned value, two or more
  `nom` binders of one path of a pattern, and two or more field takes of one local in one
  statement. So a `Move` of a sub-place of a root that a
  `Move` of another sub-place moved EARLIER IN THE SAME BLOCK is legal when neither place
  is a projection of the other (the parts are disjoint). A write, a `StorageLive`, a
  `StorageDead` or a `Drop` of the root ends that permission, and so does the end of the
  block. Any other read or move of the root after a part move is a use after a move
- no `Move` out of a `Global`
- every block ends in exactly one terminator, and every `BlockId` exists
- every `Drop` targets an owning type
- every owning local reaches exactly one of: a `Drop`, a `DropIfSet`, or a `Move` on
  every path. A `Guaranteed` parameter that owns starts with the value of the caller
  (8.10, rule 5): it is read, and nothing drops it or moves it while it holds that value;
  a write gives it a value of its own, and from there the rule holds for it
- `_0` holds a value on every path to a `return`
- a `peek` return: a `Copy` of an owning value other than a `string` into `_0`, as the
  value or as a part of an aggregate, only when `_0` is a view local, that is, when the
  declaration of the function returns `peek`; and then no `Drop` of `_0` in the function.
  This is the one owning place that a view can be copied into. A `string` is the carve-out
  of the `Owned` argument: a string bound directly from a literal gets `ADOPT` and owns no
  heap (7.9). So the verifier passes a `Copy` of a `string` into a `_0` that is no view
  local, and `borrow` refuses the return of a borrowed string (CE2411)
- every `BeginBorrow` has exactly one matching `EndBorrow` on every path
- a borrow region: no `Move`, no `Drop`, no write and no `Inout` argument of the place of
  an owner while a region over it is open. A change of the owner after the `EndBorrow` is
  legal
- every argument `Convention` matches the callee's declared parameter, with one
  carve-out: the tail of a `var_arg` extern has no declared parameters, so the rule
  applies to the declared prefix only. For a `Builtin` callee of a container method, the
  declared parameters are the answer of `receiver_convention` for the receiver and of
  `argument_conventions` for each argument (8.3). For a `Builtin` callee of a `Result` or a
  `Maybe` method, they are the answer of `receiver_convention` for the receiver and the
  mode of the row of the method for each argument: `map_err` and `or_err` take the
  receiver `Owned`, the error value of `or_err` is `Owned`, and the function of `map_err`
  is `Guaranteed`. For an `Indirect` callee, they are the parameter modes of the
  `FunctionType` of the operand (8.6); this part is not built yet. For the
  `concat_in_place` row of the string family (8.3, 8.9), the receiver is `Inout` and the
  one argument is `Guaranteed`. For the `cursor_next` row of the
  `hashmap` family (8.6, 8.9), the map is `InGuaranteed` and the cursor is `Inout`; this
  part is not built yet
- no `Copy` of an owning value into an `Owned` argument, except a `string` (7.9)
- a call of a `BuiltinMethod` has a receiver operand, the first operand
- no `Binary` carries `and` or `or` (8.9)
- a switch on an integer: the operand of a `SwitchInt` is an integer or a `bool`, and each
  case value is in the range of its type, with no value two times
- a switch on a tag: when the operand of a `SwitchInt` is the local that a `Discriminant` of
  its own block writes, each case value is the tag of a variant of that enum
- an enum value: a `Discriminant` reads a place of an enum type, and an `Aggregate` of a
  variant, an rvalue or a constant, names a variant of its enum type and gives one part for
  each value of its payload, of the type of that value
- a struct value: an `Aggregate(AggStruct)` or an `Aggregate(AggTuple)` rvalue, and a constant
  `Aggregate` with no variant and of no array type, is of a struct type and gives one part for each field, of the
  type of that field. One check of the parts serves an enum value and a struct value
- an array value: an `Aggregate(AggArray)` is of a `T[N]` or a `T[]` type and gives the
  parts of each run (8.7): a value of the element type, an `i32` count and an `i32` first
  value of a sequence, whose element is an `i32`. The value of a `RunRepeat` is never a
  `Move`: the run borrows it. The counts of a `T[N]` are constants that add up to `N`. A constant `Aggregate` of a `T[N]` gives one part for each element, of the
  element type
- a length and a slice: `Len` reads a `T[N]`, a `T[]` or a `string`, and `Slice` reads a
  `T[N]` or a `T[]` with two `i32` bounds
- an index: the index of an `Index` place is an `i32`
- a view: a local that a `Slice` writes is of a `T[]` type, no other statement writes it,
  and nothing moves or drops it. No statement copies a view into an owning local: that
  local would drop storage that the view does not own
- a view local (8.2): a `Copy` writes it, ONE time on each path, and no path writes it
  two times: the check counts the writes on each path with the flow state that it has, and
  two paths that each wrote it one time can meet (the binder of an alternative, 8.9). The
  `_0` of a `peek` return is written one time on each path to a `return` (the rule of the
  written return slot). A view local with one write on each path has the regions that name
  it on each path. The count on each path is not built yet: today the check
  refuses a second write of a view local anywhere in the function. A view local of an
  owning type is never the place of a `Drop` or a `DropIfSet`, never `Move`d, and never the
  operand of an `Owned` argument. Its owners stand in a region (8.6) at each read of it.
  The regions of a view local are the `BeginBorrow`s that name it (`view`, 8.6): the check
  reads the view from the statement, never from its position, so a statement between a
  `BeginBorrow` and the write of its view changes nothing. A view local that no
  `BeginBorrow` names reads a value that no local owns, and has no region. A region has one
  view: two `BeginBorrow`s of one region that name two different views, and a `BeginBorrow`
  whose view is no view local of the function, are CE0158 of this check (the rule `a view
  local`, `VIEW_LOCAL` of `verify_rules.py`, which `verify_regions.py` reports)
- an address local (8.2): ONE `Ref` writes it on each path, as the writes of a view local
  are counted, and a `Ref` writes a local, never a part of one. The count on each path is
  not built yet: today the check refuses a second `Ref` of an address local anywhere in the
  function. It is never the place of a `DropIfSet` as a whole and never `Move`d, at its
  root or
  at a part; the write of an owning part drops the old part through the address (8.10,
  rule 6). It is the place of a `Drop` as a whole only at a rebind through it: the next
  statement of the block writes the local (8.10, rules 6 and 8). Its `Ref` comes before
  each use of it on every path, and at each use a region over the root of the place of its
  `Ref` is open (8.6)
- a capture place: in the body of a lifted lambda, the place `Field(_1, k)` of a
  capture is never moved out of and never written, because a caller can call the closure
  two times or more. Each part of an `AggClosure` after its `FnRef` is a `Copy` or a `Move`
  of a local: lowering takes it from the `Ownership` row of the capture (7.9). This rule is
  not built yet
- a stack-state iterator never leaves: a local that an `Aggregate(AggIterator)` with
  `heap: false` writes, and each local that it moves into, never reaches `_0` and is never
  the operand of an `Owned` argument (8.7). This rule is not built yet
- a numeric cast: every `Cast` goes between two numeric types, or casts a value to its own
  type (a conversion is a call, never a `Cast`)
- a constant in range: every `Int` constant holds a value in the range of its integer type
  (8.5)
- every `Trap` code is a registered RExxxx, with one operand for each printf conversion of
  its registry text, of the type that the conversion takes (8.8): `%d` an `i32`, `%.*s` a
  `string`. A conversion of another kind and a `Move` operand are refused
- a drop flag: the flag of a `DropIfSet` is a `bool` local, and each local has one flag
  only: every `DropIfSet` of a local names the same flag
- every statement and terminator carries a `Span` (G9)

One more rule holds before the verifier runs, and it is a rule of the SLIR stage, not of
the verifier: no function comes from a body with the origin `TemplateCheck` (Q1, 4.3). The
stage refuses such a body with the internal error CE0000 (`lower_function`,
`semantics/slir/lower.py`).

**As built.** The verifier is six modules of `semantics/slir/`. `verify.py` holds the
entry (`SlirVerifier.verify`, `verify_function`) and the structure check, statement by
statement. `verify_calls.py` holds the checks of a call: the conventions of a `Direct`
callee (`declared_params`), the conventions of a container static (`argument_conventions`),
the receiver operand of a built-in method, the conventions of a method of a family of
`RULED_EFFECT_FAMILIES` (`receiver_convention` and `argument_conventions`), and no copy of
an owning value other than a `string` into an `Owned` argument. `verify_arrays.py` holds the
checks of an array value (the parts of each run, and no `Move` of a repeated value), of a
length and a slice, and of the `Drop` of a part (an owning type, off a local that is no
view; the dataflow reads its root). `verify_flow.py` holds the dataflow of the moves and the
drops. `verify_regions.py` holds the rules of a view local and of a borrow region (rules
`a view local`, `a borrow region` and `no change of an owner in its region`; the read of a
view local outside its regions is `a view local`): a dataflow
gives each block the set of open regions at its top, every path into a block brings the
same set, and a `Trap` closes nothing; a write, a `Move`, a drop or an `Inout` argument of
an owner counts at its root and at each projection of it. It runs after the structure check
and before the dataflow of the moves and the drops, which does not read a view local.
`verify_rules.py` holds the names of the rules, which the five others share. The
SLIR stage gives the verifier each
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
never drops and never moves the value of the caller, and an `Inout` parameter holds a value
again at the `return`. A `Guaranteed` parameter starts in the state "moved" (8.10, rule
5): a write over it makes it live, and from there the rule of the drops reads it as an
owning local.
A `Trap` ends the program and needs no drop. A read or a `Move` of a local in the state "no
value" is refused, and so is a `return` on a path where `_0` holds no value. The declared
parameters of a `Direct` callee come from the function table (`funcs.by_unit`), which the
verifier reads at each call, so a generic instance that a later body declares is found too.
A `Direct` callee whose name is a written name `<type>.<method>` finds its row in the
extension table instead: the record of that method whose `method_symbol` is the symbol of
the callee, with the convention of its `self` first for an instance method
(`declared_params`, `verify_calls.py`). A `Direct` callee with a module finds its row in the
signature tables of the stdlib registry: each argument is `Guaranteed`, and the count is the
arity of its row (`stdlib_arity`). A `Direct` callee with no module whose link name and
name are a declaration of the external table (`ExternalTable.by_link_name`) is a C
function: each declared parameter has the convention of its declared mode, and a
variadic extern has `var_arg`, so the rule compares its declared prefix alone. A `Direct`
callee with no row is CE0158. Today a free function, a method or a static of the build, a
function of a registry stdlib module and a C function have a row: the first emitter of
another `Direct` callee (a conversion, a function of a library) adds its row. The verifier does not check a shape that no lowering makes yet,
and it refuses such a shape with the internal error CE0000 (the growth rule): a `Drop` of a
place that is no local and no element of a local, and a `DropIfSet` of a place that is no
local.
A projection of a `Global` is checked against the type that the `Global` carries. The negative case of each
rule is a pytest test over a hand-built function (`tests/unit/test_slir_verifier.py`, and
`tests/unit/test_slir_verify_regions.py` for the views and the regions), because a correct
lowering makes none.

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

The `.sushi` suite (6,055 fixtures, measured on 2026-10-26 at `0a0d6689`) asserts on
behaviour: stdout, exit codes, error codes and leaks (1,554 fixtures carry
`EXPECT_NO_LEAKS`). It does not assert on compiler
internals. A refactor of this size usually fails because the tests encode the old
structure. Here they do not. This is the single largest reason the work is feasible.

One small group is the exception: 18 fixtures assert on the emitted LLVM IR
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

Three of these gates are deleted on the branch (P2.13). `test_scope_exit_walk_is_one` is
ported to `tests/unit/test_slir_drop_placement_is_one.py`, the one drop-placement gate.
`test_method_call_dispatch_is_a_table` is ported to
`tests/unit/test_slir_call_dispatch_is_a_table.py`. `test_owning_binding_uses_the_router` is
dropped, not ported: its replacement is the one drop-placement gate, and two ports of one
rule are two gates for one rule. Ruled 2026-10-11.

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
10. **One user-visible change.** Every run-time stop is one `Trap` that prints `Runtime
    Error RExxxx: ...`, so `expect` has a code. The failure text of `expect` on an `Err` or a
    `None` is `Runtime Error RE2029: expect failed at <file:line:col>: <msg>`, with exit
    code 1 (8.8). It is the one exception to the rule that no behaviour changes. The
    CHANGELOG states the change at the merge.
