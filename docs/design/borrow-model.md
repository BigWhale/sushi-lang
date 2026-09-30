# The Borrow Model: Four Parameter Modes

**Status: DECIDED.** Ruled 2026-08-16.

This document is the normative spec for how a value crosses a call boundary. Where
`docs/design/borrowing.md` sections 1 to 5 or `docs/design/ownership-conventions.md`
section 8.6 state a mode rule, this document wins.

`docs/design/ownership-conventions.md` stays normative for everything else about ownership:
the two type classes, the three provenances, the 3x2 table, and the twelve consuming
positions. This document owns one of those positions, the call argument: the callee's
declaration decides whether it consumes.

## 1. The rule

**A parameter mode is a property of the declaration. It is not a property of the callee's
implementation.**

The compiler does not read the convention off the callee's body. A convention read off the
body gives different answers for different callee kinds: a stdlib function written in Sushi
and one in generated IR would treat the same call differently, and the borrow checker and
the backend could disagree about whether an argument moved.

One declared mode per parameter removes the question. Every callee kind reads its modes
from the same place.

## 2. The four modes

```sushi
fn f(string name) ~:          # borrow            -- caller frees; name stays usable
fn f(nom string name) ~:      # consume           -- callee frees; CE2405 after
fn f(peek string name) ~:     # borrow by pointer -- read only; caller frees
fn f(poke string name) ~:     # borrow by pointer -- read/write; caller frees
```

A RECEIVER takes the same four, written without a type: `self`, `nom self`, `peek self`
and `poke self`. Its mode is declared and never written at the call site.

The default mode has no name of its own. It is *a borrow*. When you explain it, say that it
does not pass the value.

| | `string x` | `nom string x` | `peek string x` | `poke string x` |
|---|---|---|---|---|
| what crosses | 16-byte descriptor | 16-byte descriptor | pointer | pointer |
| who frees | caller | **callee** | caller | caller |
| callee may read | yes | yes | yes | yes |
| callee may write through it | no — CE2422 | yes (its own copy) | no — CE2408 | **yes, caller sees it** |
| callee may keep it | no — CE2411 | **yes** | no — CE2411 | no — CE2411 |
| caller may use it after | yes | no — CE2405 | yes | yes |
| how many at once | many | one | many | one, exclusive |

`peek` and `poke` have the meaning that `docs/design/borrowing.md` gives them. A borrow
mode has no `&`: a borrow mode is not an address-of operator, and `&` is bitwise-and.

## 3. Mark a marked mode at both ends

A marked mode is written at the declaration **and** at the call site. The default mode is
unmarked at both ends.

```sushi
f(name)                       # borrow
f(nom name)                   # consume -- you can see it at the call site
f(peek name)                  # borrow by pointer, read only
f(poke name)                  # borrow by pointer, read/write
```

The symmetry gives two things:

- **A consume stays visible at the call site.** Without the marker, `f(s)` would not show
  whether `s` survived the call. A reader would have to open the callee.
- **A stdlib or library call site is unmarked for the default mode.** `chdir(p)` passes
  the default mode.

A mismatch between the two ends is a diagnostic, not a coercion:

- a `nom` marker at a borrow parameter, or a missing `nom` marker at a `nom` parameter, is
  **CE2427**;
- a missing or wrong `peek` / `poke` marker is **CE2006** — a reference
  parameter has a `ReferenceType`, so the mismatch is an argument type mismatch.

`poke T` coerces to `peek T` at a call site, and nowhere else (section 7).

## 4. The representation

A by-value `string` parameter lowers to `{i8*, i32, i8}`
and is passed into a fresh alloca. **The 16-byte descriptor is copied. The `data` pointer
aliases the caller's buffer.** Two descriptors, one buffer.

```
caller  s     { data = 0x1000, size = 8, owned = 1 }
                       |
                       +------> heap: "Trillian\0"   <-- ONE buffer
                       |
callee  name  { data = 0x1000, size = 8, owned = 1 }
```

The same shape holds for `T[]`, `List@(T)`, `HashMap@(K, V)`, `Own@(T)` and a closure
value. Only `T[N]` and a large plain struct copy real content, which is where borrow by
pointer earns its keep.

The ABI of a borrow and of a `nom` is therefore identical. Three things differ, and all
three are bookkeeping:

1. A borrow clears the `owned` bit in the callee's copy, so the callee's destructor is a
   runtime no-op. A `nom` keeps the bit, because the callee is the one owner.
2. A borrow is not registered for cleanup in `begin_function`. A `nom` is.
3. A borrow does not mark the caller's binding moved. A `nom` does.

**This is why the bug class is invisible.** Both descriptors look valid whichever side
frees. The damage appears later, and somewhere else.

## 5. What each callee kind declares

| callee kind | default for an unmarked parameter | may declare `nom` |
|---|---|---|
| user function | borrow | yes |
| extension or perk method | borrow | yes, the receiver too (`nom self`) |
| lambda / closure | borrow | yes |
| stdlib function | borrow | yes, in a Sushi-source module (`compose` in `<collections/iter>`) |
| `.slib` concrete function | borrow | yes; the manifest carries the mode |
| struct or enum constructor | **consume** — a field takes ownership | not written |
| container insert (`List.push`, `HashMap.insert`, `Own.alloc`) | **consume** | not written |
| FFI extern | not applicable — **CE2428** on `nom` | no |

The last three rows are the ones that are not function calls in the surface language, and
each has its own rule:

- **A constructor consumes.** A struct or enum field takes ownership, so
  `Person(name)` moves `name`. The borrow checker sees a constructor and a function call as
  the same `Call` node, so the mode lookup applies to the function call only.
- **A container insert is its own consuming use.** It is not a call argument.
- **FFI is outside the mode system.** A C callee never receives a Sushi value. The compiler
  marshals the string into a fresh `char*` that the caller owns and frees. `nom` on an
  extern parameter has no meaning, and is **CE2428**.

## 6. Where the mode lives

`Param.mode` is derived, never stored twice. Hold this invariant:

> The mode is `PEEK` or `POKE` **if and only if** the parameter's type is a `ReferenceType`
> with that mutability.

One derivation function answers it — `param_mode` in `sushi_lang/semantics/param_modes.py`
— and `tests/unit/test_param_mode_invariant.py` pins it. The AST records only the extra
bit that the type cannot carry (`Param.is_nom`); everything else is read off the type.

`FunctionType.param_modes` carries the same tuple, normalized through the same function, so
a function type built without modes and a function type built with all-default modes are
the same type.

**That convenience is also the hazard, so hold a second invariant: every rebuild of a
`FunctionType` carries `param_modes`.** `peek` and `poke` ride on the parameter's own type
and survive any rebuild; `nom` does not, and a rebuild that omits the field silently says
"every parameter borrows" rather than "the modes are unknown". Resolution, three
substitution walks and a manifest read each rebuild a function type, and a rebuild that
drops the modes makes `nom` in a fn-type annotation unusable and can double free. Use `dataclasses.replace` to
rebuild, and `declared_modes(params)` to build from a signature.
`tests/unit/test_fn_type_metadata_survives.py` pins both halves: each transformation
round-trips a type with non-default metadata, and no construction under `semantics/` may
leave `param_modes` unstated.

One resolver answers "what are the modes of this callee?" for every kind in the section 5
table. It is modelled on the closed `ConsumingUse` enum: `CalleeKind` is a closed set, and
a member with no row fails a unit test statically. The borrow pass and the backend call the same
resolver, which stops the two halves drifting apart.

One applier applies it. Every call shape reaches `apply_mode` in
`semantics/passes/borrow/calls.py` — a direct call, an indirect call through a fn-typed
local, and an indirect call through a fn-typed struct field. Reading the mode from one place and applying it in several is the same hazard as
deriving it in several.

## 7. Rules that follow

- **A function type carries the mode, and stays invariant.** `fn(nom string) -> i32` and
  `fn(string) -> i32` are different types, in both directions. Without this, one
  indirection defeats the rule, for `peek` and `poke` and for `nom` alike. The `poke` to `peek` coercion is a property of the call-site position,
  not of the type pair, so it does not travel into a stored function type. `types_compatible`
  compares `FunctionType.modes` for this, in one place, rather than asking each parameter.
- **A perk declares the mode, and the implementation must match it.** This is CE4004, which
  is already the rule for `peek self` and `poke self`.
- **You may return, store and capture a `nom` parameter.** The callee owns it. A borrow
  parameter in any of those positions is CE2411, and the escape is `.clone()`.
- **Generic parameters are uniform.** `fn f@(T)(T x)` borrows for every instantiation. A
  pass-through such as `fn identity@(T)(T x) T` needs `nom T x`. There is no per-
  instantiation mode, because the mode is declared and not inferred.
- **A receiver carries a mode too, and `nom self` is one of them.** `peek self` and `poke self` cross by pointer; an unmarked receiver borrows;
  `nom self` CONSUMES, so the method owns what it was called on and the caller's binding
  is spent. `semantics/param_modes.py:receiver_mode` is the one reading of the marker,
  and every consumer -- the typecheck pass, the borrow pass and the backend -- asks it
  rather than comparing the string. A `nom self` receiver is an ordinary owned local
  inside the body: it may be written, it may be handed to `poke`, and it drops at the
  end of the method.
- **A variadic `...T` keeps the callee as the owner** of the collected array. The array is
  synthesized by the caller and has no other owner, so it adopts. A consuming variadic
  spelling is deferred and rejected.

## 8. `.clone()`

`.clone()` makes a fresh, independent value. It does not transfer by itself. **The mode of
the parameter decides who frees the result.**

- `f(nom s.clone())` gives the callee an independent copy. The callee may do what it wants
  with it. This is the case that matters.
- `f(s.clone())` at a borrow parameter leaves the caller with the temporary. The scope-temp
  machinery frees it.

Both shapes work with no new code, because a `.clone()` result is FRESH, and FRESH adopts
at every type class.

## 9. Diagnostics

The mode codes:

| code | what |
|---|---|
| **CE2427** | the argument's mode marker does not match the parameter's declared mode |
| **CE2428** | `nom` in a position with no consume semantics — an FFI extern parameter |
| **CE2435** | a use after a CONSUMING RECEIVER, naming the method that took the value |

**CE2435 against CE2405.** A `nom` argument is a real move and its marker is visible at
the call site, so it reads CE2405. A receiver's mode is DECLARATION-only — `f.close()`
carries no marker at all — so the diagnostic has to carry what the syntax cannot, and it
names the method. One code covers every consuming receiver: `close()` releases a
descriptor and hands nothing on, while `into_inner()` hands the value onward, and the
method name is what tells a reader which happened.

A `const` receiver is refused for both marked kinds, and each reads its own code. The
`poke` write lands in read-only storage: that is CE2400. The `nom` take has no owner to
take from, and unit-level storage is never moved out of whichever keyword declares it:
that is CE2436, and `stdout.close()` is the case it catches. A `peek self`
receiver only reads, so it is legal on a constant. The two marked kinds differ on
a TEMPORARY: a `poke self` needs an address the caller keeps, so a call result is CE2404,
while a `nom self` takes ownership and a temporary is owned by construction.

The codes that the modes shape:

- **CE2405** (use after move) fires from a call argument only when the parameter is `nom`.
  A borrow parameter does not move its argument, so a stdlib call site never reports it.
- **CE2410** (cannot move `main`'s argv view): passing `args` to an unmarked parameter is
  legal; passing it to a `nom` parameter is the error.
- **CE2422** (cannot write through a borrow parameter) is the rule for a borrow parameter
  of any callable, not only of a method.

The borrow codes CE2411, CE2408, CE2421, CE2414, CE2426, CE2412, CE2401, CE2403 and CE2407
apply as `borrowing.md` §5 states them. CE2429 (the unbound chained receiver) is one of the
read-only kinds (`borrowing.md` §5).

## 10. What this makes possible

The stdlib question, "who frees a `string` that a program gives to a stdlib function?",
has its answer in the signature. The answer does not depend on whether the module is
generated IR or Sushi source.

Two more follow:

- **A library can declare a borrow.** The `.slib` manifest carries the mode as its own
  field, so a consumer sees the same signature the library author wrote.
- **The default is the safe one.** The mode a careless author gets is the one that cannot
  double-free, and the dangerous one has to be written down at both ends.

## 10b. The other boundary: a pattern binding

A call is not the only place a value crosses
into a new name. A `match` arm binds a payload, and the binding needs a way to take
ownership of what it binds, for `List@(T)` and `T[]` as much as for a handle.

The pattern boundary carries the same three modes, with the same meanings, marked the same
way:

| pattern | what the binding is | who frees | write through it | rebind the name |
|---|---|---|---|---|
| `Ok(x)` | a SHALLOW copy of the payload | the scrutinee's owner | no -- CE2414 | no -- CE2414 |
| `Ok(poke x)` | a pointer into the payload's storage | the scrutinee's owner | yes | yes |
| `Ok(nom x)` | the value, now the arm's | **the arm** | yes | yes |

The copy in row 1 is shallow, and that is why the last column reads as it does: the slot
holds the owner's descriptor, so a rebind frees a payload the scrutinee still owns.

The differences from the call boundary are two, and both come from the same fact: a match
has no declaration side to agree with.

- **The mode is written once, at the binding.** There is nothing to mark at the other end,
  so CE2427's both-ends rule has no pattern twin.
- **Whether the mode is legal is a property of the SCRUTINEE.** A `nom` binding needs the
  match to own what it matches. A temporary is owned by construction; a place expression is
  not, and `match nom r:` is how the local is handed over -- one more consuming position,
  `ConsumingUse.MATCH_SCRUTINEE`, so `r` afterwards is CE2405 exactly as after `f(nom r)`.
  A `nom` binding under a plain `match r:` is CE2432.

An arm takes the variant WHOLE (CE2433): what suppresses the match's free is the whole
scrutinee, not one payload slot, so a payload left borrowed beside a taken one would be
freed by nobody. A per-slot take needs a drop flag per payload and is a later change.

A `peek` or `poke` binding into a TEMPORARY is legal: the match parks what it owns in a
slot for the whole statement, so the temporary has an address.

**A place, ruled 2026-09-27.** Under a match that only borrows its scrutinee, a
`peek` / `poke` binding takes the places a `let peek` / `let poke` takes: a name, or a
member or index chain off one (`match b.s:`, `match xs[1]:`, `match c.b.s:`). The address
exists -- the reference `let` reads it.
The rules are the reference `let`'s, at the binding: the owner is the ROOT of the place
(`walk_place`), frozen for the arm (CE2412); one `poke` binding of an owner at a time
(CE2403) and no `peek` beside a `poke` (CE2407), where the bindings of ONE pattern are
exempt because they point into disjoint payload slots; a `poke` through a `peek` root is
CE2408. CE2404 stays for a borrowed scrutinee that is not a place: a get-out behind a
`??` (`match l.get(0)??:`), for example.

A `poke` binding also needs a scrutinee with STORAGE, and a `const` has none: it is folded
into read-only memory, so the pointer has nothing to point at and a write through the
binding lands there. That is CE2400, the same answer a `poke self` call on a constant
reads, and `semantics/constant_borrow.py` is where every position asks it. A `peek`
and a bare binding READ the payload, and reading a constant is legal -- through a place
rooted in a constant too (`match B.s:`, `match TS[0]:`): the root decides whether a `poke`
is allowed, never whether the place has an address.

## 10c. The third boundary: a field take

A field read is a borrow. The field take is the one spelling that hands a handle back
OUT of the value that holds it, for example the `File` that a struct owns. `into_inner()`
(R26) uses it.

`nom` marks the take, in the two positions a taken value can go to:

```sushi
extend BufWriter@(W) into_inner(nom self) W | IoError:
    self.flush()??
    return nom self.sink            # a return

let File back = nom wrapper.out     # a let
```

Four conditions, each of them load-bearing:

| condition | why |
|---|---|
| the marker is written | an unmarked field read is a borrow |
| ONE step off a bare NAME | there is a local to spend, and no intermediate field is read through. `nom a.b.c` is CE2411 |
| the name is a local this function OWNS | a `peek`/`poke` parameter, a `let`-borrow or a match binding names storage the caller keeps, so a take out of one is CE2411 |
| the field OWNS something | a field that owns nothing has nothing to hand over, so the marker is an ordinary copy there and the receiver is untouched |

**A take spends the WHOLE receiver.** This is CE2433's all-or-nothing rule read on a
struct instead of a variant: what suppresses the receiver's own free is the whole value
and not one field, so a field left behind would be freed by nobody. The remaining owning
fields are destroyed at the take, in declaration order, and a later mention of the
receiver is CE2405 -- a real move, with the marker visible on the page.

**`drop()` does not run.** A destructor is written for a value that goes away whole, and
here one field survives it, so a `drop()` that flushed into the taken handle or closed it
would be told it still owns what the caller is taking. The method performing the take
does the finishing work itself, which is exactly what `into_inner()` spells above.

Like the pattern boundary, the mode is written once: there is no declaration side to
agree with, so CE2427's both-ends rule has no field-take twin either.

## 10d. The fourth boundary: `??` over a place

The unwrap moves the payload out of its wrapper, so `??` is a
consuming position too -- `ConsumingUse.TRY`. Over a call the Result is a temporary,
nothing else frees it, and the payload lands in the position that takes it. Over a NAMED
wrapper, `let string got = r??` gives `got` the buffer, so `r` must not free it too.

The rule is the one every other boundary has: the payload has the provenance of what it
was unwrapped from.

| `r` is | `r??` is | who frees the payload | `r` afterwards |
|---|---|---|---|
| a temporary (`make()??`) | fresh | the position that takes it | -- |
| a local this function OWNS | fresh, and the `??` SPENDS `r` | the position that takes it | CE2405 |
| a borrow (a parameter, a `match` or `foreach` binding, a `let`-borrow) | a read through `r`'s owner | `r`'s owner | usable |

**The class is the wrapper's, not the payload's.** A `Result@(i32, Fail)` whose `Fail`
carries a string owns heap in its Err arm, and that arm travels: on the propagation path
the error is returned to the caller while the scope cleanup runs, so a local left
registered would free what the caller is about to read. A wrapper that owns nothing in
either arm is copied out of and stays usable, like any plain value. The move is marked
BEFORE the propagation path's cleanup is emitted; the seam is `backend/ownership.py`'s
`unwrap`, and the borrow pass's twin is `unwrap_place`.

**A borrowed wrapper is read through, not refused.** `let string s = r??` over a
parameter binds a borrow -- `s` reads `r`'s payload and frees nothing -- and consuming the
read (`return Result.Ok(r??)`) is CE2411 exactly as `c.get(0)??` is. The escape is the
usual one, `.clone()`.

**The `foreach` binder is this rule and nothing else.** `foreach(line?? in it)` is
`let T line = <item>??`, and the item of a `next()` protocol iterator is a value the
iteration OWNS -- the payload of a fresh `Maybe@(T)` nobody else frees. So the generated
`??` spends the item like any owned local, the body may also hand a protocol item away
(`eat(nom item)`), and a move of it does not travel the loop's back edge because the next
iteration holds a new value.

## 11. Not designed

- **A `nom` binding inside `Own(...)`** (CE2434). Taking the pointee out would leave the
  heap cell with nothing to free it.
- **A consuming variadic** (`nom ...T`). It is rejected.
- **Lifetimes.** Nothing relates a borrow to the value it names, so a borrow still cannot
  be returned or stored (CE2415, CE2416, CE2417, CE2419).
- **Mode inference at a call site.** The marker is written, never deduced. A deduced marker
  would put the visibility of a consume back inside the callee, which is the property this
  ruling exists to give.
