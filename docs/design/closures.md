# Design: Closures & First-Class Functions

**Status:** implemented: function values, capturing closures with move-capture (including
`List@(T)`/`Own@(T)` and closure-aliasing soundness), generic higher-order functions (Gaps A/C),
`List@(T)` extensibility (Gap D), the `collections/iter` combinator module
(`map`/`filter`/`fold`/`compose`), `Call.callee` over any expression (T2.4), generic-function
references under an expected function type (T2.3), and the UFCS method form `xs.map(f)` (Gap B,
`docs/design/ufcs-combinators.md`). Deferred, in Part II: owned-element combinators for the free
functions and the method-form `map`/`fold`, `peek`/`poke` capture, bound-method values,
indirect-path parity for owning/variadic params, and C callbacks. The labels T1.x, T2.x and
Gap A-D name the work items of this record.

This document is organized in two parts: **Part I** describes what is implemented and shippable
today; **Part II** describes what is deferred, why, and the options for closing each gap.

## Summary

Sushi has function **types** (`fn(i32) -> i32`), function **values**, and capturing **closures**.
A function type names an arity/parameter/return/error-type signature; a function *value* is
callable data of that type — a top-level function reference, or a lambda literal, optionally
capturing state from its defining scope. Both forms share one representation: a four-word fat
pointer `{fn_ptr, env_ptr, drop_ptr, clone_ptr}` (the `clone_ptr` word exists because `.clone()`
is total over every type — see below). A non-capturing value (a bare `fn` reference,
or a lambda that reads nothing from its enclosing scope) carries null `env_ptr`/`drop_ptr`/
`clone_ptr` and costs nothing beyond the wider pointer; a capturing lambda heap-allocates an
environment record that the value owns, frees via `drop_ptr`, and duplicates via `clone_ptr` when
`.clone()`d.

```sushi
use <collections/iter>

fn make_adder(i32 n) fn(i32) -> i32:
    return |i32 x| x + n      # captures n by value; escapes upward (returned)

fn demo() ~ | StdError:
    let fn(i32) -> i32 add5 = make_adder(5)
    println(add5(10))                  # 15

    let i32 scale = 3
    let List@(i32) out = from([1, 2, 3]).map(|i32 x| x * scale)   # captures scale
    println(out.len())                   # 3
    return Result.Ok(~)

fn main() i32:
    match demo():
        Result.Ok(_) -> return 0
        Result.Err(_) -> return 1
```

---

# Part I — Implemented

## 1. Function types and values (non-capturing)

A top-level function can be referenced by name, stored in a variable / struct field / `List`,
passed as an argument, and called through:

```sushi
fn add_one(i32 x) i32:
    return x + 1

fn apply(fn(i32) -> i32 f, i32 v) i32:
    return f(v)      # call through the parameter

fn main() i32:
    let fn(i32) -> i32 g = add_one    # reference a named function
    let i32 r = apply(g, 41)        # pass it, call through it -> 42
    println(r)
    return 0
```

A function type mirrors the function-declaration return/error syntax:

- `fn(i32) -> i32` — return type `i32`, BARE: no error channel, a call yields the `i32`.
- `fn(i32) -> i32 | MathError` — an error channel: a call yields `Result@(i32, MathError)`.
- `fn() -> ~` — no parameters, blank return.

Collections of functions use the generic form: `List@(fn(i32) -> i32)` (a raw array of function
pointers is not expressible — the `[]` in `fn() -> T[]` binds to the return type).

**Channel-transparent call.** A Sushi `fn` with a channel lowers to `Result@(T, E)(params)`, and
a bare `fn` lowers to `T(params)`. Calling through a function value therefore yields what a direct
call would: the value for a bare type, and `Result@(T, E)` for a type with a channel, where `??`,
`.realise(default)`, `.is_ok()` and pattern matching all work unchanged. A `Result` is not a
condition: `if (f(x))` is CE2516, as for a direct call. The channel is part of the function type,
so a bare type and a type with a channel do not convert (CE2002).

A function with a channel that returns a function type writes the explicit form,
`fn make() Result@(fn(i32) -> i32, StdError):`. A `| E` written after a function type belongs to
that function type, so `fn make() fn(i32) -> i32 | StdError:` is a bare function that returns a
function type with a channel.

A bare function is the exception, not the default style. Use it only when the function is total
over its inputs and will stay so (a combinator argument, a pure arithmetic helper). Write a
channel for everything that can fail, or can gain a failure later. A public function keeps a
channel when there is any doubt, because a channel added later breaks every caller and every
binary `.slib`. The compiler does not enforce this; `docs/design/error-channel.md` carries the
rule.

**Only plain top-level `fn`s are referenceable.** Extension methods, perk methods, and FFI
externals have incompatible ABIs (bare-value, `self`-bound, raw-C) and live in separate tables, so
a bare reference to one is never recognized as a function value — it fails as an undeclared
identifier (**CE1001**), not CE2093. A *generic* function reference is recognized-but-deferred
territory; see §8 for the T2.3 exception that is allowed, and Part II §4 for what still stays CE2093.

A plain `fn` of another unit is referenceable wherever the unit may name it: through a
flat `use`, a `public use` re-export, or behind an alias (`l.plain`). The scope pass and the
typecheck pass read one unit-scoped lookup for this rung, the unit's own concrete `fn` wins over
an imported one, and the fences of the call apply to the value: CE3005 for a private
`fn`, CE3012 for a name two imports bring. A generic behind an alias (`l.gen`) follows the bare
name: legal where an expected function type solves it, CE2093 where nothing does.

## 2. Lambda syntax

Two body forms, both alternatives in the `atom` grammar production:

```sushi
# expression body: |params| expr   (bare: desugars to `return expr`;
# with a channel: desugars to `return Result.Ok(expr)`)
let fn(i32) -> i32 f = |i32 x| x + n

# block body: |params|: <indented block>  (a full fn body, with the rule of a fn body)
let fn(i32) -> i32 g = |i32 x|:
    let i32 y = x * 2
    return y + n                         # the type is bare, so the body returns the value

# optional return / error annotation after the closing pipe
let fn(i32) -> i32 | MathError h = |i32 x| -> i32 | MathError: ...

# param types inferred from an expected function type (an annotated binding, or an
# argument to a parameter of a CONCRETE function type)
let fn(i32) -> i32 k = |x| x * 2         # x : i32 from the annotation

# zero-param form: |~| ..., NOT || (the lexer reads || as `or`)
let fn() -> i32 inc = |~| n + 1
```

- Params use Sushi's `type name` form (`|i32 x, string s|`). Bare-name params (`|x|`) are allowed
  **only** where an expected `FunctionType` supplies the types (a call argument to a parameter of a
  concrete function type, or a binding with a `fn(...)` annotation); otherwise it is a "lambda
  parameter needs a type" diagnostic. A parameter of a GENERIC function type supplies nothing:
  `map(xs, |x| x * 2)` is CE2060, and `map(xs, |i32 x| x * 2)` compiles. The return type comes
  from the body or the expected type, or it is annotated with `-> T [| E]` after the closing pipe.
- **The channel comes from the TYPE, never from the body.** A lambda has an error channel only
  when its type writes `| E`: the annotation after the closing pipe, or the expected type (a
  parameter, a `let`, a field). The compiler never infers a channel from the body. A bare lambda
  is a bare fn: its expression body `|x| e` desugars to `return e`, a block body returns the value,
  a `Result.Ok` or `Result.Err` there is CE2091, and a `??` there is CE0131 (the typecheck pass
  emits it, because the channel is known only when the type is). A lambda with a channel is a fn
  with a channel: `|x| e` desugars to `return Result.Ok(e)`, a block body spells both
  constructors, and a `??` is legal in any body (#399). Calling through a closure yields what any
  call yields (and `if (f(x))` is CE2516, as for any call).
  - *Corollary for a channel lambda:* the expression body is wrapped in `Ok`, so a fallible call in
    the body must be unwrapped with `??` **at its point of use** — a `Result` left in body position
    is wrapped again (`Result@(Result@(T, E), E)`) and fails to typecheck. `compose` is bare, so its body
    is `f(g(x))`.
- **Block-body lambdas are a `let`-RHS-only form.** The grammar does not reach `lambda_block` from
  general `expr`, since it ends in a dedent with no trailing token, so `|x|: <block>` used directly
  as a call argument is a parse error — bind it to a `let` first.

### The `|` disambiguation

`|` is already the bitwise-or operator and the error-type separator in fn types/decls. The lambda
`|` is disambiguated **by position**: a `|` appearing where an *operand/atom* is expected (prefix
position — start of an expression, call argument, RHS of `=`) opens a lambda parameter list; a `|`
in *infix* position (between two operands) is bitwise-or. Inside a lambda's expression body, a
subsequent `|` is infix bitwise-or as usual (`|x| x | 2` = lambda with body `x | 2`). Sushi's
parser is LALR (the parser options in `sushi_lang/internals/parser.py`), so this disambiguation is resolved by the
grammar's shift/reduce tables alone — validated through the parser generator with no new conflicts
(the T1.1 acceptance gate).

### Function types (shared, capture-agnostic)

`fn(P...) -> T [| E]`; capture is **not** part of the type (see §3), so `fn(i32) -> i32` names
both a plain fn and any closure of that arity/ok/err.

The parameter MODES are part of the type (`docs/design/borrow-model.md` §7):
`fn(nom string) -> i32` and `fn(string) -> i32` are two types, and assigning a value of one to
the other is CE2002. A lambda parameter carries a mode too (`|nom string s| ...`; the grammar
rule is `lambda_param: NOM? type NAME`), and a value built from a function declared with
`nom string s` has the type `fn(nom string) -> i32`. `FunctionType.modes` holds the modes, and
`types_compatible` compares them in one place.

## 3. Semantics: ABI, calling convention, capture, RAII

### Representation — the four-word fat pointer

A function value lowers to `{ i8* fn_ptr, i8* env_ptr, i8* drop_ptr, i8* clone_ptr }` (32 bytes):

| Field | Non-capturing value | Capturing closure |
|-------|---------------------|-------------------|
| `fn_ptr`    | address of a thunk `f__thunk(env, ...)` wrapping the bare fn | address of the lifted `__lambda_N(env, ...)` |
| `env_ptr`   | `null` | heap `Own@(__closure_env_N)*` holding captured values |
| `drop_ptr`  | `null` | address of a type-erased env destructor |
| `clone_ptr` | `null` | address of a type-erased env cloner: allocates a fresh environment record and deep-copies each captured field into it |

`clone_ptr` exists because `.clone()` is total over every type in the language
(`docs/design/move-semantics.md` §4), and a function value is one of those types.
`build_closure_value` (`backend/runtime/closures.py`) assembles all four fields; a non-capturing
value's `clone_ptr` is `null`, so `.clone()`-ing a bare `fn` reference is a runtime-guarded no-op
that hands back an equivalent fat value, exactly like `drop_ptr`'s guarded free.

This mirrors the existing **string** fat pointer (`{i8*, i32, i8}`, see
`docs/design/string-representation.md`), applying the same insert_value/extract_value/store/load
idioms.

### Calling convention — adapter-thunk split

- **Direct calls stay bare.** `f(x)` where `f` names a top-level fn lowers to a direct call
  instruction; no signature or call site changes. FFI externs and `main` are untouched.
- **Indirect calls are uniformly env-passing.** Calling through a function *value* extracts
  `fn_ptr`/`env_ptr` and calls `fn_ptr(env_ptr, args...)` — `env_ptr` prepended as a hidden
  leading argument.
- **A bare fn used as a value is bridged by a thunk.** Materializing a top-level fn as a value
  synthesizes (once, cached) `f__thunk(i8* env, <params>) { return f(<params>) }` and stores
  `{f__thunk, null, null, null}`. The thunk ignores `env`, so the indirect ABI is uniform without
  touching any real function body.

### Capture policy

Capture is a `ConsumingUse.CAPTURE` — an ownership sink like any other in
`docs/design/ownership-conventions.md` — so what happens is the shared `classify()` table's answer
for the captured variable's provenance and type class, not a closures-specific rule:

- **Types that own no heap** (primitives, and a `string` bound directly from a
  literal, which owns nothing at that binding) are captured by **value-copy** into the environment
  record; the outer binding stays usable.
- **Types that own heap** (dynamic array, `List@(T)`, `Own@(T)`, `HashMap@(K, V)`, and
  **any `string` not bound from a literal**, e.g. one built by interpolation, returned from a
  call, or arriving as a parameter) are captured by **move** into the environment — the outer
  binding is consumed (borrow-checker enforced; later use is CE2405), and the env's recursive
  destructor frees them. A struct or fixed array composed only of non-owning fields is captured by
  copy, exactly like a bare primitive; one with an owning field (including a plain `string`
  field) is captured by move, exactly like a bare owning value.
- **A captured closure *value*** (a `fn(...)` local that is itself a capturing closure) is also
  move-captured, same as any other owning type — this is what makes `compose` and capture-and-call
  bodies work (§7).
- **Borrow capture (`poke`/`peek`) is rejected** with CE2094 — deferred (Part II §3).
- **Reading a captured field back out of the environment is a BORROW**, exactly like reading a
  struct field (`docs/design/ownership-conventions.md` §4.2): a lambda body that reads a captured
  owning value (e.g. `|~| greeting` returning a captured `string`) sees a borrow of the environment's
  copy, so *returning* or otherwise consuming it from inside the body needs its own `.clone()` —
  CE2411 otherwise. Reading it without consuming it (e.g. `println(greeting)` inside the body) is
  free.
- **A nested lambda captures through the lambda around it.** The scope pass records a free name
  for every lambda it is free in, so when an inner lambda captures a local of the function, the
  outer lambda captures it too. In the lifted outer body that name is a field of the outer
  environment, so the inner environment is filled from `#closure_env.<name>` (the `lift` pass
  sets `Param.capture_source`, and `emit_lambda` reads it). This applies at every depth. The rules
  above do not change, because the source is a read of a captured field: a plain value is copied
  into the inner environment, and an owning value is the consuming use of a borrow (CE2411). To
  move an owning capture one level deeper, clone it in the outer body
  (`let string t = s.clone()`) and capture the clone (#1127).

### Environment ownership, escape, and RAII

- The environment is **heap-allocated and owned by the closure value** (`Own@(__closure_env_N)`),
  so a closure may **escape** its creating scope — be returned, or stored in a struct/`List`.
- Freeing is **type-erased through `drop_ptr`**: at any RAII cleanup point, a function value is
  freed by `if (drop_ptr != null) drop_ptr(env_ptr)`. Non-capturing values carry `drop_ptr = null`,
  so their free is a guarded no-op. This is *why* the drop slot exists — a `fn(i32)->i32` value
  cannot tell statically whether it owns an env (capture erasure), so ownership is resolved at
  runtime by the presence of a drop function.
- **Capture-taint drives ownership analysis.** A bare fn ref / non-capturing lambda is *free*
  (copyable, non-owning). A capturing lambda is *owning* (move semantics
  + RAII). A value of erased provenance (arriving through a `fn` parameter, or read out of a
  container) is conservatively treated as owning-with-runtime-drop; the drop is runtime-guarded, so
  conservative frees are always sound.
- **Closure aliasing is sound.** A plain rebind `let fn(i32) -> i32 g = f` **moves** the env
  (source consumed, CE2405 on later use); a container get-out
  (`let fn(i32) -> i32 g = fns.get(0)??`) and a struct-field read
  (`let fn(i32) -> i32 g = s.handler`) are non-owning **borrows** (the container/struct stays the sole owner,
  mirroring `Own@(T).get()`); a closure stored in a struct field is freed by the struct's cleanup.
  No leak, no double-free (validated with `leaks --atExit`).
- **Compatibility stays invariant and capture-agnostic.** `fn(i32)->i32` matches a plain fn and a
  closure alike (the capture descriptor is metadata, excluded from type identity). Mismatch is
  **CE2002** on assignment and **CE2092** on call-through (§9).

### Lambda lowering (desugaring)

1. Synthesize an environment struct `__closure_env_N { <captured fields> }`, registered in the
   struct table (so the recursive destructor and struct lowering handle it for free).
2. Synthesize the lifted function `__lambda_N(env: poke __closure_env_N, <lambda params>)` with
   the lambda body, rewriting each captured-name read to an env-field access. This reuses the
   monomorphizer's "synthesize a `FuncDef`, register a `FuncSig`, append to `program.functions`"
   machinery, so the backend emits it with zero special-casing.

   **The env parameter is `poke`, and the mode is load-bearing.** A move-captured
   `List@(T)` / `Own@(T)` / dynamic array is MUTABLE inside the body by design (§3, T1.5), and
   after the rewrite every such write goes through this parameter: `xs.push(x)` is
   `#closure_env.xs.push(x)`. The environment is the closure's OWN storage — the closure value
   owns it and frees it through `drop_ptr` — so the lifted function writing to it is not a write
   to a caller's value.

   A `peek` env parameter would make two legal shapes a **CE2408** — a mutating method on a
   capture (`tests/closures/capture/test_closure_list_mutate.sushi`) and a `poke` borrow of a
   capture (`tests/closures/capture/test_closure_env_poke_borrow.sushi`). The declaration is
   `poke`; the rule has no carve-out.

   The mode is a SEMANTIC declaration only: no backend code reads `ReferenceType.mutability`, so
   codegen is identical either way (the env is a pointer in both cases). Four semantics sites read
   it, and for this parameter three are inert — the rebind check (captures are field accesses, the
   env is never rebound), the `poke`→`peek`
   coercion (the env is never a checked argument), and `_poke_param_indices` for the destroy-effect
   analysis of the `effects` pass (round 1 needs a bare-`Name` receiver, round 2 needs a by-name call
   site, and a lifted function is only ever dispatched indirectly).

   **Open structural note.** This is a COMPILER-SYNTHESIZED borrow riding on the same
   `ReferenceType` machinery as a user borrow, so every new reference rule has to be checked
   against it by hand. If a second rule needs an accommodation for it, that is the signal to give
   it its own kind (a `synthesized` flag on `Param`, or a distinct param kind) so a rule can ask
   "is this a user borrow?" instead of matching the name `#closure_env`. One accommodation is a
   coincidence; two is a missing concept.
3. At the lambda site, heap-allocate the env, populate captured fields (copy or move), and build
   the four-word value `{@__lambda_N, env_ptr, @__closure_env_N.__closure_drop,
   @__closure_env_N.__closure_clone}`.

## 4. The capture pieces (T1.0-T1.7)

The capture machinery, in dependency order:

- **T1.0** — fat-pointer ABI + sizing (`FunctionType.captures`, 32-byte lowering).
- **T1.1** — lambda grammar/AST (`lambda_expr`, `lambda_block`, `Lambda` node).
- **T1.2** — capture analysis (free-name recording in the scope pass).
- **T1.3** — type-checking + capture legality, including CE2094 for borrow capture.
- **T1.4** — lambda-lifting pass (env struct + lifted function synthesis).
- **T1.6** — backend materialization (`emit_lambda`, env heap-alloc, fat-value construction).
- **T1.7** — indirect-call env threading; CE2094 additionally rejects owning/variadic fn-value
  *parameter* types (the indirect path has no deep copy, so this restriction prevents a
  double free; T2.5, Part II §3, is the fix).
- **T1.5** — environment RAII + move-capture (§3), plus closure-aliasing soundness: a rebind is a
  move, and a get-out or a field read is a non-owning borrow.

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
and run. These pieces make it possible:

- **Gap C — infer type params through function-typed arguments.** Generic call-site inference walks
  each declared parameter type against the argument type to bind type params; a `FunctionType`
  branch in both twin unification routines (`instantiate` collection and
  `typecheck` validation) so
  a `fn(T) -> U` parameter recurses into its parameter types and return type, reaching the existing
  binding logic for the nested `T`/`U`. The instantiate pass also *presents* a `FunctionType`
  for a **typed-param lambda** (`|i32 x| x * k`, params from the annotation, `ok_type` from the `->
  T` annotation or best-effort body inference) and for a **bare function reference** (`inc`, built
  from its `FuncSig`).
  - **Limitation:** a *bare-param* lambda argument to a generic (`map(xs, |x| x * k)`) is not
    inferable — its param types come from expected-type propagation, which is not available at
    instantiation collection, and is circular anyway (the lambda's type depends on the type params being
    inferred *from* it). Use a **typed-param** lambda (`|i32 x| ...`) or a function reference. This
    is a graceful CE2060, not a crash.
- **Gap A — substitute `FunctionType` during monomorphization.** The three recursive
  type-substitution routines (rewriting type params to concrete types) have a `FunctionType`
  branch that rebuilds `param_types`/`ok_type`/`err_type` recursively, carrying `captures` through
  unchanged (excluded from type identity but drives ownership).
- **Gap D — `List@(T)` is user-extensible.** A first-class generic struct: both concrete
  (`extend List@(i32) sum_all()`) and generic (`extend List@(T) first_or(T)`) extends compile and run.
  A user List method **cannot shadow a builtin** List method name (providers are checked first at
  dispatch); the by-value-`self`-vs-by-pointer receiver ABI mismatch is reconciled at the dispatch
  site.
- **An inline capturing-closure argument.** A capturing closure passed *inline* as a call argument
  (`map(xs, |x| x * k)`) is not bound to a local. A per-scope caller-side temporary registry frees
  it through the runtime-guarded drop on every exit path, for ONE shape only: an inline closure passed to an **extension method** (compiled with `fn_def=None`, which
  registers no parameter cleanup — the caller must still own the argument, which is what keeps
  `return self` safe there). Every other call shape — direct, indirect, variadic — instead transfers
  ownership of the by-value argument to the callee through the seam
  (`docs/design/ownership-conventions.md`), and the callee's own registered `FunctionType` parameter
  frees the
  environment at its own scope exit, exactly like any other owning `nom` parameter; a caller-side
  registration there would double-free. Binding to a local is still not required either way.

Validated as **free generic functions** (`tests/generics/higher_order/test_ho_*`): `map@(T, U)(List@(T), fn(T) ->
U)` with a capturing closure and with `U` genuinely differing from `T` (i32 -> bool);
`filter@(T)(List@(T), fn(T) -> bool)` with a capturing predicate; `fold@(T, U)(List@(T), U, fn(U, T) ->
U)` with two independently-inferred type params; `apply@(T)(fn(T) -> T, T)` with a bare fn reference.

## 6. `collections/iter` — the bundled Sushi-source stdlib module

`use <collections/iter>` ships `map`, `filter`, `fold`, and `compose` as ordinary **generic free
functions** — the delivery of the T1.8/Gap-A/C payoff. Source:
`sushi_lang/sushi_stdlib/src_sushi/collections/iter.sushi`; docs: `docs/stdlib/collections/iter.md`.

```sushi
use <collections/iter>

fn main() i32:
    let i32 factor = 10
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    xs.push(3)
    let List@(i32) ys = map(xs, |i32 x| x * factor)
    println(ys.get(2).realise(-1))    # 30
    return 0
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
  (`collections/hashmap`, `io/fs`, `time`, ...) — Sushi has no implicit prelude, and combinators
  are unremarkable generic functions, not language primitives.

**Constraints (documented in the module and its doc page):**

- **Copy/primitive element types only.** `filter` re-pushes each kept element and `map` reads each
  one; owned-element combinators are deferred (Part II §2).
- **Two call forms:** the free function `map(xs, f)` and the method form `xs.map(f)` (Gap B, Part
  II §1). The method form is an extension with a method-level type parameter
  (`extend List@(T) map@(U)`, `extend T[] map@(U)`). It is bare, and it answers a `List@(U)` for a `List@(T)` and for a `T[]`
  receiver alike. Every combinator takes a bare function and yields the value, so a call takes no
  `??`.
- **Function argument must be a typed-param lambda or a function reference** — a bare-param lambda
  (`|x| ...`) cannot be inferred against a generic parameter (§5's Gap-C limitation).

### `compose` — the capture-and-call payoff

```sushi
fn compose@(T, U, V)(nom fn(T) -> U g, nom fn(U) -> V f) fn(T) -> V:
    return |x| f(g(x))
```

`compose`'s returned lambda **captures** `f` and `g` (both function values, one of them possibly a
closure) and **calls** them in its body — the capture-and-call case of
§7. Both parameters are `nom`: a capture CONSUMES what it captures, and a borrow parameter
cannot be consumed (CE2411 for each of `f` and `g`). The caller hands the values over, so the call
is `compose(nom inc, nom dbl)`. The lambda parameter is a bare `|x|`, and the expected return type
`fn(T) -> V` supplies its type; `|T x|` works too (Part II §5).

```sushi
use <collections/iter>

fn inc(i32 x) i32:
    return x + 1

fn dbl(i32 x) i32:
    return x * 2

fn main() i32:
    let fn(i32) -> i32 incthendouble = compose(nom inc, nom dbl)
    println(incthendouble(10))    # dbl(inc(10)) = 22
    return 0
```

Test coverage: `tests/stdlib/iter/combinators/test_iter_module_map.sushi`, `test_iter_module_filter.sushi`,
`test_iter_module_fold.sushi`, `test_iter_module_fnref.sushi`, `test_iter_compose.sushi`,
`test_err_iter_unknown_module.sushi`.

## 7. Call-through arbitrary expressions (T2.4)

`Call.callee` is any `Expr`, not only a `Name`. Calling through an arbitrary expression that
evaluates to a function value works, and it reuses the fat-pointer indirect-call path unchanged:

- **A captured closure read back in a lifted lambda body** — `env.f(x)` — is exactly what makes
  `compose` and any capture-and-call closure body compile (§6, §3).
- **A fn-typed struct field**, called directly: `obj.handler()`. A `DotCall` routes to an *indirect
  field-call* when the receiver struct has a fn-typed field of that name **and no method of that
  name** — a same-named method always wins. No workaround binding of the field to a typed `let` is needed:

  ```sushi
  struct Handler:
      fn(i32) -> i32 op

  fn run(Handler h, i32 v) i32:
      return h.op(v)     # calls the field directly; the field type is bare
  ```

- **A `List` get-out or a parenthesized expression**, called immediately: `arr[0]()`, `(e)()`,
  `fns.get(0)??(x)`, `(fns.get(0)??)(x)`.
- **A lambda literal**, called where it is written: `(|i32 q| k + q)(2)`. The lambda is the same
  value in the callee position as in a `let`, an argument or a return. The `scope` pass walks
  every callee that is not a plain `Name`, so the lambda records its captures and each name in a
  callee expression is resolved and used. The callee is a BORROW position: a closure
  built there has no other owner, so the backend gives it one (`own_temporary`) and its
  environment is freed at scope exit.

Mechanically: the AST builder emits a general `Call` for a non-`Name`, non-`MemberAccess` call
base; the type checker infers the non-`Name` callee and, when it resolves to a `FunctionType`,
dispatches to the same indirect-call validator used for a named local, annotating the node for the
backend. `??` unwraps a `Result` **and** a `Maybe` `Some` payload, so a
function-value call inside a lambda body can infer its return type through a `Maybe`-returning
chain, not just a `Result`-returning one.

**A lambda may capture another closure and call it in the body:**

```sushi
fn run() i32:
    let i32 n = 10
    let fn(i32) -> i32 g = |i32 x| x + n
    let fn(i32) -> i32 h = |i32 y|:
        return g(y) + 1
    return h(5)          # g(5) = 15, h(5) = 16
```

A type mismatch on this path is a front-end **CE2002** diagnostic.

Test coverage: `tests/closures/capture/test_closure_capture_closure.sushi`,
`tests/functions/function_values/test_call_index_result.sushi`, `tests/functions/function_values/test_fn_value_field_call.sushi`,
`tests/functions/function_values/test_fn_value_in_struct.sushi`.

## 8. Generic-function references — the T2.3 annotated slice

Referencing a generic function as a value is allowed **when an explicit expected function type
is present**:

```sushi
fn identity@(T)(T x) T:
    return x

fn run() i32:
    let fn(i32) -> i32 g = identity   # the annotation drives the instantiation identity@(i32)
    return g(41) + 1     # 42
```

This is an **expected-type-driven** rule, not a general lift of CE2093: the instantiate pass collects
the instantiation wherever an expected `FunctionType` meets a generic-fn name -- a `let`, an
argument, a rebind, a `return`, a struct field, an enum payload, a `.realise()` default -- by unifying the signature against the expected type; the type pass then solves the
type args, rewrites the `Name` to the mangled concrete name, and infers the concrete `FunctionType`.
The backend needs no special case — the mangled monomorphized function materializes as an ordinary fn value.

A generic-fn reference **into a higher-order function** works the same way. With a concrete
parameter type the bare argument is enough (`take(identity)` against `fn(i32) -> i32`). A GENERIC
callee is solved in two steps: the solver leaves the generic-fn
value out of its first pass and solves the callee's type arguments from the other arguments; then
it puts them into that parameter's type and solves the value against it, as against any declared
position. `apply@(T)(fn(T) -> i32 f, T x)` called as `apply(gen, 3)` gets `T = i32` from `3` and
then `gen@(i32)` from `fn(i32) -> i32`. This is not general unification: a callee type argument
that only the value can supply (`U` in `map`, below) is not solved, and the value needs a typed
local first:

```sushi
use <collections/iter>

fn identity@(T)(T x) T:
    return x

fn run() i32:
    let fn(i32) -> i32 id = identity   # fixes the instantiation
    let List@(i32) xs = List.new()
    xs.push(5)
    xs.push(7)
    let List@(i32) ys = map(xs, id)
    return ys.get(1).realise(-1)   # 7
```

What still stays CE2093 is covered once, in Part II §4.

Test coverage: `tests/generics/generic_fn_reference/test_generic_fn_ref.sushi`,
`tests/generics/generic_fn_reference/test_generic_fn_ref_higher_order.sushi`,
`tests/generics/generic_fn_reference/test_generic_fn_ref_no_type.sushi`, `tests/generics/generic_fn_value_positions/`,
`tests/generics/generic_fn_value_to_generic_callee/`.

## 9. Diagnostics (live)

- **CE2002** — a function value assigned to a variable or parameter of an incompatible function
  type (a plain assignment mismatch, *not* a call-through).
- **CE2092** — function-value type mismatch (arity / parameter / return / error type) when
  **calling through** a function value. Function types are invariant.
- **CE2093** — illegal function reference: a bare reference to a **generic** function with **no**
  expected function type in context (Part II §4 has the exact remaining boundary). Extension
  methods, perk methods, and FFI externals are not bare-referenceable at all — they surface as an
  undeclared identifier (**CE1001**), not CE2093.
- **CE2094** — illegal closure capture: a `peek`/`poke` borrow (Part II §3); or an owning
  /variadic fn-value *parameter* type (T2.5, Part II §3, is the fix). **Dynamic-array**,
  **`List@(T)`**, **`Own@(T)`** and **closure-value** captures are all allowed (move-capture), and a
  captured closure may be called in the body (§7).

## 10. Implementation map

The map names files and symbols, not line numbers: a line number goes stale with the next edit.

| Concern | File |
|---|---|
| `FunctionType` + capture descriptor + parameter modes | `semantics/typesys.py` (`class FunctionType`) |
| Fat-pointer LLVM lowering | `backend/types/core/mapping.py` |
| Sizing (32 bytes, four words) | `backend/types/core/sizing.py` |
| Lambda grammar / `atom` | `grammar.lark` (`lambda_param`, the lambda alternatives of `atom`, `lambda_block`) |
| `Lambda` node / `FuncDef` shape | `semantics/ast.py` |
| `Call.callee` over any `Expr` | `semantics/ast.py`; `semantics/ast_builder/expressions/chains.py` |
| Capture analysis | `semantics/passes/scope.py` |
| Lambda type-check, CE2094, bare-param inference | `semantics/passes/types/visitor.py` |
| Expected-type propagation to bare-param lambdas | `semantics/passes/types/propagation.py` |
| The `lift` pass | `semantics/passes/lift.py` |
| Shared fn-synthesis wiring | `semantics/generics/synthesis.py:register_synthesized_function` |
| Ownership predicate (single source of truth) | `semantics/typesys.py:owns_resource` (see `docs/design/ownership-conventions.md` §6) |
| Ownership seam (consume/bind/copy_out) | `semantics/ownership.py` (the `classify()` table), `backend/ownership.py` (the seam functions) |
| Env heap alloc / recursive env destructor / cloner | `backend/generics/own.py:emit_own_alloc`, `backend/destructors.py:emit_value_destructor`, `backend/runtime/closures.py` (env clone, `clone_ptr`) |
| Runtime API (thunk, build value, indirect call, `emit_lambda`) | `backend/runtime/closures.py` |
| Backend expr dispatch -> `emit_lambda` | `backend/expressions/__init__.py` (`case Lambda()`) |
| Indirect call, non-`Name` callee routing | `backend/expressions/calls/dispatcher.py`, `backend/expressions/calls/utils.py` |
| Generic higher-order unification (`typecheck` / `instantiate`) | `semantics/generics/unify.py:unify_types`; the leading type-argument solver `semantics/generics/pack_inference.py:solve_leading_type_args` |
| `FunctionType` substitution (monomorphization) | `semantics/generics/monomorphize/transformer.py`; `semantics/generics/types.py`; `semantics/generics/extensions.py` |
| Gap D (`List@(T)` extensibility) | `semantics/passes/collect/` (List as generic struct); `backend/expressions/calls/dispatcher.py` (provider-first dispatch + receiver reconcile) |
| T2.3 generic-fn-ref-under-annotation | `semantics/generics/instantiate/expressions.py`; `semantics/generics/instantiate/functions.py`; `semantics/passes/types/calls/generics.py` |
| `collections/iter` source module | `sushi_lang/sushi_stdlib/src_sushi/collections/iter.sushi` |
| Source-stdlib-module registry + pipeline injection | `semantics/stdlib_registry.py:SOURCE_STDLIB_MODULES`; `compiler/pipeline.py` |
| Diagnostics | `internals/errors/types.py` (CE2002, CE2092, CE2093, CE2094) |
| Fat-pointer precedent (strings) | `backend/runtime/strings.py` |

Where the passes actually run (worth knowing before touching any of the above): the live semantic
pipeline is `semantics/semantic_analyzer.py`. A single-file compile is a one-unit multi-file
compile. The `lift` pass (`passes/lift.py`) runs in `_check_units`, per unit,
after the `typecheck` pass and before the `borrow` pass.

---

# Part II — Deferred

## 1. UFCS method form `xs.map(f)` — Gap B — SHIPPED

`extend List@(T) map@(U)(fn(T) -> U f) List@(U)` is expressible, inferred at the call site, and
monomorphized per (receiver, method, margs). The decision record is
[ufcs-combinators.md](ufcs-combinators.md).

**Constraints on List extension methods:**

- **Builtin names cannot be shadowed.** The backend dispatcher checks List provider methods
  (`push`/`get`/`iter`/…) *before* the user-extension fallback, so a user `extend List@(T) push()` is
  unreachable. Only non-builtin names route to the extension path. This is not specific to `List` --
  it is the general precedence rule, and **CE2097** enforces it across every built-in family. See
  [method-resolution.md](method-resolution.md) for the full chain and the perk override route.
- **Receiver ABI reconciliation.** A List-backed receiver shares the dynamic-array `{i32, i32, T*}`
  layout and is passed by pointer, but `self` is declared by value; the dispatch site loads the
  header to reconcile (safe because extension bodies never register `self` for cleanup, so the
  shared buffer is not double-freed).

## 2. Owned-element combinators — partly closed

The METHOD-form `filter` is fully general: it clones each kept
element, so an owning element type works. The free functions, and the method-form
`map`/`fold`, stay copy/primitive-element: reading an element into `f`, and threading
an owning accumulator, still need move-aware handling the bodies do not do. See
[ufcs-combinators.md](ufcs-combinators.md) for the scope of the combinators.

## 3. Remaining deferred items (T2.x)

- **T2.1 — `peek`/`poke` borrow capture.** Lift CE2094 for borrows; track the borrow's lifetime
  *through* the closure value under the exclusivity rules. **Why deferred:** this is the genuinely
  hard problem the whole closures feature was scoped around — a borrow captured into an escaping,
  heap-allocated environment can outlive the stack frame that issued it, which the current
  scope-based borrow checker has no model for. Move-capture sidesteps it entirely by never
  letting a reference cross into an environment.
- **T2.2 — Bound method values.** `obj.method` as a callable via a self-binding adapter (env =
  boxed `self`); reuses the heap-env + drop machinery. Lifts the last `obj.handler()`-shaped
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
  deferred:** no FFI callback consumer yet; independent of the other deferred items.

## 4. What still stays CE2093

A generic-function reference is CE2093 where **no** expected function type solves its type
arguments: a position with no expected type at all (`println(identity)`, an expression statement),
or an expected type whose shape does not fit the generic's signature (a two-parameter function type
for a one-parameter generic). Every position that has an expected function type -- a `let`, an
argument, a rebind, a `return`, a field, a payload, a `.realise()` default -- solves it, and the
answer never depends on another call in the program:

```sushi
fn identity@(T)(T x) T:
    return x

fn take(fn(i32) -> i32 f) i32:
    return f(1)

fn main() i32:
    let i32 r = take(identity)      # T = i32 from the parameter type
    println(r)
    return 0
```

A generic callee is solved from its other arguments first, and the value then from the
substituted parameter type: `apply(gen, 3)` against `apply@(T)(fn(T) -> i32 f, T x)`
solves. A value that the substituted type does not solve is CE2093; a callee whose type argument
comes ONLY from the value (`apply1@(T)(fn(T) -> i32 f)` called as `apply1(gen)`) is CE2060 + CE2093.
Bind the value to a typed local first. Inside a generic body the copy is walked for each instance,
so a position solves a generic-fn value as it does in a concrete body. A value behind an alias
(`l.gen`) in the copy of a generic-target extension or perk implementation is not solved: it is
CE2093. Use the bare name there, or bind the value to a typed local in a concrete function.
Extension methods, perk methods, and FFI externals remain outside
CE2093 entirely -- they are not in the function table at all, so a bare reference to one is CE1001
(undeclared identifier), a distinct diagnostic for a distinct reason (incompatible ABI, not
deferred capability).

## 5. The `|T x|` lambda parameter — CLOSED

A lambda parameter annotated with a type parameter of the enclosing generic (`|T x| ...` inside a
`fn foo@(T)(...)`) is substituted in each instance. Measured shapes that compile and give the
correct value: a returned lambda (`compose` written with `|T x|`, at `i32` and at `string`), a
lambda bound to a `let fn(T) -> T` inside the body, and a lambda that captures a `nom` function
parameter. The bare `|x|` form that `compose` uses (Part I §6) is a choice, not a workaround.

## 6. Risks / open problems

1. **Capture erasure at the type boundary.** `fn(i32)->i32` erases capture-ness. Resolved by the
   runtime `drop_ptr`: ownership/free is data-driven (`if drop_ptr: drop_ptr(env)`), not
   type-driven. This is why the fat layout (four words, with `clone_ptr`) is
   mandatory: it cannot be retrofitted.
2. **Direct-vs-indirect ABI reconciliation.** "null env keeps a direct call bare" and "indirect calls pass a
   leading env" are only jointly consistent via the adapter-thunk split — direct calls bare,
   indirect uniform, bare fns bridged by a thunk. A uniform "every fn gets a leading env param" ABI
   was rejected as needlessly invasive (rewrites every signature, FFI, `main`).
3. **Indirect-path asymmetry (T1.7/T2.5).** See Part II §3 — a latent double-free dodged by
   restricting fn-value param types rather than fixed at the emitter; T2.5 is the eventual close.
4. **Grammar `|` collision — resolved.** Position-based disambiguation (prefix `|` = lambda, infix
   `|` = bitwise-or), validated through the parser generator with no new conflicts.
5. **Ownership vs. the move/borrow tracker.** Only *capturing* values are owning (capture-taint);
   non-capturing values stay copyable; the runtime-guarded drop makes
   conservative (erased-provenance) frees sound. This is answered by `owns_resource`
   (`semantics/typesys.py`), the single ownership predicate every type asks — see
   `docs/design/ownership-conventions.md` §6.
6. **Pass-ordering.** Lambda-lifting needs resolved capture *types* (post-`types`) but its
   synthesized functions must be borrow-checked (last pass), so it sits between them. If passes are
   reordered later, this insertion point moves with `types`.

## 7. Fast path to re-enter

1. Read Part I (above) for current capability, then this Part II for what's left and why.
2. Reproduce the working baseline: compile+run `tests/closures/escaping_values/test_closure_escaping.sushi` (prints
   15), `tests/closures/capture/test_closure_owned_move_capture.sushi` (13),
   `tests/closures/capture/test_closure_capture_closure.sushi` (16), `tests/stdlib/iter/combinators/test_iter_compose.sushi`
   (22), `tests/generics/generic_fn_reference/test_generic_fn_ref.sushi` (42).
3. Pick the remaining item by leverage:
   - **Owned-element combinators (§2)** — needs move-aware `map`/`filter` bodies; scope it against a
     concrete consumer (e.g. a `List@(List@(T))` transform) before generalizing.
   - **T2.1-T2.6 (§3)** — pick by consumer need; T2.2/T2.5/T2.6 are mechanically straightforward,
     T2.1 is the hard one and should stay last.
4. Keep the suite green after each step (`python tests/run_tests.py`);
   leak-check runtime cases with macOS `leaks --atExit` (baseline noise: ~16 bytes in `user_main`,
   present even in a trivial no-closure program).

## Test strategy (repo conventions)

`tests/run_tests.py`: `test_*` -> exit 0, `test_err_*` -> exit 2, `test_warn_*` -> exit 1; runtime
validated by the one runner. Ground truth lives in `tests/closures/`, `tests/generics/higher_order/test_ho_*`,
`tests/generics/generic_fn_reference/test_generic_fn_ref*`, and `tests/stdlib/iter/combinators/test_iter_*` — see the test-coverage lines
under each Part I section above for the full file list.
