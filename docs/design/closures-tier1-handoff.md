# Closures Tier 1 — Handoff (remaining work: T1.5 env RAII / move-capture, T1.8 combinators)

This is the **resume-here** document for the two outstanding Tier-1 items. The design
rationale lives in `closures.md`; this file records the **current code state**, the
**gotchas discovered during implementation**, and a **concrete, ordered plan** so the
work can be picked up cold in a new session.

Branch: `feature-closures-tier1` (6 feature commits + 1 docs commit on top of `main`).
Full enhanced suite is green (1051 tests). Closures compile and run for **copy-capture**
(primitives, strings, copyable structs), including escaping closures. Two things remain.

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

**The two stubs that define the remaining work:**
- `emit_lambda` sets **`drop_ptr = null_ptr(codegen)`** → the heap env is **never freed** (leak).
  This is the exact slot where the env destructor address must go (T1.5).
- `ExpressionValidator.visit_lambda` **rejects owned captures** (`is_owning_type(cap.ty)` →
  CE2094) because a plain `store` of an owned value shallow-copies it and would alias/UAF. Lifting
  this rejection + moving the value is the move-capture half of T1.5.

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

## 2. T1.5 — environment RAII + move-capture (the main correctness item)

**Goal:** free each closure's heap env exactly once (no leak, no double-free), and allow capturing
owned values (dynamic array / `List<T>` / `Own<T>`) by move.

**Why it's genuinely hard (the reason it was deferred, not rushed):** the env must be freed on
*every* exit path — normal scope exit, early `return`, and `??` error propagation — and a closure
that **escapes** (is returned, or stored in a struct/`List`) must be freed by its *new* owner, not
by the local scope. One wrong path = a double-free (crash) or a leak. The existing owned-struct RAII
already solves this shape (`#59/#60` in the code); function values must plug into the *same*
machinery rather than grow a parallel one.

### Ordered plan

1. **Synthesize an env destructor per env struct.** `__closure_env_N.drop(i8* env)`: bitcast to
   `env_struct*`, recursively destroy owned fields via
   `backend/destructors.py:emit_value_destructor` (it already recurses struct fields at
   `:181-190`), then `free` the buffer (mirror the `Own<T>` free idiom in `destructors.py:174-180`:
   bitcast to `i8*`, call the free func). For today's copy-only captures the field destroys are
   no-ops and it's just the `free` — but write it via `emit_value_destructor` so move-capture
   fields are handled for free. Natural home: synthesize alongside the lifted fn in
   `lambda_lift.py` (semantic-side) or emit lazily in the backend keyed by env struct name.

2. **Wire `drop_ptr` in `emit_lambda`.** Replace the `null_ptr(codegen)` drop with
   `bitcast(&__closure_env_N.drop, i8*)` for capturing lambdas. Non-capturing stays null (guarded
   free is then a no-op — this is the whole point of the runtime-guarded drop).

3. **Free function-typed locals at scope exit, runtime-guarded.** At the RAII cleanup points
   (`backend/memory/scopes.py:pop_scope:90`, `backend/statements/utils.py:emit_scope_cleanup:158`),
   for every local whose semantic type is a `FunctionType`, emit
   `if (drop_ptr != null) { drop_ptr(env_ptr); }` — load the fat value, `extract_value` fields 2
   (drop) and 1 (env), branch, call. **Every** function-typed local gets this (capture is erased
   from the type, so ownership is resolved at runtime by the presence of `drop_ptr`, per the design
   §Risks 1). Register function locals into the cleanup tracking the same way structs are
   (`_struct_cleanup` in `scopes.py`) so all exit paths reuse one emission point.

4. **Do NOT free escaped closures (move tracking).** Reuse the existing move machinery so a
   returned/stored closure is skipped at the local scope:
   - **return:** `backend/statements/returns.py:_extract_return_variables:15` already collects
     returned var names to exclude from cleanup — confirm a returned function-typed local is
     excluded (its env is then owned by the caller's binding, freed at the caller's scope exit).
   - **store in struct/`List`:** treat the closure as owning on store. Simplest correct choice is
     **move** (mark the source local moved so the local scope skips its free; the container's
     destructor frees it) rather than deep-copy (which would require cloning the env). This needs
     `backend/expressions/memory.py:deep_copy_if_owning_struct:346` and the destructor to know a
     `FunctionType` field owns memory.

5. **Finish the shared-ownership wiring (architecture-guardian correction #3 — only partly done).**
   `is_owning_type` already recognises a capturing `FunctionType`, and `borrow.py:_type_is_owning`
   delegates to it. Still to do: teach the **backend** paths the same fact —
   `backend/expressions/memory.py:deep_copy_if_owning_struct` and
   `backend/destructors.py:emit_value_destructor` must handle a `FunctionType` value (the guarded
   drop). Without this, a closure stored in a struct/`List` leaks or double-frees.

6. **Enable move-capture of owned types.** Lift the owned-capture `CE2094` in
   `type_visitor.py:ExpressionValidator.visit_lambda` (the `elif is_owning_type(cap.ty)` branch),
   then in `emit_lambda`, when populating an **owned** captured field, **move** the value: consume
   the outer binding (mark it moved via the move tracker so the outer scope doesn't also free it)
   and store it into the env, which now owns it and destroys it in the env destructor (step 1).
   Borrow-checker side: capturing an owned value must mark the outer binding moved
   (`borrow.py:_maybe_mark_own_alloc_move`-style, using `is_owning_type`).

**Acceptance:** return a closure, store one in a struct/`List` then drop it, capture an owned `List`
by move — all runtime-clean under a leak/double-free harness (ASan). Regression: a non-capturing
`&f` still works; every existing test stays green. **Test the `??`-in-a-fn early-exit path
explicitly** — that's the path most likely to double-free.

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

**Do T1.5 before the general `compose`.** `map`/`filter`/`fold` over copyable elements can land
independently of T1.5.

---

## 4. Fast path to re-enter

1. Read `closures.md` (design) + this file.
2. `git log --oneline main..feature-closures-tier1` — the 6 feature commits are the phase history.
3. Reproduce the working baseline: compile+run `tests/closures/test_closure_escaping.sushi`
   (prints 15) and `tests/closures/test_closure_capture_primitive.sushi`.
4. Start T1.5 at step 1 above; keep the enhanced suite green after each step
   (`python tests/run_tests.py --enhanced`), and add ASan/leak checks for the escape/store tests.
