# UFCS combinators — extension methods with an error channel and method-level type parameters

Status: SHIPPED (the UFCS epic, 2026-08-30). This is the decision record. The seven
rulings here are David's (2026-08-29 and 2026-08-30) and are settled.

The headline: the `<collections/iter>` combinators exist in method form, written in
Sushi, shipped in the stdlib, on a general language feature users can also write:

```sushi
extend List@(T) map@(U)(fn(T) -> U f) List@(U) | StdError:
    let List@(U) out = List.new()
    foreach(x in self.iter()):
        out.push(f(x)??)
    return out          # bare success; only Err is spelled (ruling 6)
```

Call site: `xs.map(|i32 x| x * 2)??`. Targets now: `List@(T)` and `T[]`. The free
functions stay.

## The concept

Before this epic, the error discipline followed the KIND of callable: a free function
has the Result channel, an extension method does not. The channel now follows the
SIGNATURE: a method that declares `| E` has it, a method that does not stays bare. That
matches the parameter-mode philosophy — marked at both ends: `| E` at the declaration,
`??` at the call. It also removes a built-in privilege: the built-in methods already
return `Result`/`Maybe` (`arr.get(i)`, `xs.pop()`), and users could not write one that
does. Now they can.

## The rulings

### 1. Error channel: opt-in and explicit

The default stays the bare return. Only a method that declares `| E` gets the Result
ABI, `??` in the body, and the channel at the call. Bare-return extensions keep CE0131
(no `??`) and CE2091 (no Result constructors) byte for byte.

### 2. Name claim: accepted

A stdlib extension is an ordinary extension. The resolution ladder — built-in > perk >
extension, CE0101 between extensions — is untouched. A user extension named `map` on
another type coexists; a second `map` on the SAME target is the ordinary CE0101.

### 3. Arrays: the element position binds

A bare undeclared name in the element position of an array target binds a type
parameter: `extend T[]` applies to every element type. A declared or built-in name is
concrete: `extend i32[]`, `extend Crate[]`. Anything else in that position — a generic
instantiation, a nested array — is CE2101. (This ruling also fixed a latent bug: a
concrete `extend i32[]` was silently never registered before.)

### 4. Scope: List and T[] now; HashMap later

The stdlib method form ships on `List@(T)` and `T[]` in `<collections/iter>`. A HashMap
module comes later, separately.

### 5. Chain semantics (the matrix)

A channel method stops the chain until it is handled; `??` re-enables it; methods ON
the wrapper stay legal; `??` on a bare method is an error:

| chain | verdict |
|---|---|
| `b.is_true().is_false()` | CE2515 — the channel is unhandled |
| `b.is_true()??.is_false()` | compiles |
| `b.is_false().is_true()` | compiles — bare chains freely |
| `b.is_false()??.is_true()` | CE2507 — `??` on a bare bool |
| `b.is_true().realise(false).is_false()` | compiles — `.realise` answers from the wrapper |

CE2515 is a RESOLUTION FALLBACK, not a receiver-kind ban. Resolution runs first: a
method found on the Result/Maybe enum itself is legal. CE2515 fires only when the
method is missing there but present on the payload type — which is what tells an
unhandled channel from a typo. The diagnostic is relational (it names the missing
method and the call that returned the wrapper) and its help spells the `??` fix. It
covers result-like AND maybe-like receivers: a `Maybe@(T)` is also more than the bare
`T`, so `xs.find(p).len()` is the same CE2515.

### 6. Return form in a channel body: bare success, explicit Err

`return true` auto-wraps into Ok at the emission seam. `return Result.Err(e)` is the
ONE spelled constructor. `return Result.Ok(x)` stays refused — CE2091, narrowed to the
Ok constructor in a channel body, while a bare body keeps refusing both constructors.

This is the first written rationale for the bare-return rule itself: the error is the
exceptional path and earns its ink; the success stays as light as a bare method's. A
body that spells `Result.Ok` around every success return says nothing the signature
did not already say. Free functions are UNCHANGED: they spell `Result.Ok` explicitly
(CE2030), and CW2511 for `??` in `main()` stays.

### 7. `??` on Maybe stays, and is now recorded

A `None` under `??` propagates as a payload-free `Result.Err` (CE2508's doc states it;
the emission is `backend/expressions/try_expr.py`). Stated plainly: Maybe is data,
Result is the channel, and `??` converts absence into an empty error. This was already
true; the epic records it as design rather than accident.

## Identity and the symbol

Two different solved `U`s on one receiver are two methods, so the symbol carries three
parts: the receiver, the method name, and the method-level type arguments. ONE helper —
`extension_symbol(receiver_display, method, margs)` in
`semantics/generics/name_mangling.py` — answers for all three consumers: the
declaration (`backend/functions/helpers.py`), the call site
(`backend/expressions/calls/dispatcher.py`, which reads the typecheck pass's
`callee_method_type_args` stamp instead of re-deriving), and the weak_odr dedup. The
`__{margs}` suffix appears only when method-level arguments exist, so every
pre-existing extension symbol is unchanged. An array receiver folds to
`arr__<element>`, because `[]` is not a symbol character.

There is NO third dimension in the ExtensionTable: resolution answers from the
template plus unification, and concrete per-margs copies exist only as ExtendDef nodes
in `monomorphized_extensions`, deduped by a worklist keyed `(receiver, method, margs)`.

## Call-site-driven monomorphization

An array template's element and a method-generic's solved arguments exist only at the
call, so the typecheck pass queues instantiations while it resolves calls, and the
analyzer monomorphizes and checks the copies in a bounded fixpoint round after the
per-unit loop (checking one copy can resolve a call that queues another). A solved
argument can name a type instantiation NOTHING else in the program names
(`List@(bool)`), and the copy is created after `resolve`, `finite-types` and `derive`
have run — so the analyzer installs a late interner on the tables: the new types are
monomorphized, resolved and derived at resolution time, and again for the copies'
bodies in the drain.

Inference is call-site-only in v1: there is no `@(...)` slot on a method call. The one
shape that cannot be solved is the bare-param lambda (`|x| ...` has no type of its
own), and CE2063 names the escape: annotate the parameter, or pass a named function. A
method-level name that repeats a receiver-target parameter is CE2064.

## Program-wide extension visibility (the stated asymmetry)

Extensions are program-wide and unit-blind: the ExtensionTable keys on the target type
alone, so ONE unit's `use <collections/iter>` makes `.map()` callable in every unit.
This is accepted (ruling 2) and stated rather than hidden. Consequence for API
authors: an extension is as visible as its target type, so a module cannot keep
private helper extensions on a public type — internals stay free functions.

## The footgun audit

Every unhandled-Result position has a gate:

| position | gate |
|---|---|
| assignment (`let i32 x = f()`) | CE2505 |
| chaining (`xs.map(f).filter(p)`) | CE2515 |
| an `if` condition | CE2516 |
| an argument | CE2006 |
| a discarded statement | CW2001 |
| `??` on a bare method | CE2507 |

## Scope of v1, and what is parked

**Owned elements**: `filter` ships fully general — it clones each kept element. `map`
and `fold` stay copy/primitive-element, like the free functions.

**Parked open questions**, recorded and not expanded here:

- **(a) A bare opt-out for free functions.** The remaining asymmetry: a function
  cannot be infallible. A method chooses its channel; a function cannot decline one.
- **(b) The perk-method channel.** Declaring `| E` on a perk-impl method is CE0133
  (an explicit reject that replaced a silent drop). The channel is REQUIRED before a
  perk-contract API surface like a network stack — a `Reader`/`Writer` with a fallible
  `read`. The follow-up is bounded: the grammar already parses it, `_signatures_match`
  needs an `err_type` arm, and the lift reuses this epic's seam
  (`_validate_method_body`'s channel state and the backend's return-wrap).
- **(c) Extension visibility.** See the stated asymmetry above; a visibility marker on
  extensions is a separate decision nobody has asked for yet.

**HashMap combinators** come later, in a separate module (ruling 4).
