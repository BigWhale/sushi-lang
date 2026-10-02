# Design: Tuples

**Status:** phase 1 is implemented: the tuple type in every type position, the tuple
literal, element access (`.N`, `.N.M`), the element write and the element take, the `let`
destructure, the derived contracts, generic inference through a tuple, the `| E` return,
the `.slib` round trip and the `--lib-info` report. Phase 2 (the `foreach` destructure and
the destructuring rebind) and phase 3 (tuple patterns in a `match`) are not implemented yet.
The grammar of all three phases is in place; the builder refuses the shapes of phases 2 and 3
with **CE6108** until they are implemented.

## 1. Why

A function returns exactly one value. Before tuples, a function that returns two values
needed a named struct for the pair. A field read is a borrow, so the two halves of that
struct could not both come out owned: consuming `s.a` is **CE2411**, and `nom s.a` spends
the whole struct (borrow-model.md S10c). A tuple is an anonymous, fixed-size,
heterogeneous product type that a function can return, a value can store, and a `let` can
DESTRUCTURE into owned parts.

A tuple is not a record. A value with a meaning (a file status, a size report) is a struct,
because a struct names its fields and a tuple does not.

## 2. The surface

```sushi
use <math>

fn divmod(i32 a, i32 b) (i32, i32) | MathError:
    if (b == 0):
        return Result.Err(MathError.DivisionByZero)
    return Result.Ok((a / b, a % b))

fn main() i32:
    let (i32, string) t = (42, "Arthur")       # a type and a literal
    t.0 := 43                                  # an element write
    let (i32 q, i32 r) = divmod(7, 2).realise((0, 0))
    let (q2, _) = divmod(9, 4).realise((0, 0)) # a bare binder infers; `_` discards
    println("{t}")                             # (43, "Arthur")
    return q + r + q2 - 6
```

| Position | Example | Phase |
|---|---|---|
| Return type | `fn f() (i32, string)`, `fn f() (i32, string) \| E` | 1 |
| Parameter | `fn f((i32, i32) p)`, `fn g(nom (string, string) p)` | 1 |
| Field and payload | `(i32, i32) p` in a struct, `Pt((i32, i32))` in an enum | 1 |
| Generic argument | `List@((string, i32))`, `HashMap@((i32, i32), string)` | 1 |
| Array element | `(i32, i32)[]`, `(i32, i32)[4]` | 1 |
| Function type, lambda | `fn((i32, i32)) -> (i32, i32)`, `\|(i32, i32) p\| (p.1, p.0)` | 1 |
| Element read, write, take | `t.0`, `t.0.1`, `t.0 := v`, `nom t.0` | 1 |
| `let` destructure | `let (i32 a, string b) = f()`, `let (a, _) = f()`, `let ((a, b), c) = g()` | 1 |
| `foreach` destructure | `foreach((k, v) in pairs.iter()):` | 2 |
| Destructuring rebind | `(a, b) := (b, a)` | 2 |
| `match` on a tuple | `match (x, y):` / `(Maybe.Some(a), _) ->` | 3 |

What is not in the surface, by design:

- **No tuple constant and no tuple unit variable** (ruling 11). A `var` takes a constant
  initializer, so the second rule follows from the first. A tuple literal is not a
  compile-time constant expression (**CE0108**). This is a design decision, not a known
  limitation.
- **No named elements** (ruling 9). `(i32 quot, i32 rem)` in a type position is **CE6105**.
- **No one-element tuple and no empty tuple.** `(x)` is grouping, `(x,)` is a parse error,
  and `~` is already the unit type and the unit value.
- **No index by a value** (ruling 10). The element type depends on the index, so the index
  is a literal, `.0`, and it starts at 0. `t[i]` is not a tuple access.
- **No written `Tuple@(i32, string)`.** The one spelling is `(i32, string)`.
- **No tuple in an FFI signature.** A tuple has no C layout (**CE5003**).
- **No tuple bloom and no `expand` over a tuple** (ruling 15): `f(t...)` is **CE2006**,
  `expand(x in t)` is **CE0119**. A value pack is not a tuple (ruling 14).
- **No mode on a destructure element** (decision D1): `let (peek i32 a, b) = t` is
  **CE6107**.
- **No extension and no perk implementation on a tuple type** (ruling 5): **CE2110**, the
  rule of a function type.

## 3. The grammar

The parser is Lark LALR with the basic lexer. The change adds no LALR conflict, and every
file of the corpus parses to the tree it had before.

- **One list rule for a type and a destructure.** `let (A, B) t = e` and `let (a, b) = e`
  are the same tokens up to the `)`, and LALR(1) cannot tell a type from a binder at
  `let ( NAME ,`. So `tuple_list` takes `type NAME?` or `_` for each element, and the token
  after the `)` decides: `=` (or `in`) is a destructure, a NAME is a typed binding. The AST
  builder judges each element by its position (`ast_builder/types/tuples.py`): a type
  position takes types alone (**CE6105**), a destructure position takes a bare name, a typed
  name, a `_` or a nested destructure.
- **`t.0.1` lexes as `t` `.` FLOAT(`0.1`).** The FLOAT terminal takes `0.1` before INT can.
  `member_access` takes `"." INT` (`tuple_index`) and `"." FLOAT` (`tuple_index_pair`), and
  the builder splits a FLOAT into two steps. A FLOAT after a dot is valid nowhere else, so
  this adds no conflict. A number with an underscore, an exponent or a leading zero is
  **CE6106**.
- **`(expr)` and `(expr, expr)`.** The comma decides, with a shift. `(fn(i32) -> i32)[]` is
  still grouping.
- **A statement with its own parentheses.** `println(1, 2)` stays a parse error; write
  `println((1, 2))`. `if ((a, b)):` is refused by the bool rule.
- **`| E` after a tuple return type** is the function's channel. In a function type,
  `fn(A) -> (B, C) | E` keeps the rule that a `| E` after a function type is that type's.

The grammar of phases 2 and 3 is in place: `foreach_destructure`, `tuple_pattern` (and a
literal inside a pattern item), and a tuple literal as a rebind target. The builder refuses
each with **CE6108** until its phase gives it a meaning.

## 4. Representation (rulings 1 and 2)

**A tuple is an interned `StructType`.** `(i32, string)` is the struct `$Tuple<i32, string>`
with `generic_base = "$Tuple"`, `generic_args = (i32, string)` and the fields `0` and `1`.

- The base name is one that no user can write. `NAME` is `CNAME`, so a `$` cannot start a
  written name. A user `struct Tuple` and a user `struct _Tuple@(A, B)` are other types.
- The interned name is built through `interned_name`, never by hand.
- **One interner**, `intern_tuple` (`semantics/generics/tuples.py`), builds the instance, as
  `intern_wrapper_enum` builds a `Result`. It resolves each element RECURSIVELY, so one
  interned name has one resolution depth, and it interns a nested tuple first. It derives
  `hash()` and `clone()` at the intern. An abstract tuple (an element still names a type
  parameter) and a tuple whose element names an instance that is not built yet are handed
  back and kept out of the table.
- A tuple has a variable arity and no declaration, so it never goes through
  `monomorphize_struct`, whose arity check would refuse it. A written tuple type reaches
  the builder as `GenericTypeRef("$Tuple", ...)`, and each producer routes that base to the
  interner: the monomorphize pass (`TypeMonomorphizer.intern_tuple`), the substitution of a
  template (`TypeSubstitutor`), the typecheck pass (`intern_declared_wrapper`), and the late
  intern seam. A tuple literal is interned by the typecheck pass from its element types.
- A tuple has no declaration record, so it is public by absence. The leak fence and the
  `ptr` quarantine read its elements, so `public fn f() (Secret, i32)` is **CE3009** for a
  private `Secret`.

Why a struct: identity stays nominal (two equal element lists give one interned name), and
almost every type-kind dispatch already answers a struct: the type walk, finite types,
`Eq`, `Ord`, `Display`, hash, `clone`, the lifecycle pair, `owns_resource`, the LLVM type,
the field read, the field write and the field take.

**Display.** A user reads `(i32, string)` and never the interned name. `display_type`
renders a tuple from its elements, `display_type_name` rewrites every `$Tuple<...>` in a
bare name (innermost first), and the `slib-info` tool and the Python `--lib-info` report
apply the same rule. A value prints as `(1, "a")`, with a held string quoted, as a struct
prints its fields.

**Symbols.** `$` is the unit separator in a symbol (`semantics/unit_symbols.py`). The
type-argument sanitizer (`name_mangling.py`) and the lifecycle symbol (`lifecycle.py`)
change the `$` of `$Tuple` to the digit `0`. No written type name starts with a digit, so
the symbol of a tuple is never the symbol of a user type: `show@(T)` on `(i32, string)` is
`show__0Tuple_i32_string`, and on a user `_Tuple@(i32, string)` it is
`show___Tuple_i32_string`.

## 5. Ownership

A tuple is a composite. It owns a resource if and only if an element owns one
(`owns_resource`, structural). A plain tuple copies. An owning tuple moves. `.clone()` is
total and deep, and it is refused when an element declares a resource (the escape is
`.share()` on that element).

| Operation | Rule | The same rule as |
|---|---|---|
| `(a, b)` literal | each element is a consuming position (by position) | a struct construction |
| `t.0` read | a borrow; consuming it is **CE2411** | a struct field read |
| `t.0 := v` | consumes `v`, destroys the old element | a struct field write |
| `nom t.0` | a field take: spends all of `t`, destroys the other owning elements | borrow-model.md S10c |
| `let (a, b) = <temporary>` | each binder owns its element | a `nom` binding of a temporary |
| `let (a, b) = t` (an owned local) | spends `t` whole (a later use is **CE2405**), then splits it | `let x = t` |
| `let (a, b) = p` (a parameter, a field, a binding) | each binder is a borrow; consuming one is **CE2411**, and a change of the owner while a binder lives is **CE2412** | `let x = s.f` |
| `let (_, b) = <temporary>` | the `_` element is destroyed at the destructure | a discarded `_` in a whole-variant take |
| scope exit | the elements are destroyed in field order, first to last | the fields of a struct |

**How the destructure is built.** A `let` destructure is a `Let` with a `targets` list
(`DestructureTarget`). The `Let` binds the whole value under a hidden name that the grammar
refuses (`#tuple0`, `#tuple1`, ...), so every pass that already reads a `let` reads it unchanged: the
whole takes the provenance of its value by the rule of `let x = v`. Then the targets split
it:

- The scope pass declares each binder. The typecheck pass types each binder (a typed binder
  takes the type mismatch of a `let`, **CE2002**; a bare binder takes the element type) and
  hands a typed binder's type to the element of a tuple literal first, so
  `let (u8 a, i32 b) = (7, 1)` types the literal `7` as a `u8`.
- The borrow pass (`_split_destructure`) gives each binder the class of the whole: an owned
  whole gives owned binders, a borrowed whole gives `let`-borrows of the whole's owner.
- The backend (`_emit_destructure`) loads each element into its binder's slot. An owned
  whole registers each binder for cleanup, destroys each `_` element at once, and is then
  given up with `relinquish_temp`, so nothing is freed twice. A borrowed whole gives views
  that free nothing.

**Ruling 4.** A bare binder in a `let` destructure OWNS its element (from an owned value).
A bare pattern binding in a `match` BORROWS (S10b). Both rules stay; borrow-model.md states
them in one place (S10e).

**Ruling 3** (phase 3). `match (a, b):` matches in place: a tuple-literal scrutinee builds
no tuple.

## 6. Derived contracts

Free under the struct representation, because the derive pass treats a tuple as a struct:
`==` and `!=` compare element by element; `<` and the other orders are LEXICOGRAPHIC by
element; `hash()` reads the elements in order; `Display` prints `(1, "a")`. A tuple is a
`HashMap` key when every element has a hash and an `Eq`. Two tuples of different element
types are a mixed comparison (**CE2513**), as two structs are.

## 7. Generics

- `fn swap@(T, U)(nom (T, U) p) (U, T)` infers `T` and `U` through the tuple: the argument
  is the struct `$Tuple<...>` and the parameter is `GenericTypeRef("$Tuple", (T, U))`, and
  the leading type-argument solver unifies through `generic_args`.
- A written `(T, U)` in a template substitutes in every position: a signature, a struct
  field, a `let` type, a destructure binder type.
- A constraint on a tuple argument (`@(K: Hashable)` with `K = (i32, string)`) reads the
  derived contract.

## 8. Refusals

| Fault | Code |
|---|---|
| A name or a `_` inside a tuple TYPE; a named element | CE6105 |
| `t.1e3`, `t.0_1`, `t.01` | CE6106 |
| A mode on a tuple element: a destructure element (D1) or a tuple type element | CE6107 |
| A `foreach` destructure, a tuple pattern, a literal in a pattern, a tuple-literal rebind target (temporary) | CE6108 |
| `t.N` past the last element | CE2106, with the type rendered as `(i32, string)` |
| A destructure count that is not the tuple's count | CE2116 |
| A destructure of a value that is not a tuple | CE2117 |
| A destructure of an unhandled `Result` | CE2505 |
| A typed binder whose type is not the element type | CE2002 |
| A `const` or a `var` of a tuple (ruling 11) | CE0108 |
| A tuple in an FFI signature | CE5003 |
| An extension or a perk implementation on a tuple type (ruling 5) | CE2110 |
| `f(t...)` (ruling 15) | CE2006 |
| `expand(x in t)` (ruling 15) | CE0119 |
| `x as (i32, i32)` | CE2014 |

## 9. Rulings

| # | Question | Ruling |
|---|---|---|
| 1 | Representation | An interned `StructType` |
| 2 | The internal name | A name that no user can write: `$Tuple` |
| 3 | `match (a, b):` | Match in place; a tuple-literal scrutinee builds no temporary |
| 4 | Bare binders | A bare binder owns in a `let` destructure and borrows in a `match` pattern |
| 5 | Extensions on a tuple type | Refused |
| 6 | Phase 3 scope | The full pattern matrix |
| 7 | The swap `(a, b) := (b, a)` | In phase 2 |
| 8 | Stdlib adoption | Later, after the language phases |
| 9 | Named elements | None. A record with names is a struct |
| 10 | Element access | `.N`, and the index starts at 0. `t[i]` is not a tuple access |
| 11 | Tuple constants | None, and no tuple `var`. A design decision, not a limitation |
| 12 | Packs as tuple values | Closed by rulings 14 and 15 |
| 13 | The evaluation order of a rebind | Evaluate the whole right side first, then assign from left to right |
| 14 | Is a value pack a tuple? | No. A pack stays a pack. No `(args...)` and no `(Ts...)` |
| 15 | Tuple bloom and `expand` over a tuple | Neither |
| 16 | The destructuring rebind | Any tuple value on the right, any `:=` place as a target, one name twice refused |
| 17 | Exhaustiveness | One pattern-matrix checker for every match, not only for tuples |
| 18 | An arm that no value can reach | An error, with a note at the covering arms |

Decisions taken for the execution: **D1**, no mode on a destructure element (a later change
can add one); **D2**, the whole grammar lands in phase 1, and the builder refuses the shapes
of the later phases until their phase.

## 10. Notes for the later phases

- Phase 2: a `foreach` destructure desugars in the AST builder to a `let` destructure at
  the top of the body, as the `??` binder does, so the ownership of each binder follows from
  the item. Remove the CE6108 refusals of `parse_foreach_destructure` and of the rebind
  target in `parse_rebind_stmt`, with their fixtures; the count rule of a rebind is CE2116.
- Phase 3: remove the CE6108 refusals in `ast_builder/statements/matching.py` (a tuple
  pattern at the top of an arm, and a tuple pattern or a literal inside a pattern item),
  with their fixtures, and then the code CE6108 itself.
- The destructure targets live on `Let.targets`; a reader that collects the locals of a body
  reads `destructure_binders(stmt.targets)` beside `stmt.name`.
