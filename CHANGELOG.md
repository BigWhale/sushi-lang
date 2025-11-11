# Changelog

All notable changes to Sushi Lang will be documented in this file.

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
