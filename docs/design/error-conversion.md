# Error types and error conversion

Status: ACCEPTED. The rulings are David's (2026-10-04). Nothing is built yet.

## Summary

- An error type is declared with `error`. It is an enum with a flag, and it is ordinary
  data in every position.
- The `E` of every `Result@(T, E)` in the program is an error type.
- `extend FileError as IoError:` declares a conversion from one error type to another.
  `e as IoError` calls it explicitly, and `??` calls it when the two error types differ.
- `??` takes a `Result@(T, E)` and nothing else. On a `Maybe`, `m.or_err(nom e)??` is the
  form.
- `r.map_err(f)??` converts at one site, with no declaration.

## The rulings

| # | Ruling | Section |
|---|---|---|
| **E1** | An error type is declared with the keyword `error`. It is an enum with an `is_error` flag, not a new type kind | 2.1, 2.2 |
| **E2** | An error type is ordinary data in every position | 2.3 |
| **E3** | The `E` of every `Result@(T, E)` in the program is an error type. CE2084 carries the rule, and CE2086 is retired | 2.4 |
| **E4** | E3 is judged at every written type and at each generic instance, not at the `Result` interning seam | 2.5 |
| **C1** | A conversion is a declaration: `extend <Source> as <Target>:` with a block body | 3.1 |
| **C2** | `e as <Target>` is the explicit use of a conversion. `as` consumes its operand by position, unmarked | 3.2 |
| **C3** | `??` calls the declared conversion when the two error types differ | 3.3 |
| **C4** | A conversion is one step. A chain is never followed | 3.4 |
| **C5** | Only the unit that declares the target type may declare a conversion into it | 3.5 |
| **C6** | The source and the target are non-generic error types | 3.6 |
| **C7** | The body is bare, and it consumes `self` | 3.7 |
| **C8** | `??` takes a `Result@(T, E)` only. A `Maybe` and an enum shaped like a `Result` or a `Maybe` are refused. `or_err(nom e)` is the form for a `Maybe` (#1168) | 4 |
| **C9** | `or_err` and `map_err` are built-in methods, beside `.realise()` and `.err()`, and need no import. `map_err` ships in the first version | 4, 5 |
| **C10** | A `from` marker on a variant is deferred | 9.1 |
| **C11** | A conversion has no leak check | 3.8 |

---

## 1. Motivation

### 1.1 "Enum" is a substitute for "error"

The `E` of `T | E` and of `Result@(T, E)` must be an enum (#668). CE2084 refuses a
struct, a primitive, an array and a function type. CE2086 refuses `Maybe` and `Result`.
The rule is wrong in two directions:

- It admits a type that is an enum by representation and not by intent. `Maybe@(T)` and
  `Result@(T, E)` are ordinary interned enums, so CE2086 refuses them BY NAME. A future
  built-in wrapper is not on that list.
- It admits an enum that is not an error vocabulary. `| Color` is legal.

### 1.2 `??` needs one error type

`??` propagates only when the two error types are the same type. Otherwise it is CE2511,
and its doc text ends with "Error type conversion is not supported yet." A function that
calls two modules with two error types cannot use `??` on both. Each call to the other
module is a `match`, and `match` is a statement, so each such call nests one level
deeper. At `82d6bf94`, the stdlib has 14 of these matches (10 in `io/fs.sushi`, 4 in
`net/tcp.sushi`), each of the form
`Result.Err(e) -> return Result.Err(e.to_io())`, and two conversion methods
(`FileError.to_io()`, `NetError.to_io()`). A user program has no shorter form.

### 1.3 `??` on a `Maybe` invents an error

In a body with `| E`, `??` on a `Maybe` that holds `None` returns `Result.Err` built from
`undef` (#1168). The caller reads an `E` that the program never made, and the answer
changes with `--opt`.

### 1.4 `??` reads the shape of its operand

`??` accepts any enum with `Ok`/`Err` variants or `Some`/`None` variants, not only
`Result` and `Maybe` (`_unwrapped_arms`, `passes/types/expressions.py`; the CE2507 text
says "result-like enum"). A user `enum Outcome: Ok(i32), Err(E)` propagates through
`??`. The `Err` payload of such an enum is not a `Result`, so no rule on error types
reaches it, and a user enum with `Some`/`None` has the fault of 1.3.

---

## 2. Error types

### 2.1 The declaration

<!-- docs-sweep: skip (accepted syntax, not built yet) -->
```sushi
error ConfigError:
    Unreadable(FileError)
    Empty
```

The body is the body of an `enum`: variants, payloads, type parameters, doc blocks, and
`public`. Only the keyword is different.

The keyword is `error`. No code in the stdlib, the toolchain, the tests or the docs uses
`error` as an identifier. `err` is not used, for three reasons: `Result` has an `.err()`
method, and a reserved word cannot be a method name; `err` is the usual variable name for
an error; and `err` differs from the variant `Err` by case only.

### 2.2 The representation

`error X:` builds an `EnumType` with `is_error = True`. It is not a new type kind, so
every enum mechanism applies with no change: `match` and exhaustiveness, payloads, the
generic form (`error DecodeError@(T)`), the derived `Eq`, `Ord`, `Display`, `hash` and
`clone`, extension methods, perk implementations, `Drop`, visibility, namespaces,
libraries, the `docs` pass and the `unused` lint.

The `collect` pass gives the flag to seven of the nine predefined enums: `StdError`,
`IoError`, `FileError`, `NetError`, `ProcessError`, `EnvError` and `MathError`.
`FileMode` and `SeekFrom` are not errors and stay plain enums.

### 2.3 An error type is data

An error type can be a field, a payload, a parameter, a `let`, an array element and a
generic argument (`List@(ConfigError)`, `HashMap@(NetError, i32)`). The flag means one
thing: "this type may be the `E` of a `Result`".

The language already uses an error as data in each of these positions. `Result@(T, E)`
holds `E` as a payload, `.err()` answers `Maybe@(E)`, `Result.Err(e) ->` binds a local,
`partition` in `<collections/iter>` answers a list of errors, and a wrapping variant
(`Unreadable(FileError)`) holds one error in another. Programs collect errors, keep the
last error in a field, count errors by kind, and compare an error with an expected value
in a test.

### 2.4 Every `Result` holds an error type

The `E` of every `Result@(T, E)` in the program is an error type. A `Result` is the
value of an error channel, and only an error fits into an error channel.

- The rule covers both spellings, because `T | E` is sugar for `Result@(T, E)`.
- The rule covers every position: the signature of a function, a method, a perk
  contract, a perk implementation, a lambda and a function type (`fn(i32) -> i32 | E`),
  and also a `let`, a field, a payload, a parameter and a generic argument.
- One code refuses every other type: a plain enum, a struct, a primitive, an array, a
  function type, `Maybe` and `Result`. CE2084 carries the rule with new wording: its
  meaning stays "`E` is not an error type", and only the predicate changes. CE2086 is
  retired, and with it the list of refused names.
- The rule changes at once. No release accepts both a plain enum and an error type as
  an `E`.

### 2.5 Where E3 is judged

1. **Every written `Result@(T, E)` and every `T | E`** is judged where it is written,
   through the written-type walk (`validate_type_name`). The HashMap key rule
   (`reject_unusable_key`) is the model. The diagnostic points at the `E`.
2. **A type parameter in the `E` position** (`fn f@(E)() T | E`) is judged at each
   instance, because generics are templates. The diagnostic names the type argument and
   carries a note at the template.
3. **A `Result` that the compiler infers is not judged.** A call result comes from a
   signature that was judged. A `Result.Err(x)` construction takes its type from its
   position, and that position was judged.

The `Result` interning seam (`intern_wrapper_enum`) does not judge E3. It interns a type,
not a written type, so its diagnostic would have no location. It also interns the
`Result` types that the compiler makes (stdlib signature rows, `.err()`, `partition`,
monomorphized copies), where a refusal is an internal error. And a `Result` can be
interned before `collect` sets the flag on a type declared later in the file. The seam
may carry an internal-error backstop once (1) and (2) cover every written position.

---

## 3. Error conversion

### 3.1 The declaration

<!-- docs-sweep: skip (accepted syntax, not built yet) -->
```sushi
extend FileError as IoError:
    match self:
        FileError.NotFound -> return IoError.NotFound
        FileError.PermissionDenied -> return IoError.PermissionDenied
        FileError.Other -> return IoError.Other

extend IoError as AppError:
    return AppError.Io(self)
```

A conversion has no name, no parameter list and no return type. The return type is the
target, and `self` is the source value. The wrapping case is one line.

### 3.2 The explicit use: `as`

`e as IoError` calls the declared conversion. `as` is the explicit conversion operator of
the language, so a conversion between two types is one more case of it. Today `as` casts
between numeric types only (`is_valid_cast`). It now also accepts a pair of error types
that has a declaration. Every other pair stays an invalid cast.

`as` consumes its operand by POSITION, with no marker, as a constructor argument does.
For a plain error type this is a copy, and every stdlib error type is plain. For an
error type that owns a resource, `e` is spent. When `e` is a borrow (a `match` binding
such as `Result.Err(e) ->`), the cast is the consuming use of a borrow and is refused.
The form is then `e.clone() as AppError`.

### 3.3 The implicit use: `??`

At a `??` on a `Result@(T, E_in)`, in a body whose channel is `E_out`:

1. If `E_in` is `E_out`, the error propagates unchanged.
2. If the conversion `E_in as E_out` is declared, `??` calls it on the error and
   propagates the result.
3. Otherwise the `??` is CE2511. The help names the declaration to write.

Sushi still converts nothing on its own. A conversion runs only at a written `??` or a
written `as`, and only through a declaration that the program wrote.

The rule applies to every `??`: in a function, a method, a lambda with a channel, and
the `foreach(x?? in ...)` binder, which the AST builder expands to an ordinary `??`.

### 3.4 One step

`FileError as IoError` and `IoError as AppError` do not give `FileError` to `AppError`.
The lookup is an exact match on the pair. It can never be ambiguous, and the reader finds
each conversion in one place. A program that wants `FileError` to `AppError` declares it.

An identity conversion is refused. Two declarations of one pair are the
duplicate-function error.

### 3.5 Who may declare a conversion

Only the unit that declares the target type, the rule that `Drop` already has. For a
predefined type, the declaring unit is its home module (`EnumType.home_module`).

- Each pair has at most one declaration in the program, because the target's unit is
  unique. Two libraries cannot both declare `FileError as AppError`.
- An application's `AppError` lists the errors it absorbs, beside its own declaration.
- `FileError` and `IoError` both have the home `<io/error>`, so the stdlib conversion
  lives there.
- A library cannot convert its own error into `IoError`, because the stdlib owns
  `IoError`. The library declares its own error type and converts `IoError` into it.

### 3.6 Which types

The source and the target are error types, because E3 requires it of every channel. Both
are non-generic: a generic error type, or an instance of one (`DecodeError@(i32)`), is
refused as a source and as a target. The lookup is then a pair of names, and the binary
manifest a pair of strings.

A generic FUNCTION may use a conversion. In `fn f@(E)(...) T | E`, the pair is known
for each instance only, so the conversion is looked up when the instance is checked.

### 3.7 Ownership

- The body consumes `self`. The receiver mode is `nom`, and it is not written. `??`
  spends the error in any case, so a borrow would force a deep copy of the payload.
- The body is bare. A `| E` on a conversion is refused, because a conversion that can
  fail would need a second channel at every `??`. A `??` in the body is CE0131, as in
  every bare body.
- An error type that owns a resource (a `string` payload) moves into the conversion. The
  conversion destroys what it does not put in the target.

### 3.8 Visibility

A conversion is found by its pair of types, never by a name. An import does not bring it
and does not hide it. A conversion is as visible as its target type, the rule for an
extension. A function with `| AppError` in its signature names `AppError`, so it sees
every conversion into it.

Behind an alias, the declaration spells the qualified names:
`extend fs.FileError as AppError:`. A private target makes the conversion usable in its
own unit only.

A conversion has no leak check. Take a public target and a private source in one unit:
no other unit can hold a value of the private source, so the conversion never runs
outside its unit and gives nothing away. A public function that answers the private
source is a leak, and the leak check already refuses that function.

---

## 4. What `??` takes

`??` takes a `Result@(T, E)`, by type identity, and nothing else. CE2507 refuses every
other operand:

- **A `Maybe`.** A `??` in a bare body is CE0131 as before, so `??` never applies to a
  `Maybe`. The CE2507 help names `or_err`.
- **A user enum shaped like a `Result` or a `Maybe`** (1.4). Its `Err` payload is out of
  reach of E3, and its `None` has the fault of #1168. The type identity of Sushi is
  nominal, so `??` reads the type and not its variant names.

For a `Maybe`, the program writes the error value:

<!-- docs-sweep: skip (accepted syntax, not built yet) -->
```sushi
fn first(i32[] xs) i32 | AppError:
    let i32 v = xs.get(0).or_err(nom AppError.Empty)??
    return Result.Ok(v)
```

`or_err` is a built-in method of `Maybe@(T)`, beside `.realise()`, so it needs no import.
Its signature, written as an extension, is
`extend Maybe@(T) or_err@(E)(nom self, nom E e) Result@(T, E)`. `E` is judged by E3 at
each instance.

- The parameter is `nom`, because the value moves into the `Err` and a pass-through
  generic needs `nom`. A mode is marked at both ends, so the call writes
  `or_err(nom AppError.Empty)`, as `BufReader.new(nom f, 8192)` does.
- The receiver is `nom self`, because the `Some` payload moves into the `Ok`. On a
  temporary (`xs.get(0)`) nothing changes. A named `Maybe` is spent, which is the rule
  `??` already has for a named wrapper.

A `None` has no error value, so a conversion from "nothing" to an `E` is written at the
site. Otherwise the compiler would have to invent a value, which is the fault of #1168.
Rust has the same rule: `?` on an `Option` in a function that returns a `Result` is a
compile error, and `.ok_or(e)?` is the form.

`build_err_from_return_type` never builds an `Err` from no value. A call without a value
is an internal error.

---

## 5. `map_err`

A conversion that one site needs does not need a declaration:

<!-- docs-sweep: skip (accepted syntax, not built yet) -->
```sushi
let File f = open(path, FileMode.Read()).map_err(|nom IoError e| AppError.Config(e))??
```

`map_err` is a built-in method of `Result@(T, E)`, beside `.realise()` and `.err()`, so it
needs no import. Its signature, written as an extension, is
`extend Result@(T, E) map_err@(F)(nom self, fn(nom E) -> F f) Result@(T, F)`. The
method-level type parameter `F` is solved from the function argument, as for the
combinators of `ufcs-combinators.md`.

- The function takes the error `nom`, because the error moves into the new value, as it
  does in a conversion. A mode is part of a function type, so the lambda writes
  `|nom IoError e|`.
- The receiver is `nom self`, because the `Ok` value and the error both move into the new
  `Result`. A named `Result` is spent.
- Two existing limits apply. A bare lambda parameter is not inferred, so the parameter
  type is written. A lambda parameter cannot have an owning type, so for an error type
  that owns a resource the argument is a named function
  (`fn wrap(nom ParseError e) AppError`).

---

## 6. Examples

### 6.1 The stdlib

Before:

<!-- docs-sweep: skip (fragment of a stdlib unit) -->
```sushi
fn open(string path, FileMode mode) File | IoError:
    match fd_open(path, mode.intent(), 420):
        Result.Ok(fd) -> return Result.Ok(File(fd: fd, owned: true))
        Result.Err(e) -> return Result.Err(e.to_io())
```

After:

<!-- docs-sweep: skip (accepted syntax, not built yet) -->
```sushi
fn open(string path, FileMode mode) File | IoError:
    let i32 fd = fd_open(path, mode.intent(), 420)??
    return Result.Ok(File(fd: fd, owned: true))
```

### 6.2 A program with its own error type

<!-- docs-sweep: skip (accepted syntax, not built yet) -->
```sushi
use <io/files>

error ConfigError:
    Unreadable(FileError)
    Empty

extend FileError as ConfigError:
    return ConfigError.Unreadable(self)

fn count_configs(string dir) i32 | ConfigError:
    let string[] names = read_dir(dir)??
    if (names.len() == 0):
        return Result.Err(ConfigError.Empty)
    return Result.Ok(names.len())

fn main() i32:
    match count_configs("/etc/app"):
        Result.Ok(n) -> println("{n} config files")
        Result.Err(ConfigError.Unreadable(FileError.NotFound)) -> println("no such directory")
        Result.Err(ConfigError.Unreadable(e)) -> println("cannot read the directory: {e}")
        Result.Err(ConfigError.Empty) -> println("no config files")
    return 0
```

### 6.3 Two error types in one function

Before:

<!-- docs-sweep: skip (fragment with no main) -->
```sushi
fn notify(string host, string msg) ~ | AppError:
    match connect(host, 80):
        Result.Err(e) ->
            return Result.Err(AppError.Net(e))
        Result.Ok(nom s) ->
            match s.write(msg):
                Result.Err(e) ->
                    return Result.Err(AppError.Io(e))
                Result.Ok(_) ->
                    return Result.Ok(~)
```

After:

<!-- docs-sweep: skip (accepted syntax, not built yet) -->
```sushi
error AppError:
    Io(IoError)
    Net(NetError)
    Empty

extend IoError as AppError:
    return AppError.Io(self)

extend NetError as AppError:
    return AppError.Net(self)

fn notify(string host, string msg) ~ | AppError:
    let TcpStream s = connect(host, 80)??
    s.write(msg)??
    return Result.Ok(~)
```

---

## 7. Diagnostics

| Fault | Diagnostic |
|---|---|
| The `E` of a `Result`, in either spelling and any position, is not an error type | CE2084, with new wording. The message says what the type is. For a plain enum, the help says to declare it with `error`. CE2086 is retired |
| A generic instance puts a non-error type in the `E` position | The same code at the instance, with a note at the template |
| `??` with two error types and no declaration | CE2511. The help names `extend <E_in> as <E_out>`. "Not supported yet" is removed from its doc text |
| `e as T` between two error types with no declaration | The invalid-cast error. The help names the declaration |
| `e as T` on a borrowed error that owns a resource | The consuming-use-of-a-borrow error. The help names `e.clone() as T` |
| A conversion outside the unit of its target type | New code, with a note at the target's declaration |
| A generic or non-error source or target | New code |
| An identity conversion | New code |
| Two declarations of one pair | The duplicate-function error, with a note at the first declaration |
| A `\| E` on a conversion | New code |
| `??` on a `Maybe` | CE2507, with new wording ("`??` takes a `Result@(T, E)`"). The help names `or_err(nom e)` |
| `??` on a user enum shaped like a `Result` or a `Maybe` | CE2507. The help says to answer a `Result@(T, E)` |
| `as` with a conversion in a `const` initializer | The not-a-constant-expression error |

The numbers are chosen when the work is built, each in the module that owns its range
(`internals/errors/types.py`, `internals/errors/result.py`).

---

## 8. Implementation

### 8.1 The `error` declaration

- **Grammar.** A keyword terminal `ERROR` and a top-level `error_def` with the body of
  `enum_def`. `use_path` still accepts the word `error`, because `use <io/error>` and
  `use <net/error>` spell it.
- **AST.** The enum declaration node gets an `is_error` field, set by the AST builder from
  the keyword. There is no second node class.
- **`collect`.** The flag goes on the `EnumType`, and on a generic template so that each
  instance carries it. `passes/collect/enums.py` sets it on the seven predefined error
  types.
- **E3.** One predicate, "is an error type". It is called from `signature_result_arms`
  (`semantics/generics/results.py`), where CE2084 and CE2086 are emitted today, from the
  written-type walk (`validate_type_name`, `passes/types/utils.py`), and from the
  monomorphizer where it validates type arguments. Every call emits CE2084. CE2086 is
  removed from `internals/errors/types.py`.
- **Libraries.** The `enum` row of a binary or hybrid manifest gets an `is_error` field
  (`backend/library_format.py`, read by `semantics/library_registration.py`). A source
  `.slib` carries the keyword as text. `--lib-info` and `slib-info` print `error`.
- **Display.** A message that names the declaration kind says "error type" for a flagged
  enum.

### 8.2 The conversion

- **Grammar.** One more alternative of `extend_suffix`: `AS type ":" block`. After
  `EXTEND type`, the token `AS` selects it, as `WITH` and `STATIC` select theirs.
- **AST.** The node is an `ExtendDef` with the method name `as`, which no user method can
  have because `as` is reserved. The target is in the method-level type-argument slot,
  so `extension_symbol(source, "as", (target,))` names it, and one source can convert to
  many targets. The body goes through `scope`, `typecheck`, `lift` and `borrow` as any
  extension body, and the backend emits it as any extension.
- **The seam.** One table, `SymbolTables.conversions`, keyed `(source, target)` and
  filled by `collect`. One function answers "which conversion turns `E_in` into `E_out`".
  The `??` check (`_error_arms_agree`, `passes/types/expressions.py`) and the cast check
  (`is_valid_cast`, `passes/types/compatibility.py`) both call it, and nothing else reads
  the table. A gate refuses a second reader.
- **What `??` takes.** `_unwrapped_arms` (`passes/types/expressions.py`) asks whether
  the operand IS a `Result` instance (`is_instance_of`, `semantics/type_predicates.py`),
  and no longer reads variant names. The Maybe branch is removed, and so is the
  `error_type=None` path into the backend.
- **`typecheck`.** `validate_try_expression` and `validate_cast_expression` ask the seam
  and stamp the answer on the node (`TryExpr.inferred_conversion`,
  `CastExpr.inferred_conversion`). In a generic function the question is asked per
  instance.
- **Backend.** The propagate block of `emit_try_expr` (`backend/expressions/try_expr.py`)
  extracts the error, calls the stamped conversion with it as a consuming argument, runs
  the scope cleanup, builds the `Err` from the converted value, and returns. The
  conversion runs before the cleanup, and the extracted error is not registered for
  cleanup, so it is freed once. `r??` on a named wrapper (`ownership.unwrap`, #548)
  hands the error over in the same way.
- **Libraries.** A binary or hybrid manifest gets a `conversions` row (source, target,
  link symbol). `--lib-info` and `slib-info` print it.
- **Lints.** A conversion is an `extend` block, so `--warn-unused` treats it as a root.
  Under `--warn-missing-docs`, an `error` declaration and a conversion take a doc block,
  as an `enum` and an extension method do.

### 8.3 The IR

`ir.md` S5 moves every `typecheck` stamp into `TypeckResults`. The conversion is one more
stamp, and in SHIR one more `Callee` (`ir.md` 7.6). The `error` flag is a property of the
type and needs nothing from the IR.

---

## 9. Alternatives considered

### 9.1 Deferred: a `from` marker on a variant

`Unreadable(FileError) from` would declare `extend FileError as ConfigError` on the
variant, as Rust's `thiserror` does with `#[from]`. The one-line body of 3.1 does the
same job, so the marker waits for a real need. When it comes, it is legal only on a
variant of an `error` declaration with one payload of a non-generic error type, and it
follows every rule of section 3.

### 9.2 Other ways to mark an error type

| Form | Prior art | Why not |
|---|---|---|
| An `Error` perk | Swift `: Error`, Go `Error() string` | Explicit, it puts `extend X with Error:` on every error type. Derived for every enum, it marks nothing |
| A separate type kind | Zig `error{...}`, OCaml `exception` | Every enum seam would need a second handler. The flag gives the same language |
| No mark | Rust | It keeps the problem of 1.1 |

### 9.3 Other ways to apply E3

- **Channels only.** `let Result@(i32, Color) r` would stay legal: a `Result` that no
  channel can propagate.
- **At the interning seam.** No location, compiler-made `Result` types, and the order of
  `collect` (2.5).

### 9.4 Other ways to convert

| Form | Prior art | Why not |
|---|---|---|
| A predefined perk `From@(E)` | Rust `From`, applied by `?` | It needs a perk with a type parameter (CE4010), a static in a perk (CE4014), and more than one implementation of one perk on one type: three language changes |
| Structural error unions | Zig error sets, Roc tag unions, OCaml polymorphic variants | Type identity is nominal (`type-identity.md`), and Zig errors carry no payload |
| One dynamic error type | Rust `anyhow`, Go `error`, Swift `any Error`, exceptions | Sushi has no dynamic perk object, and a top type loses the exhaustive `match` |
| Explicit mapping only | Go, Gleam, Swift typed throws, C++ `std::expected` | It is the cost of 1.2. `map_err` (section 5) keeps it for a single site |
| A binary `r ?? conv` | none | In C#, JS, Swift, PHP and Kotlin a binary `??` or `?:` means "use this default" |
| A transitive chain | none | The lookup becomes a path search, and two paths can give two answers |
| Conversion at an assignment, an argument or a `return` | C++ implicit conversions | No written mark shows it. Only `??` and `as` mark the site |
| The unit of either type may declare | Rust's orphan rule | Two units can declare one pair, and the clash must be found across units and libraries |

### 9.5 `??` on a `Maybe` for `StdError` only

`??` would stay legal on a `Maybe` where `E` is `StdError`, and build `StdError.Error`.
Fewer sites change, but the compiler still supplies a value that the source does not
spell.

### 9.6 Other choices on the details

| Choice | Why not |
|---|---|
| A new code for E3, and CE2084 and CE2086 both retired | CE2084 already means "`E` is not an error type". Only its predicate changes, and its fixtures keep their code |
| `as` with a `nom` marker (`nom e as T`) | `??` spends the error with no marker, and a constructor consumes its argument by position. `as` is one more position of that kind |
| `as` that borrows its operand | One body cannot have two receiver modes. The conversion consumes `self` for `??`, so `as` consumes too |
| A leak check on a conversion | A conversion with a private source never runs outside its unit, so the check would refuse nothing that matters |
| `??` that keeps reading variant names | It leaves a path around E3 and around the fix of #1168, and it reads the shape of a type, where identity is nominal |
