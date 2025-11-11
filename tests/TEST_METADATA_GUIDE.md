# Test Metadata Guide for Sushi Language Tests

## Overview

The Sushi test suite supports two modes of testing:

1. **Compilation-only mode** (default): Tests whether code compiles successfully, fails compilation, or generates 
warnings
2. **Enhanced runtime mode** (`--enhanced` flag): Also executes compiled binaries and validates runtime behavior

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

#### EXPECT_STDERR_EMPTY

Validates that stderr produces no output.

```sushi
# EXPECT_STDERR_EMPTY: true
```

- Common for happy path tests
- Values: `true`, `yes`, `1` (case-insensitive)

### Advanced Metadata Directives

#### TIMEOUT_SECONDS

Override the default test timeout (default: 5 seconds).

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
  error_handling/     # Result<T> and Maybe<T> tests
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
5. **Result reporting**: Pass/fail with detailed error messages

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
