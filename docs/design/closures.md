# Design: Closures & First-Class Functions

**Status:** Function values (v1, PR #91) and Tier 1 closures (T1.0-T1.5, PR #122) are complete.
Tier 1's residuals — `List<T>`/`Own<T>` move-capture and closure-aliasing soundness — landed in
PR #122 as well. Generic higher-order functions (Gaps A/C) and `List<T>` extensibility (Gap D)
landed in #125/#126, and this release ships their payoff: the `collections/iter` combinator
module (`map`/`filter`/`fold`/`compose`), `Call.callee` widened to any expression (T2.4), and
generic-function references under an explicit expected type (T2.3). What remains is documented in
Part II: the UFCS method form `xs.map(f)` (Gap B), owned-element combinators, and the rest of
Tier 2 (`&peek`/`&poke` capture, bound-method values, indirect-path parity for owning/variadic
params, C callbacks).

This document is organized in two parts: **Part I** describes what is implemented and shippable
today; **Part II** describes what is deferred, why, and the options for closing each gap.

## Summary

Sushi has function **types** (`fn(i32) -> i32`), function **values**, and capturing **closures**.
A function type names an arity/parameter/return/error-type signature; a function *value* is
callable data of that type — a top-level function reference, or a lambda literal, optionally
capturing state from its defining scope. Both forms share one representation: a three-word fat
pointer `{fn_ptr, env_ptr, drop_ptr}`. A non-capturing value (a bare `fn` reference, or a lambda
that reads nothing from its enclosing scope) carries null `env_ptr`/`drop_ptr` and costs nothing
beyond the wider pointer; a capturing lambda heap-allocates an environment record that the value
owns and frees via `drop_ptr`.

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

---

# Part I — Implemented

## 1. The v1 floor: function types and values (non-capturing)

A top-level function can be referenced by name, stored in a variable / struct field / `List`,
passed as an argument, and called through:

```sushi
fn add_one(i32 x) i32:
    return Result.Ok(x + 1)

fn apply(fn(i32) -> i32 f, i32 v) i32:
    return Result.Ok(f(v)??)      # call through the parameter

fn main() i32:
    let fn(i32) -> i32 g = add_one    # reference a named function
    let i32 r = apply(g, 41)??        # pass it, call through it -> 42
    println(r)
    return Result.Ok(0)
```

A function type mirrors the function-declaration return/error syntax:

- `fn(i32) -> i32` — return type `i32`, error type implicitly `StdError`.
- `fn(i32) -> i32 | MathError` — explicit custom error type.
- `fn() -> ~` — no parameters, blank return.

Collections of functions use the generic form: `List<fn(i32) -> i32>` (a raw array of function
pointers is not expressible — the `[]` in `fn() -> T[]` binds to the return type).

**Result-transparent call.** A Sushi `fn` lowers to `Result<T, E>(params)`. Calling through a
function value therefore yields the same `Result<T, E>` a direct call would, so `??`, `if
(result)`, and pattern matching all work unchanged.

**Only plain top-level `fn`s are referenceable in v1.** Extension methods, perk methods, and FFI
externals have incompatible ABIs (bare-value, `self`-bound, raw-C) and live in separate tables, so
a bare reference to one is never recognized as a function value — it fails as an undeclared
identifier (**CE1001**), not CE2093. A *generic* function reference is recognized-but-deferred
territory; see §8 for the T2.3 exception now allowed, and Part II §4 for what still stays CE2093.

## 2. Lambda syntax

Two body forms, both alternatives in the `atom` grammar production:

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
map(list, |x| x * 2)                     # x : i32 inferred from an expected fn(i32) -> ... type

# zero-param form: |~| ..., NOT || (the lexer reads || as `or`)
let inc = |~| n + 1
```

- Params use Sushi's `type name` form (`|i32 x, string s|`). Bare-name params (`|x|`) are allowed
  **only** where an expected `FunctionType` supplies the types (a call argument to a fn-typed
  parameter, or a binding with a `fn(...)` annotation); otherwise it is a "lambda parameter needs a
  type" diagnostic. Return/error types are inferred from the body / expected type, or annotated
  with `-> T [| E]` after the closing pipe.
- **Result semantics are identical to `fn`.** An expression-body lambda `|x| e` desugars to a fn
  whose body is `return Result.Ok(e)`; a block-body lambda is a literal fn body. Calling through a
  closure yields `Result<T, E>` exactly like any call, so `f(x)??`, `if (f(x))`, and matching are
  unchanged.
  - *Corollary:* because the expression body is auto-wrapped in `Ok`, a fallible call in the body
    must be unwrapped with `??` **at its point of use** — a bare `Result` left in body position is
    wrapped again (`Result<Result<T, E>, E>`) and fails to typecheck. This is why `compose`'s body
    is `f(g(x)??)??`, not `f(g(x)??)`. The rule generalizes: a lambda body can never let an inner
    `Result` pass through unchanged; every fallible call needs its own `??`.
- **Block-body lambdas are a `let`-RHS-only form.** The grammar does not reach `lambda_block` from
  general `expr`, since it ends in a dedent with no trailing token, so `|x|: <block>` used directly
  as a call argument is a parse error — bind it to a `let` first.

### The `|` disambiguation

`|` is already the bitwise-or operator and the error-type separator in fn types/decls. The lambda
`|` is disambiguated **by position**: a `|` appearing where an *operand/atom* is expected (prefix
position — start of an expression, call argument, RHS of `=`) opens a lambda parameter list; a `|`
in *infix* position (between two operands) is bitwise-or. Inside a lambda's expression body, a
subsequent `|` is infix bitwise-or as usual (`|x| x | 2` = lambda with body `x | 2`). Sushi's
parser is LALR (`sushi_lang/internals/parser.py:54`), so this disambiguation is resolved by the
grammar's shift/reduce tables alone — validated through the parser generator with no new conflicts
(the T1.1 acceptance gate).

### Function types (shared, capture-agnostic)

`fn(P...) -> T [| E]`; capture is **not** part of the type (see §3), so `fn(i32) -> i32` names
both a plain fn and any closure of that arity/ok/err.

## 3. Semantics: ABI, calling convention, capture, RAII

### Representation — the three-word fat pointer

A function value lowers to `{ i8* fn_ptr, i8* env_ptr, i8* drop_ptr }` (24 bytes):

| Field | Non-capturing value | Capturing closure |
|-------|---------------------|-------------------|
| `fn_ptr`   | address of a thunk `f__thunk(env, ...)` wrapping the bare fn | address of the lifted `__lambda_N(env, ...)` |
| `env_ptr`  | `null` | heap `Own<__closure_env_N>*` holding captured values |
| `drop_ptr` | `null` | address of a type-erased env destructor |

This mirrors the existing **string** fat pointer (`{i8*, i32}`, `backend/strings.py`), applying the
same insert_value/extract_value/store/load idioms.

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

### Capture policy

- **Copyable types** (primitives, strings, structs/fixed-arrays composed of those) are captured by
  **value-copy** into the environment record.
- **Owned types** (dynamic array, `List<T>`, `Own<T>`) are captured by **move** into the
  environment — the outer binding is consumed (borrow-checker enforced; later use is CE2405), and
  the env's recursive destructor frees them.
- **A captured closure *value*** (a `fn(...)` local that is itself a capturing closure) is also
  move-captured, same as `List`/`Own` — this is what makes `compose` and capture-and-call bodies
  work (§7).
- **Borrow capture (`&poke`/`&peek`) is rejected** with CE2094 — deferred to Tier 2 (Part II §3).

### Environment ownership, escape, and RAII

- The environment is **heap-allocated and owned by the closure value** (`Own<__closure_env_N>`),
  so a closure may **escape** its creating scope — be returned, or stored in a struct/`List`.
- Freeing is **type-erased through `drop_ptr`**: at any RAII cleanup point, a function value is
  freed by `if (drop_ptr != null) drop_ptr(env_ptr)`. Non-capturing values carry `drop_ptr = null`,
  so their free is a guarded no-op. This is *why* the drop slot exists — a `fn(i32)->i32` value
  cannot tell statically whether it owns an env (capture erasure), so ownership is resolved at
  runtime by the presence of a drop function.
- **Capture-taint drives ownership analysis.** A bare fn ref / non-capturing lambda is *free*
  (copyable, non-owning — preserves v1 ergonomics). A capturing lambda is *owning* (move semantics
  + RAII). A value of erased provenance (arriving through a `fn` parameter, or read out of a
  container) is conservatively treated as owning-with-runtime-drop; the drop is runtime-guarded, so
  conservative frees are always sound.
- **Closure aliasing is sound.** A plain rebind `let g = f` **moves** the env (source consumed,
  CE2405 on later use); a container get-out (`let g = fns.get(0)??`) and a struct-field read
  (`let g = s.handler`) are non-owning **borrows** (the container/struct stays the sole owner,
  mirroring `Own<T>.get()`); a closure stored in a struct field is freed by the struct's cleanup.
  No leak, no double-free (validated with `leaks --atExit`).
- **Compatibility stays invariant and capture-agnostic.** `fn(i32)->i32` matches a plain fn and a
  closure alike (the capture descriptor is metadata, excluded from type identity). Mismatch is
  **CE2002** on assignment and **CE2092** on call-through (§9).

### Lambda lowering (desugaring)

1. Synthesize an environment struct `__closure_env_N { <captured fields> }`, registered in the
   struct table (so the recursive destructor and struct lowering handle it for free).
2. Synthesize the lifted function `__lambda_N(env: &__closure_env_N, <lambda params>)` with the
   lambda body, rewriting each captured-name read to an env-field access. This reuses the
   monomorphizer's "synthesize a `FuncDef`, register a `FuncSig`, append to `program.functions`"
   machinery, so the backend emits it with zero special-casing.
3. At the lambda site, heap-allocate the env, populate captured fields (copy or move), and build
   `{@__lambda_N, env_ptr, @__closure_env_N_drop}`.

## 4. Tier 1 delivery (T1.0-T1.5)

Landed, in dependency order:

- **T1.0** — fat-pointer ABI + sizing (`FunctionType.captures`, 24-byte lowering).
- **T1.1** — lambda grammar/AST (`lambda_expr`, `lambda_block`, `Lambda` node).
- **T1.2** — capture analysis (free-name recording in the scope pass).
- **T1.3** — type-checking + capture legality, including CE2094 for borrow capture.
- **T1.4** — lambda-lifting pass (env struct + lifted function synthesis).
- **T1.6** — backend materialization (`emit_lambda`, env heap-alloc, fat-value construction).
- **T1.7** — indirect-call env threading; CE2094 additionally rejects owning/variadic fn-value
  *parameter* types (dodging the indirect path's missing deep-copy — a latent double-free; closed
  by T2.5, Part II §3).
- **T1.5** — environment RAII + move-capture (§3), plus closure-aliasing soundness (item 2): the
  get-out/rebind double-free and struct-field leak, both closed by treating a rebind as a move and
  a get-out/field-read as a non-owning borrow.

Test coverage: `tests/closures/` — positive (`test_closure_capture_primitive`,
`test_closure_bare_param_and_multi_capture`, `test_closure_escaping`, `test_closure_owned_move_capture`,
`test_closure_list_owns`, `test_closure_list_capture`, `test_closure_list_mutate`,
`test_closure_own_capture`, `test_closure_qq_early_exit`, `test_closure_rebind_move`,
`test_closure_get_out`, `test_closure_in_struct_field`, `test_closure_capture_closure`,
`test_fn_fat_layout`, `test_fn_thunk_name_collision`); negative (`test_err_closure_borrow_capture`,
`test_err_closure_owning_param`, `test_err_closure_use_after_move_capture`,
`test_err_closure_use_after_move_rebind`).

## 5. Generic higher-order functions

Generic functions that take and call a function-typed parameter (`fn(T) -> U`) infer, monomorphize,
and run. Two gaps were closed to make this possible:

- **Gap C — infer type params through function-typed arguments.** Generic call-site inference walks
  each declared parameter type against the argument type to bind type params; a `FunctionType`
  branch was added to both twin unification routines (Pass 1.5 collection and Pass 2 validation) so
  a `fn(T) -> U` parameter recurses into its parameter types and return type, reaching the existing
  binding logic for the nested `T`/`U`. Pass 1.5 additionally learned to *present* a `FunctionType`
  for a **typed-param lambda** (`|i32 x| x * k`, params from the annotation, `ok_type` from the `->
  T` annotation or best-effort body inference) and for a **bare function reference** (`inc`, built
  from its `FuncSig`).
  - **Limitation:** a *bare-param* lambda argument to a generic (`map(xs, |x| x * k)`) is not
    inferable — its param types come from expected-type propagation, which is not available at Pass
    1.5 collection, and is circular anyway (the lambda's type depends on the type params being
    inferred *from* it). Use a **typed-param** lambda (`|i32 x| ...`) or a function reference. This
    is a graceful CE2060, not a crash.
- **Gap A — substitute `FunctionType` during monomorphization.** The three recursive
  type-substitution routines (rewriting type params to concrete types) gained a `FunctionType`
  branch that rebuilds `param_types`/`ok_type`/`err_type` recursively, carrying `captures` through
  unchanged (excluded from type identity but drives ownership).
- **Gap D — `List<T>` is user-extensible.** A first-class generic struct now: both concrete
  (`extend List<i32> sum_all()`) and generic (`extend List<T> first_or(T)`) extends compile and run.
  A user List method **cannot shadow a builtin** List method name (providers are checked first at
  dispatch); the by-value-`self`-vs-by-pointer receiver ABI mismatch is reconciled at the dispatch
  site.
- **Inline capturing-closure argument leak — fixed.** A capturing closure passed *inline* as a call
  argument (`map(xs, |x| x * k)`) previously heap-allocated an environment that was never freed,
  because it was not bound to a local and so not RAII-tracked. It is now registered in a per-scope
  temporary registry and freed via the runtime-guarded drop on every exit path; binding to a local
  is no longer required.

Validated as **free generic functions** (`tests/generics/test_ho_*`): `map<T, U>(List<T>, fn(T) ->
U)` with a capturing closure and with `U` genuinely differing from `T` (i32 -> bool);
`filter<T>(List<T>, fn(T) -> bool)` with a capturing predicate; `fold<T, U>(List<T>, U, fn(U, T) ->
U)` with two independently-inferred type params; `apply<T>(fn(T) -> T, T)` with a bare fn reference.

## 6. `collections/iter` — the bundled Sushi-source stdlib module

`use <collections/iter>` ships `map`, `filter`, `fold`, and `compose` as ordinary **generic free
functions** — the delivery of the T1.8/Gap-A/C payoff. Source:
`sushi_lang/sushi_stdlib/src_sushi/collections/iter.sushi`; docs: `docs/stdlib/collections/iter.md`.

```sushi
use <collections/iter>

fn main() i32:
    let i32 factor = 10
    let List<i32> xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    let List<i32> ys = map(xs, |i32 x| x * factor).realise(List.new())
    println(ys.get(2).realise(-1))    # 30
    return Result.Ok(0)
```

**This is the first bundled-Sushi-source stdlib module** — a real pattern, not a one-off:

- A `.sushi` file lives under `sushi_lang/sushi_stdlib/src_sushi/` and is registered in
  `SOURCE_STDLIB_MODULES` (`semantics/stdlib_registry.py`), mapping the `use <...>` path to the
  bundled source file.
- The compiler **pipeline injects it as a compilation unit** (`compiler/pipeline.py`) before
  symbol-table build, exactly like a user unit — no bitcode, no platform-specific `.bc`. The module
  joins the virtual-unit set (no `.bc` resolution) and incremental codegen skips it (nothing to
  cache; it is only ever monomorphized).
- **Why bundled `.sushi` source, not Python-synthesis:** every other stdlib module (`List`, string
  methods, `HashMap`) is a Python IR emitter (`sushi_lang/sushi_stdlib/src/`), hand-lowering LLVM
  IR. Combinators over generics have no fixed concrete signature to emit ahead of time — they need
  the *existing* generic monomorphization pipeline. Shipping them as ordinary Sushi source lets them
  ride that pipeline for free: nothing is emitted unless a program actually instantiates a
  combinator, and adding a new combinator is just adding a function to the `.sushi` file.
- **Why opt-in `use`, not an auto-prelude:** consistent with every other stdlib module
  (`collections/hashmap`, `io/stdio`, `time`, ...) — Sushi has no implicit prelude, and combinators
  are unremarkable generic functions, not language primitives.

**Constraints (documented in the module and its doc page):**

- **Copy/primitive element types only.** `filter` re-pushes each kept element and `map` reads each
  one; owned-element combinators are deferred (Part II §2).
- **Free-function call syntax only:** `map(xs, f)`, not `xs.map(f)` — the UFCS method form needs
  method-level type parameters (Gap B, Part II §1).
- **Function argument must be a typed-param lambda or a function reference** — a bare-param lambda
  (`|x| ...`) cannot be inferred against a generic parameter (§5's Gap-C limitation).

### `compose` — the capture-and-call payoff

```sushi
fn compose<T, U, V>(fn(T) -> U g, fn(U) -> V f) fn(T) -> V:
    return Result.Ok(|x| f(g(x)??)??)
```

`compose`'s returned lambda **captures** `f` and `g` (both function values, one of them possibly a
closure) and **calls** them in its body — the capture-and-call case that was CE2094-blocked before
T2.4 (§7). `compose`'s lambda parameter is a **bare** `|x|`, not a type-param-annotated `|T x|` —
see Part II §5 for why a `|T x|` lambda parameter is not yet substituted during monomorphization,
which is why the bare form is used here.

```sushi
use <collections/iter>

fn inc(i32 x) i32:
    return Result.Ok(x + 1)

fn dbl(i32 x) i32:
    return Result.Ok(x * 2)

fn main() i32:
    let fn(i32) -> i32 incthendouble = compose(inc, dbl).realise(dbl)
    println(incthendouble(10).realise(-1))    # dbl(inc(10)) = 22
    return Result.Ok(0)
```

Test coverage: `tests/stdlib/test_iter_module_map.sushi`, `test_iter_module_filter.sushi`,
`test_iter_module_fold.sushi`, `test_iter_module_fnref.sushi`, `test_iter_compose.sushi`,
`test_err_iter_unknown_module.sushi`.

## 7. Call-through arbitrary expressions (T2.4)

`Call.callee` is widened from `Name` to any `Expr`. Calling through an arbitrary expression that
evaluates to a function value now works, reusing the fat-pointer indirect-call path unchanged:

- **A captured closure read back in a lifted lambda body** — `env.f(x)` — is exactly what makes
  `compose` and any capture-and-call closure body compile (§6, §3).
- **A fn-typed struct field**, called directly: `obj.handler()`. A `DotCall` routes to an *indirect
  field-call* when the receiver struct has a fn-typed field of that name **and no method of that
  name** — a same-named method always wins. No `let f = obj.handler` workaround needed:

  ```sushi
  struct Handler:
      fn(i32) -> i32 op

  fn run(Handler h, i32 v) i32:
      return Result.Ok(h.op(v)??)     # calls the field directly
  ```

- **A `List` get-out or a parenthesized expression**, called immediately: `arr[0]()`, `(e)()`,
  `fns.get(0)??(x)`, `(fns.get(0)??)(x)`.

Mechanically: the AST builder now emits a general `Call` for a non-`Name`, non-`MemberAccess` call
base; the type checker infers the non-`Name` callee and, when it resolves to a `FunctionType`,
dispatches to the same indirect-call validator used for a named local, annotating the node for the
backend. `??` was also taught to unwrap a `ResultType` **and** a `Maybe` `Some` payload, so a
function-value call inside a lambda body can infer its return type through a `Maybe`-returning
chain, not just a `Result`-returning one.

**This lifts the CE2094 "capturing and calling a closure value" clause.** Capturing another closure
and calling it in the body — previously deferred because the call `env.f(x)` was a non-`Name`
callee — now compiles:

```sushi
fn run() i32:
    let i32 n = 10
    let fn(i32) -> i32 g = |i32 x| x + n
    let fn(i32) -> i32 h = |i32 y|:
        return Result.Ok(g(y)?? + 1)
    return Result.Ok(h(5)??)          # g(5) = 15, h(5) = 16
```

Two former backend cast failures on this path are now precise front-end **CE2002** diagnostics
instead.

Test coverage: `tests/closures/test_closure_capture_closure.sushi`,
`tests/functions/test_call_index_result.sushi`, `tests/functions/test_fn_value_field_call.sushi`,
`tests/functions/test_fn_value_in_struct.sushi`.

## 8. Generic-function references — the T2.3 annotated slice

Referencing a generic function as a value is now allowed **when an explicit expected function type
is present**:

```sushi
fn identity<T>(T x) T:
    return Result.Ok(x)

fn run() i32:
    let fn(i32) -> i32 g = identity   # the annotation drives the instantiation identity<i32>
    return Result.Ok(g(41)?? + 1)     # 42
```

This is a **minimal, expected-type-driven slice**, not a general lift of CE2093: Pass 1.5 collects
the instantiation from a `let` whose declared type is a `FunctionType` and whose value is a
generic-fn name (unifying the signature against the expected type); the type pass then solves the
type args, rewrites the `Name` to the mangled concrete name, and infers the concrete `FunctionType`.
The backend is unchanged — the mangled monomorphized function materializes as an ordinary fn value.

A generic-fn reference **into a higher-order function** works the same way, via a typed local
binding first (not directly as a bare argument):

```sushi
use <collections/iter>

fn identity<T>(T x) T:
    return Result.Ok(x)

fn run() i32:
    let fn(i32) -> i32 id = identity   # fixes the instantiation
    let List<i32> xs = List.new()
    xs.push(5)
    xs.push(7)
    let List<i32> ys = map(xs, id)??
    return Result.Ok(ys.get(1).realise(-1))   # 7
```

What still stays CE2093 is covered once, in Part II §4.

Test coverage: `tests/generics/test_generic_fn_ref.sushi`,
`tests/generics/test_generic_fn_ref_higher_order.sushi`,
`tests/generics/test_err_generic_fn_ref_no_type.sushi`.

## 9. Diagnostics (live)

- **CE2002** — a function value assigned to a variable or parameter of an incompatible function
  type (a plain assignment mismatch, *not* a call-through).
- **CE2092** — function-value type mismatch (arity / parameter / return / error type) when
  **calling through** a function value. Function types are invariant.
- **CE2093** — illegal function reference: a bare reference to a **generic** function with **no**
  expected function type in context (Part II §4 has the exact remaining boundary). Extension
  methods, perk methods, and FFI externals are not bare-referenceable at all — they surface as an
  undeclared identifier (**CE1001**), not CE2093.
- **CE2094** — illegal closure capture: a `&peek`/`&poke` borrow (Tier 2, Part II §3); or an owning
  /variadic fn-value *parameter* type (before T2.5, Part II §3). **Dynamic-array**, **`List<T>`**,
  **`Own<T>`**, and now **closure-value** captures are all allowed (move-capture). The former
  "capturing and calling a closure value" clause is **lifted** by T2.4 (§7) — that call now compiles
  instead of erroring.

## 10. Implementation map (verified anchors)

| Concern | File:line |
|---|---|
| `FunctionType` + capture descriptor | `semantics/typesys.py:254-291` |
| Fat-pointer LLVM lowering | `backend/types/core/mapping.py:172-178` |
| Sizing 8->24 | `backend/types/core/sizing.py:105-107, 231-232` |
| Lambda grammar / `atom` | `grammar.lark:98, 156-173, 236` |
| `Lambda` node / `FuncDef` shape | `semantics/ast.py:82, 346` |
| `Call.callee` widened to `Expr` | `semantics/ast.py`; `semantics/ast_builder/expressions/chains.py` |
| Capture analysis | `semantics/passes/scope.py` |
| Lambda type-check, CE2094, bare-param inference | `semantics/type_visitor.py` |
| Expected-type propagation to bare-param lambdas | `semantics/passes/types/propagation.py` |
| Lambda-lifting pass | `semantics/passes/lambda_lift.py` |
| Shared fn-synthesis wiring | `semantics/generics/synthesis.py:register_synthesized_function` |
| Ownership predicate (single source of truth) | `semantics/typesys.py:is_owning_type` |
| Env heap alloc / recursive env destructor | `backend/generics/own.py:emit_own_alloc`, `backend/destructors.py:emit_value_destructor` |
| Runtime API (thunk, build value, indirect call, `emit_lambda`) | `backend/runtime/closures.py` |
| Backend expr dispatch -> `emit_lambda` | `backend/expressions/__init__.py` (`case Lambda()`) |
| Indirect call, non-`Name` callee routing | `backend/expressions/calls/dispatcher.py`, `backend/expressions/calls/utils.py` |
| Generic higher-order unification (Pass 2 / Pass 1.5) | `semantics/passes/types/calls/generics.py:_unify_types_for_inference`; `semantics/generics/instantiate/types.py:unify_types` |
| `FunctionType` substitution (monomorphization) | `semantics/generics/monomorphize/transformer.py`; `semantics/generics/types.py`; `backend/generics/extensions.py` |
| Gap D (`List<T>` extensibility) | `semantics/passes/collect/__init__.py:373` (List as generic struct); `backend/expressions/calls/dispatcher.py:268,308,355` (provider-first dispatch + receiver reconcile) |
| T2.3 generic-fn-ref-under-annotation | `semantics/generics/instantiate/expressions.py`; `semantics/generics/instantiate/functions.py`; `semantics/passes/types/calls/generics.py` |
| `collections/iter` source module | `sushi_lang/sushi_stdlib/src_sushi/collections/iter.sushi` |
| Source-stdlib-module registry + pipeline injection | `semantics/stdlib_registry.py:SOURCE_STDLIB_MODULES`; `compiler/pipeline.py` |
| Diagnostics | `internals/errors.py` (CE2002:627, CE2092:984, CE2093:988, CE2094:992) |
| Fat-pointer precedent (strings) | `backend/strings.py` |

Where the passes actually run (worth knowing before touching any of the above): the live semantic
pipeline is `semantics/semantic_analyzer.py`, not the `build_pipeline`/`add_pass` scaffold in
`semantics/pipeline.py` (that scaffold is dead code — nothing calls `build_pipeline`). The
lambda-lift pass is inserted in both `_check_single_file` and `_check_multi_file`, after
`type_validator.run(...)` and before `borrow_checker.run(...)`.

---

# Part II — Deferred

## 1. UFCS method form `xs.map(f)` — Gap B

`extend List<T> map<U>(fn(T)->U f) List<U>` cannot be expressed today. A *same-type* combinator
(`extend List<T> map(fn(T)->T f) List<T>`) already works (Gap D closed this half); only a
*type-changing* method — one that needs its own method-level type parameter `<U>` — is blocked.
Four pieces are missing:

1. **Grammar** (`grammar.lark:36`): `extend_def` is `NAME "(" [parameters] ")" type ...` — no
   `[type_params]` slot after the method name (contrast `function_def` at `:45`, which has one).
   `xs.map(f)` (inference-only call, no explicit `<U>` — Sushi has no method type-arg syntax) already
   *parses*; only the **definition** needs the slot. `<` after a method NAME in `extend_suffix` is
   unambiguous, so this is low-risk, but still needs the LALR acceptance-gate (run the grammar
   through the parser generator, as with the lambda `|`).
2. **AST/collect**: `ExtendDef` (`ast.py:135`) has no `type_params` field; collect
   (`semantics/passes/collect/functions.py:748`) derives extension type params from the *receiver's*
   `target_type.type_args` **only** (stored on `GenericExtensionMethod.type_params`). Needs a
   method-param field distinct from the receiver params, unioned for body type-resolution.
3. **Call-site inference**: method calls (`semantics/passes/types/calls/methods.py:234-255`) are
   receiver-driven — concrete type args come entirely from the receiver type; there is **no**
   argument unification. The free-function unifier
   (`semantics/passes/types/calls/generics.py:_unify_types_for_inference`, extended for `fn(T)->U`
   in §5's Gap C) would need to be reused for method calls to solve `U` from the `f` argument.
4. **Monomorphization**: `monomorphize_all_extension_methods` (`backend/generics/extensions.py:148`)
   is eager/receiver-driven, keyed on `struct_instantiations` with a strict `zip` of
   `generic_method.type_params` against the receiver's `type_args` (CE0096 on count mismatch).
   Method params need a call-site-driven instantiation dimension combining receiver args **and**
   independently-inferred method args.

**Options:**

- **(A) Do nothing — free-function form (current default).** `map(xs, f)` works today, including
  type-changing (`i32 -> bool`) and capturing closures. The method form is pure UFCS sugar. Zero
  cost; this is what `collections/iter` documents and ships.
- **(B) Same-type-only method combinators.** Ship `extend List<T>` methods whose result type is `T`
  (in-place-style map, filter, fold-to-`T`). Works **today** on the back of Gap D, no Gap B needed.
  Real subset; type-changing map/fold still fall back to free functions.
- **(C) Implement Gap B.** Medium-large. Reuses the higher-order inference (Gap C) and substitution
  (Gap A) machinery from §5; the crux is bridging the eager receiver-driven extension monomorphizer
  to a call-site-driven one for method params. Unlocks the full ergonomic `xs.map(f)`.

Recommendation unchanged: pursue (A)/(B) for the parity payoff (already done); defer (C) until a
concrete consumer wants the fluent method form.

**Constraints on List extension methods worth knowing (from the Gap D fix):**

- **Builtin names cannot be shadowed.** The backend dispatcher checks List provider methods
  (`push`/`get`/`iter`/…) *before* the user-extension fallback, so a user `extend List<T> push()` is
  unreachable. Only non-builtin names route to the extension path.
- **Receiver ABI reconciliation.** A List-backed receiver shares the dynamic-array `{i32, i32, T*}`
  layout and is passed by pointer, but `self` is declared by value; the dispatch site loads the
  header to reconcile (safe because extension bodies never register `self` for cleanup, so the
  shared buffer is not double-freed).

## 2. Owned-element combinators — deferred

`collections/iter`'s `map`/`filter`/`fold` assume copy/primitive element types: `filter` re-pushes
each kept element by copy, `map` reads each element by copy before applying `f`. A `List<T>` where
`T` is an owned type (dynamic array, `List<U>`, `Own<U>`, or a struct containing one) is not
supported yet — re-pushing/reading would need move-aware element handling the current combinator
bodies don't do. No diagnostic gate exists specifically for this; it is a correctness gap to close
before advertising owned-element support, not a capability that was evaluated and rejected.

## 3. Remaining Tier 2

- **T2.1 — `&peek`/`&poke` borrow capture.** Lift CE2094 for borrows; track the borrow's lifetime
  *through* the closure value under the exclusivity rules. **Why deferred:** this is the genuinely
  hard problem the whole closures feature was scoped around — a borrow captured into an escaping,
  heap-allocated environment can outlive the stack frame that issued it, which the current
  scope-based borrow checker has no model for. Move-capture (Tier 1) sidesteps it entirely by never
  letting a reference cross into an environment.
- **T2.2 — Bound method values.** `obj.method` as a callable via a self-binding adapter (env =
  boxed `self`); reuses the Tier 1 heap-env + drop machinery. Lifts the last `obj.handler()`-shaped
  papercut that isn't already covered by T2.4's field-call routing (§7) — specifically, a bound
  *method* reference, not a fn-typed *field* read. **Why deferred:** no concrete consumer yet;
  mechanically straightforward once wanted.
- **T2.5 — Indirect-path parity for owning/variadic fn-value params.** Implement deep-copy +
  variadic-collapse in the indirect-call emitter, driven by `FunctionType.param_types`; lifts the
  T1.7 restriction that fn-value parameter types must be non-owning, non-variadic. **Why deferred:**
  the direct-call path already does this; the indirect path's asymmetry is a latent double-free that
  T1.7 dodges by restricting param types rather than fixing the emitter — closing it is scoped,
  low-risk cleanup with no capability payoff until an owning fn-value parameter is actually needed.
- **T2.6 — First-class externals / C callbacks.** A fat value with `drop_ptr = null` and `env_ptr`
  serving the C `void* userdata` convention; reuses the adapter-thunk ABI directly. **Why
  deferred:** no FFI callback consumer yet; independent of the rest of Tier 2.

## 4. What still stays CE2093

A generic-function reference is CE2093 **except** the T2.3 annotated slice (§8): an explicit
expected function type (an fn-typed `let` annotation) must be present at the reference site. A bare
reference with **no** expected fn type — e.g. passing a generic function directly as an argument
without first binding it to a typed local — is still CE2093:

```sushi
fn identity<T>(T x) T:
    return Result.Ok(x)

fn take(fn(i32) -> i32 f) i32:
    return Result.Ok(f(1)??)

fn main() i32:
    let i32 r = take(identity)??      # CE2093 -- no expected type at this reference
    println(r)
    return Result.Ok(0)
```

Bind it to a typed local first (`let fn(i32) -> i32 id = identity; take(id)`) to get the T2.3 path.
Extension methods, perk methods, and FFI externals remain outside CE2093 entirely — they are not in
the function table at all, so a bare reference to one is CE1001 (undeclared identifier), a distinct
diagnostic for a distinct reason (incompatible ABI, not deferred capability).

## 5. The `|T x|` lambda-parameter monomorphization gap

A lambda parameter annotated with a type parameter from the enclosing generic (`|T x| ...` inside a
`fn foo<T>(...)`) is not substituted during monomorphization — the lambda-lifting machinery lifts
the lambda before the enclosing function's type-param substitution reaches its params. The
workaround is a **bare** parameter (`|x| ...`), letting expected-type propagation supply the
concrete type at each call site instead of relying on substitution. This is why `compose` (§6) is
written as `|x| f(g(x)??)??` rather than `|T x| ...`, even though `compose` is itself generic over
`T`. No diagnostic currently flags a `|T x|` misuse distinctly from any other unresolved-type case;
treat this as a known authoring gotcha rather than a validated error path.

## 6. Risks / open problems

1. **Capture erasure at the type boundary.** `fn(i32)->i32` erases capture-ness. Resolved by the
   runtime `drop_ptr`: ownership/free is data-driven (`if drop_ptr: drop_ptr(env)`), not
   type-driven. This is why the 3-word layout was mandatory from T1 and could not be retrofitted.
2. **Direct-vs-indirect ABI reconciliation.** "null env keeps v1 valid" and "indirect calls pass a
   leading env" are only jointly consistent via the adapter-thunk split — direct calls bare,
   indirect uniform, bare fns bridged by a thunk. A uniform "every fn gets a leading env param" ABI
   was rejected as needlessly invasive (rewrites every signature, FFI, `main`).
3. **Indirect-path asymmetry (T1.7/T2.5).** See Part II §3 — a latent double-free dodged by
   restricting fn-value param types rather than fixed at the emitter; T2.5 is the eventual close.
4. **Grammar `|` collision — resolved.** Position-based disambiguation (prefix `|` = lambda, infix
   `|` = bitwise-or), validated through the parser generator with no new conflicts.
5. **Ownership vs. the move/borrow tracker.** Only *capturing* values are owning (capture-taint);
   non-capturing values stay copyable to preserve v1 ergonomics; the runtime-guarded drop makes
   conservative (erased-provenance) frees sound. One localized predicate change in `is_owning_type`.
6. **Pass-ordering.** Lambda-lifting needs resolved capture *types* (post-`types`) but its
   synthesized functions must be borrow-checked (last pass), so it sits between them. If passes are
   reordered later, this insertion point moves with `types`.

## 7. Fast path to re-enter

1. Read Part I (above) for current capability, then this Part II for what's left and why.
2. `git log --oneline 430b5bd..HEAD -- sushi_lang docs/design/closures.md` — the closures + T1.8 +
   T2.3/T2.4 feature commits are the phase history; everything is on `main`.
3. Reproduce the working baseline: compile+run `tests/closures/test_closure_escaping.sushi` (prints
   15), `tests/closures/test_closure_owned_move_capture.sushi` (13),
   `tests/closures/test_closure_capture_closure.sushi` (16), `tests/stdlib/test_iter_compose.sushi`
   (22), `tests/generics/test_generic_fn_ref.sushi` (42).
4. Pick the remaining item by leverage:
   - **Gap B (§1)** — the method form `xs.map(f)`; start with the grammar acceptance-gate, reuse
     the higher-order unifier for method-call inference, then bridge the extension monomorphizer to
     a call-site-driven path.
   - **Owned-element combinators (§2)** — needs move-aware `map`/`filter` bodies; scope it against a
     concrete consumer (e.g. a `List<List<T>>` transform) before generalizing.
   - **T2.1-T2.6 (§3)** — pick by consumer need; T2.2/T2.5/T2.6 are mechanically straightforward,
     T2.1 is the hard one and should stay last.
5. Keep the enhanced suite green after each step (`python tests/run_tests.py --enhanced`);
   leak-check runtime cases with macOS `leaks --atExit` (baseline noise: ~16 bytes in `user_main`,
   present even in a trivial no-closure program).

## Test strategy (repo conventions)

`tests/run_tests.py`: `test_*` -> exit 0, `test_err_*` -> exit 2, `test_warn_*` -> exit 1; runtime
validated via `--enhanced`. Ground truth lives in `tests/closures/`, `tests/generics/test_ho_*`,
`tests/generics/test_generic_fn_ref*`, and `tests/stdlib/test_iter_*` — see the test-coverage lines
under each Part I section above for the full file list.
