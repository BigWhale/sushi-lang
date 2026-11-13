# Language rules that are easy to get wrong

This file holds the language rules that an agent gets wrong most often. Where a design
document is normative, this file names the document and keeps only the TRAPS: the rule that
surprises, the code that a fault gives, and the escape. The document wins when the two
disagree; read it before you change a rule. Read this file before you write or review Sushi
source, a fixture, a diagnostic, or a compiler change that touches one of these rules. The
seam in the code that holds each rule is in `docs/agents/one-seam-rules.md`; the gaps are in
`docs/agents/known-limitations.md`.

## Generic syntax `@(...)`, and the internal `<...>` form (no design doc)
- Source syntax is `@(...)` everywhere: `List@(i32)`, `struct Pair@(T, U):`,
  `fn id@(T)(T x) T:`, `@(T: Hashable)`, `@(...Ts: Display)`, call-site `identity@(i32)(5)`.
  Explicit call-site type arguments are all-or-nothing, and legal only on a direct call to a
  named free function.
- **Angle brackets stay INTERNALLY** and you must not "fix" them: interned type-identity
  names (`"List<i32>"`), mangled symbols, and the `.name.startswith("List<")` match sites.
  `display_type()` (`semantics/generics/type_display.py`) renders a type back to `@(...)`;
  never interpolate a `Type` directly into user-visible text.
- **A gate holds this** (#734): `SUSHI_SPELLING_GATE` refuses the regex `[A-Za-z0-9_]<` in a
  rendered message, note or help. Both runners arm it; it is OFF for a user (a cosmetic
  fault is not worth a crash). It also refuses the retired `&peek` / `&poke` borrow (#759); a
  new pattern goes in the same table.
- The gate reads RENDERED text, so it refuses only what some input renders. The help of the
  function-value type mismatch is invisible to the fixture corpus, so a per-probe test sits
  beside the gate.
- A table KEY is exempt and stays interned: `--lib-info` splits it on `"<"`, and a rendered
  key would key an array target on a name that the receiver does not resolve to (#393).
  `use <io/fs>` import paths do not change.

## Type identity is nominal ([`docs/design/type-identity.md`](../design/type-identity.md))
- A `StructType`/`EnumType` compares and hashes on its NAME alone. Never rebuild a named
  type: look it up in the struct/enum table. `resolve_type_recursively` treats a named type
  as terminal.
- Never build a `Result`/`Maybe` structurally: intern it through `intern_wrapper_enum` (the
  one-seam row "Interning a `Result@(T, E)`"). A structural build poisons the enum table, an
  internal error.
- The seam resolves a NESTED name too (an `IpAddr[]` element, a `fn(i32) -> i32` error arm):
  the interned name is the same either way, and two resolution depths under one name collide.

## The error channel, Result@(T, E) and Maybe@(T) ([`error-channel.md`](../design/error-channel.md), [`error-conversion.md`](../design/error-conversion.md))
- The channel is OPT-IN: `fn f() T | E` gives `Result@(T, E)`; `fn f() T` is BARE and the call
  yields `T`. There is NO default error type, so `Result@(T)` is a wrong type-argument count.
  A lambda takes its channel from its annotation or its expected type, never from its body.
  `main` is bare, so a channel on it and a `??` in it are refused.
- A channel function that returns a function type spells `Result@(fn(A) -> B, E)`: a `| E`
  after a function type is that type's. An explicit `fn f() Result@(T, E)` is not wrapped
  again; a mix of the two forms is an error.
- A `| E` body SPELLS its success: `return Result.Ok(...)`, and `return Result.Ok(~)` for a
  `~` body; a bare `return v` is refused (#848). A bare body refuses both constructors and a
  `??`. A body that can reach its end with no `return` is an error, except a BARE `~` body
  (#824, #845). A statement after one that always ends the path is an error, once per block,
  with a note at the ender (#854).
- `??` takes a `Result` ONLY (CE2507); a `Maybe` writes `m.or_err(nom e)??`. Two error types
  need a declared conversion `extend A as B:` (CE2511 when there is none; a chain is never
  followed). The `E` of every Result is an error type (CE2084): an `error` enum or one of the
  seven predefined error types.
- Conversion faults: only the unit that declares the TARGET may declare it (CE2519, so none
  into `StdError`), two non-generic error types (CE2520), no identity (CE2521), two of one pair
  (CE0101). `e as B`, `r.map_err(f)` and `??` on a borrow of an owning error are CE2411 (help
  `e.clone() as B`, `r.clone().map_err(f)`); `or_err` reads through a borrowed owning `Maybe`
  only as the operand of `??` (CE2522).
- FFI externals are the one carve-out: they return raw C values, not a Result.
- The `foreach` protocol has two refusals, one per fault: a `??` binder over an item that is
  not a Result, and an iterable that is neither an iterator nor a type with `next()`.
- **A condition is a bool and nothing else**, in all NINE positions: an `if`, a `while`, and
  each operand of `and`/`or`/`xor`/`not`. A `Result` or a `Maybe` has its own error (test it
  with `.is_ok()` / `.is_some()`, or take the value with `??`, `.realise(default)` or `match`);
  only an integer gets the `== 0` escape.

## Ownership and borrows ([`ownership-conventions.md`](../design/ownership-conventions.md), [`borrow-model.md`](../design/borrow-model.md), [`borrowing.md`](../design/borrowing.md))
- A type MOVES iff `owns_resource(T, drops)` (STRUCTURAL, or DECLARED by `Drop`); a string
  bound directly from a literal copies freely. The compiler inserts NO deep copy: `.clone()` is the only one, and it is REFUSED on a type
  that declares a resource or holds one; the escape is `.share()`. `drops` has no default: a
  forgotten argument answers False for every handle, a leaked descriptor with no diagnostic.
- `Drop`: only the unit that DECLARES the type may implement it (a type that no unit declares
  is CE4016), and a channel on `drop()` is an error. A generic target is legal. `drop()` runs
  FIRST, then the owning fields; scope exit destroys in reverse declaration order.
- Parameter modes (`f(s)` borrows, `f(nom s)` moves to the callee, `f(peek s)` / `f(poke s)`
  pass a read-only / an exclusive read-write pointer) are marked at BOTH ends or neither. A
  `nom` mismatch has its own error; a `peek`/`poke` mismatch reads as a type mismatch
  (`expected poke i32, got i32`) with a help to add the marker.
- Every parameter, `self` included, is a borrow unless it says `nom`: a write through it and
  a consuming use of it are errors (escape: `.clone()`, or `poke T` / `poke self` / `nom T`).
  `nom` on an FFI extern parameter is an error; a pass-through generic needs `nom T`.
  Constructors, container inserts, array elements and indexed assignment consume by
  POSITION, unmarked.
- Field reads, indexing and container get-outs are BORROWS; a `let` of one is a let-borrow
  that freezes a SET of owners to its last use (CE2412 names the owner that changed). A
  consuming use of it, a change of the owner while it lives, and a write through it are three
  errors, one per fault. A rebind (`x := v`) RE-INITIALIZES. A name with storage of its own (a
  local, a unit variable, a parameter) is rebindable; a name that VIEWS another value's storage
  (a binding, a `let`-borrow, a `peek` reference) is not, and each has its own error. A bare
  pattern binding cannot be rebound (CE2414); a `nom` binding needs a scrutinee that the
  match owns (a temporary, or `match nom r:`), else CE2432; `Own(nom x)` is refused.
- `nom s.out` is the one field read that is not a borrow (S10c): one step off a local that
  the function owns, it spends the WHOLE receiver, and `drop()` does NOT run. `??` over a
  NAMED owning wrapper spends it (S10d). A `peek T` return with no source is CE2438 (S10h).
- `main`'s `string[] args` is a borrowed argv view; a move of it is an error.
- Representation traps: a string is `{i8* data, i64 size, i8 owned}`; read and write the size
  only through `backend/string_layout.py`, and use the `i64` size as a `mem*` length
  ([`string-representation.md`](../design/string-representation.md)); a `T[]` is its
  descriptor BY VALUE ([`array-representation.md`](../design/array-representation.md)).

## Method resolution ([`docs/design/method-resolution.md`](../design/method-resolution.md))
- A name behind a TYPE's dot is a variant or a STATIC method, never both (#542). A static has
  no receiver: a receiver mode or a `self` in it is one error, and a `static` in a perk
  implementation is refused. `static` is a reserved word; `new` is a legal static name and
  not a legal free-function name.
- A static beside an instance method of one name on one type is the duplicate-function
  error; a static that spells a VARIANT is refused; a type whose dot holds no such member has
  its own error (the fault is the position, not the name).
- A function type is NOT an extension target (#771); the perk path still lets one through
  (#864). A perk method's argument mismatch reads the errors of an extension method's (one
  for a type, one for the arity).
- A built-in always wins (an extension that collides with one is an error); a perk
  implementation is the sanctioned override. `Drop`, `Hashable`, `Eq`, `Ord` and `Display`
  are PREDEFINED and importless, and a user declaration of any is refused. `Hashable` is
  satisfied by every type that `hashability_of` accepts; `extend T with Hashable` is the
  override (#696).
- No specialization: a template and a concrete target of one perk on one base are CE4002,
  in either order. A name has exactly ONE home: a perk method beside an extension method of
  the same name on one type is an error.

## Visibility ([`docs/design/visibility.md`](../design/visibility.md))
- A perk implementation and a conversion carry NO marker (`public` before either is CE6001,
  on an implementation method CE6103). Behind an alias, an implementation writes
  `extend T with ns.Perk`. A name with no declaration record is public by absence; that is
  what makes every synthesized type work.
- The leak fence reads the WRITTEN declarations alone (`ast_walk.is_written`): a
  monomorphized instance at the caller's own private type is no leak (#702). The `ptr`
  quarantine reads the instances too. A `public extend` is leak-fenced like a `public fn`,
  its TARGET type included.
- The perk rules PARTITION on whether the declaring unit may NAME the perk: another unit gets
  the perk-contract error alone; the private-perk leak error is for the perk's own unit, read
  from the declaration's marker or from an extension's TARGET (a generic target from its
  BASE). A builtin target is not fenced (#692).

## Extension visibility ([`docs/design/extension-visibility.md`](../design/extension-visibility.md), epic #1251)
- The import tier of a method never depends on how the compiler emits it (R10). A stdlib
  method on a BUILT-IN type needs NO import (R1); a stdlib import brings NAMES, never methods,
  and an import used only for a method is CW3006 (R2).
- `extend T m(...)` with no marker is private to its unit (CE3005 elsewhere). A `public
  extend` on a type that the unit does not declare is visible where the unit is imported;
  in the program but not imported here is CE3022, and the help names the `use`.
- Collisions: an own extension that hides an imported public one wins with CW3007; two
  imported public ones and no own one is CE3023 at the CALL (no qualified method call: split
  the unit); a name of a method visible everywhere is refused at the declaration (CE2097 for a
  built-in or stdlib method, CE4007 for a perk-implementation method).
- A binary/hybrid manifest records `"public"` per extension (templates schema 11, which adds
  `return_mode`). CE3015 and CW3003 are RETIRED.

## Unit-level storage: `var` ([`docs/design/unit-storage.md`](../design/unit-storage.md))
- A `var` is STORAGE with an address; a `const` is a folded value with none. A `var` is
  **never moved out of** when its type owns a resource.
- The initializer is a constant expression plus an EMPTY container; `HashMap.new()` is
  refused. `var Maybe@(HashMap@(K, V)) cache = Maybe.None` is the fill-on-first-use shape.
- A non-empty `from(...)` and another `var` are refused in the initializer, and a `const`
  cannot name a `var`.
- A `var` is never an array SIZE. The AST node is `VarDef(ConstDef)`; the walk kind is
  `"variable"`.

## Units, imports and namespaces ([`docs/design/unit-namespaces.md`](../design/unit-namespaces.md))
- Scope is per unit and NOT transitive; `use X as ns` puts NOTHING into the flat scope.
  `i32[ns.SIZE]` is the one refusal of the dot (a size is read while the unit is parsed).
- To name a TYPE, import the unit that declares it: the unknown-type error says which import
  is missing.
- Only `public use` re-exports; it takes no `as`. The stdlib error vocabulary rides on it
  (`docs/agents/stdlib.md`).

## Other topics with a design doc
- **FFI** ([`docs/ffi.md`](../ffi.md)): a variadic libc extern MUST be declared `var_arg`; a
  fixed declaration reads garbage on Apple arm64. `ptr` is opaque and quarantined.
- **Closures** ([`closures.md`](../design/closures.md)): borrow capture is refused (Tier 2).
  Parameter MODES are part of a function type. `(fn(i32) -> i32)[]` is an array of function
  values; `fn(i32) -> i32[]` returns an `i32[]`.
- **Libraries** ([`docs/libraries.md`](../libraries.md)): LOADING is transitive and
  VISIBILITY is per unit; two versions of one library in one build are CE3519.
- **Variadics** ([`variadics.md`](../design/variadics.md)): bloom `arr...` forwards a bare
  array variable with MOVE semantics.
- **Tuples** ([`tuples.md`](../design/tuples.md)): no tuple `const`/`var` (by design), no
  extension on a tuple type. The one pattern-matrix checker serves every match: a missing
  pattern is CE2040, an unreachable arm is the ERROR CE2118.
- **Iteration** ([`iteration.md`](../design/iteration.md)): the `Iterator` NAME needs
  `use <collections/iter>`; the built-in adapters need no import. `foreach` CONSUMES its
  iterable, and the protocol has NO error channel: a fallible iterator puts the failure in
  its item, `Maybe@(Result@(T, E))`, and `foreach(line?? in r.lines())` is the short form.
- **Slices** ([`slices.md`](../design/slices.md)): `data[a..b]` is a borrowed view with no
  copy (trap RE2028); a `let` of a slice of a temporary is CE2437; `.s`/`.ss` are the COPIES.

## Topics with no design doc
- **Generics**: monomorphization with auto-detected instantiations; the inference gaps are in
  `docs/agents/known-limitations.md`.
- **Hashing**: a CONTAINER hashes what it HOLDS, never its backing fields (a `List@(T)` its
  elements then its length, an `Own@(T)` its payload). A `HashMap@(K, V)` has NO derived hash,
  so it is no map KEY. A key needs a hash AND an `Eq`; a `Hashable` override alone gives no
  equality (#936). A held float hashes and compares by the total rule (-0.0 is 0.0, every NaN
  is one value). The backend has a narrower limit than `can_struct_be_hashed`: an array whose
  element has no hash (today `ptr[]`).
- **List views**: a List's `first()`/`last()` are borrowed views BY NAME, like `get`.
- **HashMap entries**: `Entry@(K, V)` is not a WRITABLE type name, so
  `let Entry@(i32, string) c = e.clone()` is an unknown-type error; a clone of an entry works
  in an inline position only.

## Arrays, ranges and patterns ([`docs/language-reference.md`](../language-reference.md), [`arrays.md`](../stdlib/collections/arrays.md))
- **Arrays**: `a[i]` removes the LAST suffix, the reverse of C. A run-time zero count is data
  and a negative one clamps to zero; a range element cannot carry a count. An EMPTY
  `from([])` or `new()` takes the element type of its POSITION (#544). A bulk copy borrows its
  source and copies an owning element with `copy_out` per slot; a self-alias is refused. A
  constant is `.rodata`, and a write to it is refused.
- **Ranges**: `.rev()` is legal on a range only (a `foreach` iterable, an array-literal
  element); the AST builder folds it into `RangeExpr.reverse`, so a range stays one node. One
  formula: `semantics/ranges.py`, with its SLIR twin `range_operands` in
  `semantics/slir/lower_control.py`.
- **Patterns**: four errors, one per fault (#741): the SCRUTINEE's rule alone, an arm or a
  nested pattern that names an enum the value is not (the scrutinee in a note), a nested
  pattern over a payload that is not an enum, and an `Own(...)` pattern over a value that is
  not an `Own@(T)`; each type slot holds a TYPE, never a sentence.
- A string match tests its arms in source order (a chain of tests,
  `semantics/slir/lower_match.py`). A dead alternative of `|` is CE2118 at its span. Integer
  pattern values are read at the TYPE of the position (`semantics/integer_patterns.py`, never a
  stamp). No constant pattern, no open-ended range, no `n @ range` binding.

## Literals and printing (no design doc)
- **Numeric literals**: one underscore between two digits in every base (`1_000_000`,
  `0xDEAD_BEEF`; a doubled or edge underscore is a parse error). A bare literal takes the
  type of its context (annotation, parameter, field, operand); out of range is an error; with
  no context it is `i32`/`f64`, and an overflow there is an error. Literal typing is not
  value coercion: a typed value still needs `as`.
- **Byte literal** `a'x'`: one byte, one character from 0 to 127 or one escape (`\n \t \r \0
  \\ \' \" \xNN`); a character above 127 is CE6015, any other malformed form CE6014. It is an
  `IntLit` with `byte_spelling`, default `u8`, with the DECIMAL range rule (`a'\xc8'` is no
  `i8`). Beside a bare number literal the number takes `u8`; in an index position (index,
  slice bound, count) it takes `i64`, as every bare literal there; in a range bound it
  takes `i32`. It is legal in an expression, a `const` and a `match`
  arm; not as an array size or a negative arm. The `a` touches the quote (`a '/'` is CE6001).
- **An index position takes any integer type** (docs/adr/0001-index-width-i64.md, the Go
  rule): `arr[i]`, `s[i]`, a slice bound, a repeat count, and the position or count
  argument of a built-in method of `T[]`, `List@(T)` and `string`. A bare literal there takes
  `i64`. A float is CE2121 (CE2006 as a method argument). The bounds check compares the
  index and the length at 64 bits, so a negative index and an index of 2^32 both trap
  (RE2020). A range bound (`a..b`) is still `i32`, `len()` still answers `i32`, and a `ptr`
  offset is no index position.
- **A bool prints as `true`/`false`** everywhere: a hole, `print`, `println` and a constant.
  Never 1/0 (#514, #523).

## Operators (no design doc)
- **Logical:** `and`/`&&`, `or`/`||`, `xor`/`^^`, `not`/`!`; every operand is a bool, with the
  rule and the errors of a condition (#532).
- **Bitwise:** `& | ^ ~ << >>`; `>>` is arithmetic on a signed value, logical on an unsigned.
- **Mixed widths** are an error for comparison, arithmetic and `& | ^`. A shift is exempt:
  the TYPE of the count is free, but a count that the compiler can read must be 0..width-1. A
  COMPUTED count past the width is defined, not checked: 0, or the sign bit for a signed
  `>>` (the Go rule; never masked, so `v << 8` on a u8 is 0 and not `v`).
- **Arithmetic operands** (`reject_non_numeric_arithmetic`, `passes/types/expressions.py`):
  each operand of `+ - * / %` and of the unary minus is an integer or a float. A `bool`, an
  enum, a struct, an array and an unhandled `Result`/`Maybe` are errors (the diagnostic names
  `??`, `.or_err(nom e)??`, `.realise(default)` and `match`). `+` with a `string` operand has
  its own error. A divisor that the compiler can read and that holds zero is an error, in a
  body as in a constant; a computed divisor is not checked (#709).
- **Comparison operands** (`reject_uncomparable_operands`, `passes/types/expressions.py`):
  `== !=` read `Eq`, `< <= > >=` read `Ord`. A primitive keeps its closed set: `== !=` take a
  number, a `bool` or a `string`; `< <= > >=` a number or a `string` (a bare `bool` has no
  order, by choice). A struct or an enum, `Maybe`/`Result` included, compares by its derived
  or implemented contract. A mixed pair is one error; a type with no such comparison is
  another, with a note that names the field that stops it. A **string order reads bytes**:
  `memcmp` over the common prefix, then the length (a prefix is less); never a collation.
- **Other:** `as` (cast), `??` (error propagation).
