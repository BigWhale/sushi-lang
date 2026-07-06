# Design: Closures

**Status:** Tier 1 nearly complete (T1.0-T1.7 landed, including T1.5 env RAII + move-capture;
T1.8 stdlib combinators outstanding — see "Implementation status" below). Successor to
`first-class-functions.md` (v1 FCF, PR #91). Scope: capturing closures + lambda literals, delivered
in two tiers. T1 is the minimal *but real* slice (escaping closures, heap env, copy + move
capture); T2 is the ergonomic and hard-case remainder (`&poke` capture, bound methods, generic-fn
refs, `Call.callee` widening, C callbacks). Additive over v1: a non-capturing function value stays
a valid closure with a null environment, so all v1 code keeps compiling and running.

## Implementation status

Landed (in dependency order — see phase descriptions below for what each covers):

- **T1.0** — fat-pointer ABI + sizing (`FunctionType.captures`, 24-byte lowering).
- **T1.1** — lambda grammar/AST (`lambda_expr`, `lambda_block`, `Lambda` node).
- **T1.2** — capture analysis (free-name recording in the scope pass).
- **T1.3** — type-checking + capture legality, including CE2094 for borrow capture.
- **T1.4** — lambda-lifting pass (env struct + lifted function synthesis).
- **T1.6** — backend materialization (`emit_lambda`, env heap-alloc, fat-value construction).
- **T1.7** — indirect-call env threading; CE2094 additionally rejects owning/variadic
  fn-value *parameter* types (dodging the indirect path's missing deep-copy).
- **T1.5** — environment RAII + move-capture. A capturing lambda's heap env is freed on every
  exit path (normal scope exit, early `return`, `??` propagation) through a synthesized,
  type-erased env destructor stored in `drop_ptr`; a returned closure escapes (its owner frees it);
  a closure stored in a `List` is owned and freed by the list. An owned **dynamic array** is now
  **move-captured** into the env (outer binding consumed — use-after-move is CE2405 — and the env
  destructor frees the value). Move-capturing a `List<T>`/`Own<T>`/capturing closure moves + frees
  correctly, but reading it back inside the body (a method/call on `__closure_env.<name>`) hits a
  lifted-body dispatch gap, so it is still CE2094 (deferred); borrow capture and owning fn-value
  *param* types also remain CE2094.

Outstanding:

- **T1.8** — stdlib combinators (`List.map`/`.filter`/`.fold`, `compose`) authored in Sushi source
  atop the now-working indirect-call path.
- **Container get-out aliasing (T1.5 residual).** Pulling a closure back *out* of a container that
  also owns it — `let g = fns.get(0)??` from a `List`, then both `g`'s scope and `fns.free()` drop
  the same env — double-frees; a simple rebind `let g = f` aliases the same way. Storing a closure
  in a *struct field* is safe but the struct does not free it (leaks unless extracted). These need
  env refcounting or get-moves ownership transfer and are deferred (see Risks).

> **Resuming this work:** `closures-tier1-handoff.md` (same directory) is the detailed handoff —
> current code state, the exact stubs (`drop_ptr = null`; owned-capture rejection), file:line
> anchors, discovered gotchas, and an ordered step-by-step plan for T1.5 and T1.8.

The `|` prefix/infix disambiguation and the `|~|` zero-parameter form (see below) were validated
by running the extended grammar through the parser generator with no new conflicts, as the T1.1
acceptance gate required. Block-body lambdas (`|params|: <block>`) are admitted only as a `let`
RHS — the grammar does not reach them from `expr` (see below), so a block-body lambda used directly
as a call argument is a parse error; bind it to a `let` first.

Test coverage: `tests/closures/` (positive: capture, escaping, bare-param inference, struct-field
fat layout, thunk-name-collision regression, dynamic-array move-capture, `List`-owns-closures, `??`
early-exit env free; negative: borrow capture, use-after-move-capture, `List` value capture
deferred, owning fn-value param).

## Summary

A closure is a function value that carries a captured environment. Sushi v1 shipped the
function-value floor (bare pointers, no capture); this note adds the environment. The value
representation migrates from a bare `ir.Function*` to a three-word fat pointer
`{fn_ptr, env_ptr, drop_ptr}`; indirect calls pass `env_ptr` as a hidden leading argument; and
freeing the (heap-owned) environment is type-erased through `drop_ptr`. Lambdas are anonymous
function literals (`|params| body`) that desugar to a synthesized top-level function plus a
fat-pointer value; a capturing lambda additionally builds an environment record. Nothing about
the layout or calling convention differs between T1 and T2 — T2 is purely additive capability.

```sushi
fn make_adder(i32 n) fn(i32) -> i32:
    return Result.Ok(|i32 x| x + n)      # captures n by value; escapes upward (returned)

fn main() i32:
    let add5 = make_adder(5)??
    println(add5(10)??)                  # 15

    let i32 scale = 3
    let i32[] out = from([1, 2, 3]).map(|x| x * scale)??   # captures scale
    return Result.Ok(0)
```

## Syntax

### Lambda literals — Rust-style pipes

Two body forms, both new alternatives in the `atom` grammar production:

```sushi
# expression body: |params| expr   (desugars to a fn returning Result.Ok(expr))
let f = |i32 x| x + n

# block body: |params|: <indented block>  (a full fn body; uses return Result.Ok(...))
let g = |i32 x|:
    let i32 y = x * 2
    return Result.Ok(y + n)

# optional return / error annotation after the closing pipe
let h = |i32 x| -> i32 | MathError: ...

# param types inferred from an expected function type (call arg, annotated binding)
map(list, |x| x * 2)                     # x : i32 inferred from List<i32>.map
```

- Params inside the pipes use Sushi's `type name` form (`|i32 x, string s|`). Bare-name params
  (`|x|`) are allowed **only** where an expected `FunctionType` supplies the types (a call
  argument to a fn-typed parameter, or a binding with a `fn(...)` annotation); otherwise it is a
  new diagnostic (missing lambda param type). Return/error types are similarly inferred from the
  body / expected type, or annotated with `-> T [| E]` after the closing pipe.
- **Result semantics are identical to `fn`.** An expression-body lambda `|x| e` desugars to a fn
  whose body is `return Result.Ok(e)`; a block-body lambda is a literal fn body. Calling through
  a closure yields `Result<T,E>` exactly like any call, so `f(x)??`, `if (f(x))`, and matching
  are unchanged.

### The `|` disambiguation

`|` is already the bitwise-or operator (`grammar.lark:236`, used by `bitwise_or` at `:131`) and
the error-type separator in fn types/decls (`fn_type_t` at `:98`, `function_def` at `:45`). The
lambda `|` is disambiguated **by position**: a `|` appearing where an *operand/atom* is expected
(prefix position — start of an expression, call argument, RHS of `=`) opens a lambda parameter
list; a `|` in *infix* position (between two operands) is bitwise-or. Inside the expression body
of a lambda, a subsequent `|` is infix bitwise-or as usual (`|x| x | 2` = lambda with body
`x | 2`). Sushi's parser is LALR (`sushi_lang/internals/parser.py:54`), not Earley, so this
disambiguation must be resolvable by the grammar's shift/reduce tables alone; the grammar must be
validated through the parser generator as an acceptance gate (see Risks). Implementation note: this
was validated with no new conflicts (see "Implementation status" above).

### Function types (unchanged from v1)

`fn(P...) -> T [| E]`; capture is **not** part of the type (see Semantics — capture erasure), so
`fn(i32) -> i32` names both a plain fn and any closure of that arity/ok/err. Collections use
`List<fn(...)>`.

## Semantics

### Representation — the three-word fat pointer

A function value lowers to `{ i8* fn_ptr, i8* env_ptr, i8* drop_ptr }` (24 bytes):

| Field | Non-capturing value | Capturing closure |
|-------|---------------------|-------------------|
| `fn_ptr`   | address of a thunk `f__thunk(env, ...)` wrapping the bare fn | address of the lifted `__lambda_N(env, ...)` |
| `env_ptr`  | `null` | heap `Own<__closure_env_N>*` holding captured values |
| `drop_ptr` | `null` | address of a type-erased env destructor |

This mirrors the existing **string** fat pointer (`{i8*, i32}`, `backend/strings.py`,
`FAT_POINTER_SIZE_BYTES=12`) — the same insert_value/extract_value/store/load idioms apply.

### Calling convention — adapter-thunk split

- **Direct calls stay bare.** `f(x)` where `f` names a top-level fn lowers to the exact v1
  instruction; no signature or call site changes. FFI externs and `main` are untouched.
- **Indirect calls are uniformly env-passing.** Calling through a function *value* extracts
  `fn_ptr`/`env_ptr` and calls `fn_ptr(env_ptr, args...)` — `env_ptr` prepended as a hidden
  leading argument.
- **A bare fn used as a value is bridged by a thunk.** Materializing a top-level fn as a value
  synthesizes (once, cached) `f__thunk(i8* env, <params>) { return f(<params>) }` and stores
  `{f__thunk, null, null}`. The thunk ignores `env`, so the indirect ABI is uniform without
  touching any real function body.

### Capture policy (T1)

- **Copyable types** (primitives, strings, structs/fixed-arrays composed of those) are captured
  by **value-copy** into the environment record.
- **Owned types** (dynamic array, `List<T>`, `Own<T>`) are captured by **move** into the
  environment — the outer binding is consumed (borrow-checker enforced), and the env's recursive
  destructor frees them.
- **Borrow capture (`&poke`/`&peek`) is rejected in T1** with a dedicated diagnostic → deferred
  to T2 (the borrow-exclusivity-through-a-function-value problem that motivated the whole
  deferral).

### Environment ownership, escape, and RAII

- The environment is **heap-allocated and owned by the closure value** (`Own<__closure_env_N>`),
  so a closure may **escape** its creating scope — be returned, or stored in a struct/`List`.
- Freeing is **type-erased through `drop_ptr`**: at any RAII cleanup point, a function value is
  freed by `if (drop_ptr != null) drop_ptr(env_ptr)`. Non-capturing values carry `drop_ptr =
  null`, so their free is a guarded no-op. This is *why* the drop slot exists in T1 — a
  `fn(i32)->i32` value cannot tell statically whether it owns an env (capture erasure), so
  ownership is resolved at runtime by the presence of a drop function.
- **Capture-taint drives ownership analysis.** A bare fn ref / non-capturing lambda is *free*
  (copyable, non-owning — preserves v1 ergonomics). A capturing lambda is *owning* (move
  semantics + RAII). A value of erased provenance (arriving through a `fn` parameter, or read out
  of a container) is conservatively treated as owning-with-runtime-drop; since the drop is
  runtime-guarded, conservative frees are always sound.
- **Compatibility stays invariant and capture-agnostic.** `fn(i32)->i32` matches a plain fn and a
  closure alike (the capture descriptor is metadata, excluded from type identity). Mismatch is
  **CE2002** on assignment and **CE2092** on call-through, exactly as v1.

### Lambda lowering (desugaring)

A lambda desugars to a synthesized top-level function plus a value build:

1. Synthesize an environment struct `__closure_env_N { <captured fields> }`, registered in the
   struct table (so the recursive destructor and struct lowering handle it for free).
2. Synthesize the lifted function `__lambda_N(env: &__closure_env_N, <lambda params>)` with the
   lambda body, rewriting each captured-name read to an env-field access. This reuses the
   monomorphizer's proven "synthesize a `FuncDef`, register a `FuncSig`, append to
   `program.functions`" machinery, so the backend emits it with zero special-casing.
3. At the lambda site, heap-allocate the env, populate captured fields (copy or move), and build
   `{@__lambda_N, env_ptr, @__closure_env_N_drop}`.

## Tier 1 — real, minimal closures (phased)

Phases are in dependency order; each names the exact seam and its acceptance test. `file:line`
anchors are under `sushi_lang/`.

**T1.0 — Representation + ABI foundation.**
`FunctionType` (`semantics/typesys.py:254-291`) gains a `captures` descriptor field, **kept out of
`__eq__`/`__hash__`** (capture-agnostic identity). Lower to the 3-word fat struct in
`backend/types/core/mapping.py:172-178`; bump sizing 8->24 in `backend/types/core/sizing.py:105-107,
231-232`. *Accept:* the full v1 fn-pointer suite still compiles + runs (`tests/run_tests.py
--enhanced`) with 24-byte values and null env/drop.

**T1.1 — Lambda grammar + AST.** Add `lambda_block` / `lambda_expr` alternatives to `atom`
(`grammar.lark:156-173`), reusing `parameters`/`type`/`block`. New `Lambda` node in
`semantics/ast.py` (near `FuncDef:82`) with `captures` and `lifted_name` fields; builder beside
`ast_builder/declarations/functions.py:parse_funcdef:14`. *Accept:* parser unit test builds a
`Lambda` from both body forms.

**T1.2 — Capture analysis (scope pass).** In `semantics/passes/scope.py`, record a lambda's free
names (resolving to enclosing locals, not globals/other top-level fns) into `Lambda.captures`.
Runs before types/borrow (`pipeline.py:349`). *Accept:* debug assertion that `|y| x + y` inside a
fn with local `x` records `captures = [x]`.

**T1.3 — Type checking + capture legality.** Add a `Lambda` handler in `passes/types/`;
type-check the body with lambda params + captured outer types; synthesize the lambda's
`FunctionType`. Reject `&poke`/`&peek` capture with a new **CE2094**. Keep call-through/assignment
compat invariant (`compatibility.py:176-185`, `calls/user_defined.py:71-105`). Factor the owning
predicate from `borrow.py:482-497` into a shared helper so capture-by-move and ownership agree.
*Accept:* positive copyable/string/struct capture; `test_err` for borrow capture (CE2094) and
invariant mismatch (CE2002/CE2092).

**T1.4 — Lambda-lifting pass.** New pass inserted **between `types` and `borrow`**
(`pipeline.py:350->351`): synthesize the env `StructType`, the lifted `FuncDef` (leading env param,
captured reads -> env GEPs), register the `FuncSig`, and append to `program.functions` /
`units[0].ast.functions` — exactly `generics/monomorphize/functions.py:325-361`. Synthesize env
destructors (reusing `backend/destructors.py:emit_value_destructor:26`, which already recurses
struct fields) and the bare-fn thunks. *Accept:* compiling a capturing lambda produces
`__lambda_N` + env struct in the debug dump.

**T1.5 — Move/ownership + RAII integration.** Add capturing (taint-driven) function values to
`_type_is_owning` (`borrow.py:482`); wire deep-copy-on-store (`backend/expressions/memory.py:346-371`),
mark-moved-on-return (`backend/statements/returns.py:_extract_return_variables:15`), and
scope-exit free (`backend/memory/scopes.py:pop_scope:90`, `backend/statements/utils.py:
emit_scope_cleanup:158`). The free is the runtime-guarded `if drop_ptr: drop_ptr(env)`. Owned-type
capture consumes the outer binding via the existing move tracker. *Accept:* return a closure,
store one in a struct/`List`, drop it — runtime-validated, no leak/double-free (ASan / leak
harness); regression: return a non-capturing `&f` still works.

**T1.6 — Backend materialization.** `emit_name` (`backend/expressions/operators.py:540-548`) builds
`{f__thunk, null, null}` for a bare fn ref; new `emit_lambda` heap-allocs the env
(`backend/generics/own.py:emit_own_alloc:136` / checked `backend/memory/heap.py:16`), populates
fields (copy or move), builds the fat value. Update the fn-valued-local sniff
(`calls/dispatcher.py:_try_function_pointer_local:108-121`) to the fat-struct shape. *Accept:*
build a capturing lambda, bind, call, print (runtime).

**T1.7 — Indirect-call env threading.** `_emit_indirect_call` (`dispatcher.py:124-135`) extracts
`fn_ptr`/`env_ptr` and prepends `env_ptr`. **T1 cut:** restrict fn-value *parameter* types to
non-owning, non-variadic (dodging the indirect path's missing deep-copy/variadic-collapse — a
latent double-free); enforce at `FunctionType` construction with the shared owning predicate. This
is independent of capture (a lambda may *capture* an owned value by move while its *signature*
takes only copyable params). *Accept:* indirect call observably reads a captured value; bare `&f`
through a `fn` param via the thunk.

**T1.8 — Stdlib combinators (the payoff).** Author `List.map` / `List.filter` / `List.fold` and a
`compose` helper **in Sushi source** (not backend intrinsics) — once indirect calls work they are
just loops calling `f(x)`, exercising the whole path end-to-end and monomorphizing through the
existing pipeline. They only *call* the fn param (never store it), so they are legal. *Accept:*
`[1,2,3].map(|x| x*scale)` and filter/fold with capture, runtime-validated; `compose(f, g)`
returning a closure.

## Tier 2 — extended (dependency-sequenced)

- **T2.1 `&poke`/`&peek` borrow capture** — lift CE2094 for borrows; track the borrow's lifetime
  *through* the closure value under the exclusivity rules. The genuinely hard problem the whole
  feature was deferred around; ship move-capture (T1) first.
- **T2.2 Bound method values** — `obj.method` as a callable via a self-binding adapter (env =
  boxed `self`); reuses T1 heap-env + drop machinery. Lifts the `obj.handler()` papercut for
  method values.
- **T2.3 Generic-function references** — lift **CE2093** (`type_visitor.py:392-394`); a generic-fn
  ref forces monomorphization at the reference site and takes the mangled address.
- **T2.4 Widen `Call.callee` from `Name` to `Expr`** (`ast.py:417-420`) — enables `obj.handler()`,
  `arr[0]()`, `(e)()`; grammar `postfix` already composes the pieces, the AST/type/backend just
  accept a non-`Name` callee routed through `_emit_indirect_call`.
- **T2.5 Indirect-path parity for owning/variadic fn-value params** — implement deep-copy +
  variadic-collapse in `_emit_indirect_call` driven by `FunctionType.param_types`; lifts the T1.7
  restriction.
- **T2.6 First-class externals / C callbacks** — a fat value with `drop_ptr = null` and `env_ptr`
  serving the C `void* userdata` convention; reuses the adapter-thunk ABI directly.

## Diagnostics

New codes (next free is **CE2094**; `errors.py` currently ends at CE2093):

- **CE2094** — illegal capture: a `&poke`/`&peek` borrow (Tier 2); an owning/variadic fn-value
  parameter type (before T2.5); or a `List<T>`/`Own<T>`/closure *value* capture (deferred — the
  lifted-body dispatch gap above). A **dynamic-array** value capture is allowed (move-capture,
  T1.5). Message names the deferred capability.
- A new "lambda parameter needs a type" diagnostic for un-inferable bare-name params (no expected
  `FunctionType` in context).

Reused unchanged: **CE2002** (assignment mismatch), **CE2092** (call-through mismatch, invariant),
**CE2093** (generic-fn ref, lifted in T2.3), **CE1001** (bare ref to a method/extern — separate
ABI).

## Risks / open problems

1. **Capture erasure at the type boundary.** `fn(i32)->i32` erases capture-ness. Resolved by the
   runtime `drop_ptr`: ownership/free is data-driven (`if drop_ptr: drop_ptr(env)`), not
   type-driven. This is the reason the 3-word layout is mandatory in T1 and cannot be retrofitted.
2. **Direct-vs-indirect ABI reconciliation.** "null env keeps v1 valid" and "indirect calls pass a
   leading env" are only jointly consistent via the **adapter-thunk split** — direct calls bare,
   indirect uniform, bare fns bridged by a thunk. A uniform "every fn gets a leading env param"
   ABI was rejected as needlessly invasive (rewrites every signature, FFI, `main`).
3. **Indirect-path asymmetry.** `_emit_indirect_call` skips the variadic-collapse and owning-struct
   deep-copy the direct path does — a latent double-free. T1 dodges it by restricting fn-value
   params to non-owning/non-variadic (T1.7); T2.5 closes it.
4. **Grammar `|` collision.** Resolved by position-based disambiguation (prefix `|` = lambda,
   infix `|` = bitwise-or). **Acceptance gate:** run the extended grammar through the parser
   generator and confirm no conflicts before landing T1.1.
5. **Ownership vs the move/borrow tracker.** Only *capturing* values are owning (capture-taint);
   non-capturing values stay copyable to preserve v1 ergonomics; the runtime-guarded drop makes
   conservative (erased-provenance) frees sound. One localized predicate change in
   `_type_is_owning`.
6. **Pass-ordering.** Lambda-lifting needs resolved capture *types* (post-`types`) but its
   synthesized functions must be borrow-checked (last pass), so it sits between them. If passes are
   reordered later, this insertion point moves with `types`.

## Test strategy

Repo conventions (`tests/run_tests.py`): `test_*`->exit 0, `test_err_*`->2, `test_warn_*`->1;
runtime via `--enhanced`. New `tests/closures/`.

- **T1 positive (exit 0, runtime):** capture primitive; call a local lambda; captured value read
  through env; capturing lambda passed down and called; bare `&f` through a fn param (thunk);
  **return a capturing closure** (escaping); **store a closure in a struct / `List`** then drop
  (leak/double-free check under ASan); capture a string + copyable struct; capture an owned `List`
  by move; `List.map/filter/fold` with capture; `compose` returning a closure.
- **T1 negative (exit 2):** borrow (`&poke`) capture -> CE2094; owning/variadic fn-value param ->
  CE2094; invariant type mismatch on assign (CE2002) and call-through (CE2092); un-inferable
  bare-name lambda param.
- **ABI guard:** a struct embedding a `fn`-typed field asserts the 24-byte fat layout; re-run the
  entire existing v1 fn-pointer suite unchanged (regression).
- **T2 (when reached):** `&poke`-capture closure; bound method value; `arr[0]()` call-expr callee;
  generic-fn ref.

## Implementation map (verified anchors)

| Concern | File:line |
|---|---|
| `FunctionType` + capture descriptor | `semantics/typesys.py:254-291` |
| Fat-pointer LLVM lowering | `backend/types/core/mapping.py:172-178` |
| Sizing 8->24 | `backend/types/core/sizing.py:105-107, 231-232` |
| Lambda grammar / `atom` | `grammar.lark:98, 156-173, 236` |
| `Lambda` node / `FuncDef` shape | `semantics/ast.py:82, 346, 417-420` |
| Capture analysis | `semantics/passes/scope.py` |
| Lambda type-check | `semantics/passes/types/` (expressions.py, type_visitor.py) |
| Lambda-lifting hook (reuse) | `semantics/generics/monomorphize/functions.py:325-361` |
| Pass ordering | `semantics/pipeline.py:342-352` |
| Ownership predicate / capture-taint | `semantics/passes/borrow.py:482-497` |
| Env heap alloc (reuse) | `backend/generics/own.py:emit_own_alloc:136`, `backend/memory/heap.py:16` |
| Recursive env destructor (reuse) | `backend/destructors.py:emit_value_destructor:26, 181-190` |
| fn-value materialization | `backend/expressions/operators.py:540-548` |
| Indirect call + local sniff | `backend/expressions/calls/dispatcher.py:108-135` |
| Move/deep-copy/return/scope-free (reuse) | `backend/expressions/memory.py:346-371`, `backend/statements/returns.py:15`, `backend/memory/scopes.py:90`, `backend/statements/utils.py:158` |
| Diagnostics | `internals/errors.py` (ends CE2093; add CE2094) |
| Fat-pointer precedent (strings) | `backend/strings.py`, `FAT_POINTER_SIZE_BYTES=12` |

## References

- `docs/design/first-class-functions.md` — v1 FCF (the floor this builds on)
- `docs/design/variadics.md` — sibling design note (house style)
