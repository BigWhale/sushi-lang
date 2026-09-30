# UFCS combinators — extension methods with an error channel and method-level type parameters

Status: SHIPPED. This is the decision record. The seven rulings here are David's and are
settled. A channel body spells its success (#848).

The combinators are bare (`docs/design/error-channel.md`). `map`, `filter`, `fold` and
`compose` take bare functions and yield the value, so a call needs no `??`. Ruling 1 (the
channel is opt-in) is the rule for EVERY callable: a free function, an extension or perk
method, a lambda and a function type.

The headline: the `<collections/iter>` combinators exist in method form, written in
Sushi, shipped in the stdlib, on a general language feature users can also write:

```sushi
extend List@(T) map@(U)(fn(T) -> U f) List@(U):
    let List@(U) out = List.new()
    foreach(x in self.iter()):
        out.push(f(x))
    return out              # bare: no `| E`, so the body returns the value
```

Call site: `xs.map(|i32 x| x * 2)`. Targets: `List@(T)` and `T[]`. The free
functions stay.

A bare combinator is correct here because it is total over its inputs: it cannot fail
unless its function argument fails, and a bare function argument cannot. A bare function
is the exception, not the default style. Write a channel (`| E`) for a function that does
I/O, parses, allocates on a size it is given, or can gain a failure later. A public
function keeps a channel when there is any doubt, because a channel added later changes
the signature and breaks every caller and every binary `.slib`. The compiler does not
enforce this. `docs/design/error-channel.md` carries the rule.

## The concept

The error channel follows the SIGNATURE, not the KIND of callable: a method that
declares `| E` has it, a method that does not stays bare. That matches the
parameter-mode philosophy — marked at both ends: `| E` at the declaration, `??` at the
call. It also removes a built-in privilege: the built-in methods return
`Result`/`Maybe` (`arr.get(i)`, `xs.pop()`), and a user method can do the same.

## The rulings

### 1. Error channel: opt-in and explicit

The default is the bare return. Only a method that declares `| E` gets the Result
ABI, `??` in the body, and the channel at the call. A bare-return extension refuses `??`
(CE0131) and the Result constructors (CE2091).

This ruling holds for every callable. A free function, a
lambda and a function type have a channel only when they write `| E` (or an explicit
`Result@(T, E)`). There is no default error type.

### 2. Name claim: accepted

A stdlib extension is an ordinary extension. The resolution ladder — built-in > perk >
extension, CE0101 between extensions — is untouched. A user extension named `map` on
another type coexists; a second `map` on the SAME target is the ordinary CE0101.

### 3. Arrays: the element position binds

A bare undeclared name in the element position of an array target binds a type
parameter: `extend T[]` applies to every element type. A declared or built-in name is
concrete: `extend i32[]`, `extend Crate[]`. Anything else in that position — a generic
instantiation, a nested array — is CE2101.

### 4. Scope: List and T[]; HashMap later

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

### 6. Return form in a channel body: both constructors are spelled

The compiler does not wrap a value automatically. A
channel body has the free function's rule and the free function's code:

- the success is `return Result.Ok(x)`, and a `~` success is `return Result.Ok(~)`;
- the failure is `return Result.Err(e)`;
- a bare `return x` (and a bare `return ~`) in a channel body is CE2030, the code a free
  function hears for the same fault.

A BARE body (no `| E`) returns the value itself and refuses both constructors (CE2091).
`main` is bare, so a `??` in it is CE0131.

The reason: a silent wrap is a value the source did not write. A channel method and a
free function that answer the same Result would then spell it two ways.

### 7. `??` on Maybe converts absence into an Err

A `None` under `??` propagates as a payload-free `Result.Err` (CE2508's doc states it;
the emission is `backend/expressions/try_expr.py`). Stated plainly: Maybe is data,
Result is the channel, and `??` converts absence into an empty error. This is design,
not accident.

## Identity and the symbol

Two different solved `U`s on one receiver are two methods, so the symbol carries three
parts: the receiver, the method name, and the method-level type arguments. ONE helper —
`extension_symbol(receiver_display, method, margs)` in
`semantics/generics/name_mangling.py` — answers for all three consumers: the
declaration (`backend/functions/helpers.py`), the call site
(`backend/expressions/calls/dispatcher.py`, which reads the typecheck pass's
`callee_method_type_args` stamp instead of re-deriving), and the dedup of the copies. The
`__{margs}` suffix appears only when method-level arguments exist, so an extension
with no method-level type parameter keeps its plain symbol. An array receiver folds to
`arr__<element>`, because `[]` is not a symbol character.

There is NO third dimension in the ExtensionTable: resolution answers from the
template plus unification, and concrete per-margs copies exist only as ExtendDef nodes
in `monomorphized_extensions`, deduped by a worklist keyed `(receiver, method, margs)`.

## The home unit of a copy

Every copy of a generic-target or method-generic extension has ONE home: the unit that
declared its template (`ExtendDef.home_unit`, set from `GenericExtensionMethod.unit_name`
when the copy is cut). A copy of a perk implementation on a generic target follows
the same rule, and so does a generic function instance (`synthesis.py`). A template whose
unit is not in the build goes to the entry unit. The rule has three consequences:

- **The check.** The copy is checked with the passes of its home unit: the unit name and
  the namespace table of that unit. A private function of that unit and a name behind a
  `use ... as` alias mean in the copy what they mean in the template. The type
  ARGUMENTS were written at the call site and checked there, so the copy names nothing:
  a home unit that cannot write `Crate` still holds the copy for `List@(Crate)`
  (`in_synthesized_body`, for the extension and the perk copy alike).
- **The emission.** Every module declares the copy; the module of its home unit alone
  defines it, with that unit as the emitting unit. One definition is one symbol, so
  there is no duplicate-symbol link failure, and a private name of the template's unit
  is declared where the copy is defined.
- **The cache.** The home unit's object holds copies that other units ask for, for types
  that other units declare. Its key covers the signature and the body of each copy it
  holds, and the interface of every unit of the program (`compiler/fingerprint.py`), so
  a layout change of a type argument in a unit the home does not import rebuilds it.
  A body-only edit elsewhere changes no interface and rebuilds nothing.

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

Inference is call-site-only: there is no `@(...)` slot on a method call. The one
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

## Scope, and what is parked

**Owned elements**: every combinator is general over the element type. `filter` clones
each kept element, and `fold` clones `init` once, so an owning accumulator works too.

**Parked open questions**, recorded and not expanded here:

- **(a) A bare free function — SHIPPED.** A function chooses its channel as a method
  does: a function without `| E` is bare (`docs/design/error-channel.md`).
- **(b) The perk-method channel — SHIPPED.** A perk method declares `| E` exactly as an
  extension method does. The contract and the implementation
  declare it in the same shape and must agree, and CE0133 is the relational
  diagnostic that says so: the primary at the implementation, a note at the contract
  method. `_validate_method_body` reads the channel, and the backend emits every perk
  method through the extension path.
- **(c) Extension visibility.** See the stated asymmetry above; a visibility marker on
  extensions is a separate decision nobody has asked for yet.

**HashMap combinators** come later, in a separate module (ruling 4).
