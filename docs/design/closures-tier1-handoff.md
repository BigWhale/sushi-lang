# Closures Tier 1 — Handoff (remaining: Gap B + prelude-home + compose)

This is the **resume-here** document for the outstanding closures work. The design
rationale lives in `closures.md`; this file records the **current code state**, the
**gotchas discovered during implementation**, and a **concrete, ordered plan** so the
work can be picked up cold in a new session.

**Status (post-#126, on `main`).** Tier 1 is functionally complete: closures compile and run
for copy-capture (primitives, strings, copyable structs) AND owned **move-capture** (dynamic
array, `List<T>`, `Own<T>`) with full **environment RAII** — a capturing lambda's heap env is
freed on every exit path (scope exit, early `return`, `??`), escaping/returned closures are
freed by their new owner, a `List<fn(...)>` owns and frees the closures stored in it, and
closure aliasing is sound (rebind moves; container get-out / struct-field read borrow;
struct-field closures freed by struct cleanup). The **generic higher-order groundwork** landed
too (#125): `map`/`filter`/`fold` work as **free generic functions** over closures/fn-refs. What
remains is *ergonomics and delivery*, not core capability: the UFCS method form (Gap B),
`compose` (Tier 2 T2.4), and deciding where a Sushi-authored prelude lives.

> **Doc-drift note (reconciled 2026-07-07).** Earlier revisions of this file, `closures.md`,
> and `generic-higher-order.md` listed **Gap D** (`List<T>` extensibility) and the **inline-
> closure env leak** as open — both were **closed by PR #126** (issues #124 and #123). `ROADMAP.md`
> and `FUTURE.md` conversely still described the env as leaking with owned-capture rejected — that
> was **closed by T1.5** (PR #122). All five docs now reflect the post-#126 state.

## Remaining work (at a glance)

Tier 1's core is done. What is left is the method-form ergonomics + delivery decision:

1. **Gap B — method-level type params on `extend`.** The single blocker for the *type-changing*
   UFCS method form `xs.map(f)` (i32 → bool). A *same-type* combinator
   (`extend List<T> map(fn(T)->T f) List<T>`) already works today (Gap D is closed). Detail +
   options in §3 and §4.

2. **`compose(f, g)` — Tier 2 (T2.4).** Its returned lambda `|x| f(g(x))` *calls* the captured
   `f`/`g`; a captured-closure call lowers to `env.f(x)`, a non-`Name` callee that needs
   `Call.callee` widening. Graceful compile error today, pinned by
   `test_err_closure_capture_closure_deferred`.

3. **Where the combinators ship.** There is no Sushi-authored stdlib for `List<T>` (every List
   method is a Python IR emitter). `map`/`filter`/`fold` work as free generic functions today;
   choosing a prelude / source-stdlib home is a separable decision (see §5).

### Former T1.5 residuals — DONE (PR #122)

1. **`List<T>`/`Own<T>` value capture — DONE.** The CE2094 gate in `type_visitor.py`
   (`ExpressionValidator.visit_lambda`) lets `List`/`Own` captures fall through like dynamic
   arrays; the backend member-access receiver routing in `backend/expressions/calls/utils.py`
   (`infer_generic_struct_type` + `emit_receiver_as_pointer` gained a `MemberAccess` env-field
   strategy) routes `__closure_env.xs.len()` to the List/Own handler. Capturing **and calling** a
   *closure* value is still deferred (its call `env.f(x)` is a non-`Name` callee — T2.4; graceful
   CE2094). Tests: `test_closure_list_capture`, `test_closure_own_capture`,
   `test_closure_list_mutate`, `test_err_closure_capture_closure_deferred`.

2. **Closure aliasing soundness — DONE.** `emit_let` (`backend/statements/variables.py`,
   `_reconcile_closure_ownership`) keeps exactly one env owner: a plain rebind `let g = f`
   **moves** (source marked moved in the `MoveTracker`; the borrow checker's
   `_reconcile_closure_bind` in `semantics/passes/borrow.py` tracks capturing-closure provenance so
   a later use is CE2405); a container get-out (`fns.get(0)??`) and a struct-field read borrow
   (unregistered via `scopes.unregister_closure_cleanup`); and struct-field closures are freed by
   struct cleanup (`backend/memory/dynamic_arrays.py`: `struct_needs_cleanup` /
   `_emit_struct_field_closure_free`). Tests: `test_closure_rebind_move`, `test_closure_get_out`,
   `test_closure_in_struct_field`, `test_err_closure_use_after_move_rebind`.

### Also closed since (PRs #125, #126)

- **Gap C + Gap A — DONE (#125).** Generic higher-order functions compile: type inference binds a
  type param nested in a `fn(T)->U` argument, and monomorphization substitutes `FunctionType`.
  `map`/`filter`/`fold`/`apply` validated as free generic functions (`tests/generics/test_ho_*`).
  See `generic-higher-order.md`.
- **Gap D — DONE (#124/#126).** `List<T>` is extensible: user `extend List<T> ...` (concrete and
  generic) compiles and runs (`tests/bugs/test_run_issue124_list_extension_{concrete,generic}`).
  There is no rejection error. Providers win on a builtin-name clash; the by-value-`self` vs
  by-pointer receiver mismatch is reconciled at the dispatch site. See §4 (Gap B) — Gap D was the
  *List-extensibility* half; Gap B (method type params) is the remaining half of the method form.
- **Inline capturing-closure argument leak — FIXED (#123/#126).** A capturing closure passed
  *inline* as a call argument (`apply(|i32 x| x + k, 10)`) is now RAII-tracked via the
  `_closure_temp_cleanup` registry (`backend/memory/scopes.py`) and freed on every exit path.
  IR free-count regression: `tests/unit/test_closure_temp_raii.py`.

---

## 0. Orientation — what exists and where

| Concern | File / symbol |
|---|---|
| Fat-pointer type `{fn_ptr, env_ptr, drop_ptr}` (24 B) | `backend/types/core/mapping.py` `FunctionType` case → `self.closure_struct`; `backend/constants/sizes.py:CLOSURE_FAT_POINTER_SIZE_BYTES` |
| Runtime API (thunk, build value, indirect call, **emit_lambda**) | `backend/runtime/closures.py` |
| Lambda AST node (`captures`, `resolved_type`, `env_struct`, `lifted_name`) | `semantics/ast.py:Lambda` |
| Capture analysis (free-name → `Lambda.captures`) | `semantics/passes/scope.py` (`_check_lambda`, `_record_capture`) |
| Lambda type-check + `CE2094` + bare-param inference | `semantics/type_visitor.py` (`infer_lambda_type`, `ExpressionValidator.visit_lambda`, `TypeInferenceVisitor.visit_lambda`) |
| Expected-type propagation to bare-param lambdas | `semantics/passes/types/propagation.py` (Lambda hook near top of `propagate_types_to_value`) |
| **Lambda-lifting pass** (env struct + lifted `__lambda_N`) | `semantics/passes/lambda_lift.py` |
| Shared fn-synthesis wiring (monomorphizer + lift share it) | `semantics/generics/synthesis.py:register_synthesized_function` |
| Ownership predicate (single source of truth) | `semantics/typesys.py:is_owning_type` |
| Backend expr dispatch → `emit_lambda` | `backend/expressions/__init__.py` (`case Lambda()`) |

### Where the passes ACTUALLY run (gotcha #1 — cost me time)
The live semantic pipeline is **`semantics/semantic_analyzer.py`**, not the
`build_pipeline`/`add_pass` scaffold in `semantics/pipeline.py` (that scaffold is **dead
code** — nothing calls `build_pipeline`). The lambda-lift pass is inserted in **both**
`_check_single_file` (~line 210) and `_check_multi_file` (~line 462), each time as:
```python
from sushi_lang.semantics.passes.lambda_lift import LambdaLifter
LambdaLifter(self.structs, self.funcs, program_or_unit_ast,
             annotate=type_validator._validate_function).run()
```
inserted **after** `type_validator.run(...)` and **before** `borrow_checker.run(...)`.

---

## 1. Current lowering, so you know exactly what to change

For each lambda the lift pass synthesizes (see `lambda_lift.py`):
- an env struct `__closure_env_N { <captured fields, in capture order> }` (registered in the
  struct table), and
- a lifted fn `fn __lambda_N(&peek __closure_env_N __closure_env, <lambda params>) -> ok | err`
  whose body has each captured read rewritten to `__closure_env.<name>` (`_rewrite_captures`),
  expression bodies wrapped as `return Result.Ok(<expr>)`.

`emit_lambda` (`backend/runtime/closures.py`) at the lambda site:
1. non-capturing → `{&__lambda_N, null, null}` (env slot unused by the body);
2. capturing → `malloc(sizeof env)`, **plain `store` of each captured value** into the env
   fields (via `emit_expr(Name(cap))`), then builds `{&__lambda_N, env_i8, null_ptr}`.

**The two stubs that defined T1.5 — both now resolved:**
- `emit_lambda` used to set `drop_ptr = null` (leak). It now stores the address of a synthesized,
  type-erased env destructor `__closure_env_N.__closure_drop` (`get_or_create_env_drop` in
  `backend/runtime/closures.py`), and RAII frees every function-value local through it (guarded by
  `drop_ptr != null`).
- `ExpressionValidator.visit_lambda` used to reject owned captures (CE2094). That branch is gone;
  owned values now **move-capture** into the env (`emit_lambda` marks the outer binding moved via
  `codegen.moves.mark`; `borrow.py` `_check_expr` `Lambda` case marks it moved semantically so a
  later use is CE2405). The env destructor frees moved-in owned fields (dynamic array / `List` /
  `Own` / nested capturing closure) before freeing the buffer.

### Verified ABI fact (gotcha #2)
Calling `__lambda_N(env_struct*, params)` **through an `(i8*, params)` bitcast function pointer**
verifies clean in LLVM 20 (tested with `binding.parse_assembly(...).verify()`), which is why the
env param can be a typed `&peek env_struct` while the indirect call passes `env` as `i8*`. Don't
"fix" this to match types — it's intentional and works.

### Other gotchas worth keeping
- **gotcha #3:** lifted fns are added *after* the type pass, so they carry no type-pass
  annotations (e.g. `DotCall.resolved_enum_type` for `Result.Ok`). The lift pass re-annotates each
  via the `annotate=type_validator._validate_function` callback. Any new synthesized statements you
  add to a lifted body must survive that re-validation.
- **gotcha #4:** the adapter-thunk symbol embeds a `.` (`name.__closure_thunk`) — unreachable in a
  Sushi `NAME` (CNAME) — to avoid user collision; the cache hit also checks `function_type`
  (`synthesize_thunk`). Keep both guards for any new synthesized symbol.
- **gotcha #5:** Sushi requires `let` type annotations, so a closure `let` needs
  `let fn(...) -> ... f = |..| ..`. Bare-param `|x|` infers from that annotation (or a fn-typed call
  arg) via the propagation hook.
- **gotcha #6:** block-body lambdas (`|x|: <block>`) are only reachable as a `let` RHS (grammar), not
  from `expr`. `emit_lambda`/lift handle them; don't expect them as call args.

---

## 2. T1.5 — environment RAII + move-capture (LANDED)

**Delivered:** each closure's heap env is freed exactly once on every exit path (no leak, no
double-free), and owned values (dynamic array / `List<T>` / `Own<T>`) are move-captured. The work
plugged into the *same* RAII machinery as owned structs (`#59/#60`) rather than a parallel one.

### What was implemented (file:symbol)

1. **Env destructor, synthesized lazily in the backend.**
   `backend/runtime/closures.py:get_or_create_env_drop(codegen, env_struct)` emits, once and
   cached, `void __closure_env_N.__closure_drop(i8* env)`: it destroys each *owning* captured field
   (`is_owning_type`) — dynamic array / `Own` / nested closure via
   `destructors.emit_value_destructor`, `List<T>` via `list.methods_destroy.emit_list_destroy` —
   then `free`s the buffer. The `.` in the symbol dodges user collision; the cache hit re-checks the
   signature (mirrors `synthesize_thunk`). The destructor helpers reach for
   `codegen.builder`/`codegen.func`, so it save/restores those two fields around the field-destroy
   loop (a fresh private `IRBuilder` bound to the drop fn; no `begin_function`, which would clobber
   the outer scope stack).

2. **`drop_ptr` wired in `emit_lambda`** (`closures.py`): capturing path stores
   `bitcast(&__closure_env_N.__closure_drop, i8*)`; non-capturing stays null (guarded free = no-op).

3. **Function-value locals freed at scope exit, runtime-guarded.** New `_closure_cleanup` registry
   in `backend/memory/scopes.py` (parallel to `_struct_cleanup`), populated in
   `create_local`/`create_local_nostore` for `FunctionType` locals, drained + freed in `pop_scope`
   (fall-through) and via `statements/utils.py:emit_closure_cleanup` on early-exit
   (`emit_scope_cleanup`) and `emit_loop_exit_cleanup` (break/continue). The guarded free lives in
   `destructors.emit_function_value_destructor` (load fat value → extract drop/env → `if drop:
   drop(env)`). Function **parameters** are deliberately NOT registered (a passed closure is owned
   by the caller, not the callee).

4. **Escape on return** (`backend/statements/returns.py:emit_return`): a returned `FunctionType`
   local is marked moved (`codegen.moves.mark`), so the local scope skips its free and the caller's
   binding owns it.

5. **`FunctionType` taught to the destructor/`needs_cleanup` paths**
   (`backend/destructors.py`): `emit_value_destructor` gains a `FunctionType` branch (guarded drop)
   and `needs_cleanup(FunctionType)` returns True — so a `List<fn(...)>`'s element cleanup frees
   each stored closure's env (List-owns pattern).

6. **Move-capture of owned types — dynamic array, `List<T>`, `Own<T>` all DONE.** In
   `type_visitor.py:ExpressionValidator.visit_lambda`, `DynamicArrayType`, `List<T>`, and `Own<T>`
   captures are allowed (move-capture); `emit_lambda` marks the owned binding moved
   (`codegen.moves.mark(cap.name)`) and the borrow checker's `_check_expr` `Lambda` case marks it
   moved semantically (`is_owning_type` → `state.is_moved = True`), so a later use is CE2405. The
   backend dispatch gap is closed: `infer_generic_struct_type` + `emit_receiver_as_pointer`
   (`backend/expressions/calls/utils.py`) gained a `MemberAccess` env-field strategy, so reading a
   captured collection back as `__closure_env.<name>.len()` routes to the List/Own handler instead
   of the dynamic-array path. **Only capturing + *calling* a closure value stays CE2094** — the call
   `env.f(x)` is a non-`Name` callee (T2.4, `Call.callee` widening); it is now reported gracefully
   (`validate_function_call` / `visit_call` guard the non-`Name` callee) rather than crashing.

**Validated (macOS `leaks --atExit`):** copy-capture, escaping/returned closure, `??`-early-exit
with a live closure, owned dynamic-array move-capture (+ its use-after-move CE2405), and
`List`-owns-then-`free` — all leave only the pre-existing ~16-byte `user_main` runtime leak (present
even in a trivial no-closure program), no closure-env leak, no double-free. Tests in
`tests/closures/` (`test_closure_owned_move_capture`, `test_closure_list_owns`,
`test_closure_qq_early_exit`, `test_err_closure_use_after_move_capture`,
`test_err_closure_list_capture_deferred`). Full enhanced suite green.

### 2.5 Container get-out aliasing — DONE (move-on-rebind + borrow-on-get-out + struct-field free)

Previously, pulling a closure out of a container that also owned it double-freed, and a closure in
a struct field leaked. Closed on this branch via **move semantics** (the option chosen over env
refcounting). `emit_let` (`backend/statements/variables.py:_reconcile_closure_ownership`) reconciles
the single-owner invariant by binding shape:

- `let g = f` where `f` is a registered owning local → **MOVE**: `codegen.moves.mark(f)` so only `g`
  frees. The borrow checker (`borrow.py:_reconcile_closure_bind`) tracks capturing-closure
  provenance (`BorrowState.is_owning_closure`, set for a capturing lambda literal and propagated on
  move) and marks `f` moved, so a later use of `f` is CE2405 (`_check_expr` also now visits
  `Call.callee`, catching `f(x)` after a move).
- `let g = fns.get(0)??` (container get-out) and `let g = s.handler` (struct-field read) → **BORROW**:
  `scopes.unregister_closure_cleanup(g)` drops the second owner; the container/struct stays sole
  owner (mirrors the `Own<T>.get()` guard).
- A closure stored in a **struct field** is freed by struct cleanup:
  `dynamic_arrays.struct_needs_cleanup`/`_get_cleanup_fields` now see `FunctionType` fields and
  `_emit_struct_field_closure_free` emits the guarded env free.

Validated with `leaks --atExit` (only the ~16-byte `user_main` baseline remains). Tests:
`test_closure_rebind_move`, `test_closure_get_out`, `test_closure_in_struct_field`,
`test_err_closure_use_after_move_rebind`. Residual (accepted, matches the pre-existing `Own<T>.get()`
borrow model): returning a get-out/struct-read borrow while the container still owns it — a full fix
needs get-moves/refcounting, deferred.

---

## 3. T1.8 — stdlib combinators (`map`/`filter`/`fold`, `compose`)

**Groundwork landed** (PR #125; see `docs/design/generic-higher-order.md` for the detailed design
+ implementation map). The real blocker was not "where stdlib lives" but that **generic
higher-order functions did not compile at all** (even `fn apply<T>(fn(T)->T f, T x) T` was CE2060).
Two gaps were closed:

- **Gap C** — type inference now binds type params nested in a `fn(T) -> U` argument (a
  `FunctionType` branch in both twin unifiers; Pass 1.5 presents a `FunctionType` for a typed-param
  lambda or a fn reference).
- **Gap A** — monomorphization now substitutes `FunctionType` (branch added to all three
  substitution routines).

`map` / `filter` / `fold` are validated as **free generic functions** (`tests/generics/test_ho_*`)
with capturing-closure and fn-ref arguments — this is the T1.8 *payoff*, delivered as free
functions. The **inline capturing-closure argument leak** the milestone surfaced is **FIXED**
(#123/#126) — an inline `map(xs, |x| ...)` no longer leaks its env.

**Still outstanding:**
- The UFCS **method form** `xs.map(f)` — needs only **Gap B** now (method-level type params on
  `extend`). **Gap D** (`List<T>` extensibility) is **DONE** (#124/#126): a *same-type* method
  (`extend List<T> map(fn(T)->T f) List<T>`) works today; only a *type-changing* method
  (`fn(T)->U`, needing its own `<U>`) is blocked by Gap B. Full options in §4.
- **`compose`** — its returned lambda calls the captured `f`/`g` (a non-`Name` callee), which needs
  Tier 2 T2.4 `Call.callee` widening; graceful compile error today.
- **Where the combinators ship** — no Sushi-authored stdlib exists; a prelude / source-stdlib path
  is a separate decision (§5).

---

## 4. Gap B — the issue, and options

`extend List<T> map<U>(fn(T)->U f) List<U>` cannot be expressed. Four pieces are missing (all
verified against the current tree):

1. **Grammar** (`grammar.lark:36`): `extend_def` is `NAME "(" [parameters] ")" type ...` — there is
   no `[type_params]` slot after the method name (contrast `function_def` at `:45`, which has it).
   `xs.map(f)` (inference-only, no explicit `<U>` at the call — Sushi has no method type-arg syntax,
   `method_call` at `:151`) already *parses*; only the **definition** needs the slot. Adding it
   requires the LALR acceptance-gate (run the grammar through the parser generator, like the lambda
   `|`); `<` after a method NAME in `extend_suffix` is unambiguous, so low risk.
2. **AST/collect**: `ExtendDef` (`ast.py:135`) has no `type_params`; collect
   (`semantics/passes/collect/functions.py:748`) derives extension type params from the *receiver's*
   `target_type.type_args` **only** (stored on `GenericExtensionMethod.type_params`). Needs a
   method-param field distinct from the receiver params, unioned for body type-resolution.
3. **Call-site inference**: method calls (`semantics/passes/types/calls/methods.py:234-255`) are
   receiver-driven — concrete type args come entirely from the receiver type; there is **no**
   argument unification. The free-function unifier
   (`semantics/passes/types/calls/generics.py:_unify_types_for_inference`, extended in #125 to
   handle `fn(T)->U`) would need to be reused for method calls to solve `U` from the `f` argument.
4. **Monomorphization**: `monomorphize_all_extension_methods`
   (`backend/generics/extensions.py:148`) is eager/receiver-driven, keyed on `struct_instantiations`
   with a strict `zip` of `generic_method.type_params` against the receiver's `type_args` (CE0096 on
   count mismatch). Method params need a call-site-driven instantiation dimension combining receiver
   args **and** independently-inferred method args.

**Options:**

- **(A) Do nothing — free-function form (recommended default).** `map(xs, f)` works today,
  including type-changing (`i32 -> bool`) and capturing closures. The method form is pure UFCS
  sugar. Zero cost; document the combinators as free functions.
- **(B) Same-type-only method combinators.** Ship `extend List<T>` methods whose result type is `T`
  (in-place-style map, `filter`, fold-to-`T`). Works **today** on the back of Gap D, no Gap B. Real
  subset; type-changing map/fold still fall back to free functions.
- **(C) Implement Gap B.** Medium–large. Reuses #125's inference (Gap C) and substitution (Gap A)
  machinery; the crux is bridging the **eager receiver-driven** extension monomorphizer to a
  **call-site-driven** one for method params. Unlocks the full ergonomic `xs.map(f)`.

Gap B is medium–large; Gap D is already closed. Recommendation: pursue (A)/(B) for the parity
payoff now; defer (C) until a concrete consumer wants the fluent method form.

### Constraints on List extension methods (from the Gap D fix, worth knowing)

- **Builtin names cannot be shadowed.** The backend dispatcher checks List provider methods
  (`push`/`get`/`iter`/…) *before* the user-extension fallback, so a user `extend List<T> push()`
  is unreachable. Only non-builtin names route to the extension path.
- **Receiver ABI reconciliation.** A List-backed receiver shares the dynamic-array
  `{i32, i32, T*}` layout and is passed by pointer, but `self` is declared by value; the dispatch
  site loads the header to reconcile (safe because extension bodies never register `self` for
  cleanup, so the shared buffer is not double-freed).

## 5. Future options (parity, not self-host-critical)

Closures are a **parity** feature — ROADMAP R1 (MessagePack) needs none of this. Rough
leverage-per-effort order, once parity is the goal:

1. **Decide the Sushi-prelude home** and ship `map`/`filter`/`fold` as free functions (they work
   today; this is the T1.8 delivery decision).
2. **T2.4 `Call.callee` widening** (small, self-contained) — unblocks `compose`,
   capturing-and-calling closures, `arr[0]()`, and `obj.handler()`. Highest leverage-per-effort.
3. **Gap B** (method-level type params) for the ergonomic `xs.map(f)` form.
4. **Larger Tier 2:** `&poke`/`&peek` borrow capture, bound-method values, generic-fn references
   (lift CE2093), T2.5 indirect-path parity for owning/variadic params, C callbacks.

---

## 6. Fast path to re-enter

1. Read `closures.md` (design), `generic-higher-order.md` (Gaps A/C, and the Gap B/D framing), and
   this file — especially **Remaining work (at a glance)** above.
2. `git log --oneline 430b5bd..2f288a9` — the closures feature commits (#120/#122/#125/#126) are the
   phase history; everything is on `main`.
3. Reproduce the working baseline: compile+run `tests/closures/test_closure_escaping.sushi`
   (prints 15), the T1.5 tests (`test_closure_owned_move_capture` → 13, `test_closure_list_owns`,
   `test_closure_qq_early_exit`), and the Gap D tests
   (`tests/bugs/test_run_issue124_list_extension_generic` → 7).
4. Pick the remaining item that matches the goal:
   - **Prelude + free-function combinators (§5 step 1)** — decide where a Sushi-authored prelude
     lives; `map`/`filter`/`fold` already compile as free generic functions, so this is a delivery
     decision, not a capability one.
   - **T2.4 `Call.callee` widening (§5 step 2)** — unblocks `compose` and capturing-and-calling
     closures; the pinning negative test is `test_err_closure_capture_closure_deferred`.
   - **Gap B (§4)** — the method form `xs.map(f)`; start with the grammar acceptance-gate, reuse the
     #125 unifier for method-call inference, then bridge the extension monomorphizer to a
     call-site-driven path.
5. Keep the enhanced suite green after each step (`python tests/run_tests.py --enhanced`);
   leak-check the runtime cases with macOS `leaks --atExit` (baseline noise: ~16 bytes in
   `user_main`, present even in a trivial no-closure program).
