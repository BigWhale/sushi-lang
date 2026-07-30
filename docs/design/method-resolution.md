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
   `hash`/`clone` pair, which Pass 1.8 registers *from semantics*. Everything else in that
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
- **the compiler-derived pair** -- `hash()` and `clone()`, auto-derived in Pass 1.8 for every
  struct and enum

`tests/unit/test_builtin_method_seam.py` pins that list against `validate_method_call`'s in
both directions. Two places answering one question drift; that is the #248 lesson (*if the
same question is asked in six places, the fix is a seam, not a fallback*).

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

- **after Pass 1.8**, which registers the struct/enum `hash`/`clone`;
- **after the generic-extension merge loop**, because a monomorphized `extend Box@(i32) hash()`
  only enters the extension table there. Running earlier is exactly why that shape went
  uncovered in the first cut.

It keys on whether a built-in genuinely exists for that pair, never on the bare method name --
so a type that carries no such built-in (a struct the compiler could not derive `hash` for, say)
can still be extended with a `hash()` of its own.

## Related

- `docs/design/closures.md` -- the same rule stated for `List@(T)` extension methods
- `docs/language-guide.md` -- the Extension Methods section, user-facing
- `docs/design/type-identity.md`, `docs/design/move-semantics.md` -- sibling decision records
