# Error types and error conversion at `??`

Status: PROPOSED. Nothing in this document is built. The `error` declaration (section 3,
decisions E1 to E4) is RULED by David on 2026-10-04. The conversion (section 4,
decisions C1 to C8) is a proposal until David rules on it. Section 13 lists the open
questions.

## The decisions, in one place

| # | Decision | Status | Where |
|---|---|---|---|
| **E1** | An error type is DECLARED with the keyword `error`. It is an enum with an `is_error` flag, not a new type kind | Ruled | 3.1, 3.2 |
| **E2** | An error type is ordinary data in every position | Ruled | 3.3 |
| **E3** | The `E` of every `Result@(T, E)` in the program is an error type. This replaces CE2084 and CE2086 | Ruled | 3.4 |
| **E4** | The compiler judges E3 at every WRITTEN type and at each generic instance, not at the `Result` interning seam | Ruled | 3.5 |
| **C1** | A conversion is a DECLARATION: `extend FileError as IoError:` with a block body | Proposed (O1) | 4.1 |
| **C2** | `e as IoError` is the explicit use. `as` is the one conversion operator | Proposed (O1) | 4.2 |
| **C3** | `??` applies a declared conversion when the two error types differ | Proposed | 4.3 |
| **C4** | One step only. A chain of conversions is never followed | Proposed | 4.4 |
| **C5** | Only the unit that declares the TARGET type may declare a conversion into it | Open (O2) | 4.5 |
| **C6** | The source and the target are both non-generic error types | Proposed | 4.6 |
| **C7** | The body is bare, and it consumes `self` | Proposed | 4.7 |
| **C8** | `??` on a `Maybe` in a `\| E` body is refused, and `or_err(e)` is the escape (#1168) | Open (O3) | 4.9 |

---

## 1. The problem

### 1.1 What an error type is today

The `E` of `T | E` and of `Result@(T, E)` must be an ENUM (#668: CE2084 for a struct, a
primitive, an array or a function type; CE2086 for `Maybe` and `Result`). "Enum" stands
in for "error", and it is wrong in both directions:

- It admits a type that is an enum by representation and not by intent. `Maybe@(T)` and
  `Result@(T, E)` are ordinary interned enums, so CE2086 must refuse them BY NAME. A
  future built-in wrapper is not on that list.
- It admits an enum that is not an error vocabulary. `| Color` is legal.

### 1.2 The rule of `??` today

`??` propagates an error only when the two error types are the same type. In a body with
`| IoError`, a `??` on a `Result@(T, FileError)` is CE2511:

```
error type mismatch in propagation: cannot propagate Result@(i32, FileError) to
function returning Result@(i32, IoError)
```

The CE2511 doc text ends with "Error type conversion is not supported yet."

### 1.3 What the rule of `??` costs

A function that calls two modules with two error types cannot use `??` on both. Every
call to the other module needs a `match`, and `match` is a statement, so each such call
nests one level deeper. Measured at `82d6bf94`:

- The stdlib has 14 three-line conversion matches: 10 in `io/fs.sushi` and 4 in
  `net/tcp.sushi`. Each one is
  `match f(): Result.Ok(x) -> ... / Result.Err(e) -> return Result.Err(e.to_io())`.
- Two hand-written conversion methods exist: `extend FileError to_io() IoError` in
  `io/error.sushi` and `extend NetError to_io() IoError` in `net/error.sushi`.
- A user program has no shorter route.

### 1.4 A defect in the same place

`??` on a `Maybe` that holds `None`, in a `| E` body, returns `Result.Err` with an
undefined `E` (#1168). The propagate block builds the `Err` from `undef` and writes only
the tag, so the answer changes with `--opt`. Section 4.9 gives the rule for that case,
because a `None` under `??` is also a conversion: from "no value" to an `E`.

---

## 2. What other languages do

### 2.1 How a language marks an error type

| Language | Form |
|---|---|
| Zig | A dedicated declaration: `const E = error{ NotFound, Denied };` |
| Swift | A conformance on an ordinary enum: `enum E: Error { ... }` |
| OCaml | A dedicated declaration for exceptions: `exception Not_found` |
| Rust | No mark. Any type can be the `E` of `Result<T, E>`. The `std::error::Error` trait is optional |
| Go | An interface: any type with `Error() string` |

### 2.2 How a language converts one error type into another

| Family | Languages | Mechanism | Fit for Sushi |
|---|---|---|---|
| **1. A declared conversion, applied at the propagation site** | Rust: `?` calls `From::from(e)`. The `thiserror` crate generates the `impl` from `#[from]` | One declaration per (source, target) pair. `?` applies it | **Fits**, with a dedicated declaration. A perk cannot carry a type parameter (CE4010), so `From@(E)` is not writable |
| **2. Structural error unions** | Zig (error sets coerce to a superset), Roc (tag unions), OCaml (polymorphic variants) | The error type grows by itself | **Does not fit.** Type identity is nominal (`type-identity.md`), and Zig errors carry no payload |
| **3. Explicit mapping at each site** | Go (`%w`), Gleam (`result.map_error`), Haskell (`withExceptT`), Swift typed throws, C++ `std::expected` | Each site writes the conversion | **Fits as a supplement** (`map_err`, 4.10) |
| **4. One dynamic error type** | Rust `Box<dyn Error>` and `anyhow`, Go `error`, Swift `any Error`, Java and C# exceptions | Every error converts to one top type | **Does not fit.** Sushi has no dynamic perk object, and a top type loses the exhaustive `match` |

Two lessons:

- **Rust refuses `?` on an `Option` in a function that returns a `Result`.** The escape
  is `.ok_or(e)?`, so the program always writes the error value. Sushi takes this rule
  (4.9).
- **In C#, JS, Swift, PHP and Kotlin, a binary `??` or `?:` means "use this default".**
  A Sushi `r ?? f` would read as that operator and mean something else (12.6).

---

## 3. The `error` declaration (ruled)

### 3.1 The form

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
error FileError:
    NotFound
    PermissionDenied
    Other

error ConfigError:
    Unreadable(FileError)
    Empty
```

The body is the body of an `enum`: variants, payloads, type parameters, doc blocks and
`public`. Only the keyword is different.

### 3.2 The representation: an enum with a flag

`error X:` builds an `EnumType` with `is_error = True`. It is not a new type kind. So every
enum mechanism applies to it with no change: `match` and exhaustiveness, payloads, the
generic form (`error DecodeError@(T)` stays legal), the derived `Eq`, `Ord`, `Display`,
`hash` and `clone`, extension methods and perk implementations, `Drop`, visibility,
namespaces, libraries, the `docs` pass and the `unused` lint.

A separate type kind would need a second handler at each enum seam (the lifecycle table,
the contract walk, the type walk, the manifest, every dispatch gate). The flag avoids
that cost.

### 3.3 An error type is ordinary data

An error type can be a field, a payload, a parameter, a `let`, an array element and a
generic argument (`List@(ConfigError)`, `HashMap@(NetError, i32)`). The flag means one
thing only: "this type may be the `E` of a `Result`".

The language already uses an error as data in each of these positions. `Result@(T, E)`
holds `E` as a payload, `.err()` answers `Maybe@(E)`, a `match` binds `Result.Err(e)` to
a local, and `partition` in `<collections/iter>` answers a list of errors. A wrapping
variant (`Unreadable(FileError)`) is an error type used as a payload. Programs collect
errors (a validator that reports every fault), keep the last error in a field, count
errors by kind in a map, and compare an error with an expected value in a test.

### 3.4 Every `Result` holds an error type

**The rule:** the `E` of every `Result@(T, E)` in the program is an error type. "Only an
error fits into an error channel", and a `Result` is the value of a channel.

- The rule covers both spellings: `T | E` is sugar for `Result@(T, E)`.
- The rule covers every position: a signature of a function, a method, a perk contract, a
  perk implementation, a lambda and a function type (`fn(i32) -> i32 | E`), and also a
  `let`, a field, a payload, a parameter and a generic argument.
- `Maybe`, `Result`, a struct, a primitive, an array, a function type and a plain enum are
  all refused by this one rule. CE2084 and CE2086 are replaced by one code, and the list
  of refused names goes away.
- The rule changes at once, with the migration (section 10). No release accepts both
  `enum` and `error` as an `E`.

### 3.5 Where the compiler judges it

The model is the HashMap key rule: `reject_unusable_key` judges every WRITTEN
`HashMap@(K, V)` through `validate_type_name` over one walk, and never judges
`HashMap.new()`.

1. **Every written `Result@(T, E)` and every `T | E`** is judged where it is written. The
   diagnostic points at the `E`.
2. **A type parameter in the `E` position** (`fn f@(E)() T | E`) is judged at each
   instance, because generics are templates. `f@(Color)` is refused at the instance, and
   the diagnostic names the type argument, with a note at the template.
3. **A `Result` that the compiler infers is not judged.** A call result comes from a
   signature that was judged. A `Result.Err(x)` construction takes its type from its
   position, and that position was judged.

The `Result` interning seam (`intern_wrapper_enum`) does NOT judge the rule:

- It interns a type, not a written type, so a diagnostic from there has no location.
- It also interns the `Result` types that the compiler makes (stdlib signature rows,
  `.err()`, `partition`, monomorphized copies). A refusal there is an internal error,
  not a user diagnostic.
- A `Result` can be interned before the `collect` pass sets the flag on a type that is
  declared later in the file.

The seam can carry an internal-error backstop later, with a gate that no fixture
reaches it, once (1) and (2) cover every written position.

### 3.6 The keyword: `error`

- `error` is used as an identifier nowhere in the stdlib, the toolchain, the tests or the
  docs. Every hit is prose in a doc block, and a doc block is one token.
- `err` was refused. `Result` has an `.err()` method, and a reserved word cannot be a
  method name (`v.static()` is not writable for the same reason). `err` is also the usual
  variable name for an error, and it differs from the variant `Err` by case only.
- Two import paths spell the word: `use <io/error>` and `use <net/error>`. `use_path` is
  `NAME ("/" NAME)*`, so the grammar must still accept `error` in a path (8.1).

### 3.7 The predefined types

The `collect` pass makes nine enums (`passes/collect/enums.py`, the names in
`semantics/predefined_types.py`). Seven are error vocabularies and get the flag:
`StdError`, `IoError`, `FileError`, `NetError`, `ProcessError`, `EnvError`, `MathError`.
`FileMode` and `SeekFrom` are not errors and stay plain enums.

---

## 4. The conversion

### 4.1 The declaration

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
extend FileError as IoError:
    match self:
        FileError.NotFound -> return IoError.NotFound
        FileError.PermissionDenied -> return IoError.PermissionDenied
        FileError.Other -> return IoError.Other
```

- The form is `extend <Source> as <Target>:` and a block. It has no name, no parameter
  list and no return type. The return type is the target.
- `self` is the source value. The body returns a target value.
- The wrapping case is one line:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
extend IoError as AppError:
    return AppError.Io(self)
```

### 4.2 The explicit use: `as`

A declared conversion is a user-defined `as`. `e as IoError` calls it. Today `as` casts
between numeric types only (`is_valid_cast`, `passes/types/compatibility.py`). With this
design, `as` also accepts a pair of error types that has a declaration. Every other
pair stays an invalid cast.

`as` is already the explicit conversion operator of the language. A second operator or
a method name would be one more spelling of the same idea.

### 4.3 The implicit use: `??`

At a `??` on a `Result@(T, E_in)` in a body whose channel is `E_out`:

1. If `E_in` is `E_out`, `??` propagates the error as it does today.
2. If a conversion `E_in as E_out` is declared, `??` calls it on the error and
   propagates the result.
3. In all other cases, the `??` is CE2511. The help names the declaration to write.

The rule "Sushi converts nothing on its own" stays true. A conversion happens only at
a written `??` or a written `as`, and only through a declaration the program wrote.

The rule applies everywhere a `??` occurs: a function body, a method body, a lambda with
a channel, and the `foreach(x?? in ...)` binder, which the AST builder expands to an
ordinary `??`.

### 4.4 One step only

`FileError as IoError` and `IoError as AppError` do NOT give `FileError` to `AppError`.
The lookup is an exact match on the pair, so it can never be ambiguous, and the reader
finds the conversion in one place. A program that wants the two-step conversion
declares it.

An identity conversion (`extend IoError as IoError`) is refused. Two declarations of
one pair are the duplicate-function error.

### 4.5 Who may declare a conversion (open: O2)

**Proposal: only the unit that declares the TARGET type.** This is the `Drop` rule ("only
the unit that declares a type may implement it").

- Each pair has at most one declaration in the whole program, because the target's
  unit is unique. Two libraries cannot both declare `FileError as AppError`.
- It fits the use case. An application's `AppError` lists what it absorbs, as
  thiserror's `#[from]` does on the target type.
- It fits the stdlib. `FileError` and `IoError` are predefined, and a predefined type's
  declaring unit is its HOME module (`EnumType.home_module`). Both have the home
  `<io/error>`, where `to_io()` is written today.
- The cost: a library cannot convert its own error into `IoError`, because the stdlib
  owns `IoError`. The library declares its own error type and a conversion of
  `IoError` into it.

The alternative is "the unit of either type", as Rust's orphan rule allows. Then two
units can declare one pair, and the analysis must find the clash across units and
across libraries.

### 4.6 Which types

The source and the target are both error types (E3 already requires it of a channel).
Both are non-generic: a generic error type, or an instance of one (`DecodeError@(i32)`),
is refused as a source and as a target. This keeps the lookup a pair of names and the
binary manifest a pair of strings. A real need can lift it later.

A generic FUNCTION is a different case, and it is allowed. In `fn f@(E)(...) T | E`, the
pair is known for each instance only, because generics are templates. The conversion is
looked up when the instance is checked (8.4).

### 4.7 Ownership

- The body consumes `self`. The receiver mode is not written, and it is `nom`. `??`
  spends the error in any case, so a borrow would force a deep copy of the payload.
- The body is bare. A `| E` on the declaration is refused: a conversion that can fail
  would need a second channel at every `??`. A `??` in the body is therefore CE0131,
  as in every bare body.
- An error type that owns a resource (a `string` payload) moves into the conversion.
  The conversion destroys what it does not put in the target.

### 4.8 Visibility

A conversion is found by its PAIR OF TYPES, never by a name, so an import never brings
or hides it. A conversion is as visible as its target type, the extension rule. A
function that has `| AppError` in its signature already names `AppError`, so it can see
every conversion into it.

Behind an alias, the declaration spells the qualified names:
`extend fs.FileError as AppError:`.

A private target makes the conversion usable in its own unit only. A public target with
a private source cannot exist: the target's unit must name the source, and the leak rule
applies to the declaration as it does to a public signature.

### 4.9 `??` on a `Maybe` (open: O3, #1168)

**Proposal: refuse `??` on a `Maybe` in every `| E` body**, with a new code. Add
`Maybe@(T).or_err(nom E e) Result@(T, E)`:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
fn first(i32[] xs) i32 | AppError:
    let i32 v = xs.get(0).or_err(AppError.Empty)??
    return Result.Ok(v)
```

A `None` has no error value. A conversion from "nothing" to an `E` must be written at
the site, or the compiler must invent a value. Rust's rule is the same.

The alternative (#1168, option B) keeps `??` on a `Maybe` where `E` is `StdError` only,
and builds `StdError.Error` explicitly. It changes fewer sites, but it keeps a value
that the source does not spell.

For either option, `build_err_from_return_type` must never build an `Err` from no value.

### 4.10 `map_err` (open: O4)

A conversion used at one site only does not need a declaration. The library form is:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
let File f = open(path, FileMode.Read()).map_err(|IoError e| AppError.Config(e))??
```

`map_err` is an extension with a method-level type parameter, on the `ufcs-combinators.md`
model. It does not exist today. A bare lambda parameter cannot be inferred (a known
inference gap), so the parameter type must be written.

### 4.11 The `from` marker on a variant (open: O5)

The wrapping case could be declared on the variant of the target:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
error ConfigError:
    Unreadable(FileError) from      # declares `extend FileError as ConfigError`
    Empty
```

The marker would be legal only on a variant of an `error` declaration with exactly one
payload, whose type is a non-generic error type. It gives the same declaration as 4.1,
so every rule of section 4 applies to it. Two `from` variants with one payload type are
the duplicate-pair error. Without the marker, the one-line body of 4.1 does the same
job.

---

## 5. Examples

### 5.1 The stdlib: `open()`

Before (today):

<!-- docs-sweep: skip (fragment of a stdlib unit) -->
```sushi
fn open(string path, FileMode mode) File | IoError:
    match fd_open(path, mode.intent(), 420):
        Result.Ok(fd) -> return Result.Ok(File(fd: fd, owned: true))
        Result.Err(e) -> return Result.Err(e.to_io())
```

After:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
fn open(string path, FileMode mode) File | IoError:
    let i32 fd = fd_open(path, mode.intent(), 420)??
    return Result.Ok(File(fd: fd, owned: true))
```

### 5.2 A program with its own error type

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
use <io/files>                       # read_dir() answers string[] | FileError

error ConfigError:
    Unreadable(FileError)
    Empty

extend FileError as ConfigError:
    return ConfigError.Unreadable(self)

fn count_configs(string dir) i32 | ConfigError:
    let string[] names = read_dir(dir)??     # a FileError becomes a ConfigError here
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

### 5.3 Two error types in one function

Before (today):

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

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
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

The two declarations are written once, beside `AppError`. Every function with
`| AppError` uses them.

### 5.4 The same function in Rust

```rust
impl From<io::Error> for AppError {
    fn from(e: io::Error) -> Self { AppError::Io(e) }
}
impl From<NetError> for AppError {
    fn from(e: NetError) -> Self { AppError::Net(e) }
}

fn notify(host: &str, msg: &str) -> Result<(), AppError> {
    let mut s = connect(host, 80)?;
    s.write_all(msg.as_bytes())?;
    Ok(())
}
```

---

## 6. Diagnostics

| Fault | Diagnostic |
|---|---|
| The `E` of a `Result` (either spelling, any position) is not an error type | One code in place of CE2084 and CE2086. The message names the type and says what it is (an enum, a struct, a wrapper). The help says to declare it with `error` when it is an enum |
| A generic instance puts a non-error type in the `E` position | The same code, at the instance, with a note at the template |
| `??` with two error types and no declaration | CE2511, as today. The help names the missing `extend <E_in> as <E_out>`. The text "not supported yet" is removed |
| `e as T` between two error types with no declaration | The existing invalid-cast error. The help names the declaration |
| A conversion in a unit that does not declare the target (C5) | New code. A note at the target's declaration |
| A source or a target that is generic or not an error type (C6) | New code |
| An identity conversion | New code |
| Two declarations of one pair | The duplicate-function error. A note at the first declaration |
| A `\| E` on a conversion | New code |
| `??` on a `Maybe` in a `\| E` body (C8) | New code, CE25xx. The help names `or_err(e)` |
| A `from` marker on a variant that has no single error-type payload (O5) | New code |
| `as` with a conversion in a `const` initializer | The existing not-a-constant-expression error |

The numbers are chosen in `internals/errors/types.py` and `internals/errors/result.py`
when the work is built. A code can only be added in the file that owns its range.

---

## 7. Implementation

### 7.1 The `error` declaration

- **Grammar.** A keyword terminal `ERROR` and a top-level `error_def` with the body of
  `enum_def`. `use_path` must still accept the word `error` (8.1).
- **AST.** The `enum` declaration node gets an `is_error` field. The AST builder sets it
  from the keyword. No second node class.
- **`collect`.** The flag goes on the `EnumType`, and on a generic enum template so that
  every instance carries it. `passes/collect/enums.py` sets it on the seven predefined
  error types (3.7).
- **The E3 check.** One predicate, "is this an error type", in the place where CE2084 and
  CE2086 are emitted today (`signature_result_arms`, `semantics/generics/results.py`), and
  one call from the written-type walk (`validate_type_name`,
  `passes/types/utils.py`) for every other position. The instance check runs where the
  monomorphizer validates type arguments.
- **Libraries.** A binary or hybrid `.slib` manifest carries the flag: one more field on
  the `enum` row in `backend/library_format.py` (`is_error`, `bool`), read by
  `semantics/library_registration.py`. A source `.slib` carries the keyword as text.
  `--lib-info` and the `slib-info` tool print `error` instead of `enum`.
- **Display.** Every message that names the declaration kind says "error type" for a
  flagged enum.

### 7.2 The conversion: front end

- **Grammar.** One more alternative of `extend_suffix`:
  `AS type ":" block -> extend_conversion`. After `EXTEND type`, the token `AS`
  selects it, as `WITH` and `STATIC` select theirs, so LALR needs no more lookahead.
- **AST.** The node is an `ExtendDef` with a reserved method name. `as` is a reserved
  word, so no user method can have that name. The target goes in the method-level type
  argument slot. The symbol is then `extension_symbol(source, "as", (target,))`, which
  already gives the `__{margs}` suffix (`semantics/generics/name_mangling.py`), and one
  source can convert to many targets without a duplicate-method clash.
- The body goes through `scope`, `typecheck`, `lift` and `borrow` as any extension body
  does, and the backend emits it as any extension. No new emitter is written for the
  body.

### 7.3 The conversion: the seam

One table, `SymbolTables.conversions`, keyed `(source, target)`, filled by the `collect`
pass. One function answers "which conversion turns `E_in` into `E_out`, or none". The
`??` check (`_error_arms_agree`, `passes/types/expressions.py`) and the cast check
(`is_valid_cast`, `passes/types/compatibility.py`) both call it. Neither one reads the
table directly. A gate scans for a second reader.

### 7.4 The conversion: the `typecheck` pass

- `validate_try_expression` asks the seam when the arms differ. On an answer, it stamps
  the conversion on the node (`TryExpr.inferred_conversion`).
- `validate_cast_expression` asks the seam for a pair of error types, and stamps the
  conversion on the `CastExpr`.
- In a generic function, the question is asked for each instance. The diagnostic points
  into the template at the instance, as for every other instance check.

### 7.5 The conversion: the backend

The propagate block of `emit_try_expr` (`backend/expressions/try_expr.py`) changes from
"extract, clean up, build `Err`, `ret`" to:

1. extract the error value;
2. call the stamped conversion with the error value (a consuming argument);
3. run the scope cleanup;
4. build the `Err` from the converted value, and `ret`.

The order is fixed. The conversion runs before the cleanup, and the extracted error is
not registered for cleanup, so it is not freed twice. `r??` on a named wrapper already
spends `r` through `ownership.unwrap` (#548). That path must hand the error to the
conversion in the same way.

### 7.6 The conversion: libraries

- A source `.slib` carries the declaration as text. Nothing changes.
- A binary or hybrid `.slib` needs one more manifest row (`conversions`: source, target,
  link symbol) in `backend/library_format.py`, read by
  `semantics/library_registration.py`. `--lib-info` and the `slib-info` tool print the
  row. The library-report gate gets a case.

### 7.7 Other passes and lints

- `--warn-unused`: a conversion is an `extend` block, so it is a root and is never
  reported.
- `--warn-missing-docs`: an `error` declaration and a conversion are declarations and
  take a doc block, as an `enum` and an extension method do. The stdlib doc gate then
  covers them.
- `effects` (the whole-program destroy summary) and every other whole-program analysis
  must see the hidden call from a `??` to its conversion as a call edge (8.5).

### 7.8 The IR

`ir.md` S5 moves every stamp the `typecheck` pass writes into `TypeckResults`. The
conversion is one more stamp, and in SHIR it is one more `Callee` (`ir.md` 7.6). The
migration ports it with the `??` lowering. The `error` flag is a property of the type
and needs nothing from the IR.

---

## 8. Pitfalls

1. **The keyword in an import path.** `use <io/error>` and `use <net/error>` spell the
   new keyword. Write a fixture that imports both modules before the keyword is added,
   and show it red if the grammar breaks it.
2. **Binary libraries built by an older compiler.** Their manifest has no `is_error`
   field, so their error types would fail E3. The compiler-version check already refuses
   a library from another compiler version unless `--ignore-compiler-version` is given.
   With that flag, a missing field reads as `false`, and the diagnostic must say to
   rebuild the library.
3. **The incremental cache.** A conversion in unit U changes the code of every `??` in a
   unit that depends on U. The unit key folds in the INTERFACE of every unit in the
   dependency closure, so the conversion must be part of U's interface, or a dependent
   unit keeps a stale `.o`. The same holds for the `is_error` flag. Write a REBUILD
   fixture (`v2/` adds the declaration; `EXPECT_REBUILT`).
4. **A generic instance.** E3 and the conversion lookup both run per instance. A
   diagnostic must point at the instance and carry a note at the template, or it reads
   as a fault in the template.
5. **A double free in the propagate block.** The error moves into the conversion. If the
   scope cleanup also frees it, the payload is freed twice. Write a leak fixture with an
   owning error before the emitter changes (`EXPECT_NO_LEAKS`).
6. **`as` binds after `??`.** `??` is postfix and binds tighter, so `r?? as AppError` is
   `(r??) as AppError`: it casts the VALUE, not the error. The CE2511 help must not
   suggest that spelling.
7. **A hidden call.** No call is written at a `??`. Every analysis that walks calls must
   see the edge: the destroy summary, any future reachability check (threads and unit
   storage), and a profile. A missing edge is a wrong answer with no diagnostic.
8. **Binary libraries and conversions.** Exported extension methods were missing from
   binary libraries until #1119. The conversion row needs a fixture that builds the
   library with `BUILD_LIB_BINARY` and `BUILD_LIB_HYBRID`, and a consumer that
   propagates through it.
9. **Constants.** A conversion is a function call. `const_eval` must refuse
   `X as AppError` with the existing not-a-constant error, not an internal error.
10. **The spelling gate.** A conversion's internal identity has angle brackets (the
    method-level argument). Every diagnostic renders it through `display_type()`, or the
    spelling gate refuses the message.
11. **One home.** `to_io()` and `as IoError` must not both exist on `FileError`. The
    migration deletes `to_io()` (section 10).

---

## 9. Order of the work

1. The `error` declaration and E3 (section 3), with its migration. It changes no
   behaviour at run time, and the conversion depends on it.
2. The fix of #1168 (4.9), after the ruling on O3.
3. The conversion (section 4), with the stdlib migration of `to_io()`.
4. `map_err` and the `from` marker, if O4 and O5 are ruled yes.

---

## 10. Migration

- **The `error` keyword.** Every written enum that is an `E` becomes `error`. In
  `src_sushi/` these are four: `UrlError` (`net/url.sushi`), `ZError`
  (`compression/zlib.sushi`), `MpError` (`encoding/msgpack.sushi`) and `SlibError`
  (`toolchain/slib.sushi`). Every test and doc enum used as an `E` changes too. The seven
  predefined types get the flag in the `collect` pass (3.7). This is a breaking change,
  and no compatibility form is kept.
- **CE2084 and CE2086.** Their fixtures move to the new code. The docs that state the
  enum rule change (`docs/error-handling.md`, `docs/language-reference.md`,
  `docs/tutorial/06-error-handling.md`).
- **The conversion.** `extend FileError to_io() IoError` becomes
  `extend FileError as IoError`, and `extend NetError to_io() IoError` becomes
  `extend NetError as IoError`. Both `to_io()` methods are deleted. The 14 conversion
  matches in `io/fs.sushi` and `net/tcp.sushi` become one `??` each. Ten sites under
  `tests/` and `docs/` call `.to_io()`; each becomes `as IoError` or a `??`.
- **C8.** Every `??` on a `Maybe` in a `| E` body becomes `.or_err(e)??`. #1168 has the
  count.
- **Docs.** `docs/error-handling.md`, `docs/tutorial/06-error-handling.md`,
  `docs/stdlib/result.md`, `docs/stdlib/maybe.md`, `docs/language-reference.md` (the
  `error` declaration, `??` and `as`), and the CE2511 doc text.

---

## 11. Tests

Test-first, under `tests/types/result/error_types/` and
`tests/types/result/error_conversion/`. Each batch is shown red before any code changes.

| Fixture | Asserts |
|---|---|
| an `error` declaration used as an `E`, matched and printed | `EXPECT_STDOUT_EXACT` |
| an `error` type as a field, a payload and a `List@(E)` element | `EXPECT_STDOUT_EXACT` |
| a plain `enum`, a struct, `Maybe` and `Result` as an `E`, in each spelling | `test_err_`, the new code, `EXPECT_ERROR_CODES_EXACT` |
| a non-error `E` in a `let`, a field, a payload and a generic argument | `test_err_`, the new code |
| a generic function instantiated with a non-error `E` | `test_err_`, the new code with a note at the template |
| `use <io/error>` and `use <net/error>` with the keyword present | the program builds |
| an `error` type from a binary and from a hybrid library | `BUILD_LIB_BINARY`, `BUILD_LIB_HYBRID`, a consumer |
| `--lib-info` prints `error` | the library-report gate |
| `??` through a declared conversion | `EXPECT_STDOUT_EXACT` on the converted variant |
| `as` through a declared conversion | the same, with no `??` |
| an owning error (a `string` payload) through `??` | `EXPECT_NO_LEAKS`, at `--opt none` and at `--opt O2` |
| `r??` on a named wrapper with a conversion | `EXPECT_NO_LEAKS` |
| a conversion in a lambda with a channel, and in `foreach(x?? in ...)` | `EXPECT_STDOUT_EXACT` |
| a conversion in a generic function instance | `EXPECT_STDOUT_EXACT` |
| a chain is not followed | `test_err_`, CE2511 |
| each refusal in section 6 | `test_err_`, one fixture per code |
| a conversion behind an alias | `EXPECT_STDOUT_EXACT` |
| a conversion from a binary and from a hybrid library | `BUILD_LIB_BINARY`, `BUILD_LIB_HYBRID`, a consumer |
| adding a conversion, or the flag, rebuilds the dependent unit | REBUILD form, `EXPECT_REBUILT` |

---

## 12. Rejected alternatives

### 12.1 An `Error` perk

A perk that an error type implements (Swift's `: Error`, Go's `Error() string`). Explicit,
it puts `extend X with Error:` on every error type. Derived for every enum and struct,
it marks nothing. The `error` keyword says the fact once, where the type is declared.

### 12.2 A separate type kind for `error`

Every enum seam would need a second handler. The flag gives the same language for none
of that cost (3.2).

### 12.3 The keyword `err`

It takes the `.err()` method name away, collides with the usual variable name, and
differs from the variant `Err` by case only (3.6).

### 12.4 The rule on channels only

`let Result@(i32, Color) r` would stay legal: a `Result` that no channel can propagate.
The rule covers every position (3.4).

### 12.5 The rule at the interning seam

No source location, compiler-made `Result` types, and the order of the `collect` pass
(3.5).

### 12.6 A predefined perk `From@(E)` for the conversion

This is Rust's shape. It needs a perk with a type parameter (CE4010), a static method in
a perk (CE4014), and more than one implementation of one perk on one type. Each one is
a language change.

### 12.7 Structural error unions, and one dynamic error type

An anonymous union type is a large change to nominal type identity, and the Zig form
loses the payload. A dynamic error type needs a dynamic perk object, and it loses the
exhaustive `match` over the error.

### 12.8 A binary `r ?? conv`

Readers of five major languages read a binary `??` as "use this default value". The
meaning here would be different.

### 12.9 Transitive conversion

A chain makes the lookup a path search. Two paths could give two answers, and the reader
cannot find the conversion in one place.

### 12.10 Implicit conversion outside `??`

A conversion at an assignment, an argument or a `return` is a conversion that no written
mark shows. Only `??` and `as` mark the site.

---

## 13. Open questions

| # | Question | Proposal |
|---|---|---|
| **O1** | Is `extend X as Y:` the syntax of a conversion? | Yes (C1, C2) |
| **O2** | Who may declare a conversion: the target's unit only, or either type's unit? | The target's unit only (4.5) |
| **O3** | `??` on a `Maybe`: refuse it and add `or_err`, or keep it for `StdError` only? | Refuse it, and add `or_err` (4.9, #1168) |
| **O4** | Does `map_err` ship with the first version? | Yes, as a library addition (4.10) |
| **O5** | Does a variant of an `error` declaration get a `from` marker that declares the wrapping conversion? | Not in the first version. The one-line body is enough (4.11) |
