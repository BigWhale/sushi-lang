# Method resolution

How `x.method(args)` is resolved, in what order, and why a user extension method can never
displace a built-in one.

Status: **decided**. The rule is enforced by `CE2097` (issue #239).

## The rule

> **Built-in beats perk beats extension.** A method the compiler defines is always chosen
> before a user extension method of the same name. A perk implementation is the only
> sanctioned way to replace one.

Stated as precedence, highest first:

| | provider | example | can a user replace it? |
|---|---|---|---|
| 1 | **built-in** | `arr.len()`, `n.to_str()`, `p.hash()`, `xs.push(1)` | not by an extension -- `CE2097` |
| 2 | **perk implementation** | `extend Point with Hashable: fn hash() u64` | this *is* the replacement mechanism |
| 3 | **extension method** | `extend i32 squared() i32` | only for names no built-in carries |

The consequence that motivates the error: because every layer resolves a built-in first, an
extension method whose name collides with one is *compiled and then never called*. It is not
lower-priority; it is unreachable.

## The three layers

The same precedence is implemented three times, and all three must agree. Any disagreement is
a bug of the class #239 collected:

| layer | file | what it decides |
|---|---|---|
| **validation** | `semantics/passes/types/calls/methods.py:validate_method_call` | which family checks arity and argument types |
| **inference** | `semantics/passes/types/method_registry.py` | what the call expression's type is |
| **codegen** | `backend/expressions/calls/dispatcher.py:emit_method_call` | which body actually runs |

Inference is the layer that goes wrong quietly. `validate_assignment_compatibility` opens with
`if value_type is None: return`, so a family that fails to infer does not report anything --
the annotation is simply never checked, and the mismatch reaches the backend as a `CE0017`
internal error. Two separate defects hid there for weeks:

- Every **primitive** return type was un-inferred, because the inference layer read the
  builtin-method registry, which the *backend* populates at import time -- and the pipeline
  imports codegen lazily, after semantic analysis. `let u32 b = f64val.to_bits()` compiled and
  silently truncated the 64-bit pattern.
- `string.to_str()` / `string.hash()` were un-inferred for a second reason:
  `METHOD_TYPE_REGISTRY.infer_method_type` is **first-match-wins**, and the string checker
  matched on the receiver type alone. It claimed every method name on a `string`, and a claim
  whose inferrer then returns `None` *ends* the chain rather than falling through.

Two rules follow, and both are load-bearing:

1. **A checker must claim only what it can actually type.** First-match-wins makes an
   over-broad claim indistinguishable from a missing family.
2. **A semantics pass must not read the builtin-method registry** except for the struct/enum
   `hash`/`clone` pair, which the derive pass registers *from semantics*. Everything else in that
   registry is backend-populated, so from semantics it answers differently depending on what
   else the process imported. `semantics/generics/builtin_methods.py` exists to give that
   question one deterministic answer.

## The built-in families

`builtin_method_exists(receiver_type, method_name)` in
`semantics/generics/builtin_methods.py` is the single seam. It mirrors `validate_method_call`'s
receiver dispatch, family for family:

- **arrays** (fixed and dynamic) -- `len`, `get`, `push`, `pop`, `iter`, `clone`, `hash`, ...
- **string** -- the stdlib string methods, plus the primitive `to_str`/`hash`
- **stdio** (`stdin`/`stdout`/`stderr`) and **File**
- **primitives** -- `to_str`, `hash`, and the float-only `to_bits`
- **containers** -- `Result`, `Maybe`, `Own`, `List`, `HashMap`
- **the compiler-derived pair** -- `hash()` and `clone()`, auto-derived in the derive pass for every
  struct and enum

`tests/unit/test_builtin_method_seam.py` pins that list against `validate_method_call`'s in
both directions. Two places answering one question drift; that is the #248 lesson (*if the
same question is asked in six places, the fix is a seam, not a fallback*).

## The family order

Inside the built-in step, both layers try the families in one canonical order:

> perk -> derived hash -> derived clone -> function-value clone -> primitive -> extension fallback

The receiver kinds are disjoint -- a primitive is a `BuiltinType`, the derived pair applies
to `StructType`/`EnumType`, the function-value clone to `FunctionType` -- so the order is
arbitrary. What matters is that validation and codegen state the SAME one: the two layers
used to state it oppositely, and a type that ever satisfied two families would have
dispatched differently per layer with no diagnostic (#273).
`tests/unit/test_method_resolution_family_order.py` pins the order in both files.

## Why extensions lose

Precedent is one-sided. Every language with extension-method-like features resolves the type's
own members first:

| language | rule |
|---|---|
| **C#** (the model `docs/language-guide.md` cites) | instance methods always win; extension methods are considered only when no instance method applies |
| **Kotlin** | "members always win over extensions", with a docs section warning about it |
| **Rust** | inherent methods beat trait methods silently; two applicable traits is **E0034**, a hard error; `#[derive(Hash)]` plus a manual `impl Hash` is **E0119**, a hard error |
| **Go** | no extension methods at all -- methods must live in the type's package, and redeclaration is a compile error |
| **Java** | no extension methods; static methods *hide* rather than override, a long-standing source of confusion |
| **JavaScript** | user wins -- which produced SmooshGate: TC39's `Array.prototype.flatten` broke MooTools-patched sites and had to be renamed `flat` |

Sushi's auto-derived `hash`/`clone` are the `#[derive]` analogue, and **Sushi has no opt-out
from derivation**. So a colliding extension is not merely lower-priority, it is unreachable by
construction -- which meets the project's own bar for erroring rather than warning, recorded in
the `CW3505` deletion note (`internals/errors/warnings.py`): *if the situation cannot possibly
do what the user wrote, it is an error, not a warning*. `CE4007` (perk vs extension) and
`CE0101` (duplicate extension method) are already hard errors for strictly milder collisions.

## Why perks win

A perk implementation is Sushi's equivalent of writing a manual trait impl, and it deliberately
takes precedence at all three layers -- `calls/methods.py` resolves perks before the built-in
families, `visitor.py` prefers a perk method during inference, and the codegen dispatcher runs
its perk step before the auto-derived ones. `tests/perks/test_perk_override_hash.sushi` pins it.

```sushi
perk Hashable:
    fn hash() u64

struct Point:
    i32 x
    i32 y

# The supported way to replace the compiler-derived hash.
extend Point with Hashable:
    fn hash() u64:
        return 999999 as u64
```

Perk implementations are collected into `PerkImplementationTable` and never enter
`ExtensionTable`, so `CE2097` cannot see them. That is a structural property, not a special
case in the check -- and it is pinned by a test so a future refactor merging the two tables
trips over it.

## CE2097

```
error [CE2097]: extension method 'hash()' conflicts with the built-in 'P.hash()'.
  | extend P hash() u64:
  `          --+--
  = note: 'P.hash()' is defined by the compiler
  = help: a built-in method is always chosen before an extension method, so this one could
          never be called -- rename it, or provide 'hash()' through a perk implementation
          ('extend P with <Perk>'), which does take precedence
```

The check (`semantic_analyzer.py:_check_extension_shadows_builtin`) walks `ExtensionTable` and
asks the seam about each `(target type, method name)` pair. Its placement is constrained at
both ends:

- **after the derive pass**, which registers the struct/enum `hash`/`clone`;
- **after the generic-extension merge loop**, because a monomorphized `extend Box@(i32) hash()`
  only enters the extension table there. Running earlier is exactly why that shape went
  uncovered in the first cut.

It keys on whether a built-in genuinely exists for that pair, never on the bare method name --
so a type that carries no such built-in (a struct the compiler could not derive `hash` for, say)
can still be extended with a `hash()` of its own.

## Which types an extension applies TO

A separate question from precedence, and settled by the same principle. Status: **decided**,
issue #393.

> **A concrete type argument in an extension target is a CONSTRAINT, not a type-parameter
> name.** `extend Box@(i32)` applies to `Box@(i32)` and to nothing else.

| declaration | applies to |
|---|---|
| `extend Box@(T) f()` -- fully generic | every instantiation of `Box` |
| `extend Box@(i32) f()` -- fully concrete | `Box@(i32)` only |
| `extend Box@(i32) f()` **and** `extend Box@(string) f()` | legal -- two types, two methods |
| `extend Box@(T) f()` **and** `extend Box@(i32) f()` | **rejected** -- `CE0101`, relational |
| `extend Pair@(i32, U) f()` -- partially concrete | **rejected** -- `CE2098` |

The argument used to be stored as a type-parameter *name*, so `extend Box@(i32) tag()`
registered for every instantiation of `Box`: it answered a `Box@(string)` receiver, and it was
a `CE0000` as soon as the body touched the type. A perk implementation on the same target had
always scoped correctly -- one question, two answer sites, which is the #239 class exactly.

**A PERK IMPLEMENTATION reads the same table.** `extend Box@(T) with Show` is a template
and `extend Box@(i32) with Show` is a constraint, exactly as above, and a partially
concrete target is the same CE2098. The template is re-filed out of `Program.perk_impls`
and into `Program.generic_perk_impls` -- the same move a generic extension makes, and for
the same reason: the typecheck pass, the backend's declaration and definition loops and
the unit fingerprint all walk `perk_impls` assuming a concrete `self`. One copy per
instantiation the program names is registered in the perk-impl table under the
instantiation's interned name and appended to the DECLARING unit's AST, so it is an
ordinary implementation from there on.

**The instantiation runs before the functions are monomorphized**, and that order is
load-bearing: a `@(S: Show)` constraint is checked while a generic function is
monomorphized, so `Box@(i32)` has to already say it implements `Show` or the call is
CE4006 for a type that does. It also puts `Drop` within reach: `TypeQueries.drops` reads
the perk table, so the copy registered here is in the set before `derive`, `effects` and
the `borrow` pass ask whether the instantiation owns a resource.

**A LATE instantiation gets its copy too** (#555). A type named only inside a generic body
-- `let Box@(T) b` in `outer@(T)`, or the return of a generic it calls -- is interned while
that body is substituted, after the first cut. Every instantiation the tables hold with no
copy yet is cut afterwards, to a fixpoint, so `Box@(string)` has its `show()` whether the
program spelled it or a substitution produced it. The constraint check reads the templates
beside the registered copies: a template applies to every instantiation of its base name by
construction, so `@(S: Show)` holds for a late `Box@(string)` before its copy is cut.

**Why the overlap is rejected rather than resolved by most-specific-wins.** Under
specialization, whether the template's body is dead code would depend on which instantiations
exist ELSEWHERE in the program: with only `Box@(string)` live the template method is compiled
and never called, and one `Box@(i32)` anywhere makes it live again. That is a reachability rule
keyed on the rest of the program, and `CE2097` above is built on the opposite rule -- an
unreachable declaration is a diagnostic. Rejecting is also forward-compatible: an error can
become working code later, while removing specialization later breaks programs.

**Why the partial form is rejected too.** It removes the ordering question entirely. Two fully
concrete targets cannot overlap, and template-versus-concrete is strictly ordered, so
`Pair@(i32, U)` against `Pair@(T, string)` -- equally specific, neither more so -- cannot
arise. Partial ordering is where Rust's specialization has stalled for years. Rust and Haskell
reject overlap unless you opt in (`E0592`; `{-# OVERLAPPING #-}`); C++, C#, Swift and Kotlin
allow it and pay for a formal specificity ordering.

**The escape** is a perk implementation on the concrete target, which already outranks
extension methods in the ladder above and already scopes correctly.

## The UFCS epic's additions (the ladder is untouched)

Three extension capabilities joined without moving any rung. An **array target**
resolves like any other concrete type, and `extend T[]` instantiates per element type
at the CALL SITE (`$array` templates; ruling 3). A **method-level type parameter**
(`name@(U)`) is solved from the arguments and never enters the ExtensionTable — the
template answers by unification, and the copy's symbol carries the solved arguments.
An **error channel** (`| E`) changes what a resolved call YIELDS (the interned
`Result@(T, E)`), not how it resolves. The one new resolution-adjacent diagnostic is
**CE2515**, a FALLBACK where CE2008 would fire: the method is missing on a
Result/Maybe receiver and present on its payload type, which is an unhandled channel,
not a typo. Resolution still runs first — a method found on the wrapper itself
(`.realise`) is rung 1 as always. The decision record is
[ufcs-combinators.md](ufcs-combinators.md).

## The static method: a name behind the TYPE's dot

A separate question again, and the constructor half of method resolution. Status:
**decided**, issue #542.

> **A name behind a type's dot is a MEMBER of that type: a variant, or a static method.
> Never both. A local of the same name wins first.**

One namespace, reached with a dot. Rust reaches the same place with `::`; Sushi does not
need a second path operator, because "a name has exactly one home" already says it.

### The spelling

A `static` marker before the method name. It is read AFTER the target type, so a generic
target, a namespaced target and a method-level type parameter all fall out with no
lookahead.

```sushi
struct Vec:
    i32 x
    i32 y

extend Vec static at(i32 x, i32 y) Vec:
    return Vec(x, y)

let Vec v = Vec.at(3, 4)
```

`static` is a reserved word. It is not re-admitted in `method_name`, so `v.static()` and
a method named `static` are not writable.

### What a static IS, and is not

| | a static | an instance method |
|---|---|---|
| receiver | **none** -- no implicit `self`, and no mode to declare | implicit `self`, borrowed unless marked |
| call site | on the TYPE name: `Vec.at(3, 4)` | on a value: `v.sum()` |
| parameters | ordinary, and the modes are the ordinary four | the same, plus the receiver's |
| return | ordinary; `\| E` opts into the channel exactly as elsewhere | the same |
| visibility | none of its own -- as visible as its target type | the same |
| in a perk | **never** -- a perk has no `Self` (CE4014) | that is what a perk contracts |

Everything in the right column that is not about the receiver is unchanged. A static's
parameters BORROW unless marked `nom`; its owning return is the caller's; its `| E`
channel wraps a bare success at the return seam. The one thing it lacks is a receiver,
and the two positions that could name one are one fault with one code:

- a receiver MODE in the signature -- `extend Vec static at(poke self)`,
- a mention of `self` in the body.

Both are **CE0134**, tier 2, with the caret on whichever was written.

### Which targets

Every target an extension may name, **except an array**. A struct, an enum, a
**primitive** (`extend f64 static of_int(i32 v) f64:`), a built-in generic
(`extend List@(i32) static of_one(i32 v) List@(i32):`) and a **generic** target, which
is a template like any other: one copy per instantiation, and the type argument comes
from the PROPAGATION STAMP at the binding site, because there is no receiver to read it
from.

An ARRAY target is **CE2104**. An array type has no spelling in an expression position
-- `i32[].two()` is a parse error, and no form reaches it -- so the declaration would
compile and never be callable. That is CE2097's hazard, and the answer is the same one:
if the situation cannot possibly do what the user wrote, it is an error.

```sushi
struct Cage@(T):
    T item

extend Cage@(T) static holding(T item) Cage@(T):
    return Cage(item)

let Cage@(i32) a = Cage.holding(9)      # T from the declared type
```

That stamp is the reason a static is not only ergonomics. A free function whose `T`
names only the RETURN cannot be inferred (CE2060) and has to spell `box_new@(i32)()`; a
static reads the binding site instead. It is also why `new` is available as a static's
name and not as a free function's (CE6001).

### The two collisions

A name has exactly one home, so both are refused where they are written:

| written | refusal |
|---|---|
| a static and an instance method of one name on one type | **CE0101**, the duplicate-extension rule it always was |
| a static and a VARIANT of one name on one enum | **CE2103**, relational -- the variant would always win, which is CE2097's hazard |

CE2045 grew a second half for the same reason: a name behind an enum's dot could have
been either member, so its help names both escapes.

### The built-in statics are static methods

`List.new`, `List.with_capacity`, `HashMap.new`, `Own.alloc`, `f64.from_bits` and
`f32.from_bits` are static methods on their types -- one rule, not two. They are NAMED
in one table (`semantics/statics.py:BUILTIN_STATICS`) and each is still emitted by its
container's own narrow handler, because a container static has no `ExtendDef` to resolve
and so has nothing yet to converge onto. The general path DEFERS to that table rather
than refusing what it cannot find; #553's lesson is why the narrow handlers stay until a
test proves the general path covers them.

### The seams

| question | where |
|---|---|
| does this name denote a TYPE, of any kind | `semantics/statics.py:names_a_type` -- the scope pass and the typecheck pass both ask it |
| which type does this receiver name, and does it declare that static | `passes/types/calls/statics.py` -- the validation half and the inference half both read it |
| instance or static (they share one table) | ONE filter at the end of `resolve_extension_method`; `resolve_method(..., static=True)` skips the perk rung outright |
| what modes do the arguments cross in | `CalleeKind.STATIC_METHOD` -- a new kind, not a widened `METHOD`, because a receiver-less callee asks a different question. Gate: `tests/unit/test_callee_mode_matrix.py` |
| the alias fold | `fold_namespaced_static`, unchanged in shape: it asks whether the namespace holds a type, so `geo.Vec.origin()` folds like `hm.HashMap.new()` (#506) |

The refusal for a type whose dot holds no such member is **CE2102**. It replaced a
CE1001 "use of undeclared identifier 'Box'" for a struct declared three lines above: the
fault was the POSITION, not the name, and the fix is in the scope pass, which now lets a
type name through in a receiver position and leaves the answer to the pass that has the
method tables.

### Prior art

| | the term | how it is spelled |
|---|---|---|
| **Rust** | associated function | no marker; a method is one that takes `self`. `::` is a different operator from `.` |
| **Swift** | type method | `static func origin() -> Vec` |
| **C++** | static member function | `static Vec origin();` |
| **Java, C#, Dart, PHP, JavaScript** | static method | `static Vec origin()` |
| **Python** | static / class method | `@staticmethod`, `@classmethod` |
| **Ruby, Objective-C, Smalltalk** | class method | `def self.origin` |
| **Kotlin, Scala** | companion object member -- no statics | `companion object { fun origin() }` |
| **Go, Zig** | no term -- a package or namespace function | `bufio.NewReader(f)` |

Sushi says **static method**, which is what most of that table says.
`docs/design/unit-storage.md` had already reserved the word: `var` took unit-level
storage precisely so that "static" could keep meaning a function called on a type name.
Sushi has no static STORAGE.

Java's statics *hide* rather than override, a long-standing confusion. It cannot occur
here: Sushi has no inheritance.

The Rust route -- no marker, distinguish by an explicit `self` in the parameter list --
was priced at ~416 declarations in the corpus and rejected. The Go route -- no functions
behind a dot at all -- would have turned each of the 230 `List.new()` sites into
`list_new@(i32)()`, and it leaves variants behind the dot regardless: `Type.Variant`
outnumbers a static call 22 to 1 (10,469 to 479).

### What a static does NOT do

- **No `::`.** A second path operator to disambiguate what a dot already means is a
  bigger change than the feature, and on a struct the dot is not ambiguous at all.
- **No static in a perk.** No `Self` (HANDLES.md R7). CE4014.
- **No overloading.** A name has one home; both collisions above are refusals.
- **No export through a BINARY `.slib`.** A binary library ships no extension method at
  all today, instance or static, so this is a pre-existing limit and not a static one. A
  SOURCE `.slib` ships the declaration as text and a static exports through it.

## A perk method and an extension method differ in one thing

> **The target type comes from a different place. Nothing else separates them.**

An extension method names its target in its own declaration (`extend i32 squared()`); a
perk-implementation method takes it from the `extend X with P` header. Every other
property is shared, and the compiler shares the code that reads it: one body validator
(`passes/types/signatures.py:_validate_method_body`), one `CalleeKind.METHOD` for
parameter modes, and one backend path -- a perk method is wrapped as a synthetic
`ExtendDef` and emitted through the extension emitter.

The **error channel** was the last exception, and `HANDLES.md` ruling R1 removed it. A
perk method declares `| E` in the same shape an extension method does, on the CONTRACT
and on every implementation alike:

```sushi
perk Source:
    fn read_one() i32 | SourceError

extend Counter with Source:
    fn read_one() i32 | SourceError:
        return self.value          # the success returns BARE; the seam wraps it
```

The channel is part of the signature, so the contract and the implementation must
agree: a contract that declares one and an implementation that omits it, an
implementation that invents one the contract has not got, and two channels over
different error types are all the same mismatch. **CE0133** is the relational
diagnostic -- the primary at the implementation, a note at the contract method.

A perk method has no `Self` type, so a contract cannot say "returns another one of me".
That is the one thing a perk still cannot express, and it is why `.share()` is written
on each handle rather than on a contract.

**Where the rule lives.** The classification is decided ONCE, in the collect pass
(`semantics/generics/extension_targets.py:classify_extension_target`), because that is the
pass whose struct and enum tables say which bare names are declared types -- `Box@(T)` and
`Box@(Point)` are spelled identically and mean opposite things. The answer is carried on the
declaration (`ExtendDef.target_shape`) and on its collected signature
(`GenericExtensionMethod.target_key`), so the instantiate and monomorphize passes read it instead of deciding
again from a different set of visible types. `instantiation_key` is the one authority for the
mangled name a concrete target matches; the perk-impl table built its own and joined the
arguments differently, so a two-argument concrete target registered under a name no receiver
ever resolved to.

## Related

- `docs/design/closures.md` -- the same rule stated for `List@(T)` extension methods
- `docs/language-guide.md` -- the Extension Methods section, user-facing
- `docs/design/type-identity.md`, `docs/design/move-semantics.md` -- sibling decision records
