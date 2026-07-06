# Design: Generic Higher-Order Functions (T1.8 groundwork)

**Status:** Gaps C + A landed. Generic functions that take and call a function-typed
parameter (`fn(T) -> U`) now infer, monomorphize, and run. `map` / `filter` / `fold` work
as **free generic functions** with capturing-closure or function-reference arguments. The
UFCS *method* form (`xs.map<U>(...)`), `compose`, and where combinators ship are deferred
(see "Deferred" below). Successor context: `closures.md` (Tier 1 closures), which this
builds on.

## Motivation

T1.8's payoff — `map`/`filter`/`fold`/`compose` — was to be authored in Sushi so it
exercises the real indirect-call path end-to-end. Two problems blocked it:

1. There is no Sushi-authored stdlib (every `List` method is a Python IR emitter), so the
   combinators had nowhere to live *and* no way to be written in Sushi.
2. More fundamentally, **generic higher-order functions did not compile at all**: even
   `fn apply<T>(fn(T) -> T f, T x) T` failed with CE2060 — type inference could not bind a
   type parameter that appears inside a function-typed argument.

This note covers closing (2) — the language-capability blocker — which is the real unlock.
Once generic higher-order functions work, the combinators are ordinary Sushi and the
stdlib-delivery question (1) is a separable, later decision.

## What landed

### Gap C — infer type params through function-typed arguments

Generic call-site inference walks each declared parameter type against the argument type to
bind type params. The two twin unification routines (Pass 1.5 collection and Pass 2
validation) had branches for a bare `T`, a named generic (`List<T>`/`Pair<T,U>`), and
concrete equality, but **none for a `FunctionType`** — so a `fn(T) -> U` parameter fell
through to "no match", aborting inference (CE2060). Added a symmetric `FunctionType` branch
to both: when both sides are `FunctionType` of equal arity, recurse on each parameter type
and on the return (`ok_type`), so nested `T`/`U` reach the existing binding branches.

- `semantics/passes/types/calls/generics.py` — `_unify_types_for_inference` (Pass 2).
- `semantics/generics/instantiate/types.py` — `unify_types` (Pass 1.5 twin).

Pass 1.5 additionally could not *present* a type for a function-typed argument, so the
instantiation was never collected (CE2060 would become CE2061). `TypeInferrer` now infers a
`FunctionType` for:
- a **typed-param lambda** (`|i32 x| x * k`) — params from `p.ty`, `ok_type` from the
  lambda's `-> T` annotation or a best-effort body inference (a new `BinaryOp` case handles
  `x * 2` / `x > 3`);
- a **bare function reference** (`inc`) — built from its `FuncSig` via a newly threaded
  `func_table` (mirrors `type_visitor.function_value_type_of`).

(`semantics/generics/instantiate/types.py:infer_simple_expr_type`; `func_table` threaded
through `InstantiationCollector` and the two construction sites in `semantic_analyzer.py`.)

**Limitation:** a *bare-param* lambda argument to a generic (`map(xs, |x| x * k)`) is not
inferable — its param types come from expected-type propagation (Pass 2), which is not
available at Pass 1.5 collection, and is circular anyway (the lambda's type depends on the
type params being inferred *from* it). Use a **typed-param** lambda (`|i32 x| ...`) or a
function reference. This is a graceful CE2060, not a crash.

### Gap A — substitute FunctionType during monomorphization

When a generic is monomorphized, the recursive type-substitution routines rewrite type
params to concrete types. None had a `FunctionType` branch, so `fn(T) -> U` was left
unsubstituted. Added a branch to all three (rebuild `param_types`/`ok_type`/`err_type`
recursively; carry `captures` through unchanged — it is excluded from type identity but
drives ownership):

- `semantics/generics/monomorphize/transformer.py` — `substitute_type`.
- `semantics/generics/types.py` — `_substitute_type_params`.
- `backend/generics/extensions.py` — `substitute_type_params`.

(The two shallow/detection walkers — `_extract_type_instantiations` in
`monomorphize/functions.py` and `substitute_type_simple` in `instantiate/types.py` — do not
yet descend into `fn(...)`; extend them only if a case needs a *named* generic nested inside
a function type, which `map`/`filter`/`fold` over bare `T`/`U` do not.)

## Validation

Combinators as free generic functions, runtime-validated (`tests/generics/test_ho_*`):

- `map<T, U>(List<T>, fn(T) -> U)` — with a capturing closure (`test_ho_map_closure`) and
  with `U` genuinely differing from `T` (i32 -> bool, `test_ho_map_type_change`).
- `filter<T>(List<T>, fn(T) -> bool)` — capturing predicate (`test_ho_filter`).
- `fold<T, U>(List<T>, U, fn(U, T) -> U)` — two independently-inferred type params
  (`test_ho_fold`).
- `apply<T>(fn(T) -> T, T)` called with a bare fn reference (`test_ho_apply_fnref`) — the
  original CE2060 case.

## Deferred

- **Method form `xs.map<U>(...)` (Gap B + Gap D).** Two pieces: **(B)** extension methods
  have no method-level type params — the single-line `extend_def` grammar is `NAME "("
  params ")" ...` with no `[type_params]`, `ExtendDef` carries none, collect
  (`passes/collect/functions.py`) derives type params from the *receiver* only, and there is
  no call-site inference or on-demand monomorphization for extension type params (it is
  driven eagerly by receiver instantiations). **(D)** `List<T>` is not extensible at all
  today — it is dynamic-array/provider-backed, and both concrete (`extend List<i32>` → CE0017
  on the `self` List) and generic (`extend List<T>` → a `Param.__init__` crash in collect)
  extends fail; the dispatch/monomorphization path is gated on `StructType` receivers /
  `struct_instantiations`. UFCS resolves only to registered `extend` methods, not free
  functions, so the method form needs both B and D.

- **`compose(f, g)` (Tier 2, T2.4).** Its returned lambda `|x| f(g(x))` *calls* the captured
  `f`/`g`; a captured closure call lowers to `env.f(x)`, a non-`Name` callee that needs
  `Call.callee` widening (T2.4). Today this is a graceful compile error (a capturing +
  calling-a-closure case is already pinned by
  `tests/closures/test_err_closure_capture_closure_deferred.sushi`).

- **Inline capturing-closure argument leaks its env (pre-existing closures RAII gap).**
  Passing a capturing closure *inline* as a call argument — `map(xs, |x| x * k)` — heap-
  allocates an environment that is never freed (~16 bytes/closure), because a closure created
  as a call argument is not bound to a local and so is not RAII-tracked. This is independent
  of Gaps C/A (pure inference/substitution, no runtime effect) and reproduces with a plain
  non-generic higher-order call. **Workaround:** bind the closure to a local first
  (`let f = |i32 x| x * k` then `map(xs, f)`) — the local is registered and freed. A proper
  fix (register inline closure-argument envs for enclosing-scope cleanup) belongs with
  closures RAII, not this milestone.

## Implementation map (verified anchors)

| Concern | File |
|---|---|
| Unify (Pass 2 / Pass 1.5) | `semantics/passes/types/calls/generics.py:_unify_types_for_inference`; `semantics/generics/instantiate/types.py:unify_types` |
| Pass 1.5 arg-type presentation | `semantics/generics/instantiate/types.py:infer_simple_expr_type` (Lambda / BinaryOp / fn-ref) |
| func_table threading | `semantics/generics/instantiate/__init__.py`; `semantics/semantic_analyzer.py` (2 sites) |
| FunctionType substitution | `semantics/generics/monomorphize/transformer.py`; `semantics/generics/types.py`; `backend/generics/extensions.py` |
| Deferred method form (B/D) | `grammar.lark:extend_def`; `semantics/ast.py:ExtendDef`; `semantics/passes/collect/functions.py`; `backend/generics/extensions.py`; `semantics/passes/types/calls/methods.py` |

## References

- `docs/design/closures.md` — Tier 1 closures (the indirect-call path this exercises).
- `docs/design/first-class-functions.md` — v1 function values.
