# Test Metadata Guide for Sushi Language Tests

## Overview

The Sushi test suite supports two modes of testing:

1. **Compilation-only mode** (default): Tests whether code compiles successfully, fails compilation, or generates
warnings
2. **Enhanced runtime mode** (`--enhanced` flag): Also executes compiled binaries and validates runtime behavior

## Output Assertion Convention (REQUIRED)

**Any success-category test (`test_*.sushi`) that calls `print(` or `println(` MUST include at least one
`EXPECT_STDOUT_CONTAINS` or `EXPECT_STDOUT_EXACT` directive in its first 20 lines.**

Rationale: compilation-only mode cannot detect regressions in computed results. A test that prints `"Sum: 42"` but
only validates that it compiled gives no protection against the compiler emitting wrong arithmetic. Runtime assertions
close this gap.

### When to use each assertion form

- `EXPECT_STDOUT_CONTAINS: <substring>` — preferred for most tests. Choose a **computed result** or a unique token
  that identifies the test actually ran successfully (e.g., `Sum: 30`, `All tests passed`, `Value: 42`).
- `EXPECT_STDOUT_EXACT: "<full output>"` — use only for small, fully-deterministic programs where the complete output
  can be expressed in a single quoted string on one line. This form catches extraneous output as well.
- Multiple `EXPECT_STDOUT_CONTAINS` directives are allowed; all must be present in stdout.

### When NOT to add a stdout assertion

- `test_err_*` and `test_warn_*` files — these test compilation failure/warnings only, and their binaries are
  not executed in enhanced mode. The one exception is a `test_warn_*` file carrying `EXPECT_NO_LEAKS`, which
  is executed so the leak assertion can be evaluated.
- Tests with no print statements (compilation-only is sufficient).
- Tests that produce nondeterministic output: HashMap/`.keys()`/`.values()`/`.entries()` iteration order, pointer
  addresses, RAII debug addresses, platform-specific float formatting. For those, either choose a stable substring
  or skip with a comment explaining why.
- Tests that crash (SIGSEGV/abort) due to known compiler bugs — add to `RUNTIME_QUARANTINE` in
  `tests/enhanced_test_runner.py` and file a GitHub issue.

### Lowering the coverage ratchet

The pytest unit test `tests/unit/test_stdout_coverage.py` tracks a `BASELINE` constant (the count of in-scope tests
that print but lack an assertion). After backfilling a new directory, run
`uv run --extra dev pytest tests/unit/test_stdout_coverage.py` to confirm it passes, then lower `BASELINE` to the
new gap count and commit.

All happy path tests (`test_*.sushi`, not `test_err_*` or `test_warn_*`) should include metadata to support enhanced
runtime validation.

## Metadata Format

Metadata is specified using special comments at the top of the test file (within the first 20 lines). These directives
configure expected runtime behavior for validation.

The runner reads a fixture as bytes and decodes only its leading comment block, which
must be UTF-8. A later line can hold any byte. A fixture whose directive block holds a
byte that is not UTF-8 FAILS; it never passes with its directives unread.

A line of the leading comment block that starts with an upper-case name of four or more
characters (`^[A-Z][A-Z0-9_]{3,}`, a whole word) IS a directive, and the runner reads it
or FAILS the fixture with a message that names the line. So these fail:

- a name that is in neither directive table (`# EXPECTED_OUTPUT: 42`, `# NOTE: ...`);
- a valued directive with no `:` (`# EXPECT_STDERR_EMPTY`, `# EXPECT_RUNTIME_EXIT 3`);
- a flag directive followed by text that is not `: value` (`# EXPECT_NO_LEAKS true`).

A line that starts with a diagnostic code (`CE`, `CW`, `RE` or `NE` and four digits, as
in `# CE2510: ...`) is prose. Other prose must not start with an upper-case word: write
`# Note: ...`, not `# NOTE: ...`. The known names are the rows of `VALUED_DIRECTIVES`
and `FLAG_DIRECTIVES` in `tests/test_metadata.py`, and nothing else.

Every gate that scans the corpus reads it through `corpus_files` and `corpus_text` in
`tests/test_metadata.py`. `corpus_files` gives files only (a directory named `x.sushi` is
not a source). A file that is not UTF-8 is allowed only when a `test_err_` fixture in the
SAME directory declares CE3017 (the code of an unreadable source) in its directives; for
such a file, `corpus_text` gives the leading comment block alone. Any other file that is
not UTF-8 fails the gate that reads it.

### Basic Metadata Directives

#### EXPECT_RUNTIME_EXIT

Specifies the expected exit code from the compiled binary.

```sushi
# EXPECT_RUNTIME_EXIT: 0
```

- Common values: `0` (success), non-zero for error conditions
- If not specified, the test runner defaults to expecting exit code 0

#### EXPECT_STDOUT_CONTAINS

Validates that stdout contains a specific string. Can be specified multiple times.

```sushi
# EXPECT_STDOUT_CONTAINS: "All tests passed"
# EXPECT_STDOUT_CONTAINS: "Result: 42"
```

- Supports escape sequences: `\n` (newline), `\t` (tab)
- Quotes are optional but recommended for clarity
- Multiple directives check for multiple strings (all must be present)

#### EXPECT_STDOUT_EXACT

Validates that stdout matches exactly (useful for precise output verification).

```sushi
# EXPECT_STDOUT_EXACT: "Hello World\nDone\n"
```

- Supports escape sequences
- Mutually exclusive with `EXPECT_STDOUT_CONTAINS` (use one or the other)

#### EXPECT_STDERR_CONTAINS

Validates that stderr contains specific content.

```sushi
# EXPECT_STDERR_CONTAINS: "Warning: deprecated feature"
```

- Supports escape sequences
- Can be specified multiple times
- Enforced on both the runtime path and the compilation path. For `test_err_*`
  / `test_warn_*` tests (whose binaries are never executed) it asserts against
  the compiler's stderr, so you can pin a diagnostic's message text.

#### EXPECT_STDERR_EMPTY

Validates that stderr produces no output.

```sushi
# EXPECT_STDERR_EMPTY: true
```

- Common for happy path tests
- Values: `true`, `yes`, `1` (case-insensitive). The value is REQUIRED: the bare
  `# EXPECT_STDERR_EMPTY` fails the fixture.
- It reads the stderr of the binary, on the runtime path.

#### EXPECT_NO_LEAKS

Asserts the compiled binary leaks no heap memory. The runner re-runs the binary under the
malloc-interposer (`tests/leakcheck/leakcheck.c`, preloaded via `DYLD_INSERT_LIBRARIES` on
macOS / `LD_PRELOAD` on Linux); the interposer prints its outstanding byte balance at exit
and any non-zero balance fails the test.

```sushi
# EXPECT_NO_LEAKS: true
```

- Values: `true`, `yes`, `1` (case-insensitive). The **bare** form with no colon --
  `# EXPECT_NO_LEAKS` -- also means true.
- **Enforced by every `--enhanced` run** that executes the test. It is not opt-in at the
  command line; the directive is the opt-in.
- `--leaks-only` selects *only* the tests carrying this directive and runs the identical
  check. It is a faster gate over the same assertion, not a stronger one.
- Only allocations made by the program's own code are counted (backend output plus the
  merged stdlib), so a correct RAII program nets exactly zero -- there is no baseline to
  subtract.
- If the interposer cannot be built, fails to load, or the run times out, the assertion is
  **skipped and reported** (with the test name and the reason in the summary), never
  silently passed. **A skip FAILS the run**: a run that asserted nothing must not report a
  pass. `--allow-leak-skips` is the one escape, for a platform that cannot check at all.
- A `test_warn_*` test may carry it: warning tests are not normally executed, but one that
  declares a leak assertion is, because that is the only way to leak-check a
  warned-but-legal construct such as shadowing an owning binding.

#### EXPECT_NO_OPEN_FDS

Asserts the compiled binary holds no open **descriptor** at exit -- a file or a socket it
opened and never closed.

```sushi
# EXPECT_NO_OPEN_FDS: true
```

**This is not what `EXPECT_NO_LEAKS` checks.** The malloc interposer counts BYTES, so a
program that opens a file and never closes it reports a perfectly clean byte balance. A
handle test gets no coverage from the leak directive at all; this is the half that sees
it, and the two are declared independently.

- Values: `true`, `yes`, `1` (case-insensitive). The **bare** form with no colon --
  `# EXPECT_NO_OPEN_FDS` -- also means true.
- Same enforcement as `EXPECT_NO_LEAKS`: every `--enhanced` run that executes the test.
- **One re-run answers both.** The check rides the same interposer, which counts the
  per-process fd directory (`/dev/fd` on macOS, `/proc/self/fd` on Linux) in its
  constructor and again at exit and reports the delta. Descriptors 0, 1 and 2 are
  excluded, and so is the handle the count itself holds. Deliberately no shim of its own:
  two shims would be two build steps racing to link onto one path, which is already why
  `pytest` and `run_tests.py --enhanced` must not run at the same time.
- If the interposer is too old to report the field, or the fd directory cannot be read,
  the assertion is **skipped and reported**, never silently passed -- the rule the byte
  half follows.
- Reported BEFORE the byte balance when both are declared: a leaked descriptor is the
  defect the byte gate cannot see, so it must not be shadowed by it.
- `--leaks-only` does not select on this directive; it selects on `EXPECT_NO_LEAKS`.
  Declare both on a test that should be covered by that faster gate too.

### Compilation Diagnostics Directives

#### EXPECT_ERROR_CODE

Asserts that the compiler emits a specific diagnostic code (e.g. `CE2007`) for an
`test_err_*` / `test_warn_*` test. Enforced on the compilation path, alongside the
exit-code check (2 for errors, 1 for warnings) -- so the test proves not just *that*
compilation failed but *which* diagnostic fired.

```sushi
# EXPECT_ERROR_CODE: CE2007
```

- Accepts a single code, a comma/space separated list, or the directive repeated
  for multi-error compiles. Every listed code must appear in stderr.

  ```sushi
  # EXPECT_ERROR_CODE: CE2044, CE2049
  ```

- Matches the bare code token (`CE2007`), which is ANSI-independent; the runner
  forces `NO_COLOR` so the token is never split by color escapes.
- Prefer this over `EXPECT_STDERR_CONTAINS` for error/warning tests: the code is
  stable, whereas message text is brittle.
- It is a SUBSTRING check: a fixture that expects `CE2009` also passes when the
  compiler prints `CE3015` beside it. Use `EXPECT_ERROR_CODES_EXACT` to pin the codes and their counts.

#### EXPECT_ERROR_CODES_EXACT

Asserts the WHOLE multiset of diagnostic codes the compiler printed, warnings included,
for a `test_err_*` / `test_warn_*` test. A code that is missing fails the test, a code
that is printed and not listed fails it, and so does a code that is printed more times
or fewer times than it is listed.

```sushi
# EXPECT_ERROR_CODES_EXACT: CE2009
# EXPECT_ERROR_CODES_EXACT: CW1001, CE1001, CE2002
```

- A comma/space separated list; the directive may be repeated, and the lists add up
- It compares MULTISETS (#1060): `CE0112` means exactly one CE0112, and
  `CE0112, CE0112` means two. A warning is counted the same way. A code that the
  compiler prints twice for one fault is a duplicate diagnostic: do not list it twice
  to make the fixture pass
- A code is read from the head of each diagnostic (`error [CE1001]`,
  `warning [CW1001]`); a code inside a message or a note does not count
- The failure names each code whose count is different, with both counts
  (`CE0112: expected 1, printed 2`)

#### EXPECT_IR_HOLDS / EXPECT_IR_LACKS

Asserts a text in the emitted LLVM IR of ONE function. Use it for a rule that the output
of the program cannot show, for example a bounds check that `dont_panic` removes.

```sushi
# COMPILER_FLAGS: --dont-panic
# EXPECT_IR_LACKS: read_dynamic _bounds_fail
# EXPECT_IR_HOLDS: read_dynamic_checked _bounds_fail
```

- The value is `<function> <text>`. The text is a substring of the body of the function
- The function is its source name. The runner finds the symbol that is the name, or the
  name after its unit (`prog$read_dynamic`). No symbol, or two symbols, fail the fixture
- The runner adds `--write-ll --no-incremental` to the compilation and reads the IR
  before optimization. A program of more than one unit gets the one-module build too
- Put a marked function and an unmarked twin in one fixture, and assert both. The twin
  proves that the text is there when the rule does not apply
- The directive may be repeated. A fixture that does not compile has no IR, and fails

### Advanced Metadata Directives

#### TIMEOUT_SECONDS

Override the default test timeout (default: 10 seconds).

```sushi
# TIMEOUT_SECONDS: 10
```

#### No TEST_TYPE directive

The file name prefix sets the category of a test (`test_err_`, `test_warn_`, and the
others). There is no `TEST_TYPE` directive: the runner did not read it, so it was
removed (#1043). A `TEST_TYPE` line is an unknown directive and fails its fixture.

#### CMD_ARGS

Provide command-line arguments to the compiled binary.

```sushi
# CMD_ARGS: --verbose input.txt
```

#### STDIN_INPUT

Provide standard input to the compiled binary.

```sushi
# STDIN_INPUT: "line1\nline2\nline3\n"
```

- Supports escape sequences
- Useful for testing interactive programs

#### TEST_ENV

Set environment variables for the compiled binary.

```sushi
# TEST_ENV: HOME=/home/trillian
# TEST_ENV: USER=trillian
```

- One `KEY=VALUE` per directive; repeat the directive to set several variables
- Merged over the runner's own environment (the test's values win)
- Also applied to the `EXPECT_NO_LEAKS` re-run, so both runs see the same environment
- Use it instead of baking the developer's host environment into an expected-stdout
  snapshot: a test that prints `getenv("HOME")` is otherwise unreproducible

#### TEST_CWD

Run the compiled binary in a specific working directory.

```sushi
# TEST_CWD: /
```

- With no `TEST_CWD`, the binary runs in an empty scratch directory of its own, inside the
  run's temporary directory. A relative path that the program writes goes there, and the
  runner deletes it after the fixture
- A relative `TEST_CWD` is read from the project root: `# TEST_CWD: .` is for a program
  that reads files of the checkout
- Makes `getcwd()`-style output host-independent
- Also applied to the `EXPECT_NO_LEAKS` re-run

#### COMPILER_FLAGS

Append flags to the `./sushic` command line, so a fixture can exercise a diagnostic that
lives behind a compiler flag.

```sushi
# COMPILER_FLAGS: --warn-missing-docs
```

- Several flags on one line, or repeat the directive
- Applied by both runners, the compilation-only one and the enhanced one
- A flag the RUNNER owns is refused with a printed warning: `-o`, `--lib`, `--lib-info`,
  `--clean-cache`, `--build-stdlib` and `--cache-dir` decide the output path, the build
  kind and the cache, so a fixture that changed one would break the run rather than test
  anything. The one exception is `--lib` in a fixture that has `OUTPUT_PATH`: that fixture
  names its own output, so the build kind is its own too
- Pair the fixture with a second one that carries the same source and NO directive. The
  quiet twin is what proves the flag is a gate; without it a lint that became always-on
  would pass both ways

### Directory Fixtures: the Rebuild and the Working Directory

A fixture that needs more than one file, a second compilation or its own working
directory lives in a directory of its own, for example `tests/cache/<name>/`. The
fixture is the one `test_*.sushi` file there; the other `.sushi` files are the units it
imports. The runner never compiles in the tree: it copies the fixture's directory to a
temporary directory and compiles, runs and rebuilds the copy. `--cache-dir` stays the
runner's flag, and the runner gives each such fixture a cache of its own.

#### The rebuild form (`v2/`)

When the fixture's directory holds a `v2/` directory, the fixture is a rebuild fixture:

1. The runner compiles the copy. The compilation must succeed (exit 0 or 1), and every
   unit must report `[rebuilt]`, because the cache is new.
2. It copies each file of `v2/` over its namesake in the copy.
3. It compiles again, with the same cache. This second compilation, and its binary, are
   what the ordinary directives (`EXPECT_STDOUT_EXACT`, `EXPECT_ERROR_CODE`, the exit code
   of the file name, ...) describe.

`v2/` is data, never a fixture: the collector steps over it, and it may not hold the
fixture file itself, because the one set of directives describes both steps.

```sushi
# EXPECT_STDOUT_EXACT_BEFORE_REBUILD: "3 4\n"
# EXPECT_STDOUT_EXACT: "3 4\n"
# EXPECT_REBUILT: geo, test_cache_struct_shape
# EXPECT_CACHED: other
```

- `EXPECT_STDOUT_EXACT_BEFORE_REBUILD` -- the exact stdout of the FIRST binary, which
  must also exit 0. It proves that the change `v2/` makes is visible.
- `EXPECT_REBUILT` / `EXPECT_CACHED` -- the units the SECOND compilation reports as
  `[rebuilt]` and `[cached]` in its code-generation report. A unit name is the one the
  compiler prints: the file stem for a unit of the program (`geo`), the module name for a
  stdlib module (`fixture/limits`), and `lib/<lib>/<unit>` for a unit of a source
  library. A comma/space separated list; the directive may be repeated. When either is
  present, every unit the compiler reports must be named in one of the two, so an
  unexpected rebuild fails the fixture too.
- A rebuild directive in a fixture whose directory holds no `v2/` fails the fixture.

#### RUN_IN_FIXTURE_DIR

```sushi
# RUN_IN_FIXTURE_DIR
```

- The runner starts `sushic` from the copy of the fixture's directory, with the bare
  file name as the source path, the way a user does. The `sushic` wrapper passes that
  directory to the compiler in `SUSHI_CWD`.
- The runner passes no `--cache-dir`, unless the fixture has `FIXTURE_CACHE_DIR`: the
  compiler picks its own cache, as it does for a user, and that cache is in the copy.
- The binary also runs in the copy, unless `TEST_CWD` names another directory.
- `-o` stays the runner's, and it is an absolute path outside the copy, unless the fixture
  has `OUTPUT_PATH`.

#### FIXTURE_CACHE_DIR

```sushi
# RUN_IN_FIXTURE_DIR
# FIXTURE_CACHE_DIR: relcache
```

- The runner passes `--cache-dir relcache` exactly as written. It is a RELATIVE path, and
  the compiler resolves it against the directory the compiler starts in: the copy.
- It needs `RUN_IN_FIXTURE_DIR`. In any other fixture the compiler starts in the
  checkout, and a relative cache would go into the runner's own tree.
- An absolute path, or a path with a `..` part, fails the fixture.

#### EXPECT_PATH_EXISTS / EXPECT_PATH_ABSENT

```sushi
# EXPECT_PATH_EXISTS: relcache/libsrc/geolib
# EXPECT_PATH_ABSENT: __sushi_cache__
```

- Paths relative to the fixture's copy. A comma/space separated list; the directive may
  be repeated. Either directive gives the fixture a copy of its own.
- The runner reads them ONCE, after the fixture's LAST compiler invocation (the
  `THEN_CLEAN_CACHE` one when the fixture has it) and before the binary runs.
- The compilation must first pass its other compilation checks.
- An absolute path, or a path with a `..` part, fails the fixture.

#### OUTPUT_PATH

```sushi
# OUTPUT_PATH: nodir/out
# EXPECT_ERROR_CODES_EXACT: CE3019
# EXPECT_STDERR_CONTAINS: "is not a directory"
```

- The runner passes `-o <the fixture's copy>/nodir/out`. With `RUN_IN_FIXTURE_DIR`, it
  passes the relative spelling `-o nodir/out`, and the compiler resolves it against the
  copy. The directive gives the fixture a copy of its own.
- The runner never creates the parent of the path. A parent that the fixture's directory
  does not hold is missing; a parent that is a file of the fixture's directory (`afile/out`
  beside a file `afile`) is a file. That parent is the subject of the test.
- `--lib` in `COMPILER_FLAGS` is accepted in such a fixture (`OUTPUT_PATH: nodir/x.slib`
  with `COMPILER_FLAGS: --lib --lib-version 1.0.0`). A library has no binary to run, so a
  library fixture that would run one (a success fixture, or a `test_warn_` fixture with a
  runtime directive) fails before it compiles.
- A fixture that expects success writes its output at the path: its directory must hold
  the parent (for example a directory `out/` for `OUTPUT_PATH: out/x`). The runtime
  directives describe the binary at that path, and `EXPECT_PATH_EXISTS: out/x` can read
  it. A missing parent then fails the fixture with the compiler's CE3019.
- An empty path, an absolute path, or a path with a `..` part fails the fixture.

#### THEN_CLEAN_CACHE

```sushi
# RUN_IN_FIXTURE_DIR
# EXPECT_PATH_EXISTS_BEFORE_CLEAN: __sushi_cache__/units
# THEN_CLEAN_CACHE: bare
# EXPECT_PATH_ABSENT: __sushi_cache__
```

- After the compilation passes its checks, the runner starts `sushic` one more time, in the
  same directory, with the same environment and the same `--cache-dir` (the
  `FIXTURE_CACHE_DIR` one, or none):
  - `bare`: `sushic --clean-cache`. It must exit 0.
  - `source`: `sushic --clean-cache <source> -o <binary> <COMPILER_FLAGS>`, which cleans
    and then builds again. It must exit as the compilation must. Its binary is the one the
    runtime directives describe, and its code-generation report is the one
    `EXPECT_REBUILT` / `EXPECT_CACHED` read. So these two need no `v2/` here. For `bare`,
    they read the report of the compilation.
- `EXPECT_PATH_EXISTS_BEFORE_CLEAN` -- paths of the copy that must exist after the
  compilation and BEFORE the clean. It proves that the clean removed something that was
  there. It needs `THEN_CLEAN_CACHE`.
- The runner fails the fixture if the checkout's own `__sushi_cache__/` was there before
  the clean and is gone after it.
- It needs `RUN_IN_FIXTURE_DIR`. A value other than `bare` or `source` fails the fixture.

#### BUILD_LIB

```sushi
# BUILD_LIB: geolib.sushi
```

- Before each compilation, the runner builds the named file of the fixture's directory
  as a SOURCE `.slib` (`--lib --lib-version 0.0.0 --lib-kind source`), and puts the
  directory that holds it first on `SUSHI_LIB_PATH`. The fixture imports it as
  `use <lib/geolib>`. `BUILD_LIB_BINARY` builds a binary one, and `BUILD_LIB_HYBRID`
  a hybrid one.
- The runner builds every `BUILD_LIB`, `BUILD_LIB_BINARY`, `BUILD_LIB_HYBRID` and
  `BUILD_LIB_WARNS` in the order the fixture writes them, whatever the directive, and
  then every `BUILD_LIB_AT` in written order. A library build has the same directory on its `SUSHI_LIB_PATH`, so
  a library may `use <lib/...>` a library built before it. Write a dependency above the
  library that uses it.
- `# BUILD_LIB: apub.sushi -> a.slib` builds the source as `a.slib`, so the library name
  (`a`) is not the unit name (`apub`). The target is a file name with no directory.
- `# BUILD_LIB: b.sushi @ 0.2.0` stamps the version `0.2.0` in place of `0.0.0`. A
  fixture can build one source twice at two versions; the second build replaces the
  first `.slib`. The two forms combine: `b.sushi -> c.slib @ 0.2.0`.
- The three directives take both forms. A form names one source.
- In a rebuild fixture the library is built again after `v2/` is copied in.
- A library that does not build fails the fixture. A build must exit 0: a build with a
  warning (exit 1) fails the fixture too. Use `BUILD_LIB_WARNS` for a library that must
  build with a warning.
- The build has a cache of its own, outside the copy. The version is `0.0.0`, unless a
  `nori.toml` beside the library source states one.

#### LIB_FLAGS

```sushi
# BUILD_LIB: dp_src_lib.sushi
# LIB_FLAGS: --dont-panic
```

- The runner adds these flags to EVERY library build of the fixture: `BUILD_LIB`,
  `BUILD_LIB_BINARY`, `BUILD_LIB_HYBRID`, `BUILD_LIB_WARNS` and `BUILD_LIB_AT`. It does
  not add them to the compilation of the fixture itself; that is `COMPILER_FLAGS`.
- Several flags on one line, or repeat the directive.
- A flag the runner owns is refused with a printed warning, as in `COMPILER_FLAGS`.
  `--lib` is refused here always, because the runner spells it for each library build.
  `--lib-kind` and `--lib-version` are refused here too, for the same reason: use the
  directive forms (`BUILD_LIB_BINARY`, `@ version`).

#### BUILD_LIB_BINARY

```sushi
# BUILD_LIB_BINARY: geolib.sushi
```

- As `BUILD_LIB`, but the runner builds a BINARY `.slib` (`--lib-kind binary`). Use it
  to test what a consumer does with a binary library: the manifest, the bitcode, a
  generic template that the consumer monomorphizes.
- The library goes to the same directory as a `BUILD_LIB` library, first on
  `SUSHI_LIB_PATH`. The fixture imports it as `use <lib/geolib>`. Do not name one file
  in both directives.
- The build, the version, the rebuild and a failed build are as for `BUILD_LIB`. A
  binary library is bound to the platform that built it; the runner builds it again for
  each run, so the fixture stays portable.
- There is no binary form of `BUILD_LIB_AT`; the `-> name.slib` form names the file in
  the `SUSHI_LIB_PATH` directory.

#### BUILD_LIB_HYBRID

```sushi
# BUILD_LIB_HYBRID: geolib.sushi
```

- As `BUILD_LIB_BINARY`, but the runner builds a HYBRID `.slib` (`--lib-kind hybrid`):
  the bitcode and the source in one file.
- The library goes to the same directory, and the build, the version, the rebuild and
  a failed build are as for `BUILD_LIB`. Do not name one file in two directives.
- There is no hybrid form of `BUILD_LIB_AT`.

#### BUILD_LIB_WARNS

```sushi
# BUILD_LIB_WARNS: geolib.sushi -> CW3003
# BUILD_LIB_WARNS: other.sushi -> CW3003, CW1001
# BUILD_LIB_WARNS: third.sushi binary -> CW3003
```

- As `BUILD_LIB`, but the build must exit 1, and the SET of warning codes in its stderr
  must be equal to the named set (#1122). A missing code or a code that is not named
  fails the fixture, and so does a build that exits 0. Use it for a library that extends
  a type it does not declare (CW3003): that is a correct library, with a warning.
- The library goes to the same directory as a `BUILD_LIB` library. It is a SOURCE
  `.slib`, unless a kind word follows the source: `binary` or `hybrid` (or `source`).
  It builds in written order with the other three directives.
- The value is one source, an optional kind word, `->`, and one or more `CW` codes,
  comma or space separated. There is no `-> name.slib` form and no `@ version` form.
  Any other value fails the fixture.

#### BUILD_LIB_AT

```sushi
# BUILD_LIB_AT: vendor/boxes.sushi -> .sushi_bento/boxes/lib/boxes.slib
```

- Before each compilation, the runner builds the named file of the copy as a SOURCE
  `.slib` at the target path. Both paths are relative to the copy. The runner adds
  NOTHING to `SUSHI_LIB_PATH`, so the compiler must find the library itself: next to the
  program, or in the project's `.sushi_bento/` (with a `nori.toml` in the fixture).
- Use it with `RUN_IN_FIXTURE_DIR`, so that the compiler starts in the copy.
- The directive may be repeated. The build, the version and the rebuild are as for
  `BUILD_LIB`. A value with no `->`, an absolute path, or a path with a `..` part fails
  the fixture.

#### STDLIB_MODULE

```sushi
# STDLIB_MODULE: fixture/limits=limits.sushi
```

- Registers the named file of the fixture's directory as a Sushi-source stdlib module,
  in the one compiler process, the way the bundled modules under `src_sushi/` are
  registered. The fixture imports it as `use <fixture/limits>`. This is how a fixture
  tests what a change to a bundled module does, without a change to the stdlib.
- The compiler then runs without the `sushic` wrapper, so the runner does what the
  wrapper does: it starts the compiler in the checkout and names the fixture's directory
  in `SUSHI_CWD`.

## Test File Naming Conventions

Test files must follow naming conventions to indicate expected compilation behavior:

- `test_<name>.sushi` - Must compile successfully (exit 0)
- `test_warn_<name>.sushi` - Should compile with warnings (exit 1)
- `test_err_<name>.sushi` - Should fail compilation (exit 2)
- `test_run_<name>.sushi` - Always executed in enhanced mode

A test file name must also be UNIQUE across the whole of `tests/`, whatever directory it
sits in. The runners report each test by its file name and key their quarantine sets on
it, so a name that picks out two files drops one of them from the count and makes a
failure unattributable. `tests/unit/runner/test_fixture_identity_is_its_path.py` refuses a
duplicate. Give the name enough of its subject to stand alone:
`test_run_socket_close_then_scope_exit.sushi`, not `test_run_close_then_scope_exit.sushi`
next to another of that name.

## Complete Example: Constant Expression Test

```sushi
# Test constant expression evaluation
# EXPECT_RUNTIME_EXIT: 0
# EXPECT_STDOUT_CONTAINS: "All constant tests passed"

const i32 WIDTH = 100
const i32 HEIGHT = 50
const i32 AREA = WIDTH * HEIGHT

fn main() i32:
    # Runtime validation: verify constant was evaluated correctly
    if (AREA != 5000):
        return Result.Err()

    println("All constant tests passed")
    return Result.Ok(0)
```

Key points:
1. Metadata directives at the top (lines 2-3)
2. Expected exit code 0 (success)
3. Expected stdout message to confirm test passed
4. Runtime validation logic using conditional returns
5. Success message printed before returning

## Auto-Detection

The test framework automatically detects runtime requirements based on:

1. **Filename patterns**: `test_run_*` always runs in enhanced mode
2. **Content patterns**: Files with conditional returns or validation logic
3. **Explicit metadata**: Any `EXPECT_*` directive triggers runtime validation

## Best Practices

### 1. Always Include Runtime Validation for Happy Path Tests

```sushi
# GOOD: Has metadata and validation logic
# EXPECT_RUNTIME_EXIT: 0
# EXPECT_STDOUT_CONTAINS: "Test passed"

fn main() i32:
    if (some_condition):
        return Result.Err()
    println("Test passed")
    return Result.Ok(0)
```

```sushi
# BAD: No metadata, test only validates compilation
fn main() i32:
    let i32 x = 42
    return Result.Ok(0)
```

### 2. Use Specific Exit Codes

Return `Result.Err()` for validation failures and `Result.Ok(0)` for success.

```sushi
fn main() i32:
    if (constant_value != expected_value):
        return Result.Err()  # Non-zero exit indicates failure
    return Result.Ok(0)
```

### 3. Provide Clear Success Messages

Always print a success message that the metadata can validate.

```sushi
# EXPECT_STDOUT_CONTAINS: "All <feature> tests passed"

fn main() i32:
    # ... validation logic ...
    println("All <feature> tests passed")
    return Result.Ok(0)
```

### 4. Group Related Tests

Organize tests in logical directories:

```
tests/
  constants/          # Constant expression tests
  generics/           # Generic type tests
  error_handling/     # Result@(T) and Maybe@(T) tests
  stdlib/             # Standard library tests
```

### 5. Add Trailing Newlines

Always add a trailing newline to `.sushi` files to avoid compilation warnings.

## Running Tests

### Compilation-only mode

```bash
python tests/run_tests.py
```

### Enhanced runtime mode

```bash
python tests/run_tests.py --enhanced
```

### Leak-annotated subset only

```bash
python tests/run_tests.py --leaks-only
```

Runs just the tests declaring `EXPECT_NO_LEAKS`, with the same enforcement `--enhanced` applies. Implies
`--enhanced`; used in CI as a fast gate ahead of the full suites.

### Filter specific tests

```bash
python tests/run_tests.py --enhanced --filter constants/
```

## Metadata Validation Workflow

When a test runs in enhanced mode:

1. **Compilation phase**: Compiler attempts to compile the test
2. **Exit code check**: Validates compilation exit code matches category
3. **Binary execution**: Runs the compiled binary (if compilation succeeded)
4. **Runtime validation**:
   - Exit code matches `EXPECT_RUNTIME_EXIT`
   - Stdout contains `EXPECT_STDOUT_CONTAINS` strings
   - Stdout matches `EXPECT_STDOUT_EXACT` (if specified)
   - Stderr contains `EXPECT_STDERR_CONTAINS` strings
   - Stderr is empty if `EXPECT_STDERR_EMPTY: true`
5. **Leak and descriptor checks** (if `EXPECT_NO_LEAKS` or `EXPECT_NO_OPEN_FDS` is
   declared): ONE re-run under the interposer, which reports both the outstanding byte
   balance and the open-descriptor delta. Fails on a leaked descriptor first, then on any
   outstanding allocation
6. **Result reporting**: Pass/fail with detailed error messages

## Common Pitfalls

### Missing Metadata

```sushi
# WRONG: Happy path test without metadata
fn main() i32:
    let i32 x = compute_something()
    return Result.Ok(0)
```

Enhanced mode cannot validate this test. Add metadata:

```sushi
# CORRECT: Metadata enables runtime validation
# EXPECT_RUNTIME_EXIT: 0

fn main() i32:
    let i32 x = compute_something()
    if (x != 42):
        return Result.Err()
    return Result.Ok(0)
```

### Incorrect Expected Values

```sushi
# BUG: Test expects wrong value
const i32 RESULT = (100 + 50) * 2 / 3  # Evaluates to 100
fn main() i32:
    if (RESULT != 199):  # WRONG: Should be 100
        return Result.Err()
    return Result.Ok(0)
```

Always manually verify expected values match actual constant evaluation.

### Missing Validation Logic

```sushi
# INCOMPLETE: Metadata present but no validation
# EXPECT_RUNTIME_EXIT: 0
# EXPECT_STDOUT_CONTAINS: "Test passed"

fn main() i32:
    # Missing: validation of actual behavior
    return Result.Ok(0)  # Always succeeds
```

Add explicit validation checks for the feature being tested.

## Future Enhancements

When adding new language features:

1. Create tests in appropriate directory
2. Follow naming conventions (`test_<feature>.sushi`)
3. Add metadata for happy path tests
4. Include runtime validation logic
5. Test both compilation and enhanced modes
6. Document any new metadata requirements

## Conclusion

Metadata-driven testing ensures comprehensive validation of both compilation and runtime behavior. Always add proper
metadata to happy path tests to enable enhanced runtime validation and catch runtime bugs early in development.
