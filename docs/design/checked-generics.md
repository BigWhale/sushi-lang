# Checked generics

Status: ACCEPTED and BUILT (#1070). The rulings are David's: option (b) on 2026-09-28,
R1 to R9 on 2026-10-07, and the two pack rulings on 2026-10-08. Section 1 describes the
language before this work.

## Summary

- A generic body is checked ONE time, where it is written. A template that no code calls
  is checked too.
- In that check, each type parameter is OPAQUE. The body can do with a `T` only what a
  constraint of `T` promises.
- `==` and `!=` need `Eq`, `< <= > >=` need `Ord`, a hole and `println` need `Display`,
  `.hash()` needs `Hashable`, and `.clone()` needs the new predefined perk `Clone`. A user
  perk gives its methods. Arithmetic on a `T` is refused.
- A call, a written type and a constructor in a template pass `T` on only when the
  constraints of `T` satisfy the constraints of the callee (entailment).
- An extension or a perk implementation on `Box@(T)` inherits the bounds that `Box`
  declares, and its target can add a bound: `extend List@(T: Clone) filter(...)`.
- An element of a pack is opaque too, and the body of an `expand` is checked one time.
- A fault in a template is reported one time, at the template. A type argument that does
  not satisfy a constraint is an error at the call, never in the body.
- **Breaking:** a template with an unconstrained `T` that calls `.hash()`, `.clone()`,
  `==`, `<` or `{x}` on it stops compiling. The fix is the constraint that the help names.

## The rulings

| # | Ruling | Section |
|---|---|---|
| **(b)** | A template is checked once, where it is written, with each type parameter opaque. An operation on `T` is legal only when a constraint promises it (2026-09-28) | 2 |
| **R1** | An extension or a perk implementation on `Box@(T)` INHERITS every bound that `Box` declares. It may ADD bounds in its target: `extend List@(T: Clone) filter(...)` | 5 |
| **R2** | `==`/`!=` need `Eq`, and `< <= > >=` need `Ord`. Arithmetic on `T` is REFUSED. Generic numbers are a separate design item | 3.1, 3.2 |
| **R3** | There are no perk bundles. `Ord` does not give `Eq` | 3.4 |
| **R4** | `Hashable`, `Eq`, `Ord`, `Display` and `Clone` stay structural for a concrete type. A user perk stays explicit, and so does `Drop` | 3.4 |
| **R5** | `.clone()` on `T` is opt-in, through a new predefined structural perk `Clone`. Every type that holds no resource satisfies it. A handle does not, and `.share()` stays. An opaque `T` always moves | 3.3, 8 |
| **R6** | An element of a pack is opaque and carries the constraints of the pack. The body of an `expand` is checked once | 6 |
| **R7** | The per-instance remainder: the consume-or-copy of an owning value, the drop set and the order of `drop()`, the derived method of the concrete type, the layout, and E3. Every other rule runs once, on the template | 8 |
| **R8** | E3 with an opaque `E` stays per instance, with a note at the template. `??` on an opaque `E` propagates the same `E` only | 7 |
| **R9** | The constraints of a method-level type parameter were dropped. That is a separate fault, fixed as #1191 | 5.5 |
| **P1** | An `expand` may run zero times, as a `foreach` may. A pack template whose only `return` is in its `expand` is [CE0107](../error-catalog.md#ce0107), at the template (2026-10-08) | 6.3 |
| **P2** | The binder of an `expand` gets the lints of a `foreach` binder: [CW1001](../error-catalog.md#cw1001) when nothing reads it, [CW1002](../error-catalog.md#cw1002) for a shadow, and `expand(_ in args)` is the discard (2026-10-08) | 6.4 |

---

## 1. Motivation

Before #1070, the compiler checked a generic body only in its copies, one copy for each
set of type arguments that the program used. Three faults came from this.

- **A template that nothing calls was not checked.** `fn f@(T: Hashable)(T v) u64 |
  StdError: return v.hash()` returns a bare value from a channel body ([CE2030](../error-catalog.md#ce2030)). The
  build passed when nothing called `f`.
- **The fault appeared far from its cause.** `fn size@(T)(T x) i32: return x.len()`
  compiled for a `string`. A call `size(4)` gave [CE2008](../error-catalog.md#ce2008) inside the body, at a line
  the caller did not write. The signature did not tell the caller what `T` must have.
- **A library author saw nothing.** A `--lib` build checked a template only when the
  library code instantiated it. The consumer then found the fault in code that it did
  not write.

A constraint was only a check of a type argument at the call. The body read the
concrete type of each copy, never the constraint. So a constraint was a filter, and not
a promise.

## 2. The rule

### 2.1 Three options

| Option | What the written template gets | Result |
|---|---|---|
| (a) The statement shape | [CE2030](../error-catalog.md#ce2030), [CE0107](../error-catalog.md#ce0107), [CE0140](../error-catalog.md#ce0140), [CW1001](../error-catalog.md#cw1001) | A small change. No type fault in a body is found |
| Two phases (C++) | Every expression that does not depend on a type parameter | The template stays duck typed: the signature does not tell the caller what `T` must have |
| **(b) The constraints (Rust, Swift, Go)** | The whole body, with `T` opaque | **Chosen.** The signature is the whole contract, a bad type argument is an error at the call, and a constraint is a real promise |

Option (b) includes (a): the statement rules run on the template too.

### 2.2 The opaque type parameter

In the check of a template, `T` stands for "some type that satisfies the constraints of
`T`", and for nothing more. The body can:

- move a `T`, hold it in a local, a field, a payload, a tuple, an array, a `List@(T)` or
  a `Maybe@(T)`, and give it to a parameter of type `T`;
- call a method that a constraint of `T` declares;
- use an operator or a built-in method that a constraint of `T` answers (3.1);
- pass `T` to another generic whose constraints the constraints of `T` satisfy (4).

Everything else on a `T` is refused, with a note at the declaration of `T` and a help
that names the constraint to add:

<!-- docs-sweep: error CE2008 -->
```sushi
fn size@(T)(T x) i32:
    return x.len()          # CE2008: no constraint of 'T' gives 'len'

fn main() i32:
    return 0
```

```
error [CE2008]: undefined function 'T.len'.
  = note: 'T' is declared here with no constraint
  = help: no perk declares 'len'; a type parameter has the methods of its constraints and nothing more
```

A template whose body is correct gives no diagnostic, also when nothing calls it.

### 2.3 Where the check runs

- **Every template kind**: a generic function, a generic-target extension (`extend
  Box@(T)`), an array extension (`extend T[]`), an extension with a method-level type
  parameter (`extend i32 pick@(U)`), a perk implementation on a generic target (`extend
  Box@(T) with P`) and on every array (`extend T[] with P`), and a pack template.
- **Every unit of the program**, the units of a library at its own `--lib` build
  included, in each library kind, and every bundled stdlib unit.
- **Not a consumed library unit.** Its own `--lib` build checked it. A library that an
  older compiler built did not get this check. The compiler-version check refuses it at a
  later minor version ([CE3503](../error-catalog.md#ce3503)). With `--ignore-compiler-version`, its templates keep
  the old, per-instance behaviour at the consumer.
- **Not a template whose constraint names no perk.** [CE4003](../error-catalog.md#ce4003) is the one fault, and
  the body would read a promise that does not exist.

The statement rules of option (a) run on the written template: [CE0107](../error-catalog.md#ce0107), [CE0140](../error-catalog.md#ce0140),
[CE2030](../error-catalog.md#ce2030), and the scope lints [CW1001](../error-catalog.md#cw1001) and [CW1002](../error-catalog.md#cw1002). A copy does not report them
again.

---

## 3. What a constraint promises

### 3.1 The predefined perks

| Constraint | What the body may do with a `T` |
|---|---|
| `Eq` | `a == b`, `a != b`, `a.eq(b)`; `contains` and `index_of` on a `T[]`, a `T[N]` and a `List@(T)` |
| `Ord` | `a < b`, `a <= b`, `a > b`, `a >= b`, `a.compare(b)` |
| `Display` | a hole `"{x}"`, `print(x)`, `println(x)`, `x.to_str()` |
| `Hashable` | `x.hash()` |
| `Clone` | `x.clone()`, and `.clone()` of a type that holds `T` (`List@(T)`, `Box@(T)`, `Maybe@(T)`) |
| `Hashable + Eq` | a `HashMap@(T, V)` key |

A refusal reuses the code of the same fault on a concrete type: [CE2514](../error-catalog.md#ce2514) for a
comparison, [CE2035](../error-catalog.md#ce2035) for a hole, [CE2115](../error-catalog.md#ce2115) for `print`, [CE2008](../error-catalog.md#ce2008) for a method,
[CE2100](../error-catalog.md#ce2100) for `contains`, [CE2054](../error-catalog.md#ce2054) and [CE2055](../error-catalog.md#ce2055) for a key. `.clone()` has its own code,
[CE4018](../error-catalog.md#ce4018) (3.3).

```sushi
fn largest@(T: Ord)(nom T a, nom T b) T:
    if (a > b):
        return a
    return b

fn label@(T: Display + Eq)(T x, T y) string:
    if (x == y):
        return "same {x}"
    return "{x} and {y}"

fn main() i32:
    println(largest(nom 3, nom 9))           # 9
    println(label(4, 4))                     # same 4
    println(label("Arthur", "Ford"))         # Arthur and Ford
    return 0
```

### 3.2 What no constraint promises

| Operation on a `T` | Diagnostic | Why |
|---|---|---|
| `+ - * / %` and the unary minus | [CE2518](../error-catalog.md#ce2518) | R2: no perk gives arithmetic. Generic numbers are a separate design item, and adding them later breaks no program |
| a field (`x.weight`) | [CE2106](../error-catalog.md#ce2106) | a perk has no fields |
| a cast (`x as i32`, `v as T`) | [CE2014](../error-catalog.md#ce2014) | a cast reads the concrete type |
| a `T` where a concrete type is expected | [CE2006](../error-catalog.md#ce2006), [CE2003](../error-catalog.md#ce2003) | `T` is not `i32` in every copy |
| a static (`T.make()`) | the existing path | a perk has no static (Known Limitation 4) |
| a method of a built-in family (`len`, `push`, `to_bits`) | [CE2008](../error-catalog.md#ce2008) | no family claims an opaque receiver |

<!-- docs-sweep: error CE2518 -->
```sushi
fn total@(T)(T a, T b) T:
    return a + b            # CE2518: a type parameter has no arithmetic

fn main() i32:
    return 0
```

### 3.3 `Clone` (R5)

`.clone()` is the only deep copy. A deep copy of a value that holds a resource is a
second handle, and that closes one descriptor two times. So `.clone()` on a `T` needs the
promise "`T` holds no resource". That promise is the predefined perk `Clone`.

- `Clone` is structural. A type satisfies it when it holds no resource: it is not a
  `Drop` type, and no field, element or payload holds one. This is the predicate of
  [CE2431](../error-catalog.md#ce2431) (`holds_declared_resource`). For an opaque `T`, the predicate answers "not
  `T.promises(Clone)`", so the two rules are one fact.
- The contract is `fn clone() Self`. The built-in `.clone()` is the contract, so an
  implementation has nothing to override: `extend X with Clone` is [CE4017](../error-catalog.md#ce4017).
- A handle (`File`, `TcpStream`) has no `Clone`. `.share()` stays the way to get a second
  owner of a handle.
- `.clone()` on a `T` with no `Clone`, or on a type that holds such a `T`, is
  [CE4018](../error-catalog.md#ce4018). [CE2431](../error-catalog.md#ce2431) is not used: its message says "it owns a resource", and an
  opaque `T` only MAY own one.

```sushi
fn twice@(T: Clone)(T x) List@(T):
    let List@(T) out = List.new()
    out.push(x.clone())
    out.push(x.clone())
    return out

fn main() i32:
    let List@(string) l = twice("towel")
    println("{l.len()}")                     # 2
    return 0
```

### 3.4 Structural and explicit perks (R3, R4)

- For a CONCRETE type argument, `Hashable`, `Eq`, `Ord`, `Display` and `Clone` stay
  structural: the derived contract satisfies them, and an implementation is the override.
- For an OPAQUE `T`, a perk is satisfied only when `T` declares it. `T: Ord` does not
  give `==` ([CE2514](../error-catalog.md#ce2514)): there are no bundles, so write `@(T: Eq + Ord)`.
- A user perk stays explicit: an implementation is the only way to satisfy it. `Drop`
  stays explicit too.

### 3.5 A user perk

A user perk gives its methods, with their modes and their error channel. A method of
the perk that declares `| E` gives a `Result@(T, E)` at the call, as a method of a
concrete type does. `read_all@(R: Reader)` in `<io/contracts>` is the stdlib case: its
body calls `poke self` methods with `| IoError` through the constraint.

Two constraints of one `T` that both declare a method of one name give that name two
homes. The body cannot call it, and the template is [CE4015](../error-catalog.md#ce4015), at the second
constraint, with a note at the first.

---

## 4. Entailment

A template passes its `T` on in three positions. In each one, the constraints of the
receiving generic must be promised by the constraints of `T`:

1. **A call of a generic function**: `outer@(T)` calls `inner@(U: Hashable)(x)`.
2. **A written type**: `let Box@(T) b` with `struct Box@(U: Hashable)`.
3. **A constructor whose type the call infers**: `Box(x)`, `Maybe.Some(x)`.

A constraint that the caller does not promise is [CE4006](../error-catalog.md#ce4006) at the site, with a note at
the constraint of the callee, a note at `T`, and a help that names the constraint to add.
A template that writes `Box@(T)` three times gets one diagnostic.

<!-- docs-sweep: error CE4006 -->
```sushi
fn inner@(U: Hashable)(U x) u64:
    return x.hash()

fn outer@(T)(T x) u64:
    return inner(x)         # CE4006: 'T' does not promise 'Hashable'

fn main() i32:
    return 0
```

`outer@(T: Hashable)` fixes it. A concrete type argument at a call outside a template is
judged as before: [CE4006](../error-catalog.md#ce4006) at the call, and the analysis stops before the body
of the template is read again.

---

## 5. Extensions and perk implementations (R1)

### 5.1 Three forms of a target argument

| Target | Meaning |
|---|---|
| `Box@(Point)`, `Box@(Show)` | a bare DECLARED name: a constraint to one instance. A perk name then fails as a type ([CE2001](../error-catalog.md#ce2001)) |
| `Box@(T)` | a bare UNDECLARED name: a type parameter. It inherits the bounds of `Box` |
| `Box@(T: Show)` | always a type parameter, with a bound that the target ADDS |

- The array form is `(T: Clone)[]`.
- A bound on a name that a unit declares as a type is [CE2124](../error-catalog.md#ce2124).
- A bound is legal only at the top level of an `extend` target. In every other position
  (a `let` type, a parameter, a return, a field, an explicit type argument, a conversion
  source, inside a target argument) it is [CE6110](../error-catalog.md#ce6110). A function, a struct and an enum
  write the bound in their own type-parameter list.
- A target bound names a perk in scope ([CE4003](../error-catalog.md#ce4003)), and the visibility rules of a
  constraint apply to it ([CE4011](../error-catalog.md#ce4011), [CE3010](../error-catalog.md#ce3010)).

The grammar accepts `NAME: P + Q` in every type-argument list, because the parser cannot
know the position. The AST builder accepts it at the top level of a target only.

### 5.2 Implied bounds

The receiver bounds of a target parameter are the bounds that the base type declares,
then the bounds that the target adds. The check of the body reads both:

```sushi
struct Keyed@(K: Hashable):
    K key

extend Keyed@(K) code() u64:
    return self.key.hash()                   # K: Hashable is inherited

struct Box@(T):
    T value

extend Box@(T: Display) describe() string:
    return "Box holding {self.value}"        # T: Display is added

fn main() i32:
    let Keyed@(string) k = Keyed(key: "x")
    let Box@(i32) b = Box(value: 42)
    println("{k.code() == k.key.hash()} {b.describe()}")   # true Box holding 42
    return 0
```

**The `HashMap` key rule is the implied bound of `K`.** `HashMap@(K, V)` needs a key with
a hash and an equality, so `extend HashMap@(K, V)` inherits `K: Hashable + Eq`. The rule
stays a rule of the written type ([CE2054](../error-catalog.md#ce2054), [CE2055](../error-catalog.md#ce2055)), and it is not put on the type
parameter, so a bad key gives one diagnostic and not two.

### 5.3 An added bound fails at the call

A receiver whose type argument does not satisfy an added bound gets [CE4006](../error-catalog.md#ce4006) at the
call, with a note at the bound. The compiler makes no copy of the method for that
instance.

<!-- docs-sweep: error CE4006 -->
```sushi
use <io/fs>

struct Box@(T):
    T value

extend Box@(T: Clone) dup() Box@(T):
    return Box(value: self.value.clone())

fn copy_of(Box@(File) b) Box@(File):
    return b.dup()          # CE4006: File does not implement Clone

fn main() i32:
    return 0
```

The method is not invisible to that instance ([CE2008](../error-catalog.md#ce2008)). CE4006 names the type, the perk
and the bound, and this is the rule of a free function. A bound does not make two
declarations disjoint: that needs the reasoning "`File` is not `Clone`", which changes when
an implementation is added, and that is specialization. A template and a concrete target
of one method on one base stay [CE0101](../error-catalog.md#ce0101), and two perk templates of one perk on one base
stay [CE4002](../error-catalog.md#ce4002).

One predicate (`target_bounds_hold`, `generics/constraints.py`) answers "does this
template apply to this instance". The cutters of the copies, the derived-contract
overrides and the call rung all read it. If two of them disagreed, an override would name
a method with no copy.

### 5.4 `Drop` adds no bound

`extend Guard@(T: Clone) with Drop` is [CE4019](../error-catalog.md#ce4019). A bound in the target of `Drop` gives
`drop()` to some instances and not to others. An instance that the bound excludes then
releases nothing, with no diagnostic. A `Drop` implementation inherits the bounds of its
type and adds none. Put the bound on the type, or remove it.

### 5.5 Method-level type parameters (R9, #1191)

`extend Box@(T) pair_with@(U: Weigh)(U other)` keeps the constraint of `U`. A call whose
argument does not satisfy it is [CE4006](../error-catalog.md#ce4006) at the call, as for a free function. In the
check of the body, `U` is opaque with its constraints, beside the receiver parameters, and
both are parameters of one template.

### 5.6 The stdlib

The combinators of `<collections/iter>` clone their elements, so their targets state the
bound: `extend List@(T: Clone) filter(...)`, `extend (T: Clone)[] enumerate()`,
`fold@(U: Clone)`, `unzip@(T: Clone, U: Clone)`. A `List@(File)` then gets no copy of
`filter`, and a call of it is [CE4006](../error-catalog.md#ce4006). `<io/buf>` needs no change: `BufReader@(R:
Reader)` gives every extension on `BufReader@(R)` the bound `R: Reader`.

---

## 6. Packs and `expand` (R6)

### 6.1 The element type

The body of an `expand` is checked one time. The binder has an opaque ELEMENT type that
carries the constraints of the pack:

```sushi
perk Show:
    fn show() string

extend i32 with Show:
    fn show() string:
        return "int {self}"

extend string with Show:
    fn show() string:
        return "text {self}"

fn show_all@(...Ts: Show)(...Ts items) ~:
    expand(it in items):
        println(it.show())

fn main() i32:
    show_all(42, "Mostly Harmless")
    return 0
```

- Each `expand` node binds its own element type. In a copy, the elements of two `expand`s
  of one pack can have two different types, so `a == b` across two binders is a mixed
  pair ([CE2513](../error-catalog.md#ce2513)), with a note at each binder. One shared type would accept it on the
  template and refuse it in a copy.
- An operation that the pack does not promise is refused with the code of 3.1 or 3.2.
  The note says that the binder holds one element of `...Ts`, and the help spells
  `@(...Ts: Display)`.
- The lead parameters of a pack template (`@(H, ...Ts)`) are opaque, as in every
  template.
- The value pack is not a value. A use of `args` outside `expand` is [CE0144](../error-catalog.md#ce0144).
- A call of a pack template inside a template is checked against its signature, with the
  pack fanned out. An argument whose type does not promise the perk of the pack is
  [CE2090](../error-catalog.md#ce2090).

### 6.2 Where an `expand` stands

An `expand(a in args):` walks the value pack of its own function. [CE0119](../error-catalog.md#ce0119) refuses it
in a lambda body, in a body with no pack, when the iterable is not a name, and when the
name is not the value pack. The collect pass judges this on the written body, and the
analysis stops after it, as it does after [CE0147](../error-catalog.md#ce0147).

### 6.3 An `expand` may run zero times (P1)

A call with no pack argument runs the body zero times. So a `return` inside an `expand`
does not end the path, as a `return` inside a `foreach` does not. The rule is decided one
time, on the template:

<!-- docs-sweep: error CE0107 -->
```sushi
fn first@(...Ts: Display)(...Ts args) string:
    expand(a in args):
        return "{a}"        # CE0107: 'first' must return a value on all code paths

fn main() i32:
    return 0
```

Before this ruling, the copy for an empty pack decided it and named the copy.

### 6.4 The binder lints (P2)

The binder of an `expand` is a declaration, as a `foreach` item is:

| Body | Lint |
|---|---|
| nothing reads the binder | [CW1001](../error-catalog.md#cw1001) at the binder |
| a `let` of the binder name in a nested block, or a pattern binding of that name | [CW1002](../error-catalog.md#cw1002) |
| `expand(a in args)` after a `let a` | [CW1002](../error-catalog.md#cw1002) at the binder |
| `expand(_ in args)` | nothing: `_` is the discard |

```sushi
fn count_all@(...Ts)(...Ts items) i32:
    let i32 n = 0
    expand(_ in items):
        n := n + 1
    return n

fn main() i32:
    println(count_all(1, "two", true))       # 3
    return 0
```

---

## 7. An error channel with an opaque `E` (R8)

- **E3 stays per instance.** In `fn f@(E)() T | E`, whether `E` is an error type depends
  on the type argument. The copy judges it ([CE2084](../error-catalog.md#ce2084)), with a note at the template.
  `docs/design/error-conversion.md` section 2.5 has the rule.
- **`??` on an opaque `E` propagates the same `E` only.** A conversion is declared for a
  pair of named error types (`extend FileError as AppError:`), and a template cannot name
  one for its `E`. So a `??` of a `Result@(i32, E)` in a `| AppError` body is [CE2511](../error-catalog.md#ce2511),
  with a note at `E`. The help names `.map_err(f)??` at the site, or a concrete error type
  in the signature.

<!-- docs-sweep: error CE2511 -->
```sushi
error AppError:
    Bad

fn unwrap_any@(E)(Result@(i32, E) r) i32 | AppError:
    return Result.Ok(r??)   # CE2511: an opaque E has no conversion

fn pass_on@(E)(Result@(i32, E) r) i32 | E:
    return Result.Ok(r??)   # legal: the same E

fn main() i32:
    return 0
```

---

## 8. Ownership and the per-instance remainder (R5, R7)

An opaque `T` may own a resource, so it always MOVES (R5). `owns_resource` answers True
for an opaque `T`, and the ownership rules of the template follow from it: a `T` given
away is spent, and a `T` that the body uses two times must be borrowed or cloned
(`T: Clone`). The rule does not depend on the type argument, so it is judged once.

Some facts exist only for a concrete type. They stay per instance (R7):

| Per instance | Why |
|---|---|
| the consume-or-copy of an owning value | a plain value copies, an owning value moves; the instruction differs |
| the drop set and the order of `drop()` | a `File` has a `drop()`, an `i32` has none |
| the derived method of the concrete type | `hash`, `clone`, `Eq`, `Ord` and `Display` of `Point` are the methods of `Point` |
| the layout | each instance has its own LLVM type |
| E3 | section 7 |

Every other rule runs once, on the template.

---

## 9. One fault, one diagnostic

- **A fault of the template** is reported one time, at the template, with a note at the
  declaration of the type parameter. Every copy of a refused template is MUTED: the
  reporter drops each diagnostic of a body whose template is in the refused set
  (`SymbolTables.refused_templates`, `Reporter.enter_body`). This holds for the typecheck
  pass, the lift pass and the borrow pass, and for the copies of an extension, a perk
  implementation and a lifted lambda. A copy's message names a concrete type (`expected
  i32, got string`), so the identity collapse of repeats cannot see that it is the same
  fault.
- **A type argument that does not satisfy a constraint** is [CE4006](../error-catalog.md#ce4006) (or [CE2090](../error-catalog.md#ce2090) for a
  pack) at the call. The body of the template reports nothing for it.
- **A clean template** keeps the per-instance remainder of its copies (section 8).

The mute can hide a per-instance fault of a copy until the template is fixed. That costs
the user one more build, and no fault is lost.

---

## 10. The mechanism

### 10.1 The opaque parameter

The opaque parameter is the existing `TypeParameter` (`semantics/generics/types.py`) with
an `owner` (a `TemplateId`: the unit and the name of the template) and its
`constraints`. No new kind enters the `Type` union, so every totality gate stays total.
Identity is the name and the owner: the `T` of `f` and the `T` of `g` are two types. An
element of a pack adds an element number to the identity.

- The one builder is `opaque_type_params` (and `opaque_pack_element` for a pack element),
  in `passes/collect/functions.py`. The gate `tests/unit/test_opaque_parameter_is_built_once.py`
  refuses a second one.
- An interned name holds the owner (`List<T#main.f>`). `display_type` renders `T`. The
  spelling gate refuses a rendered `T#main.f`, as it refuses `List<i32>`.
- Each type predicate answers for the opaque parameter. A constraint is a perk
  implementation that the table answers: `PerkImplementationTable.implements_type(T, P)`
  is `T.promises(P)`, and `get_method(T, m)` builds the method from the contract, with
  `T` in the place of `Self`. The hash walk, the contract walk, the constraint check, the
  `HashMap` key rule, the method ladder and the `foreach` protocol read it with no new
  path.

### 10.2 The check copy

The typecheck pass checks each template on a CHECK COPY, then discards it
(`passes/types/templates.py`: `check_function_template`, `check_extension_template`,
`check_perk_template`):

1. Find the record of the template. Stop for a refused declaration, or a constraint that
   names no perk.
2. Refuse a method that two constraints give a second home ([CE4015](../error-catalog.md#ce4015)).
3. Open an OVERLAY of the program tables (`semantics/template_scope.py`). A read goes
   through to the program table, and a write stays in the overlay. A scratch
   monomorphizer builds `List@(T)`, `Maybe@(T)`, a tuple or a user generic over `T` in
   the overlay only.
4. Deep-copy the template, then cut it with each type parameter replaced by its opaque
   form. The deep copy comes first, because the cut shares the nodes it does not visit,
   and a stamp on the template would reach every later copy.
5. Check the copy with a `TemplateValidator`. Its flag `in_template_check` stops every
   writer that cuts or queues a copy (an array extension, a method-generic extension, a
   static, a late function instance); each one answers the substituted signature only.
   The gate `tests/unit/test_template_check_writes_no_copy.py` holds the list.
6. Run `lift` on the check copy, into a scratch program.
7. If the error count grew, add the template to the refused set (section 9).

Nothing holds the check copy, the overlay or the scratch objects after step 7.

### 10.3 The backstop

At the end of the analysis, one scan reads every struct and enum name in the program
tables. A name that holds an opaque parameter is [CE0148](../error-catalog.md#ce0148), an internal error: a writer
that the flag missed put a check-copy instance in a program table, and the backend has no
layout for it.

---

## 11. Diagnostics

### 11.1 New codes

| Code | Module | Fault |
|---|---|---|
| [CE4017](../error-catalog.md#ce4017) | `perk.py` | `extend X with Clone`: the compiler decides `Clone` |
| [CE4018](../error-catalog.md#ce4018) | `perk.py` | `.clone()` of a type parameter with no `Clone`, or of a type that holds one |
| [CE4019](../error-catalog.md#ce4019) | `perk.py` | `Drop` with a bound in its target |
| [CE2124](../error-catalog.md#ce2124) | `types.py` | a target bound on a name that is a type |
| [CE6110](../error-catalog.md#ce6110) | `syntax.py` | a bound outside the top level of an `extend` target |
| [CE0148](../error-catalog.md#ce0148) | `internal.py` | an opaque parameter reached a program table (internal) |

### 11.2 Reused codes

| Code | In a template |
|---|---|
| [CE2008](../error-catalog.md#ce2008) | a method that no constraint promises; the help names the perks that declare it |
| [CE2514](../error-catalog.md#ce2514) | `==`/`!=` with no `Eq`, `<` with no `Ord` |
| [CE2518](../error-catalog.md#ce2518) | arithmetic on a type parameter |
| [CE2035](../error-catalog.md#ce2035), [CE2115](../error-catalog.md#ce2115) | a hole, `print` or `println` with no `Display` |
| [CE2100](../error-catalog.md#ce2100) | `contains`, `index_of` with no `Eq` |
| [CE2054](../error-catalog.md#ce2054), [CE2055](../error-catalog.md#ce2055) | a `HashMap` key with no `Hashable`, no `Eq` |
| [CE2106](../error-catalog.md#ce2106) | a field of a type parameter |
| [CE2014](../error-catalog.md#ce2014) | a cast of or to a type parameter |
| [CE2003](../error-catalog.md#ce2003), [CE2006](../error-catalog.md#ce2006), [CE2009](../error-catalog.md#ce2009) | a type parameter where a concrete type is expected |
| [CE4006](../error-catalog.md#ce4006) | entailment (section 4), an added target bound at the call (5.3) |
| [CE4015](../error-catalog.md#ce4015) | two constraints give one method two homes |
| [CE2511](../error-catalog.md#ce2511) | `??` from an opaque `E` into another error type |
| [CE2090](../error-catalog.md#ce2090), [CE2513](../error-catalog.md#ce2513) | a pack element that does not promise the perk of a callee; two binders compared |
| [CE0107](../error-catalog.md#ce0107), [CE0119](../error-catalog.md#ce0119), [CE0144](../error-catalog.md#ce0144) | a pack template (section 6) |
| [CW1001](../error-catalog.md#cw1001), [CW1002](../error-catalog.md#cw1002) | the statement lints on the template, the `expand` binder |

Each reused code carries a note at the declaration of the type parameter (or at the
binder of an `expand`), and a help that names the constraint to add where one exists.

---

## 12. Limits

- **No generic numbers.** Arithmetic, a numeric literal as a `T` and a zero or one of `T`
  have no constraint (R2). Write the function for a numeric type.
- **No perk bundles** (R3). `@(T: Eq + Ord + Display)` is written in full.
- **No static on `T`.** A perk has no static method, so no constraint promises
  `T.make()`.
- **A user cannot implement `Clone`** ([CE4017](../error-catalog.md#ce4017)). Allowing it later breaks no program.
- **A consumed library is not checked again.** A library that an older compiler built,
  and that `--ignore-compiler-version` lets in, keeps the per-instance behaviour at the
  consumer (2.3).
- **A pack on a struct or an enum** (`struct S@(...Ts)`) is outside this design.

## 13. Before and after

| Program | Before | After |
|---|---|---|
| `fn size@(T)(T x) i32: return x.len()`, called with a `string` | compiles | [CE2008](../error-catalog.md#ce2008) at the template |
| the same, called with an `i32` | [CE2008](../error-catalog.md#ce2008) in the copy for `i32` | [CE2008](../error-catalog.md#ce2008) at the template, one time |
| `fn h@(T)(T v) u64: return v.hash()` | compiles | [CE2008](../error-catalog.md#ce2008); write `@(T: Hashable)` |
| `fn d@(T)(T v) T: return v.clone()` | compiles; [CE2431](../error-catalog.md#ce2431) in the copy for a `File` | [CE4018](../error-catalog.md#ce4018); write `@(T: Clone)` |
| `extend Box@(T) show() string: return "{self.value}"` | compiles | [CE2035](../error-catalog.md#ce2035); write `extend Box@(T: Display)` |
| a template that nothing calls, with a fault | compiles | the fault, at the template |
| a `--lib` build of such a template | compiles | the fault, at the author's build |
