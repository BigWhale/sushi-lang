# Design: First-Class Functions

Status: implemented in v1 (shipped in PR #91). Scope: function *types* and *values* via bare
function pointers; no closures — closures are the additive v2 (see "path forward"). Historically
the last self-hosting-enabler gate (`REPORT.md` W8/§4).

## Summary

Sushi gains function **types** (`fn(i32) -> i32`) and function **values**: a top-level
function can be referenced by name, stored in a variable / struct field / `List`, passed as
an argument, and called through. This unblocks dispatch tables and iterator callbacks that
otherwise require hand-rolled `match`. (A raw array of function pointers is not expressible —
the `[]` in `fn() -> T[]` binds to the return type — so collections use `List<fn(...)>`.)

The function value is a **zero-cost bare function pointer** — the raw address of an
already-monomorphized LLVM function. There is no captured environment — the bare-pointer choice
(see "Options considered" below). Closures are designed to be additive later (see "path forward").

## Syntax

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

Collections of functions use the generic form: `List<fn(i32) -> i32>`.

## Semantics

- **Result-transparent call.** A Sushi `fn` lowers to LLVM `Result<T, E>(params)`. Calling
  through a function value therefore yields the same `Result<T, E>` a direct call would, so
  `??`, `if (result)`, and pattern matching all work unchanged.
- **The function type captures `T` and `E`.** Two function types are compatible only when
  arity, every parameter type, the ok type `T`, and the error type `E` match exactly
  (invariant — no variance in v1). A mismatch surfaces as **CE2002** on an assignment (or
  passing a function value to an incompatible parameter) and **CE2092** on a call-through.
- **Only plain top-level `fn`s are referenceable.** Extension methods, perk methods, and FFI
  externals have incompatible ABIs (bare-value, `self`-bound, raw-C) and live in separate
  tables, so a bare reference to one is never recognized as a function value — it fails as an
  undeclared identifier (**CE1001**), not **CE2093**. (CE2093 is reserved for a *generic*
  function reference, which *is* recognized but deferred.) This keeps the indirect-call path
  byte-identical to the existing Result-returning direct-call path.
- **No closures, no captures.** A function value is a single pointer; it carries no state.
- **`Call.callee` stays a `Name`.** `f()` resolves to a *direct* call when `f` names a
  top-level function and to an *indirect* call when `f` is a local variable of function type.
  No AST widening, so the hot call path is untouched.

## What v1 leaves out, why, and the path forward

### (A) Left out of v1 — and why

- **Closures / captured environment.** Capturing a variable (especially a `&poke` borrow)
  requires lifetime tracking through the function value — deep RAII and borrow-checker
  interactions. By far the largest item; kept out so (a) is not blocked by a hard problem.
- **Lambda / anonymous-function literals.** Pure ergonomics: a larger grammar/AST surface
  with no capability a named `fn` + reference doesn't already provide.
- **Generic function references** (`identity<i32>`). Needs forcing the monomorphized
  instantiation to exist and taking the address of its mangled name — orthogonal to the
  core mechanism. Referencing a generic function is **CE2093** in v1.
- **Extension/perk-method and FFI-extern values.** Three incompatible ABIs vs. the uniform
  `Result<T, E>` ABI of top-level fns; allowing them now would fork the indirect-call path
  before it has earned its keep. Not bare-referenceable at all — a bare name resolving to one
  of these is an undeclared identifier (**CE1001**), not a function value.
- **Call-through arbitrary expressions** — `(expr)()`, `arr[0]()`, `getfn()()` — and
  **call-through a struct field in one expression** (`obj.handler()` parses as a *method*
  call, not a call of the function-valued field `handler`). Both require widening
  `Call.callee` to a general expression, the exact hot-path change v1 avoids. Bind to a local
  first: `let f = arr.get(0)??` then `f(x)`; `let h = obj.handler` then `h(x)`.

### (B) Options considered (and why the chosen one won)

- *Error type in the function type*: (i) capture `T` and `E` with optional `| E` **[chosen]**;
  (ii) `T`-only, reject custom-error fns; (iii) `T`-only, erase `E` (unsound across error
  types). Chose (i): type-safe and mirrors fn-declaration syntax.
- *Reference scope*: (i) top-level fns only **[chosen]**; (ii) also extension/perk methods.
  Chose (i): one predictable ABI; the indirect path stays identical to the direct path.
- *Generics*: (i) defer **[chosen]**; (ii) include now. Chose (i): orthogonal complexity.
- *Callee AST*: (i) keep `Name`, resolve direct-vs-indirect semantically **[chosen]**;
  (ii) widen `Call.callee` to `Expr` now. Chose (i): avoids touching every call site that
  reads `callee.id` — the regression risk the report flagged.
- *Value representation*: bare `ir.Function*` (zero-cost) vs. a `{fn_ptr, env_ptr}` fat
  pointer. Chose the bare pointer; the fat pointer is the closure shape and is deliberately
  the v2 migration.

### (C) What the next iteration changes

- **Closures = `FunctionType` + environment pointer.** The value representation migrates from
  a bare `ir.Function*` to a `{fn_ptr, env_ptr}` fat pointer; `FunctionType` gains an optional
  capture descriptor. Indirect-call emission passes `env_ptr` as a hidden leading argument.
  Additive: a non-capturing reference is a fat pointer with a null `env_ptr`.
- **Lambdas** desugar to a synthesized top-level fn + a reference — no new runtime concept
  once closures land (a capturing lambda just builds the environment).
- **Generic function references** force the instantiation (reuse the monomorphizer's
  call-site detection) and emit the address of the mangled name; CE2093 lifts for that case.
- **Widen `Call.callee` to `Expr`** to enable `(expr)()`, `arr[0]()`, `getfn()()`, and direct
  `obj.handler()` field calls — a self-contained follow-up touching the validator, inferencer,
  and backend dispatcher together.
- **Extension/perk-method values** require a `self`-binding adapter (partial application,
  overlaps with closures) or a normalized ABI shim — revisit after closures exist.

## Diagnostics

- **CE2092** — function-value type mismatch (arity / parameter / return / error type) when
  **calling through** a function value. Function types are invariant.
- **CE2002** — a function value assigned to a variable or parameter of an incompatible
  function type (a plain assignment mismatch, *not* a call-through).
- **CE2093** — illegal function reference: a **generic** function (deferred in v1). Extension
  methods, perk methods, and FFI externals are not bare-referenceable at all — they surface as
  an undeclared identifier (**CE1001**) / namespace error, not CE2093.

## Implementation map

- `semantics/typesys.py` — `FunctionType(param_types, ok_type, err_type)`; `Type` union;
  `fn_type_t` in `TYPE_NODE_NAMES`.
- `grammar.lark` — `fn_type_t` at the `?type` level (`->` / `|` already exist as terminals).
- `semantics/ast_builder/types/functions.py` — `parse_function_type()`; registered in
  `types/parser.py`. Absent `| E` parses as `UnknownType("StdError")` so existing resolution
  binds it to the `StdError` enum.
- `semantics/type_resolution.py` / `passes/types/resolution.py` — recurse into `FunctionType`
  members.
- `semantics/passes/types/compatibility.py` — structural (invariant) `FunctionType` compat.
- `semantics/type_visitor.py` — infer `FunctionType` for a top-level-fn `Name` in value
  position (`function_value_type_of`); reject a generic-fn reference with CE2093. Ext/perk/
  extern names are absent from the function table, so a bare reference to them is an
  undeclared identifier (CE1001), not a function value.
- `semantics/passes/types/calls/user_defined.py` — indirect-call validation (CE2092).
- `backend/types/core/mapping.py` — lower `FunctionType` to
  `ptr to FunctionType(Result<T,E>, params)`.
- `backend/expressions/operators.py` — `emit_name` returns the `ir.Function` address.
- `backend/expressions/calls/dispatcher.py` — load a function-pointer local and call indirectly.
