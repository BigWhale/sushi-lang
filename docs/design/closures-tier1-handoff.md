# Closures Tier 1 — Handoff (remaining work: 1 item — T1.8)

This is the **resume-here** document for the outstanding Tier-1 work. The design
rationale lives in `closures.md`; this file records the **current code state**, the
**gotchas discovered during implementation**, and a **concrete, ordered plan** so the
work can be picked up cold in a new session.

Branch: `feature-closures-tier1`. Full enhanced suite is green (1062 tests). Closures
compile and run for copy-capture (primitives, strings, copyable structs) AND, since
**T1.5**, for **owned move-capture** (dynamic array, `List<T>`, `Own<T>`) with full
**environment RAII**: a capturing lambda's heap env is freed on every exit path (scope
exit, early `return`, `??`), escaping/returned closures are freed by their new owner, and
a `List<fn(...)>` owns and frees the closures stored in it. **Closure aliasing is now
sound** (rebind moves, container get-out / struct-field read borrow, struct-field closures
are freed by struct cleanup).

## Remaining work (at a glance)

Only **T1.8** remains before Tier 1 is fully closed. The two former T1.5 residuals are
**DONE** (this branch):

1. **`List<T>`/`Own<T>` value capture — DONE.** The CE2094 gate in `type_visitor.py`
   (`ExpressionValidator.visit_lambda`) now lets `List`/`Own` captures fall through like
   dynamic arrays; the backend member-access receiver routing was fixed in
   `backend/expressions/calls/utils.py` (`infer_generic_struct_type` + `emit_receiver_as_pointer`
   gained a `MemberAccess` env-field strategy) so `__closure_env.xs.len()` dispatches to the
   List/Own handler instead of crashing in the dynamic-array path. Capturing **and calling**
   a *closure* value is still deferred (its call `env.f(x)` is a non-`Name` callee — T2.4;
   now a graceful CE2094, not a crash). Tests: `test_closure_list_capture`,
   `test_closure_own_capture`, `test_closure_list_mutate`,
   `test_err_closure_capture_closure_deferred`.

2. **Closure aliasing soundness — DONE.** `emit_let` (`backend/statements/variables.py`,
   `_reconcile_closure_ownership`) keeps exactly one env owner: a plain rebind `let g = f`
   **moves** (source marked moved in the `MoveTracker`; the borrow checker's
   `_reconcile_closure_bind` tracks capturing-closure provenance so a later use is CE2405);
   a container get-out (`fns.get(0)??`) and a struct-field read borrow (unregistered via
   `scopes.unregister_closure_cleanup`); and struct-field closures are freed by struct
   cleanup (`dynamic_arrays.struct_needs_cleanup`/`_get_cleanup_fields`/
   `_emit_struct_field_closure_free`). Tests: `test_closure_rebind_move`,
   `test_closure_get_out`, `test_closure_in_struct_field`,
   `test_err_closure_use_after_move_rebind`.

3. **T1.8 — stdlib combinators** (`List.map`/`.filter`/`.fold`, `compose`; detail in §3).
   Greenfield: there is no Sushi-source stdlib for `List<T>` today (every List method is a
   Python IR emitter), so this hinges on a decision about a Sushi-authored stdlib/prelude
   path. `compose` over fn params compiles today (copy-capture); its true dependency is the
   now-closed aliasing soundness (item 2), not move-capture. `map`/`filter`/`fold` over
   copyable elements are independent of both.

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

**Groundwork landed** (branch `feature-generic-higher-order`; see
`docs/design/generic-higher-order.md` for the detailed design + implementation map). The real
blocker was not "where stdlib lives" but that **generic higher-order functions did not compile at
all** (even `fn apply<T>(fn(T)->T f, T x) T` was CE2060). Two gaps were closed:

- **Gap C** — type inference now binds type params nested in a `fn(T) -> U` argument (a
  `FunctionType` branch in both twin unifiers; Pass 1.5 presents a `FunctionType` for a typed-param
  lambda or a fn reference).
- **Gap A** — monomorphization now substitutes `FunctionType` (branch added to all three
  substitution routines).

`map` / `filter` / `fold` are validated as **free generic functions** (`tests/generics/test_ho_*`)
with capturing-closure and fn-ref arguments.

**Still outstanding (deferred, documented in `generic-higher-order.md`):**
- The UFCS **method form** `xs.map<U>(...)` — needs **Gap B** (method-level type params on `extend`:
  grammar + AST + collect + call-site inference for method params + on-demand extension
  monomorphization) and **Gap D** (`List<T>` is not extensible at all — provider-backed, both
  concrete and generic extends fail).
- **`compose`** — its returned lambda calls the captured `f`/`g` (a non-`Name` callee), which needs
  Tier 2 T2.4 `Call.callee` widening; graceful compile error today.
- **Where the combinators ship** — no Sushi-authored stdlib exists; a prelude / source-stdlib path
  is a separate decision.
- **Pre-existing leak:** an inline capturing-closure call argument (`map(xs, |x| ...)`) leaks its
  env (~16 B); bind to a local first as a workaround. Independent of Gaps C/A.

---

## 4. Fast path to re-enter

1. Read `closures.md` (design) + this file, especially **Remaining work (at a glance)** above.
2. `git log --oneline main..feature-closures-tier1` — the feature commits are the phase history.
3. Reproduce the working baseline: compile+run `tests/closures/test_closure_escaping.sushi`
   (prints 15) and the T1.5 tests (`test_closure_owned_move_capture`, `test_closure_list_owns`,
   `test_closure_qq_early_exit`).
4. Pick one of the three remaining items:
   - **Item 1 (List/Own/closure capture, §2 step 6)** — the isolated repro is a lambda body reading
     a captured `List<T>` (`|x| x + xs.len()`), which crashes in `backend/types/arrays/` dispatch;
     `let zs = s.xs; zs.len()` (extract-to-local) works, so trace the method-call-on-member-access
     receiver typing in the re-annotated lifted body. Then delete the CE2094 branch in
     `type_visitor.py` and convert `test_err_closure_list_capture_deferred.sushi` to a positive test.
   - **Item 2 (container get-out aliasing, §2.5)** — decide the ownership model (env refcount vs
     get-moves vs struct-field cleanup extension) before coding; add the crashing/leaking repros as
     tests once a model is chosen.
   - **Item 3 (T1.8 combinators, §3)** — first confirm generic extension methods that take a
     `fn(...)` parameter and monomorphize; there is **no** Sushi-source stdlib for `List<T>` today
     (every List method is a Python LLVM emitter under `backend/generics/list/`, none take an fn
     param), so a stdlib home + the fn-param-monomorphization path is the main unknown.
5. Keep the enhanced suite green after each step (`python tests/run_tests.py --enhanced`);
   leak-check the runtime cases with macOS `leaks --atExit` (baseline noise: ~16 bytes in
   `user_main`, present even in a trivial no-closure program).
