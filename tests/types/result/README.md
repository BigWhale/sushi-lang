# Result<T, E> Test Suite

Comprehensive test suite for the new `Result<T, E>` type system with typed error parameters.

## Test Organization

```
tests/types/result/
├── basic/           # Basic Result<T, E> functionality (12 tests)
├── pattern/         # Pattern matching tests (7 tests)
├── propagation/     # Error propagation (??) tests (6 tests)
├── methods/         # Method tests (9 tests)
├── stdlib/          # Stdlib error types tests (8 tests)
├── warnings/        # Compiler warnings tests (3 tests)
└── errors/          # Compiler error tests (9 tests)
```

**Total: 54 tests**

## Test Categories

### 1. Basic Functionality (basic/)

Tests fundamental `Result<T, E>` construction and usage:

- `test_result_ok_construction.sushi` - Creating Result.Ok(value)
- `test_result_err_construction.sushi` - Creating Result.Err(error)
- `test_result_with_primitives.sushi` - Result with i32, string, bool
- `test_result_with_structs.sushi` - Result with custom structs
- `test_result_with_enums.sushi` - Result where T is enum
- `test_result_nested.sushi` - Nested Result<Result<T, E1>, E2>
- `test_result_with_arrays.sushi` - Result with fixed and dynamic arrays
- `test_result_with_generic_struct.sushi` - Result with generic structs (Pair<T, U>)
- `test_result_function_return.sushi` - Result as function return type
- `test_result_as_struct_field.sushi` - Result as struct field
- `test_result_multiple_variants.sushi` - Error enum with many variants
- `test_result_with_maybe.sushi` - Result<Maybe<T>, E> combination

### 2. Pattern Matching (pattern/)

Tests pattern matching on Result variants:

- `test_pattern_match_ok.sushi` - Match Ok with value binding
- `test_pattern_match_err.sushi` - Match Err with error binding
- `test_pattern_match_specific_errors.sushi` - Match specific error variants
- `test_pattern_match_wildcard.sushi` - Match with _ for any error
- `test_pattern_exhaustiveness.sushi` - Exhaustive matching verification
- `test_pattern_nested_errors.sushi` - Match nested error patterns
- `test_pattern_with_nested_data.sushi` - Match enum variants with data

### 3. Error Propagation (propagation/)

Tests error propagation operator (??):

- `test_propagation_same_error_type.sushi` - ?? with matching error types
- `test_propagation_in_function.sushi` - Multiple ?? in one function
- `test_propagation_nested_calls.sushi` - Chained function calls with ??
- `test_propagation_preserves_error_data.sushi` - Verify error data preserved
- `test_propagation_with_raii.sushi` - RAII cleanup during propagation
- `test_propagation_early_return.sushi` - Verify early return behavior

### 4. Methods (methods/)

Tests Result<T, E> methods:

- `test_realise_ok.sushi` - .realise(default) on Ok
- `test_realise_err.sushi` - .realise(default) on Err
- `test_is_ok.sushi` - .is_ok() method
- `test_is_err.sushi` - .is_err() method
- `test_expect_ok.sushi` - .expect(msg) on Ok
- `test_err_method.sushi` - .err() returns Maybe<E>
- `test_realise_with_structs.sushi` - realise with struct defaults
- `test_chained_methods.sushi` - Chaining multiple methods
- `test_result_in_loop.sushi` - Methods used in loops

### 5. Stdlib Error Types (stdlib/)

Tests predefined stdlib error enums:

- `test_std_error.sushi` - StdError.Error (generic error)
- `test_file_error.sushi` - FileError variants (NotFound, PermissionDenied, etc.)
- `test_io_error.sushi` - IoError variants (ReadError, WriteError, FlushError)
- `test_math_error.sushi` - MathError variants (DivisionByZero, Overflow, etc.)
- `test_env_error.sushi` - EnvError variants (NotFound, InvalidValue, etc.)
- `test_process_error.sushi` - ProcessError variants (SpawnFailed, etc.)
- `test_multiple_error_types.sushi` - Using multiple error types together
- `test_error_conversion_manual.sushi` - Manual error type conversion

### 6. Compiler Warnings (warnings/)

Tests expected compiler warnings:

- `test_warn_unused_result.sushi` - Warn on unused Result value
- `test_warn_result_in_condition.sushi` - Warn: use .is_ok() instead
- `test_warn_partial_error_match.sushi` - Warn: not all error variants matched

### 7. Compiler Errors (errors/)

Tests expected compilation failures:

- `test_err_missing_error_type.sushi` - ERROR: Result<T> requires 2 params
- `test_err_wrong_ok_type.sushi` - ERROR: Ok type mismatch
- `test_err_wrong_err_type.sushi` - ERROR: Err type mismatch
- `test_err_propagation_type_mismatch.sushi` - ERROR: cannot propagate different error types
- `test_err_non_enum_error_type.sushi` - ERROR: error type must be enum
- `test_err_result_in_const.sushi` - ERROR: Result not allowed in const
- `test_err_incompatible_error_conversion.sushi` - ERROR: incompatible error type assignment
- `test_err_err_without_argument.sushi` - ERROR: Err() needs error argument
- `test_err_ok_extra_argument.sushi` - ERROR: Ok() takes one argument

## Standard Error Types

The implementation plan defines these predefined error enums:

```sushi
enum StdError:
    Error  # Generic error for simple cases

enum FileError:
    NotFound
    PermissionDenied
    AlreadyExists
    InvalidPath
    IoError

enum IoError:
    ReadError
    WriteError
    FlushError

enum MathError:
    DivisionByZero
    Overflow
    Underflow
    InvalidInput

enum EnvError:
    NotFound
    InvalidValue
    PermissionDenied

enum ProcessError:
    SpawnFailed
    ExitFailure
    SignalReceived
```

## Running Tests

```bash
# Run all Result<T, E> tests
python tests/run_tests.py --filter "tests/types/result"

# Run specific category
python tests/run_tests.py --filter "tests/types/result/basic"
python tests/run_tests.py --filter "tests/types/result/pattern"
python tests/run_tests.py --filter "tests/types/result/propagation"

# Run with enhanced validation
python tests/run_tests.py --enhanced --filter "tests/types/result"
```

## Test Conventions

1. **Naming**:
   - `test_*.sushi` - Must compile and run successfully (exit 0)
   - `test_warn_*.sushi` - Must compile with warnings (exit 1)
   - `test_err_*.sushi` - Must fail compilation (exit 2)

2. **Structure**:
   - Each test focuses on ONE aspect of functionality
   - Tests include clear PASS/FAIL output messages
   - Tests verify both success and error paths
   - All .sushi files have trailing newlines

3. **Coverage**:
   - Basic construction (Ok/Err)
   - Pattern matching (exhaustive, specific, wildcard)
   - Error propagation (?? operator)
   - All methods (is_ok, is_err, realise, expect, err)
   - All stdlib error types
   - Edge cases (nested Results, arrays, structs, generics)
   - RAII cleanup during error paths
   - Type compatibility checks

## Expected Behavior

After implementation, all tests should:

- **Basic tests (test_*.sushi)**: Compile and execute successfully
- **Warning tests (test_warn_*.sushi)**: Compile with expected warnings
- **Error tests (test_err_*.sushi)**: Fail compilation with appropriate error codes

## Test-Driven Development

These tests are written BEFORE implementation to guide the development process:

1. Tests define the expected API and behavior
2. Implementation makes tests pass incrementally
3. Tests serve as regression prevention
4. Tests document usage patterns

## Key Test Scenarios

### Error Propagation with Type Safety

```sushi
enum DbError:
    ConnectionFailed

fn connect() Result<i32, DbError>:
    return Result.Err(DbError.ConnectionFailed)

fn query() Result<string, DbError>:
    let i32 conn = connect()??  # Error propagates with type preserved
    return Result.Ok("data")
```

### Pattern Matching on Specific Errors

```sushi
match open_file("test.txt"):
    Result.Ok(fd) -> println("Success")
    Result.Err(FileError.NotFound) -> println("Not found")
    Result.Err(FileError.PermissionDenied) -> println("Access denied")
    Result.Err(_) -> println("Other error")
```

### Manual Error Type Conversion

```sushi
fn convert_errors() Result<i32, AppError>:
    match file_operation():
        Result.Ok(data) -> Result.Ok(42)
        Result.Err(FileError.NotFound) -> Result.Err(AppError.IoFailure)
        Result.Err(_) -> Result.Err(AppError.UnknownError)
```

## Implementation Phases

These tests align with the RES.md implementation phases:

- **Phase 1-2**: Basic construction, type system (basic/)
- **Phase 2**: Pattern matching, methods (pattern/, methods/)
- **Phase 3**: Error propagation (propagation/)
- **Phase 4**: Stdlib error types (stdlib/)
- **Phase 5-6**: Warnings and errors (warnings/, errors/)

## Coverage Statistics

- Total tests: 54
- Positive tests (success path): 41
- Warning tests: 3
- Error tests: 10
- Stdlib tests: 8
- Edge case tests: 15

This comprehensive suite ensures robust implementation of the Result<T, E> type system.
