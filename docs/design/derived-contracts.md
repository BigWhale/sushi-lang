# Derived Contracts: `Eq`, `Ord` and `Display`

**Status: DECIDED.** The compiler predefines three perks and derives them for every struct
and enum. An implementation is the override.

## The three contracts

| Perk | Contract | Read by |
|---|---|---|
| `Eq` | `fn eq(Self other) bool` | `==`, `!=`, `contains`, `index_of`, `HashMap` keys |
| `Ord` | `fn compare(Self other) i32` | `<`, `<=`, `>`, `>=` |
| `Display` | `fn to_str() string` | an interpolation hole, `print`, `println` |

`compare` answers a negative number, zero or a positive number. The three perks sit beside
`Drop` and `Hashable`. They are public and importless, and a declaration of one of the
names is [CE4001](../error-catalog.md#ce4001).

The model is the derived hash (`docs/design/method-resolution.md`, ruling #696): the
compiler derives the method from what the type holds, and `extend T with <Perk>` replaces
it everywhere.

## Rulings

### 1. The override is full, through a receiver placeholder

A user perk still has no `Self`. The contracts of `Eq`
and `Ord` need one, because `eq` takes a value of the implementing type.

The compiler solves it with a placeholder type, `ReceiverType` (`semantics/typesys.py`). It
stands for the implementing type, and only `register_predefined_perks` builds it. The
implementation matcher (`passes/types/perks.py`) substitutes the target for the placeholder.
So an implementation writes its own type: `extend Point with Eq: fn eq(Point other) bool:`.
A generic target writes its own instantiation: `extend Box@(T) with Eq:` with
`fn eq(Box@(T) other) bool`. A mismatch is [CE4004](../error-catalog.md#ce4004), and its help prints the contract with the
target filled in.

The other options were a user-visible `Self` (a new language feature with no other use) and a
contract with a fixed type (a perk per type). The placeholder adds nothing to the language
surface. `tests/unit/test_receiver_placeholder_is_confined.py` keeps it confined.

An implementation wins in every position: the operator, a field of a derived method, a
`contains` search, a `HashMap` key and `print`. A position that read a separate structural
rule would make the override a half-measure.

### 2. The names are `Eq`, `Ord` and `Display`

The names match the vocabulary of the language: `Hashable` is the hash contract, and `Eq`,
`Ord` and `Display` are the short names that Rust and Haskell use. The methods are `eq`,
`compare` and `to_str`. `to_str` was already the name of the primitive method, so a primitive
and a struct answer one name.

### 3. The text is named fields

A struct prints `Point(x: 1, y: 2)`. A positional form (`Point(1, 2)`) hides what a field
means. A generic struct prints its base name only (`Box(value: 1)`), because the interned
name (`Box<i32>`) is an internal spelling.

An enum prints `Shape.Circle(5)`, `Shape.Rect(3, 4)` and `Colour.Red`. A payload has no
name, so it prints by position. `Maybe` and `Result` print as any enum does.

A string that a type holds prints in quotes: `User(name: "Arthur Dent", age: 42)`. The quotes
show where the string starts and stops. The bytes are written as they are, with no escaping.
A string in a hole of its own prints bare, as before. An array and a `List@(T)` print as
`[1, 2, 3]`, and an empty one as `[]`, as a field and at the top level alike (#1132). An
`Own@(T)` prints its payload. A float prints as `%g`, as a float hole does. A `u8[]` prints
as numbers, `[72, 105]`; `.to_string()` gives the text.

### 4. One equality for operators, search and map keys

The HashMap probe had an equality of its own. It could not see an `Eq` implementation, so
an override would have changed `==` and left the map alone. Now `==`, `contains`,
`index_of`, a field of a derived equality and the map probe all go through one emitter
(`emit_value_eq`). [CE2055](../error-catalog.md#ce2055) reads the same rule.

### 5. Float fields use a total rule

A bare float `==` stays IEEE: `NaN == NaN` is false. A float that a type HOLDS uses a total
rule: `-0.0 == 0.0`, every NaN equals every NaN, and a NaN orders after every number.

The reason is one law: `compare(a, b) == 0` exactly when `a == b`. IEEE equality is not an
equivalence, because `NaN != NaN`. A struct with such a field would not equal itself, and
it could not be a `HashMap` key, because a stored key would never be found again. The
total rule makes a struct with a float a usable sort key and map key. The bare operator
keeps its IEEE meaning because every other language does, and a change there would surprise.

The hash follows the equality. `0.0` and `-0.0` hash alike, and every NaN hashes alike. That
changes `f64.hash()` and `f32.hash()` for a NaN.

### 6. A bool has no order at the top level, and orders as a field

A bare `a < b` on two bools is almost always a typo for `!=` or a missing `and`, so [CE2514](../error-catalog.md#ce2514)
stays. Inside a derived order the same refusal would make a struct with a bool field
unorderable for no good reason, so a bool field orders `false` before `true`. The same split
holds for the constraint: `bool` satisfies `Eq` and `Display`, and does not satisfy `Ord`.

### 7. An enum orders by variant, then payload

The variant order is the declaration order. So `Priority.Low < Priority.High`, and
`Maybe.Some < Maybe.None`, and `Result.Ok < Result.Err`. Two values of one variant compare
their payloads from left to right. A struct compares its fields in declaration order, and the
first difference decides.

### 8. Two rules: the held rule and the top-level rule

The semantic walk (`semantics/generics/contracts.py`) answers two questions.

- **`contract_of` is the held rule.** May a derived method read a value of this type as a
  field, a payload or an element? An array, a `List@(T)` and an `Own@(T)` answer from what
  they hold: element by element, with the length breaking a tie (a prefix is less), and an
  `Own@(T)` by its payload. A nested array is an array of arrays and takes the same walk
  at each level, so a struct field `i32[2][3]` or `i32[][]` compares, orders and prints
  row by row. A `HashMap@(K, V)`, a `ptr`, a function value, a closure and an
  iterator have no contract, and neither has a type that holds one. A note names the field:
  `no derived Eq: field 'f' -> a function value`.
- **`operand_contract` is the top-level rule**, for an operator, a method call and a
  constraint. A primitive keeps its closed set. A struct or an enum asks the held rule. An
  array, a `List@(T)` and an `Own@(T)` are refused at the top level: they compare, take
  `.to_str()` and meet a constraint only when a type holds them.
- **`printed_contract` is the top-level rule of a hole and `print` / `println`.** It is
  `operand_contract`, with one difference: an array, a `List@(T)` and an `Own@(T)` ask the
  held rule (#1132). So `println(xs)` prints `[1, 2, 3]`, the form a struct that holds `xs`
  prints, and an element with no string form is refused with the note that names it
  (`no derived Display: element -> a function value`). A `HashMap@(K, V)` stays refused,
  with the held rule's reason: its iteration order is not specified, so its printed form
  could change from one run to the next.

  The reason for the difference: Sushi has one string channel. There is no second
  channel, such as `{:?}` in Rust, to send a programmer to. The form of a held array
  exists, so a refusal at the top level only forced a wrapper struct or a hand-written
  `join`. The operators, the method call and the constraint do not change.

`Maybe` and `Result` take `==` and `<`. A bare variant on one side takes its type from the
other side, so `m == Maybe.None` is legal. They are not PRINTED at the top level. A
`Maybe` in a hole would hide a missing value, and a `Result` would hide an error. The program
has to handle them: `match`, `.realise(default)` or `??`. A type that holds one prints it.

The diagnostics follow. A top-level `Maybe`, or an array whose element has no string form,
in `print` or `println` is [CE2115](../error-catalog.md#ce2115). A top-level `Result` stays [CE2037](../error-catalog.md#ce2037). In a hole, every one of
them is [CE2035](../error-catalog.md#ce2035), and the error has a note that names the field or the element when it is the
cause. [CE2115](../error-catalog.md#ce2115) closes a gap: the typecheck pass
asked nothing but [CE2037](../error-catalog.md#ce2037), and such a value reached the backend and became the internal error
[CE0017](../error-catalog.md#ce0017).

### 9. Out-of-line functions, and recursion

An inline walk would grow with the depth of the type, and it cannot be recursive. The backend
emits one `linkonce_odr` function per composite type per contract: `__sushi_eq_<k>`,
`__sushi_cmp_<k>` and `__sushi_fmt_<k>`. The function is cached BEFORE its body is emitted,
so a nested value of the same type is a call of the function that is being built. So a
recursive type, an enum that reaches itself through `Own@(T)` or `List@(T)`, compares and
prints. The hash still refuses a recursive type.

`linkonce_odr` lets every unit emit its own copy and the linker keeps one.

The string form writes piece by piece into one growing buffer
(`backend/runtime/string_builder.py`). Appending keeps the time linear, where joining the
pieces would copy the whole prefix at every step.

### 10. Constants are out of scope

A constant initializer cannot compare a struct or an enum ([CE0110](../error-catalog.md#ce0110)) or interpolate one ([CE0108](../error-catalog.md#ce0108)).
The evaluator (`semantics/const_eval.py`) folds primitives, and the derived contracts are
run-time functions. A second copy of the derived format in Python would drift from the
backend, and the evaluator cannot run an implementation. A struct or enum constant is still
compared and printed at run time, in a function body.

### 11. One home for a method name: [CE4015](../error-catalog.md#ce4015)

Two perks that give one type the same method name leave a call of the name naming neither
one, and the two bodies take one symbol. That is [CE4015](../error-catalog.md#ce4015), relational, with a note at the
first perk. The rule existed for a perk against an extension method ([CE4007](../error-catalog.md#ce4007)), and this is
the perk against perk half.

A DERIVED method is not a home. A type may implement a user perk that provides `compare`
and still get the derived `Ord`. An explicit `a.compare(b)` reads the implementation,
because the `contract` method family yields to a perk (`beats_perk=False`). `a < b` still
reads `Ord`, because the operator asks the contract and not the method name.

### 12. The binary `.slib` ships the implementations

A binary library now ships its implementations of `Hashable`, `Eq`, `Ord` and `Display`. A
consumer's derived method of a type that holds a library type reads the implementation. A
consumer that only saw the derived rule would compare a library type by its fields and
ignore the override.

## Where it lives

| Piece | File |
|---|---|
| The held rule and the top-level rules | `semantics/generics/contracts.py` (`contract_of`, `operand_contract`, `printed_contract`) |
| The walk mechanics, shared with the hash | `semantics/generics/contract_walk.py` (`hashing.py` also uses it) |
| The receiver placeholder | `ReceiverType`, `semantics/typesys.py`; built only by `register_predefined_perks` |
| The implementation matcher | `passes/types/perks.py` |
| The method family `contract` | `passes/types/method_registry.py` (`beats_perk=False`) |
| The method emitter | `try_emit_contract_method`, `backend/expressions/calls/intrinsics.py` |
| Equality and order emitters | `emit_value_eq`, `emit_value_compare`, `backend/types/contracts.py` |
| String form emitters | `emit_value_to_str`, `emit_value_fmt`, `backend/types/display.py` |
| The string buffer | `backend/runtime/string_builder.py` |

The typecheck pass stamps the answer on the AST: `BinaryOp.operand_type`,
`InterpolatedString.display_types` and `Print`/`PrintLn.display_type`. The backend reads the
stamp and never guesses a type from a layout.

The derive pass does not register `Eq`, `Ord` or `Display`. The contract walk answers each
call. A name in the contract family is `eq`, `compare` or `to_str`, and the family covers
every struct and enum. `eq` and `compare` also cover every primitive, `bool` included, and
`to_str` was already a primitive method.

## Gates

| Gate | What it holds |
|---|---|
| `tests/unit/test_contract_dispatch_is_total.py` | `contract_of` answers every type kind, for each of the three contracts |
| `tests/unit/test_held_value_contracts_are_one.py` | one equality, one order and one string form for a held value; the retired HashMap structural equality does not come back |
| `tests/unit/test_predefined_perks.py` | which perks the compiler declares, what their contracts say, and the override predicate for each derived contract |
| `tests/unit/test_receiver_placeholder_is_confined.py` | `ReceiverType` has one builder and two readers |

The fixtures are under `tests/operators/eq_derived/`, `tests/operators/ord_derived/`,
`tests/strings/interpolation_display/`, `tests/perks/predefined_contracts/` and
`tests/basic/print_output/`.
