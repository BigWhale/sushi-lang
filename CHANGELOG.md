# Changelog

All notable changes to Sushi Lang will be documented in this file.

## [0.10.0] - 2026-07-07

The closures release. Tier 1 closures are complete, and three follow-ups land together: an
opt-in combinators module, call-through arbitrary function values, and generic-function
references. This supersedes the 0.9.0 note that there were "no closures in v1".

### Added
- `use <collections/iter>`: `map`, `filter`, `fold`, and `compose` over `List<T>`, as ordinary
  generic free functions. This is the first Sushi-source standard-library module -- a bundled
  `.sushi` file merged as a compilation unit and monomorphized through the normal generic
  pipeline (no bitcode; nothing emitted unless a combinator is instantiated). Element types are
  copy/primitive for now
- Call-through arbitrary expressions that evaluate to a function value (T2.4): a captured closure
  called in a lambda body (`env.f(x)`, the basis for `compose` and capturing-and-calling a
  closure), a fn-typed struct field called directly as `obj.handler()` (a same-named method
  wins), and call-through a `List` get-out or parenthesized expression (`fns.get(0)??(x)`,
  `(e)()`)
- Generic-function references (T2.3): a generic function may be referenced as a value when an
  explicit function type is present, e.g. `let fn(i32) -> i32 g = identity` with
  `fn identity<T>(T x) T`. Passing a generic-fn reference into a higher-order function works via
  such a typed binding

### Changed
- The `??` operator now unwraps a first-class function call's result and a `Maybe` `Some`
  payload during type inference, so a function value called in a lambda body infers its return
  type. As a result, two type mismatches that previously surfaced as a backend cast failure now
  report the precise front-end `CE2002` at the assignment
- `CE2094` no longer rejects capturing-and-calling a closure value; an owning closure value is
  move-captured into the environment like `List`/`Own`
- `CE2093` is lifted for a generic-fn reference that has an explicit expected function type; a
  bare reference with no expected type (an argument position without a typed binding) still
  reports `CE2093`

## [0.9.1] - 2026-07-06

A maintenance release: a new safe process-spawn stdlib primitive (the last self-hosting
linchpin), the correctness fixes surfaced by the 0.9.0 stdout-correctness test baseline, and a
documentation-link fix.

### Added
- Safe process spawning in `sys/process`: `run(string cmd, string[] args) -> Result<ProcessOutput,
  ProcessError>`. Built on `posix_spawnp` — argv, no shell — it returns the child's exit code plus
  separately-captured stdout/stderr in `ProcessOutput`; a non-zero exit is `Ok`, and `SpawnFailed`
  / `SignalReceived` are the `ProcessError` variants. `run("clang", [...])` is verified end to end,
  completing the text-IR -> toolchain path for self-hosting

### Fixed
- `Maybe<T>` / `Result<T, E>` `.realise(default)` crashed for a struct payload with a nested
  aggregate field
- Nested `Own<Own<T>>` was double-freed on cleanup; ownership is now coherent through the nested box
- `HashMap.debug()` corrupted string keys/values in its output

### Testing
- Established a stdout-correctness test baseline: the runtime-output ratchet is at gap 0
  (`BASELINE = 0`) with a quarantine guard, so a wrong codegen result can no longer pass the suite
  silently
- Fixed stale hard-coded paths in the file-I/O tests

### Documentation
- README documentation links now point to the rendered docs site

## [0.9.0] - 2026-07-04

A maintenance release: one new language feature — context-typed numeric literals — plus a
batch of correctness fixes surfaced by a documentation audit, a diagnostic-wording change, and
CI/docs improvements.

### Added
- Context-typed numeric literals (Rust/Go untyped-constant model): a bare numeric literal takes
  its type from its expected/context type — annotation, const, function argument/return, struct
  field, array element, and binary-op operand (`a + 1` where `a: u8` types the `1` as `u8`) —
  range-checked at compile time. A literal with no numeric context still defaults to `i32`/`f64`.
  This is literal *typing*, not value coercion (an already-typed value still needs `as`)
  - New diagnostic **CE2073** (context-typed literal out of range for its target type); **CE2070**
    retained for a context-free literal that overflows its `i32` default

### Changed
- Diagnostic wording: user-facing "rebind" renamed to "reassign" — **CE1002** now reads
  "assignment to undeclared variable" and **CE2401** "cannot move/reassign"

### Fixed
- One-argument `Result<T>` annotation was rejected (CE2001); it now normalizes to
  `Result<T, StdError>` at parse time
- `foreach` / `.iter()` over a borrowed dynamic array (`&peek`/`&poke T[]`) failed with CE0042
- Higher-order function call-through (`??` through an `fn`-typed parameter) was order-dependent
  (CE0055 depending on definition order)
- `Maybe<enum>.realise(default)` with a non-primitive (enum) payload misrouted to the `Result`
  handler and failed to lower (CE0017)
- Loop-body locals abandoned via `break`/`continue` were never freed (RAII leak); a bounded
  loop-exit cleanup now frees them exactly once without double-freeing locals that outlive the loop
- Inline `match` / `??` on stdlib builtins (`getcwd`, `getenv`, `.find_last`) failed with CE0055 /
  "undefined name" unless the call was first bound to a `let`

### Documentation
- Language reference updated for context-typed numeric literals
- `sys/process` guide examples corrected (all 20 examples compile with zero errors/warnings)
- First-class-functions design note reconciled with the implemented compiler behavior
- CI: docs site auto-deploys after the Tests workflow passes on `main`

## [0.8.0] - 2026-06-11

A milestone release centered on self-hosting enablers: a foreign function interface, three
forms of variadic functions, generic instantiation across library boundaries, and first-class
functions — alongside a hardened test suite, asserted diagnostics, and a large batch of
correctness fixes.

### Added
- Foreign Function Interface (FFI) for calling C
  - `unsafe external "C" as <ns> [because "..."]:` blocks with namespaced call sites
    (`libc.strlen(s)`)
  - Opaque, unmanaged foreign `ptr` type (LLVM `i8*`), exempt from borrow checking and RAII
  - `string` arguments auto-marshalled to C `char*` and freed at scope exit (no leak)
  - Variadic externs: a bare trailing `...` binds libc varargs (`printf` family)
  - Full `ptr` quarantine — unit gate, and rejection of `ptr` in public APIs, operators, method
    calls, and generic arguments; diagnostics CW5001 and CE5001–CE5012
- Variadic functions in three deliberately separate forms
  - Native homogeneous `...T` — trailing arguments collected into an owned `T[]`
  - Parameter packs `...Ts` with a compile-time-unrolled `expand` block (heterogeneous, generic,
    zero-cost) — including perk-constrained packs
  - Parameter packs ship and monomorphize across `.slib` library boundaries
  - Diagnostics CE0114–CE0119, CE2090, CE2091
- Generic instantiation across `.slib` library boundaries
  - Generic functions, generic structs/enums, and variadic-generic packs ship as source templates
    and monomorphize at the consumer
  - Concrete perk implementations ship and register at the consumer
  - Export closure of library-private symbols an exported generic transitively references;
    CE5006 narrowed and CE5007 added
- First-class functions
  - Function types `fn(P...) -> T [| E]` and function values: reference a top-level function,
    store it in a variable/struct field/`List`, pass it, and call through it (no closures in v1)
  - Diagnostics CE2092 (call-through mismatch) and CE2093 (illegal/generic function reference)
- Testing and tooling
  - `pytest` layer for compiler internals (fingerprints, cache, semantic errors)
  - Dedicated enum suite and end-to-end incremental-compilation/cache tests
  - Performance regression harness (report mode)
  - Error codes asserted on the compilation path, with a broad diagnostics backfill

### Changed
- Runtime validation is the default for tests; CI runs the enhanced runner on a macOS + Linux
  matrix
- Internal refactors: `stdlib.py` split into a `stdlib/` package; `TypeValidator` decomposed into
  focused collaborator modules
- CI: GitHub Actions bumped to Node 24 runtimes; tests run on `merge_group`

### Fixed
- i64 width correctness across inference, printing, comparisons, and literals
- Unsigned `/` and `%` no longer emit signed `sdiv`/`srem`
- CE0021 crash on `Result<ptr>`; `ptr` is confined to its declaring unit (CE5008)
- Heap-owning struct value semantics (independent buffer per by-value argument)
- Local `List<T>` variables freed via RAII; dynamic-array RAII leak on the all-but-one return
  path
- `string[]` heap corruption from an element alloc-size mismatch
- `List<EnumType>.destroy()` internal compiler error
- `compute_unit_fingerprint` crash on enums
- `bool` string methods rendering as `0`/`1` in interpolation
- Direct `.hash()` on an enum value (CE0019 internal compiler error)

### Documentation
- Unified MkDocs site combining the guided tutorial and the reference, with Sushi syntax
  highlighting
- New guides and tutorial chapters for FFI / foreign pointers, variadic functions, and
  first-class functions
- Design notes for variadics and first-class functions
- CHANGELOG backfilled for 0.7.0 and 0.7.1

## [0.7.1] - 2026-03-21

### Added
- Glossary section in README

### Fixed
- Nori banner rendering

## [0.7.0] - 2026-03-21

### Added
- Nori package manager (`nori` CLI), shipped alongside the compiler
  - Commands: `init`, `build`, `install`, `list`, `info`, `remove`, `publish`, `search`, `status`, `login`, `help`
  - Packages install to `~/.sushi/bento/` with executable symlinks in `~/.sushi/bin/`
  - `.nori` archive format for distributable library packages
  - Compiler auto-discovers installed libraries without `SUSHI_LIB_PATH`
- Project-level dependency environments
  - Global versioned store (`~/.sushi/store/`) with per-project `.sushi_bento/` symlinks
  - Compiler resolves project-local dependencies before global packages
- Omakase package repository integration (omakase.lubica.net)
  - `nori login <api-key>` with credentials stored in `~/.sushi/credentials.toml`
  - `nori publish` uploads packages; `nori search` browses the registry; `nori status` reports login and published packages
  - Omakase API contract specification: authentication, users, groups, ownership, stats, and package storage layout

### Changed
- CI split into separate test and badges jobs

### Documentation
- New documentation: `docs/package-manager.md` for the Nori package manager

## [0.6.1] - 2026-02-22

### Added
- End-to-end release tests that validate wheels before publishing
  - Installs wheel into clean venv, compiles and runs 5 sushi programs
  - Tests basic compilation, stdlib linking, generics, HashMap, multi-file builds
  - Runs on both Linux and macOS in CI

### Changed
- Release workflow restructured: wheels are tested on both platforms before publishing to GitHub Releases

## [0.6.0] - 2026-02-22

### Added
- Incremental compilation with per-unit object file caching
  - Each .sushi unit compiles to its own .o file, cached in `__sushi_cache__/`
  - Cache invalidation via content-based SHA-256 semantic fingerprints
  - Stdlib and library imports also cached as separate .o files
  - Monomorphized generics use `linkonce_odr` linkage for linker deduplication
- New CLI flags: `--no-incremental`, `--clean-cache`, `--cache-dir`
- HashMap `.entries()` method returning `Iterator<Entry<K, V>>` for key-value pair iteration
- `Entry<K, V>` struct with `.key` and `.value` fields
- Error diagnostic infrastructure with note/help sub-diagnostics
  - Polished rendering with T-junction markers, continuous box drawing, colored notes
- `--platform` flag for stdlib build script

### Changed
- Multi-unit compilation now uses two-path architecture: monolithic (single-file) and incremental (multi-unit)
- Semantic analysis remains whole-program; only LLVM codegen is cached per-unit

### Fixed
- Struct type propagation in `Result.realise()` method
- Missing Linux stdlib .bc files
- CI workflow: always run tests on push, skip only for docs-only PRs
- Reliable code change detection using dorny/paths-filter

### Documentation
- Stdlib build and library format documentation
- Updated compiler reference with incremental compilation section
- Updated architecture docs with incremental compilation internals

## [0.5.0] - 2025-12-20

### Changed
- Restructured package into single `sushi_lang` directory for cleaner pip installation
- Updated all internal imports to use new package structure
- Release workflow now extracts changelog for GitHub release notes automatically
- Test workflow installs dev dependencies (tqdm) via `--extra dev`

### Added
- Package entry points (`__init__.py`, `__main__.py`) for proper Python module support
- PACKAGE.md with packaging and release documentation

## [0.4.2] - 2025-12-17

### Added
- Constant folding for arithmetic and bitwise operations
- Content-based string constant deduplication
- AST type annotations for TryExpr error propagation
- LibraryRegistry to centralize library metadata parsing
- AST visitor base class with complete node coverage
- Semantic analysis pipeline with timing instrumentation
- Progress bar for test runner (tqdm)

### Changed
- Enforce type propagation single entry point via private functions
- Implement type-level COW for monomorphization transformer
- Increase default test timeout from 5s to 10s
- Extend enum_utils with variant data packing and unpacking utilities
- Extract shared type resolution helpers for TypeMapper and TypeSizing
- Extract symbol merging into dedicated SymbolTableMerger class
- Refactor scope manager to use flat cache as primary storage
- Consolidate Result type handling into ResultBuilder class
- Consolidate type resolution functions into TypeResolver class
- Use --frozen flag when invoking sushi compiler

### Fixed
- Reference parameter type resolution and array indexing
- HashMap and generic types passed by reference to functions

### Examples
- Added rudimentary Markov chain example

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
