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
- Values: `true`, `yes`, `1` (case-insensitive)

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
  silently passed. A skip does not fail the run.
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

### Advanced Metadata Directives

#### TIMEOUT_SECONDS

Override the default test timeout (default: 10 seconds).

```sushi
# TIMEOUT_SECONDS: 10
```

#### TEST_TYPE

Explicitly categorize the test type.

```sushi
# TEST_TYPE: runtime
```

- Values: `default`, `runtime`, `compilation`, `error`, `warning`
- Usually auto-detected from filename, rarely needs explicit specification

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
  anything
- Pair the fixture with a second one that carries the same source and NO directive. The
  quiet twin is what proves the flag is a gate; without it a lint that became always-on
  would pass both ways

## Test File Naming Conventions

Test files must follow naming conventions to indicate expected compilation behavior:

- `test_<name>.sushi` - Must compile successfully (exit 0)
- `test_warn_<name>.sushi` - Should compile with warnings (exit 1)
- `test_err_<name>.sushi` - Should fail compilation (exit 2)
- `test_run_<name>.sushi` - Always executed in enhanced mode

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
