# Error conversion at `??`

Status: PROPOSED (2026-10-04). Nothing in this document is built. The decisions in the
table are proposals until David rules on them. Section 11 lists the open questions.

## The decisions, in one place

| # | Decision | Status | Where |
|---|---|---|---|
| **C1** | A conversion is a DECLARATION: `extend FileError as IoError:` with a block body | Proposed | 3.1 |
| **C2** | `e as IoError` is the explicit use. `as` is the one conversion operator | Proposed | 3.2 |
| **C3** | `??` applies a declared conversion when the two error types differ | Proposed | 3.3 |
| **C4** | One step only. A chain of conversions is never followed | Proposed | 3.4 |
| **C5** | Only the unit that declares the TARGET type may declare a conversion into it | Open (O2) | 3.5 |
| **C6** | The source and the target are both non-generic enums | Proposed | 3.6 |
| **C7** | The body is bare, and it consumes `self` | Proposed | 3.7 |
| **C8** | `??` on a `Maybe` in a `\| E` body is refused, and `or_err(e)` is the escape (#1168) | Open (O3) | 3.9 |

---

## 1. The problem

### 1.1 The rule today

`??` propagates an error only when the two error types are the same type. In a body with
`| IoError`, a `??` on a `Result@(T, FileError)` is CE2511:

```
error type mismatch in propagation: cannot propagate Result@(i32, FileError) to
function returning Result@(i32, IoError)
```

The CE2511 doc text ends with "Error type conversion is not supported yet."

### 1.2 What the rule costs

A function that calls two modules with two error enums cannot use `??` on both. Every
call to the other module needs a `match`, and `match` is a statement, so each such call
nests one level deeper. Measured at `82d6bf94`:

- The stdlib has 14 three-line conversion matches: 10 in `io/fs.sushi` and 4 in
  `net/tcp.sushi`. Each one is
  `match f(): Result.Ok(x) -> ... / Result.Err(e) -> return Result.Err(e.to_io())`.
- Two hand-written conversion methods exist: `extend FileError to_io() IoError` in
  `io/error.sushi` and `extend NetError to_io() IoError` in `net/error.sushi`.
- A user program has no shorter route. Each program writes the same `match` for each
  call.

### 1.3 A defect in the same place

`??` on a `Maybe` that holds `None`, in a `| E` body, returns `Result.Err` with an
undefined `E` (#1168). The propagate block builds the `Err` from `undef` and writes only
the tag, so the answer changes with `--opt`. This document gives the conversion rule for
that case too (3.9), because a `None` under `??` is also a conversion: from "no value"
to an `E`.

---

## 2. What other languages do

| Family | Languages | Mechanism | Why it does or does not fit Sushi |
|---|---|---|---|
| **1. A declared conversion, applied at the propagation site** | Rust: `?` calls `From::from(e)`. The `thiserror` crate generates the `impl` from `#[from]` | One declaration per (source, target) pair. `?` applies it | **Fits**, with a dedicated declaration. A perk cannot carry a type parameter (CE4010), so `From@(E)` is not writable |
| **2. Structural error unions** | Zig (error sets coerce to a superset), Roc (tag unions), OCaml (polymorphic variants) | The error type grows by itself. No conversion code | **Does not fit.** Type identity is nominal (`type-identity.md`), and Zig errors carry no payload |
| **3. Explicit mapping at each site** | Go (`%w`), Gleam (`result.map_error`), Haskell (`withExceptT`), Swift typed throws, C++ `std::expected` | Each site writes the conversion | **Fits as a supplement** (`map_err`, 3.10). As the only answer it is what Sushi has today |
| **4. One dynamic error type** | Rust `Box<dyn Error>` and `anyhow`, Go `error`, Swift `any Error`, Java and C# exceptions | Every error converts to one top type | **Does not fit.** Sushi has no dynamic perk object, and a top type loses the exhaustive `match` |

Two lessons:

- **Rust refuses `?` on an `Option` in a function that returns a `Result`.** The escape
  is `.ok_or(e)?`, so the program always writes the error value. Sushi takes this rule
  (3.9).
- **In C#, JS, Swift, PHP and Kotlin, a binary `??` or `?:` means "use this default".**
  A Sushi `r ?? f` would read as that operator and mean something else. Sushi does not
  use it (10.4).

---

## 3. The design

### 3.1 The declaration

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

### 3.2 The explicit use: `as`

A declared conversion is a user-defined `as`. `e as IoError` calls it. Today `as` casts
between numeric types only (`is_valid_cast`, `passes/types/compatibility.py`). With this
design, `as` also accepts a pair of enums that has a declaration. Every other enum pair
stays an invalid cast.

`as` is already the explicit conversion operator of the language. A second operator or
a method name would be one more spelling of the same idea.

### 3.3 The implicit use: `??`

At a `??` on a `Result@(T, E_in)` in a body whose channel is `E_out`:

1. If `E_in` is `E_out`, `??` propagates the error as it does today.
2. If a conversion `E_in as E_out` is declared, `??` calls it on the error and
   propagates the result.
3. In all other cases, the `??` is CE2511. The help names the declaration to write.

The rule "Sushi converts nothing on its own" stays true. A conversion happens only at
a written `??` or a written `as`, and only through a declaration the program wrote.

The same rule applies everywhere a `??` occurs: a function body, a method body, a
lambda with a channel, and the `foreach(x?? in ...)` binder, which the AST builder
expands to an ordinary `??`.

### 3.4 One step only

`FileError as IoError` and `IoError as AppError` do NOT give `FileError` to `AppError`.
The lookup is an exact match on the pair, so it can never be ambiguous, and the reader
finds the conversion in one place. A program that wants the two-step conversion
declares it.

An identity conversion (`extend IoError as IoError`) is refused. Two declarations of
one pair are the duplicate-function error.

### 3.5 Who may declare a conversion (open: O2)

**Proposal: only the unit that declares the TARGET type.** This is the `Drop` rule ("only
the unit that declares a type may implement it").

- Each pair has at most one declaration in the whole program, because the target's
  unit is unique. Two libraries cannot both declare `FileError as AppError`.
- It fits the use case. An application's `AppError` lists what it absorbs, as
  thiserror's `#[from]` does on the target enum.
- It fits the stdlib. `FileError` and `IoError` are both in `io/error.sushi`.
- The cost: a library cannot convert its own error into `IoError`, because the stdlib
  owns `IoError`. The library declares its own error enum and a conversion of
  `IoError` into it.

The alternative is "the unit of either type", as Rust's orphan rule allows. Then two
units can declare one pair, and the analysis must find the clash across units and
across libraries. That is more machinery for a case that has a good escape.

### 3.6 Which types

The source and the target are both enums, because an error type is an enum (`T | E` and
`Result@(T, E)` both refuse anything else). Both are non-generic: a generic enum, or an
instance of one (`ParseError@(i32)`), is refused as a source and as a target. This keeps
the lookup a pair of names and the binary manifest a pair of strings. A real need can
lift it later.

A generic FUNCTION is a different case, and it is allowed. In `fn f@(E)(...) T | E`, the
pair is known for each instance only, because generics are templates. The conversion is
looked up when the instance is checked (6.3).

### 3.7 Ownership

- The body consumes `self`. The receiver mode is not written, and it is `nom`. `??`
  spends the error in any case, so a borrow would force a deep copy of the payload.
- The body is bare. A `| E` on the declaration is refused: a conversion that can fail
  would need a second channel at every `??`. A `??` in the body is therefore CE0131,
  as in every bare body.
- An error enum that owns a resource (a `string` payload) moves into the conversion. The
  conversion destroys what it does not put in the target.

### 3.8 Visibility

A conversion is found by its PAIR OF TYPES, never by a name, so an import never brings
or hides it. A conversion is as visible as its target type, the extension rule. A
function that has `| AppError` in its signature already names `AppError`, so it can see
every conversion into it.

Behind an alias, the declaration spells the qualified names:
`extend fs.FileError as AppError:`.

A private target makes the conversion usable in its own unit only. A public target with
a private source cannot exist: the target's unit must name the source, and the leak rule
applies to the declaration as it does to a public signature.

### 3.9 `??` on a `Maybe` (open: O3, #1168)

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

### 3.10 `map_err` (open: O4)

A conversion used at one site only does not need a declaration. The library form is:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
let File f = open(path, FileMode.Read()).map_err(|IoError e| AppError.Config(e))??
```

`map_err` is an extension with a method-level type parameter, on the `ufcs-combinators.md`
model. It does not exist today. A bare lambda parameter cannot be inferred (a known
inference gap), so the parameter type must be written.

---

## 4. Examples

### 4.1 The stdlib: `open()`

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

### 4.2 A program with two error types

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

### 4.3 The same function in Rust

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

## 5. Diagnostics

| Fault | Diagnostic |
|---|---|
| `??` with two error types and no declaration | CE2511, as today. The help names the missing `extend <E_in> as <E_out>`. The text "not supported yet" is removed |
| `e as T` between two enums with no declaration | The existing invalid-cast error. The help names the declaration |
| A conversion in a unit that does not declare the target (C5) | New code, CE2xxx. A note at the target's declaration |
| A generic source or target (C6) | New code, CE2xxx |
| A source or a target that is not an enum (C6) | New code, CE2xxx (the same code as above, with the type in the message) |
| An identity conversion | New code, CE2xxx |
| Two declarations of one pair | The duplicate-function error. A note at the first declaration |
| A `\| E` on the declaration | New code, or the existing channel-on-`drop()` shape (one fault, its own code) |
| `??` on a `Maybe` in a `\| E` body (C8) | New code, CE25xx. The help names `or_err(e)` |
| `as` with a conversion in a `const` initializer | The existing not-a-constant-expression error |

The numbers are chosen in `internals/errors/types.py` and `internals/errors/result.py`
when the work is built. A code can only be added in the file that owns its range.

---

## 6. Implementation

### 6.1 Front end

- **Grammar.** One more alternative of `extend_suffix`:
  `AS type ":" block -> extend_conversion`. After `EXTEND type`, the token `AS`
  selects it, as `WITH` and `STATIC` select theirs, so LALR needs no more lookahead.
- **AST.** The node is an `ExtendDef` with a reserved method name. `as` is a reserved
  word, so no user method can have that name. The target goes in the method-level type
  argument slot. The symbol is then `extension_symbol(source, "as", (target,))`, which
  already gives the `__{margs}` suffix (`semantics/generics/name_mangling.py`), and one
  source can convert to many targets without a duplicate-method clash.
- The reuse is deliberate. The body goes through `scope`, `typecheck`, `lift` and `borrow`
  as any extension body does, and the backend emits it as any extension. No new emitter
  is written for the body.

### 6.2 The seam

One table, `SymbolTables.conversions`, keyed `(source, target)`, filled by the `collect`
pass. One function answers "which conversion turns `E_in` into `E_out`, or none". The
`??` check (`_error_arms_agree`, `passes/types/expressions.py`) and the cast check
(`is_valid_cast`, `passes/types/compatibility.py`) both call it. Neither one reads the
table directly. A gate scans for a second reader.

### 6.3 The `typecheck` pass

- `validate_try_expression` asks the seam when the arms differ. On an answer, it stamps
  the conversion on the node (`TryExpr.inferred_conversion`).
- `validate_cast_expression` asks the seam for an enum pair, and stamps the conversion
  on the `CastExpr`.
- In a generic function, the question is asked for each instance. The diagnostic points
  into the template at the instance, as for every other instance check.

### 6.4 The backend

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

### 6.5 Libraries

- A source `.slib` carries the declaration as text. Nothing changes.
- A binary or hybrid `.slib` needs one more manifest row (`conversions`: source, target,
  link symbol) in `backend/library_format.py`, read by
  `semantics/library_registration.py`. `--lib-info` and the `slib-info` tool print the
  row. The library-report gate gets a case.

### 6.6 Other passes and lints

- `--warn-unused`: a conversion is an `extend` block, so it is a root and is never
  reported.
- `--warn-missing-docs`: a conversion is a declaration and takes a doc block, as an
  extension method does. The stdlib doc gate then covers the stdlib conversions.
- `effects` (the whole-program destroy summary) and every other whole-program analysis
  must see the hidden call from a `??` to its conversion as a call edge (7.4).

### 6.7 The IR

`ir.md` S5 moves every stamp the `typecheck` pass writes into `TypeckResults`. The
conversion is one more stamp, and in SHIR it is one more `Callee` (`ir.md` 7.6). The
migration ports it with the `??` lowering. Building it before the IR costs one stamp and
one change to the propagate block.

---

## 7. Pitfalls

1. **The incremental cache.** A conversion in unit U changes the code of every `??` in a
   unit that depends on U. The unit key folds in the INTERFACE of every unit in the
   dependency closure. The conversion must be part of U's interface, or a dependent
   unit keeps a stale `.o` that propagates without the conversion. Write a REBUILD
   fixture (`v2/` adds the declaration; `EXPECT_REBUILT`).
2. **A double free in the propagate block.** The error moves into the conversion. If the
   scope cleanup also frees it, the payload is freed twice. Write a leak fixture with an
   owning error before the emitter changes (`EXPECT_NO_LEAKS`).
3. **`as` binds after `??`.** `??` is postfix and binds tighter, so `r?? as AppError` is
   `(r??) as AppError`: it casts the VALUE, not the error. The CE2511 help must not
   suggest that spelling.
4. **A hidden call.** No call is written at a `??`. Every analysis that walks calls must
   see the edge: the destroy summary, any future reachability check (threads and unit
   storage), and a profile. A missing edge is a wrong answer with no diagnostic.
5. **Binary libraries.** Exported extension methods were missing from binary libraries
   until #1119. The conversion row needs a fixture that builds the library with
   `BUILD_LIB_BINARY` and `BUILD_LIB_HYBRID`, and a consumer that propagates through it.
6. **Constants.** A conversion is a function call. `const_eval` must refuse
   `X as AppError` with the existing not-a-constant error, not an internal error. The
   `const_eval` dispatch gate holds `CastExpr` already. The new branch is inside it.
7. **The spelling gate.** A conversion's internal identity has angle brackets (the
   method-level argument). Every diagnostic renders it through `display_type()`, or the
   spelling gate refuses the message.
8. **One home.** `to_io()` and `as IoError` must not both exist on `FileError`. The
   migration deletes `to_io()` (section 8).

---

## 8. Migration

- `extend FileError to_io() IoError` becomes `extend FileError as IoError`, and
  `extend NetError to_io() IoError` becomes `extend NetError as IoError`. Both `to_io()`
  methods are deleted. This is a breaking change, and no compatibility form is kept.
- The 14 conversion matches in `io/fs.sushi` and `net/tcp.sushi` become one `??` each.
- Ten sites under `tests/` and `docs/` call `.to_io()`. Each becomes `as IoError` or a
  `??`.
- With C8, every `??` on a `Maybe` in a `| E` body becomes `.or_err(e)??`. #1168 has the
  count.
- The docs to change: `docs/error-handling.md`, `docs/tutorial/06-error-handling.md`,
  `docs/stdlib/result.md`, `docs/stdlib/maybe.md`, `docs/language-reference.md` (the
  `??` and `as` sections), and the CE2511 doc text.

---

## 9. Tests

Test-first, under `tests/types/result/error_conversion/`. The batch is shown red before
any code changes.

| Fixture | Asserts |
|---|---|
| `??` through a declared conversion | `EXPECT_STDOUT_EXACT` on the converted variant |
| `as` through a declared conversion | the same, with no `??` |
| an owning error (a `string` payload) through `??` | `EXPECT_NO_LEAKS`, at `--opt none` and at `--opt O2` |
| `r??` on a named wrapper with a conversion | `EXPECT_NO_LEAKS` |
| a conversion in a lambda with a channel, and in `foreach(x?? in ...)` | `EXPECT_STDOUT_EXACT` |
| a conversion in a generic function instance | `EXPECT_STDOUT_EXACT` |
| a chain is not followed | `test_err_`, CE2511 |
| each refusal in section 5 | `test_err_`, one fixture per code |
| a conversion behind an alias | `EXPECT_STDOUT_EXACT` |
| a conversion from a binary and from a hybrid library | `BUILD_LIB_BINARY`, `BUILD_LIB_HYBRID`, a consumer |
| adding a conversion rebuilds the dependent unit | REBUILD form, `EXPECT_REBUILT` |
| `--lib-info` prints the conversion | the library-report gate |

---

## 10. Rejected alternatives

### 10.1 A predefined perk `From@(E)`

This is Rust's shape. It needs a perk with a type parameter (CE4010), a static method in
a perk (CE4014), and more than one implementation of one perk on one type. Each one is
a language change. The predefined `Eq` and `Ord` name their receiver through a compiler
placeholder, and that placeholder does not give a type parameter.

### 10.2 Structural error unions

An anonymous union type is a large change to nominal type identity. The Zig form also
loses the payload.

### 10.3 One dynamic error type

Sushi has no dynamic perk object, and a top type loses the exhaustive `match` over the
error.

### 10.4 A binary `r ?? conv`

Readers of five major languages read a binary `??` as "use this default value". The
meaning here would be different.

### 10.5 Transitive conversion

A chain makes the lookup a path search. Two paths could give two answers, and the reader
cannot find the conversion in one place.

### 10.6 Implicit conversion outside `??`

A conversion at an assignment, an argument or a `return` is a conversion that no written
mark shows. Only `??` and `as` mark the site.

---

## 11. Open questions

| # | Question | Proposal |
|---|---|---|
| **O1** | Is `extend X as Y:` the syntax? | Yes (C1, C2) |
| **O2** | Who may declare a conversion: the target's unit only, or either type's unit? | The target's unit only (3.5) |
| **O3** | `??` on a `Maybe`: refuse it and add `or_err`, or keep it for `StdError` only? | Refuse it, and add `or_err` (3.9, #1168) |
| **O4** | Does `map_err` ship with the first version? | Yes, as a library addition (3.10) |
| **O5** | Does a variant get a marker that declares the wrapping conversion (`Io(IoError) from`)? | Not in the first version. The one-line body is enough |
