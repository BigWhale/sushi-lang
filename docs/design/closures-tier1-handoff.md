# Closures Tier 1 — Handoff (remaining work: 3 items — see below)

This is the **resume-here** document for the outstanding Tier-1 work. The design
rationale lives in `closures.md`; this file records the **current code state**, the
**gotchas discovered during implementation**, and a **concrete, ordered plan** so the
work can be picked up cold in a new session.

Branch: `feature-closures-tier1`. Full enhanced suite is green (1055 tests). Closures
compile and run for copy-capture (primitives, strings, copyable structs) AND, since
**T1.5**, for **dynamic-array move-capture** with full **environment RAII**: a capturing
lambda's heap env is freed on every exit path (scope exit, early `return`, `??`),
escaping/returned closures are freed by their new owner, and a `List<fn(...)>` owns and
frees the closures stored in it.

## Remaining work (at a glance)

Three items remain before Tier 1 is fully closed. In rough dependency/effort order:

1. **List/Own/closure *value* capture** (currently CE2094; detail in §2 step 6). The env
   move + free already works for these types — the blocker is purely the lifted lambda
   **body**: reading the captured value is a method/call on `__closure_env.<name>`, and
   that member-access receiver mis-types to the dynamic-array dispatch and crashes the
   backend. Fix = type a method-call-on-member-access receiver for generic collection
   types in the re-annotated lifted body; then lift the CE2094 in `type_visitor.py`.
   `tests/closures/test_err_closure_list_capture_deferred.sushi` pins the current behavior.

2. **Container get-out aliasing** (a soundness hole, not just a missing feature; detail in
   §2.5). Pulling a closure back *out* of a container that also owns it double-frees:
   `let g = fns.get(0)??` then `fns.free()`, and the same shape for a plain rebind
   `let g = f`. A closure in a *struct field* is the mirror case (the struct never frees
   it → leak). Needs env **refcounting**, **get-moves** ownership transfer, or extending
   the (dynamic-array-only) struct-field cleanup path to closures. **No test yet** — these
   patterns crash/leak at runtime and are currently only prose-documented.

3. **T1.8 — stdlib combinators** (`List.map`/`.filter`/`.fold`, `compose`; detail in §3).
   Greenfield: there is no Sushi-source stdlib for `List<T>` today. General `compose` over
   *capturing* closures depends on item 1 (a captured closure is a List/Own/closure-style
   owned capture); `compose` over non-capturing fn refs and `map`/`filter`/`fold` over
   copyable elements do not.

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

6. **Move-capture of owned types (dynamic arrays now; List/Own/closure deferred).** In
   `type_visitor.py:ExpressionValidator.visit_lambda`, a `DynamicArrayType` capture is now allowed
   (move-capture); `emit_lambda` marks the owned binding moved (`codegen.moves.mark(cap.name)`) and
   the borrow checker's `_check_expr` `Lambda` case marks it moved semantically (`is_owning_type` →
   `state.is_moved = True`), so a later use is CE2405. **`List<T>`/`Own<T>`/capturing-closure value
   captures stay CE2094** — the env-side move + free works (the drop already handles them via
   `emit_list_destroy` / `emit_value_destructor`), but reading the captured value back inside the
   lambda body is a MethodCall/DotCall on `__closure_env.<name>`, and that member-access receiver
   mis-types to the dynamic-array dispatch (crashes the backend). Extracting to an intermediate
   local first works (`let zs = s.xs; zs.len()`), so the fix is in typing a
   method-call-on-member-access receiver for generic collection types in the re-annotated lifted
   body. Lifting this CE2094 (in `type_visitor.py`) is the only change needed to enable List/Own
   move-capture once that dispatch is fixed.

**Validated (macOS `leaks --atExit`):** copy-capture, escaping/returned closure, `??`-early-exit
with a live closure, owned dynamic-array move-capture (+ its use-after-move CE2405), and
`List`-owns-then-`free` — all leave only the pre-existing ~16-byte `user_main` runtime leak (present
even in a trivial no-closure program), no closure-env leak, no double-free. Tests in
`tests/closures/` (`test_closure_owned_move_capture`, `test_closure_list_owns`,
`test_closure_qq_early_exit`, `test_err_closure_use_after_move_capture`,
`test_err_closure_list_capture_deferred`). Full enhanced suite green.

### 2.5 Residual — container get-out aliasing (remaining item 2; NOT a regression)

Pulling a closure back *out* of a container that also owns it double-frees, because the extracted
local registers for its own scope-exit free while the container will free the same env:
`let g = fns.get(0)??` then `fns.free()` (crash), and the same shape for a plain rebind `let g = f`.
A closure stored in a **struct field** is the mirror case: `struct_needs_cleanup` (dynamic-array
specialized) does not see `FunctionType` fields, so the struct never frees them (leak unless
extracted-and-scoped, which is then the sole freer — safe but by luck). Correctly closing this needs
either env **refcounting**, **get-moves** ownership transfer out of the container, or extending the
struct-field cleanup path (`dynamic_arrays.emit_struct_field_cleanup`/`_get_cleanup_fields`, today
dynamic-array only) to closures. Deferred — it is orthogonal to the T1.5 lifecycle above and to
T1.8.

---

## 3. T1.8 — stdlib combinators (`map`/`filter`/`fold`, `compose`)

**Foundation is proven:** a capturing closure passed to a higher-order function and called returns
correctly (verified: `apply_twice(f, 5)` with `f = |i32 x| x + step` → 25). So combinators are
"just" Sushi source once written.

- Author `List.map` / `List.filter` / `List.fold` in Sushi stdlib source
  (`sushi_lang/sushi_stdlib/src/collections/`), as **generic extension methods with fn-typed
  params**, e.g. conceptually `extend List<T> map<U>(fn(T) -> U f) List<U>`. First confirm the
  current level of support for generic extension methods that take a `fn(...)` parameter and
  monomorphize — that's the main unknown. They only ever *call* the fn param (never store it), so
  they are legal under the T1.7 non-owning-param rule.
- `compose(f, g)` returns `|x| f(g(x))`, which **captures `f` and `g`** (function values). A
  *capturing* closure captured by another closure is an **owned** capture (a capturing
  `FunctionType` is owning), so **`compose` over capturing closures depends on T1.5 move-capture**.
  `compose` over *non-capturing* fn refs works today (they are copyable). Note this dependency when
  scoping T1.8.

**T1.5 has landed**, so the general `compose` (which captures its two fn args — a *capturing*
closure captured by another closure) now has its move-capture prerequisite. `map`/`filter`/`fold`
over copyable elements were always independent of T1.5.

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
