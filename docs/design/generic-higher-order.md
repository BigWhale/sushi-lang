# Design: Generic Higher-Order Functions (T1.8 groundwork)

**Status:** Gaps C + A landed (#125); **Gap D landed** (#124/#126). Generic functions that
take and call a function-typed parameter (`fn(T) -> U`) now infer, monomorphize, and run.
`map` / `filter` / `fold` work as **free generic functions** with capturing-closure or
function-reference arguments, and `List<T>` is now user-extensible. The UFCS *method* form
(`xs.map(f)`) is blocked only by **Gap B** (method-level type params on `extend`); `compose`
needs Tier 2 (T2.4); and where combinators ship is a separable delivery decision (see
"Deferred" below). Successor context: `closures.md` (Tier 1 closures), which this builds on.

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

- **Method form `xs.map(f)` — needs Gap B only (Gap D is DONE).** **Gap D landed** (#124/#126):
  `List<T>` is now user-extensible — a first-class generic struct, with both concrete
  (`extend List<i32> sum_all()`) and generic (`extend List<T> first_or(T)`) extends compiling and
  running (`tests/bugs/test_run_issue124_list_extension_{concrete,generic}.sushi`). The old
  `CE0017` (by-value `self` vs by-pointer List receiver) and `Param.__init__` crash are fixed;
  there is no rejection error. **Constraints:** a user List method **cannot shadow a builtin** List
  method name (providers are checked first at dispatch), and the receiver ABI is reconciled at the
  dispatch site. So a *same-type* method (`extend List<T> map(fn(T)->T f) List<T>`) works today.
  What remains is **(B)**: extension methods have no method-level type params — the `extend_def`
  grammar is `NAME "(" params ")" ...` with no `[type_params]` (`grammar.lark:36`), `ExtendDef`
  carries none (`ast.py:135`), collect (`passes/collect/functions.py:748`) derives type params from
  the *receiver* only, and there is no call-site inference or call-site-driven monomorphization for
  method type params (extension monomorphization is eager/receiver-driven,
  `backend/generics/extensions.py:148`). So a *type-changing* `fn(T)->U` method (needing its own
  `<U>`) is the only piece still blocked. Full Gap B breakdown + options:
  `docs/design/closures-tier1-handoff.md` §4.

- **`compose(f, g)` (Tier 2, T2.4).** Its returned lambda `|x| f(g(x))` *calls* the captured
  `f`/`g`; a captured closure call lowers to `env.f(x)`, a non-`Name` callee that needs
  `Call.callee` widening (T2.4). Today this is a graceful compile error (a capturing +
  calling-a-closure case is already pinned by
  `tests/closures/test_err_closure_capture_closure_deferred.sushi`).

- **Inline capturing-closure argument leak — FIXED (#123/#126).** Passing a capturing closure
  *inline* as a call argument — `map(xs, |x| x * k)` — previously heap-allocated an environment that
  was never freed (~16 bytes/closure), because a closure created as a call argument was not bound to
  a local and so was not RAII-tracked. It is now registered in a per-scope temporary registry
  (`_closure_temp_cleanup`, `backend/memory/scopes.py`) and freed via the runtime-guarded drop on
  every exit path; regression `tests/unit/test_closure_temp_raii.py`. Binding to a local is no
  longer required.

## Implementation map (verified anchors)

| Concern | File |
|---|---|
| Unify (Pass 2 / Pass 1.5) | `semantics/passes/types/calls/generics.py:_unify_types_for_inference`; `semantics/generics/instantiate/types.py:unify_types` |
| Pass 1.5 arg-type presentation | `semantics/generics/instantiate/types.py:infer_simple_expr_type` (Lambda / BinaryOp / fn-ref) |
| func_table threading | `semantics/generics/instantiate/__init__.py`; `semantics/semantic_analyzer.py` (2 sites) |
| FunctionType substitution | `semantics/generics/monomorphize/transformer.py`; `semantics/generics/types.py`; `backend/generics/extensions.py` |
| Gap D (List extensibility) — DONE (#124/#126) | `semantics/passes/collect/__init__.py:373` (List as generic struct); `backend/expressions/calls/dispatcher.py:268,308,355` (provider-first dispatch + by-value-`self` reconcile); tests `tests/bugs/test_run_issue124_list_extension_*` |
| Deferred method form (Gap B only) | `grammar.lark:36` (`extend_def`, no `[type_params]`); `semantics/ast.py:135` (`ExtendDef`); `semantics/passes/collect/functions.py:748`; `backend/generics/extensions.py:148`; `semantics/passes/types/calls/methods.py:234` |

## References

- `docs/design/closures.md` — Tier 1 closures (the indirect-call path this exercises).
- `docs/design/first-class-functions.md` — v1 function values.
