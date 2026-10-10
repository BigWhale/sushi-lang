# `dont_panic`: an unchecked index inside a marked function

Status: BUILT (#1247). The rulings are David's (2026-10-09). The measurements are
from commit `6deb42e3` on `darwin_arm64`. The code anchors are at `5760e925`; the
build choices of section 11 are the state after the build.

## Summary

- A function header may carry the marker `dont_panic because "<reason>"`.
- Inside that body, `a[i]` on an array and `s[i]` on a string emit no bounds check. A read
  and a write alike. Nothing else changes.
- The marker is legal in a bundled stdlib unit, and in any unit of a build that passes
  `--dont-panic`. Everywhere else it is an error.
- A source library that uses the marker needs the consent of each consumer, the same flag.
  So does a marked generic template of any library kind.
- A marker that removes no check is a warning (CW0004).
- The stdlib names each marked function in one ratchet, with its reason and its benchmark.

## 1. The problem

The stdlib moves from IR written by hand in Python to Sushi source. The hand-written IR of
`string.contains` reads each byte with `gep` and `load` and no check. Its author proved the
range from the three loop guards and left the check out. The same search in Sushi source
writes the same three guards, but the compiler applies one rule to every `[]`: emit the
check. LLVM removes the check of a one-counter loop, `while (k < n)` with `s[k]`. It does
not remove the check of `hay[i + j]`, because the proof needs two facts and the `add` has
no `nsw` flag.

The cost, 3,000,000 calls of a naive search over a 64-byte string, minimum of 7 runs:

| Build at `--opt O2` | Time |
|---|---|
| The hand-written IR, `string.contains` today | 87 ms |
| The same algorithm in Sushi source, checks kept | 206 ms |
| The same Sushi IR, checks removed by hand | 81 ms |

With the checks removed, Sushi source matches the hand-written IR. The whole measured loss
is the checks.

## 2. What other languages do

Every language with a mandatory index check ends with three tools, and the stdlib of each
one leans on the first two.

1. **An escape hatch.** Rust `unsafe { get_unchecked(i) }`, Julia `@inbounds`, Zig
   `@setRuntimeSafety(false)`, Swift `UnsafeBufferPointer`, Nim `{.push boundChecks: off.}`.
   The author takes the proof. Rust's std requires a `SAFETY` comment on each block.
2. **An API shape that indexes nothing.** An iterator walks a pointer. A slice compare calls
   `memcmp`. The check is not removed. It never exists.
3. **Range analysis in the compiler.** Go's `prove` pass, the JVM and .NET JITs, LLVM's
   induction-variable, correlated-value and constraint passes. It serves every line, user
   code and stdlib alike. It is best-effort: it proves the simple shapes and leaves the rest.
   No stdlib relies on it alone for its hot loops.

`dont_panic` is tool 1. Range analysis for Sushi is tool 3, and it is the epic #1244. The
two do not depend on each other. Rust has both.

## 3. The rulings

| # | Ruling | Section |
|---|---|---|
| **D1** | The marker is on the function header: `dont_panic because "<reason>"`. The `because` is required | 4 |
| **D2** | The marker removes the bounds check of `[]` on an array and on a string, and nothing else | 5.1 |
| **D3** | A read and a write are both unchecked | 5.1 |
| **D4** | Every named declaration with a body may carry it: a free function, a static, an extension method, a perk implementation method, a conversion. Not a lambda, not a perk contract method, not an extern | 5.2 |
| **D5** | The marker stops at a lambda body. A lambda inside a marked function is checked | 5.3 |
| **D6** | The marker does not propagate. A callee is checked. An instance of a marked template is marked, because it is the same body | 5.3 |
| **D7** | The marker is legal in a bundled stdlib unit without a flag | 6.1 |
| **D8** | The marker is legal in any unit of a build that passes `--dont-panic`. Without the flag, a user unit that writes it gets an error | 6.2 |
| **D9** | A source library that writes the marker needs the consumer's `--dont-panic`. The error names the library and the flag. A concrete marked body of a binary or a hybrid library needs no consent. A marked generic template of any library kind needs it | 6.3 |
| **D10** | Every `.slib` kind records its marked functions with their reasons. `--lib-info` lists them | 7 |
| **D11** | A build with `--dont-panic` prints no list of what it accepted | 7 |
| **D12** | A marked function that holds no index to uncheck is a warning, always on | 8.1 |
| **D13** | `--dont-panic` on a build that marks no user function gets the no-effect warning | 8.2 |
| **D14** | The stdlib names each marked function in one ratchet, with its reason and its benchmark | 9.2 |
| **D15** | The name is `dont_panic`. The flag is `--dont-panic` | 4 |
| **D16** | A global switch that removes every check is a different feature and is not ruled here | 10 |
| **D17** | Omakase shows "needs `--dont-panic`" on the package page. That is a follow-up in the registry repositories | 9.3 |

## 4. The shape

```sushi
public fn contains_naive(string hay, string needle) bool dont_panic because "i + m <= n and j < m hold i + j < n":
    let i32 n = hay.size()
    let i32 m = needle.size()
    let i32 i = 0
    while (i + m <= n):
        let i32 j = 0
        while (j < m and hay[i + j] == needle[j]):
            j := j + 1
        if (j == m):
            return true
        i := i + 1
    return false
```

The marker follows the return type and the error channel, before the colon. The `because`
text is the proof: it states the guards that hold every index in range. It is required
(D1), where `unsafe external` makes it optional, because the text is the one record of why
the check may go.

The check is what traps. The marker says there is no need for one. Hence the name (D15).

## 5. Semantics

### 5.1 What the marker removes (D2, D3)

One thing: the bounds check that `a[i]` and `s[i]` emit. A fixed array, a dynamic array
and a string alike. A read and an indexed assignment alike, because a copy loop writes as
often as it reads, and the two go through one emitter.

Everything else stays. `get(i)` still answers `Maybe`. `insert(i, v)` still checks its
position. `assert` still traps. Division by a zero still traps. The borrow pass, the
overflow rules and every diagnostic are unchanged. The marker unlocks one operation, as
Rust's `unsafe` unlocks five and changes nothing else.

An index that is out of range inside a marked body is undefined behaviour. That is the
contract the `because` signs.

### 5.2 Where the marker may stand (D4)

On every named declaration that has a body. A perk contract method has no body. An extern
has no body. A lambda is refused (D5 says why). `main` is a named function with a body and
is not excluded; the gate (section 6) decides whether a user may write it there.

### 5.3 The marker does not escape (D5, D6)

The stamp is on the index nodes of one body. A function that a marked function calls has
its own nodes, and they are not stamped. If LLVM inlines a marked function into its caller,
it copies IR that has no check for the callee's indexes, and the caller's indexes keep
theirs. The IR is fixed before LLVM runs.

A generic template that is marked gives marked instances. An instance is the same body in
its home unit. The unrolled copies of an `expand` share the stamp for the same reason.

The one hole is a lambda. The `lift` pass makes it a function of its own, and it may leave
the marked function as a value and run after that function has returned. So the stamp stops
at a lambda body (D5): a lambda inside a marked function is checked as usual. A marked
function that holds its only indexes inside a lambda gets the warning of section 8.1.

## 6. Who may write it

### 6.1 The bundled stdlib (D7)

A bundled stdlib unit may write the marker with no flag. The predicate exists: a unit that
is not from a library and whose name is in `SOURCE_STDLIB_MODULES`
(`semantics/semantic_analyzer.py:61-65` and again at line 74). The build gives it one name
and calls it from a third place.

### 6.2 A user unit (D8)

A user unit may write the marker when the build passes `--dont-panic`. The flag is one
field on `BuildOptions` (`compiler/options.py:13`). Without it, the marker is an error at
the header, once per function, with the flag in the help.

### 6.3 A library (D9)

The consumer's build parses a source library again. Thus the marker of a source library
meets the gate again in each consumer. The consumer must also pass the flag. The flag is
the consent of the consumer, because the consumer compiles the unchecked code. The error
names the library, so the consumer knows which code asks for the flag. A library author
who uses the marker must state this in the documentation of the library.

A binary library and a hybrid library have this case only for a marked generic template:

- A CONCRETE marked body is in the bitcode that the library ships. The consumer links
  that bitcode, as it links a C library. The consumer does not need to consent.
- A marked generic TEMPLATE ships as source in all library kinds. The consumer makes the
  copies of the template, so the consumer compiles that source. The consumer must pass
  the flag.

The first draft of this section said that a binary library has no such case. That
statement was wrong for a template.

## 7. The manifest and `--lib-info` (D10, D11)

Every `.slib` kind records its marked functions, each with its `because`: a source library
as text, a binary or hybrid library as a manifest record beside `reexports` and
`conversions` (`backend/library_manifest.py`). `--lib-info` lists them in both halves, the
Python fallback (`compiler/lib_info.py`) and the `slib-info` tool (`toolchain/src/`). A
reviewer sees the unchecked code and its proof before the first build.

A build that passes `--dont-panic` prints nothing about what it accepted (D11). The
compiler has no informational tier, a warning would make the exit status 1 on every such
build, and the flag itself is the per-build reminder.

## 8. The warnings

### 8.1 A marker that removes nothing (D12)

A marked function that holds no `[]` to uncheck gets a warning. Always on, not behind a lint
flag, because an inert marker is a fault: the author believed a check was removed, and none
was. The indexes inside a lambda do not count (D5). When #1244 lands, the same code gains a
second message, "the compiler proves every index here", so that the stdlib can remove
markers as the compiler grows, and the number of marked functions goes down over time.

### 8.2 A flag with no effect (D13)

`--dont-panic` on a build that compiles no marked user function gets the no-effect warning,
as every other flag with no effect does. The stdlib's own marked functions do not count,
because they need no flag. A build that has an error does not get the warning. A
`--lib-info` run compiles no function, so the flag gets the same warning there.

## 9. The brakes

The marker must stay rare. The consent (D9) is the strongest brake and costs nothing: a
library that marks a function makes every consumer type a flag. The others:

### 9.1 The guideline

A marked function is a measured hot loop and nothing else. The measurement is in the pull
request, before and after. The `because` states the guards that hold every index in range.
The function is as small as the loop. A function that is faster only on paper is not
marked. A library author who marks a function says so in the library's documentation.

### 9.2 The stdlib ratchet (D14)

A text scan of `src_sushi/` in pytest names each marked function with its reason and the
benchmark that justified it, as `vulture_whitelist.py` names each name live by reflection.
A new marked function fails CI until it is named. The scan reads text, not the compiler.

### 9.3 Omakase (D17)

The package record carries "needs `--dont-panic`", and the package page shows it. The
compiler's part is the manifest record of section 7. The registry's part is a follow-up in
the `sushi-omakase-api` and `sushi-omakase-front` repositories.

## 10. Out of scope (D16)

- A global switch that removes every check in a build, as Zig's `ReleaseFast` does. It
  changes what a correct program means, from a runtime error to undefined behaviour, and
  it makes the marker pointless when it is on. It is a separate ruling, if ever.
- An unchecked index spelling or method, `a.at_unchecked(i)`. The header marker was chosen
  instead (D1). The Rust shape, a checked `[]` everywhere plus a separate unchecked
  operation, stays available as a later addition if the header form proves too coarse.
- A marker on a lambda (D5), on a perk contract method, on an extern (D4).
- A size limit on a marked function. The number would be arbitrary, and the ratchet (9.2)
  reviews each function at the one place a reviewer reads.

## 11. Build choices

- **The stamp.** The stamp is one `bool` field on `IndexAccess`, `unchecked`, beside
  `reads_a_string_byte` (`semantics/ast.py`). The AST builder sets it on each index node
  in a marked function. It stops at a `Lambda`. The monomorphizer copies the node with
  `copy.copy` (`generics/monomorphize/transformer.py`), so an instance keeps the stamp.
  The consumer parses the text of a source library again, so the stamp is set again there.
- **The marker.** The marker is one field on the function node: a `DontPanicMarker`
  record that holds the `because` text and the span (`semantics/ast.py`). `FuncDef`,
  `ExtendDef` and the generic function record have this field. None means no marker.
- **The gate.** The `collect` pass reads the marker one time, at the header
  (`passes/collect/dont_panic.py`). Thus a function gets one diagnostic, and not one for
  each index. The gate has one new code in `internals/errors/func.py`. For a library
  unit, the message names the library.
- **The name.** `marked_name` (`passes/collect/dont_panic.py`) gives the name of a marked
  declaration. The manifest record uses it. The diagnostics of the marker use it for a
  conversion. The walk over the declarations that have a body gives the perk
  implementation of each method (`ast_walk.bodied_with_owner`). The manifest reads the
  target type from it.
- **The backend.** Two sites do not call `emit_bounds_check`
  (`backend/types/arrays/bounds.py`) when the stamp is set. One site is the string byte
  read. The other site is the array element pointer, which the indexed assignment also
  uses. Both sites are in `backend/types/arrays/indexing.py`.
- **The warnings.** The inert marker has one new code in `internals/errors/warnings.py`.
  The no-effect warning uses the path of the other flags.
- **The cache.** The source of a unit is in its cache key. Thus a change to a marker
  builds the unit again. No change is necessary.
- **The fixtures.** The fixtures are in `tests/array/dont_panic/`,
  `tests/array/dont_panic_pins/` and `tests/strings/dont_panic/`. They pass
  `COMPILER_FLAGS: --dont-panic`. They cover these cases: the values of a marked search,
  a marked write and a marked generic instance; a lambda in a marked body that still
  stops with RE2020; the refusal in a user unit without the flag, one fixture for each
  position; the refusal of the function of a source library in a consumer without the
  flag; the inert-marker warning; and the no-effect warning.
- **The removal of the check.** The runner directives `EXPECT_IR_LACKS` and
  `EXPECT_IR_HOLDS` read the IR of one function (`tests/TEST_METADATA_GUIDE.md`). One
  fixture marks a function and keeps an unmarked twin for each kind of index. The fixture
  asserts that the marked body has no `_bounds_fail` block and that the twin has one.
- **The codes.** `CE0152` is the gate (`internals/errors/func.py`). `CE6111` is the
  marker in a position that takes none: a lambda (the expression form and the block
  form), a perk contract method or an extern. The message names the position
  (`syntax.py`). `CW0004` is the inert marker. `CW0003` also applies to `--dont-panic`,
  "on a build that marks no function". A missing `because` is the general parse error
  `CE6001`, because the grammar requires the word.
- **Who gets the inert warning.** `CW0004` is only for the author: a user unit and a
  bundled stdlib unit. A unit of a source library in the build of a consumer gets no
  warning, because the consumer cannot change the library.
- **The marker words.** `dont_panic` is a reserved word (the terminal `DONT_PANIC`). The
  grammar accepts the marker in the three refused positions. It does this only so that
  the AST builder can name the position.
- **The consent.** The marker of a source library needs the `--dont-panic` of the
  consumer. A concrete marked body of a binary or hybrid library does not need it. A
  marked generic template of each library kind needs it (section 6.3). The library
  author also builds the library with `--dont-panic`. For a test, the fixture directive
  `LIB_FLAGS` states this.
- **The manifest.** The key `dont_panic` is a list of `{unit, name, reason}` records. All
  library kinds have the key, and it is absent when the list is empty. The
  `sushi_lib_version` does not change, because an older reader ignores a key that it does
  not know. The `name` has three forms. A function or a template has its name. An
  extension method, a static and a perk implementation method have `Type.method`, with
  the type in the `@(...)` spelling. A conversion has `Source as Target`. A consumer does
  not read the key. Only `--lib-info` reads it (`docs/library-format.md`).
- **`--lib-info`.** The section "Unchecked Indexes" has one line for each record:
  `  {unit}: {name} dont_panic because "{reason}"`. The unit comes first, because two units
  of one library can each declare a private `fn head`. Without the unit, the two lines are
  the same. The reason is the raw text. Both halves print
  the section: `compiler/lib_info.py` and `toolchain/src/slib_info.sushi`.
- **The lambda.** A lambda in a marked body keeps its checks. A measurement of the
  backend at `--opt O2` with `--dont-panic` shows this. The marked search function has no
  bounds-fail block. The lambda in a marked body keeps its block.
- **The ratchet.** `tests/unit/test_dont_panic_ratchet.py` is a text scan (9.2). It fails
  closed: a `dont_panic` word outside a comment, a string or a doc block that it cannot
  name causes a failure. Its table is empty now.
- **The fixture directive `LIB_FLAGS`** (`tests/TEST_METADATA_GUIDE.md`) gives flags to
  each library build of a fixture. It refuses `--lib`, `--lib-kind` and `--lib-version`,
  because the runner writes these flags for each build.
- **The toolchain programs** in `toolchain/src/` are user programs. If one of them marks a
  function, `toolchain/build.py` must pass the flag.
