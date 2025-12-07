# Changelog

All notable changes to Sushi Lang will be documented in this file.

## [0.4.1] - 2025-12-07

### Changed
- Refactored library_format.py to eliminate DRY violation
- Reorganized tests into appropriate subdirectories
- Test cleanup, added test lib build to run_tests.py

## [0.4.0] - 2025-12-07

### Added
- Unified binary library format (`.slib`)
  - Single file combines LLVM bitcode and MessagePack-encoded metadata
  - Magic bytes: sushi emoji surrounding "SUSHILIB"
  - Version field and reserved space for future extensions
- `--lib-info` CLI command for library introspection
  - Displays library name, platform, compiler version, compile timestamp
  - Lists public functions with full signatures
  - Shows structs, enums, constants, and dependencies
  - Reports bitcode size
- New error codes for library format validation
  - CE3508: Invalid magic bytes
  - CE3509: Unsupported format version
  - CE3510: Metadata section truncated
  - CE3511: Bitcode section truncated
  - CE3512: Invalid metadata (MessagePack decode error)
  - CE3513: File too large

### Changed
- Library output now uses `.slib` extension instead of `.bc`
- Removed separate `.sushilib` JSON manifest files
- Updated CE3500 error message for `.slib` extension requirement

### Dependencies
- Added `msgpack>=1.0` for binary metadata serialization

## [0.3.0] - 2025-12-06

### Added
- Library system for creating and using precompiled libraries
  - `--lib` flag compiles source to reusable bitcode (`.bc`) with manifest (`.sushilib`)
  - `use <lib/name>` syntax imports precompiled libraries
  - `SUSHI_LIB_PATH` environment variable for library search paths
  - Two-phase linking with priority-based symbol resolution (Main > Library > Stdlib > Runtime)
  - Dead code elimination removes unused library functions
  - Platform mismatch warnings (CW3505)
- Library error codes (CE35xx)
  - CE3500: Library output path must have .bc extension
  - CE3502: Library not found in search paths
  - CE3503: Invalid library manifest
  - CE3507: Failed to link library
- Library type registration from manifests
  - Structs, enums, and functions from libraries are registered in semantic analysis
  - Local definitions take precedence over library definitions
- Library integration tests in `tests/libs/`
  - Runtime symbol deduplication
  - Circular function calls
  - Symbol priority/override
  - Dead code elimination
- GenericTypeProvider interface for plugin-style generic types
  - HashMap now conditionally loaded via `use <collections/hashmap>`
- Math module enhancements (`use <math>`)
  - Trigonometric: `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`
  - Hyperbolic: `sinh`, `cosh`, `tanh`
  - Logarithmic: `log`, `log10`, `log2`
  - Exponential: `exp`, `pow`

### Changed
- Removed `--link` flag (breaking change)
  - Libraries are now imported via `use <lib/...>` statements in source code
  - This simplifies the compilation model to a single mechanism

### Documentation
- New documentation: `docs/libraries.md` - Complete library system guide
- Updated `docs/compiler-reference.md` with library options
- Updated `docs/examples/26-libraries.sushi` library usage example
- Updated `docs/stdlib/math.md` with new math functions

## [0.2.0] - 2025-11-27

### Added
- Dual-mode borrow syntax replacing single `&` operator
  - `&peek T`: Read-only borrow (multiple allowed simultaneously)
  - `&poke T`: Read-write borrow (exclusive access)
- Type coercion: `&poke T` can be passed where `&peek T` is expected
- New error codes for borrow checking:
  - CE2407: Cannot have &peek and &poke borrows simultaneously
  - CE2408: Cannot modify through &peek reference (read-only)

### Changed
- Reference syntax now requires explicit borrow mode (peek or poke)
  - Old: `fn process(&i32 x) ~`
  - New: `fn process(&poke i32 x) ~` or `fn process(&peek i32 x) ~`
- Borrow checker updated for dual-mode semantics
- All tests migrated to new &peek/&poke syntax (29 files)

### Breaking Changes
- Plain `&T` syntax removed (no backward compatibility)
- All existing code using `&T` must be updated to `&peek T` or `&poke T`

## [0.1.0] - 2025-11-27

### Added
- Result<T, E> error type system implementation
  - Custom error types with | syntax: fn foo() T | ErrorType
  - Explicit Result<T, E> syntax for nested Results
  - Six built-in error enums: StdError, MathError, FileError, IoError, ProcessError, EnvError
  - Error type validation: CE2085 prevents mixing explicit Result with | syntax
- sys/process stdlib module for process control
  - exit(code) - Terminate process with exit code
  - getpid() - Get current process ID
  - sleep(seconds) - Sleep for N seconds (POSIX-compliant)
- Hash functions for Result<T, E> types
  - Enables Result types as HashMap keys
  - FNV-1a combining hash for Ok/Err variants
- Equality operations for Result<T, E> types
  - Enables == and != comparisons between Result values
- Warning CW2511 for ?? operator usage in main()
  - Encourages explicit error handling in entry point
  - Prevents propagation from top-level function

### Fixed
- Result<T, E> enum type size calculation for unit error types
- Result type resolution for stdlib file operations in match statements
- Result<T, E>.realise() type inference and integer conversions
- Result<T, E> recursive type propagation and stdlib backend integration
- Result<T, E> type propagation in Let statements
- Result method validation with user-defined generic enums
- Result<T, E> support for generic return types
- Nested Result pattern matching with enhanced type resolution
- Explicit Result<T, E> double-wrapping prevention
- Struct field extraction from Result enums with LLVM padding handling
- Result.Err() error type validation requiring error values
- FuncSig parameter types synchronization when resolving GenericTypeRef
- Result<T, E> boolean conditionals support (if/while statements)

### Changed
- Result.Err() now requires error value argument
  - Old: Result.Err()
  - New: Result.Err(StdError.Error)
- Result type annotation syntax requires explicit error type
  - Old: Result<T>
  - New: Result<T, E>
- Refactored type validation into modular components
  - semantics/passes/types/resolution.py - Type resolution
  - semantics/passes/types/propagation.py - Type propagation
  - semantics/passes/types/result_validation.py - Result pattern validation
- Refactored backend constants into modular architecture
- Refactored stdlib string operations into modular structure
- Refactored generics system (instantiate.py, monomorphize.py, collect.py)
- Complete test suite migration to Result<T, E> syntax

### Documentation
- Updated all 25 documentation examples to Result<T, E> syntax
- Updated language-reference.md with Result<T, E> type system
- Comprehensive error handling documentation in docs/error-handling.md
- sys/process module documentation in docs/stdlib/process.md
- ?? operator usage guidelines and best practices

## [0.0.12] - 2025-11-17

### Added
- Named parameters for struct constructors
  - Order-independent syntax: Point(y: 20, x: 10)
  - All-or-nothing: cannot mix positional and named arguments
  - Zero-cost abstraction resolved at compile-time
  - Error codes: CE2080-CE2083 for validation
- Single-quote string literals ('...') for plain strings without interpolation
  - Double quotes ("...") support {expr} interpolation
  - Single quotes provide literals for use in interpolation arguments
  - Example: {text.pad_left(10, '*')} uses single quotes for arguments
- File utilities in io/files module
  - remove(path) - Delete files
  - rename(old_path, new_path) - Rename/move files
  - mkdir(path, mode) - Create directories with permissions
  - rmdir(path) - Remove empty directories
  - copy(src, dest) - Copy files
- String methods in collections/strings module
  - reverse() - UTF-8 aware character-level reversal
  - repeat(n) - Repeat string n times
  - count(needle) - Count non-overlapping occurrences
  - find_last(needle) - Find last occurrence index
  - join(separator, array) - Join string array with separator
  - pad_left(width, pad_char) - Left-pad to width
  - pad_right(width, pad_char) - Right-pad to width
  - strip_prefix(prefix) - Remove prefix if present
  - strip_suffix(suffix) - Remove suffix if present

### Fixed
- Pattern matching segfault on Result<T> function calls
  - Added Call node handling in _get_scrutinee_type()
- HashMap array key implementation
  - Corrected GEP indexing for fixed and dynamic array equality
  - Fixed arrays now use gep_fixed_array_element() utility
- Directory-based stdlib imports now include submodules
  - use <collections> properly provides collections/strings
- String methods now UTF-8 aware
  - ss(start, length) works with character indices instead of bytes

### Changed
- Migrated to uv-only dependency management with direnv integration
- Refactored version management to use pyproject.toml as single source of truth
- Refactored file utilities to use fat_pointer_to_cstr() helper function
- Reorganized stdlib documentation into modular structure (docs/stdlib/)

### Validation
- Dynamic arrays disallowed as HashMap keys at compile time (error CE2058)
- Dynamic arrays disallowed in enum variants at compile time (error CE2059)
- Fixed arrays remain supported in both contexts

### Documentation
- Comprehensive documentation for named struct constructors
- Single-quote string literal syntax documented across language reference
- File utilities documentation in docs/stdlib/io/files.md
- String methods documentation in reorganized stdlib docs

## [0.0.11] - 2025-11-11

### Added
- Range expressions with .. (exclusive) and ..= (inclusive) operators
  - Zero-cost iteration that compiles to optimized for-loops
  - Automatic direction detection (ascending vs descending)
  - Supports break/continue statements
  - Returns Iterator<i32> for consistency with array iteration
- Random number generator module (<random>)
  - rand() -> u64: Random 64-bit unsigned integer
  - rand_range(i32, i32) -> i32: Random integer in range
  - srand(u64) -> ~: Seed RNG for reproducibility
  - rand_f64() -> f64: Random float in [0.0, 1.0)
  - POSIX-compliant using libc random()/srandom()
- Manual workflow dispatch trigger for CI

### Fixed
- GitHub Actions badges display

## [0.0.10] - 2025-11-11

### Added
- Full Linux platform support with CI testing
- Platform-specific errno access (`__errno_location` on Linux, `__error` on macOS)
- Platform-specific linker flags (`-lm` for math library on Linux)
- Stdio platform abstraction (stdin/stdout/stderr handles)
- Docker-based Linux build testing script

### Fixed
- Generic function enum handling in Match statements
- Perks with nested generic functions
- Buffer overflow in Maybe<T> for struct field method calls
- Method calls on borrowed variables inside functions
- Array access with .get() in structs
- Test suite compatibility with array fixes

### Changed
- Moved stdio platform implementations to `stdlib/src/_platform/{darwin,linux}/stdio.py`
- Updated backend to use platform detection for linker flags and errno functions
- Updated documentation to reflect Linux support status

## [0.0.9] - 2025-11-07

### Added
- Compile-time constant expression evaluation
  - Arithmetic operations: +, -, *, /, %
  - Bitwise operations: &, |, ^, ~, <<, >>
  - Logical operations: and, or, xor, not
  - Comparison operations: ==, !=, <, <=, >, >=
  - Type casts: as operator
  - Constant references with cycle detection
  - Array constants with constant elements
- Hexadecimal numeric literals (0xFF, 0xDEAD_BEEF)
- Binary numeric literals (0b1111, 0b1010_1010)
- Octal numeric literals (0o755, 0o644)
- Comprehensive constant expression test suite (19 tests)
- Test metadata guide (tests/TEST_METADATA_GUIDE.md)
- Error codes CE0108-CE0112 for constant expression validation

### Fixed
- ArrayType attribute error in const_eval.py (element_type -> base_type)
- Test logic bugs in test_constants_logical.sushi and test_constants_complex.sushi

### Changed
- Refactored AST builder into modular architecture (40+ modules)
  - semantics/ast_builder/ with utils/, types/, expressions/, statements/, declarations/
- Reorganized test suite into logical directory structure
  - tests/basic/, tests/constants/, tests/control_flow/, tests/error_handling/
  - tests/generics/, tests/io/, tests/literals/, tests/memory/, tests/operators/
  - tests/stdlib/, tests/strings/, tests/types/, tests/array/, tests/list/, tests/perks/
- Updated docs/language-reference.md with constant expressions section
- Updated docs/compiler-reference.md with constant expression error codes
- Bumped version to 0.0.9

### Documentation
- Complete constant expression syntax and examples
- All 5 constant expression error codes documented
- Test metadata format and requirements

## [0.0.8] - 2025-11-05

### Added
- Perks (traits/interfaces) system with static dispatch
  - Phase 1: Grammar, AST, and parsing support
  - Phase 2: Type system integration (PerkTable, PerkImplementationTable)
  - Phase 3: Implementation validation and checking
  - Phase 4: Constraint validation for generic structs, enums, and functions
  - Phase 5: Code generation with bare return types
- Generic functions with automatic type inference
- Generic functions with perk constraints (T: PerkName)
- Multiple perk constraints support (T: Perk1 + Perk2)
- Synthetic perk implementations for primitive types
- Comprehensive perks test suite (38 tests)
- Comprehensive perks documentation in docs/perks.md
- Example 23: Basic perks usage (docs/examples/23-perks-basic.sushi)
- Example 24: Generic constraints with perks (docs/examples/24-perks-constraints.sushi)
- Complete examples catalog in docs/examples/README.md
- Standard library documentation
  - docs/standard-library.md
  - docs/stdlib/env.md (environment variables)
  - docs/stdlib/math.md (mathematical functions)
  - docs/stdlib/platform.md (platform-specific APIs)
- Enhanced internals documentation
  - docs/internals/architecture.md
  - docs/internals/backend.md
- GitHub Actions workflow for automated testing
- Test badges in README.md showing test status
- Linux stdlib support in test builds

### Fixed
- f64 arithmetic and comparison operations
- Generic extension methods resolution during type validation
- Mixed-type numeric operations and validation
- Struct/enum extension method lookup
- Perk method argument type validation
- Result handling in perk tests
- BoundedTypeParam compatibility with generics system

### Changed
- Refactored name mangling to use shared utility functions
- Updated README.md with perks section and example
- Updated README.md with improved feature descriptions
- Updated docs/README.md to include perks in language reference
- Improved error messages for perk constraint violations

### Removed
- Various obsolete markdown documentation files

## [0.0.7] - 2025-11-03

First public release.
