# Compiler Architecture

[← Back to Documentation](../index.md)

Internal documentation for Sushi compiler architecture and design.

## Overview

Sushi follows a clean multi-pass compiler architecture:

```
Source Code (.sushi)
    ↓
Lark Parser (grammar.lark)
    ↓
AST Builder (semantics/ast_builder/)
    ↓
Multi-Pass Semantic Analysis (semantics/passes/)     ← always whole-program
    ↓
Per-Unit Fingerprint Computation                      ← cache check
    ↓
LLVM IR Generation (backend/codegen_llvm.py)          ← per-unit, cached .o files
    ↓
LLVM Optimization Pipeline
    ↓
Clang Linking (all .o files)
    ↓
Native Executable
```

For multi-unit projects, the compiler uses **incremental compilation**: each unit
is compiled to its own `.o` file and cached in `__sushi_cache__/`. Only units
whose semantic fingerprint has changed are recompiled. Single-file programs use
the direct monolithic path.

## Directory Structure

```
sushi/
├── compiler/                   # Compiler package (entry point: compiler/cli.py:main)
│   ├── cli.py                  # CLI argument parsing, main() entry point
│   ├── pipeline.py             # Multi-file compilation orchestration
│   ├── loader.py               # Unit loading and dependency resolution
│   ├── cache.py                # Incremental compilation cache manager
│   └── fingerprint.py          # Per-unit semantic fingerprint computation
├── grammar.lark                # Lark grammar specification
├── semantics/
│   ├── ast_builder/           # Modular AST construction, split by concern
│   │   ├── builder.py         # Main orchestrator
│   │   ├── declarations/      # Top-level constructs
│   │   │   ├── functions.py   # Function parsing
│   │   │   ├── structs.py     # Struct definitions
│   │   │   ├── enums.py       # Enum definitions
│   │   │   ├── extensions.py  # Extension methods
│   │   │   ├── perks.py       # Perk definitions
│   │   │   ├── constants.py   # Constant declarations
│   │   │   └── imports.py     # Use statements
│   │   ├── expressions/       # Expression parsing
│   │   │   ├── parser.py      # Main expression parser
│   │   │   ├── literals.py    # Literal values
│   │   │   ├── operators.py   # Binary/unary operators
│   │   │   ├── calls.py       # Function calls
│   │   │   ├── members.py     # Member access
│   │   │   ├── arrays.py      # Array expressions
│   │   │   └── chains.py      # Chained expressions
│   │   ├── statements/        # Statement parsing
│   │   │   ├── parser.py      # Main statement parser
│   │   │   ├── variables.py   # Let/rebind
│   │   │   ├── returns.py     # Return statements
│   │   │   ├── control_flow.py # If/elif/else
│   │   │   ├── loops.py       # While/foreach
│   │   │   ├── matching.py    # Pattern matching
│   │   │   ├── blocks.py      # Block statements
│   │   │   ├── flow.py        # Break/continue
│   │   │   ├── io.py          # Print/println
│   │   │   └── calls.py       # Statement-level calls
│   │   ├── types/             # Type parsing
│   │   │   ├── parser.py      # Main type parser
│   │   │   ├── generics.py    # Generic types
│   │   │   ├── arrays.py      # Array types
│   │   │   ├── references.py  # Reference types
│   │   │   └── user_defined.py # Struct/enum types
│   │   ├── utils/             # Shared utilities
│   │   │   ├── tree_navigation.py # Tree traversal
│   │   │   ├── expression_discovery.py # Expression finding
│   │   │   └── string_processing.py # String handling
│   │   └── exceptions.py      # Custom parsing exceptions
│   ├── passes/
│   │   ├── collect/           # Phase 0: Collection passes
│   │   │   ├── constants.py   # Constant definitions
│   │   │   ├── functions.py   # Function signatures
│   │   │   ├── structs.py     # Struct definitions
│   │   │   ├── enums.py       # Enum definitions
│   │   │   ├── perks.py       # Perk definitions
│   │   │   └── utils.py       # Collection utilities
│   │   ├── scope.py           # Phase 1: Scope and variable analysis
│   │   ├── ast_transform.py   # Phase 1.7: AST transformation
│   │   ├── const_eval.py      # Constant evaluation
│   │   ├── hash_registration.py # Phase 1.8: Hash function derivation
│   │   ├── borrow.py          # Phase 3: Borrow checking
│   │   └── types/             # Phase 2: Modular type validation
│   │       ├── utils.py       # Type utilities
│   │       ├── inference.py   # Type inference
│   │       ├── compatibility.py # Type compatibility
│   │       ├── propagation.py # Type propagation
│   │       ├── resolution.py  # Type resolution
│   │       ├── result_validation.py # Result handling
│   │       ├── field_matcher.py # Struct field matching
│   │       ├── perks.py       # Perk constraint checking
│   │       ├── expressions.py # Expression type checking
│   │       ├── matching.py    # Pattern match validation
│   │       ├── statements.py  # Statement validation
│   │       └── calls/         # Function call validation
│   │           ├── user_defined.py # User function calls
│   │           ├── methods.py # Method calls
│   │           ├── structs.py # Struct construction
│   │           ├── enums.py   # Enum construction
│   │           └── generics.py # Generic calls
│   └── generics/
│       ├── types.py           # Generic type definitions
│       ├── name_mangling.py   # Name mangling for monomorphization
│       ├── constraints.py     # Perk constraints
│       ├── instantiate/       # Phase 1.5: Instantiation collection
│       │   ├── types.py       # Type instantiations
│       │   ├── functions.py   # Function instantiations
│       │   └── expressions.py # Expression instantiations
│       ├── monomorphize/      # Phase 1.6: Monomorphization
│       │   ├── transformer.py # Main transformer
│       │   ├── types.py       # Type monomorphization
│       │   └── functions.py   # Function monomorphization
│       └── providers/         # Generic type providers
│           ├── interface.py   # Provider protocol
│           └── registry.py    # Provider registry
├── backend/
│   ├── codegen_llvm.py        # Main LLVM orchestrator
│   ├── constants/              # Centralized LLVM constants (DRY)
│   │   ├── llvm_values.py      # FALSE_I1, ZERO_I32, make_i32_const(), etc
│   │   ├── bit_widths.py
│   │   ├── error_codes.py
│   │   ├── hash_constants.py
│   │   ├── indices.py
│   │   └── sizes.py
│   ├── gep_utils.py           # GetElementPtr utilities
│   ├── enum_utils.py          # Enum tag/data utilities
│   ├── destructors.py         # Unified recursive destruction
│   ├── platform_detect.py     # Target platform detection
│   ├── expressions/           # Expression emission (array codegen lives under
│   │   │                      # backend/types/arrays/ instead, see below)
│   │   ├── literals.py
│   │   ├── operators.py       # Binary/unary operators
│   │   ├── memory.py
│   │   ├── casts.py
│   │   ├── structs.py         # Struct operations
│   │   ├── enums.py
│   │   ├── type_utils.py
│   │   └── calls/             # Subdivided for complexity
│   │       ├── dispatcher.py  # Main call routing
│   │       ├── generics.py    # Generic method instantiation
│   │       ├── intrinsics.py  # Compiler intrinsics
│   │       ├── file_open.py   # File I/O operations
│   │       ├── utils.py       # Call utilities
│   │       └── stdlib/        # Standard library calls, one module per area
│   │           # (io.py, strings.py, math.py, time.py, random.py, env.py,
│   │           #  process.py, primitives.py)
│   ├── statements/            # Statement emission
│   │   ├── io.py
│   │   ├── loops.py           # while, foreach (19KB)
│   │   ├── control_flow.py
│   │   ├── returns.py
│   │   ├── variables.py       # let, rebind (13KB)
│   │   ├── matching.py        # Pattern matching (20KB)
│   │   ├── initialization.py  # Variable initialization patterns
│   │   └── utils.py           # Statement utilities (11KB)
│   ├── runtime/               # Runtime support
│   │   ├── strings.py         # String operations
│   │   ├── formatting.py      # String interpolation
│   │   ├── errors.py          # Error handling
│   │   └── externs/           # Organized libc bindings
│   │       ├── libc_stdio.py  # printf, fopen, etc.
│   │       ├── libc_strings.py # strlen, strcmp, memcpy
│   │       ├── libc_ctype.py  # isalpha, isdigit, etc.
│   │       └── libc_process.py # exit, getenv, setenv
│   ├── types/                 # Type-specific codegen
│   │   ├── arrays/             # Construction, indexing, bounds checks
│   │   │   ├── dispatcher.py   # Array-method call routing
│   │   │   ├── indexing.py     # Direct indexing, GEP helpers
│   │   │   ├── bounds.py       # Bounds-check emission
│   │   │   ├── literals.py     # Array literal construction
│   │   │   ├── utils.py
│   │   │   └── methods/        # Array method implementations
│   │   │       ├── core.py     # len, get, push, pop
│   │   │       ├── hashing.py  # Hash function generation
│   │   │       ├── iterators.py # Iterator creation
│   │   │       ├── safe_access.py # .get() Maybe@(T) wrapping
│   │   │       ├── transforms.py # fill, reverse, clone
│   │   │       └── utf8_validate.py # u8[].to_string_checked()
│   │   ├── primitives/          # i8..u64, f32/f64, bool
│   │   │   ├── bit_reinterpret.py # to_bits()/from_bits()
│   │   │   ├── hashing.py
│   │   │   └── to_str.py
│   │   ├── structs.py
│   │   ├── enums.py
│   │   └── hash_utils.py
│   ├── memory/                # Memory management
│   │   ├── scopes.py          # Scope-based cleanup
│   │   ├── dynamic_arrays.py  # Dynamic array management
│   │   └── heap.py            # Heap allocation (malloc/free)
│   └── generics/              # Generic type implementations
│       ├── codegen.py         # Generic code generation
│       ├── enum_methods_base.py # Base for Result/Maybe
│       ├── extensions.py      # Generic extension methods
│       ├── maybe.py           # Maybe@(T) (19KB)
│       ├── own.py             # Own@(T)
│       ├── results.py         # Result@(T)
│       ├── hashmap/           # HashMap@(K,V) implementation
│       │   ├── types.py
│       │   ├── validation.py
│       │   ├── utils.py
│       │   └── methods/
│       │       ├── core.py    # new, insert, get, contains
│       │       ├── mutations.py # remove, free, rehash
│       │       ├── debug.py   # debug printing
│       │       └── iterators.py # iterator support
│       └── list/              # List@(T) implementation
│           ├── types.py
│           ├── validation.py
│           ├── methods_simple.py # len, is_empty
│           ├── methods_capacity.py # reserve, shrink
│           ├── methods_modify.py # push, insert, remove
│           ├── methods_destroy.py # free, destroy
│           ├── methods_debug.py # debug printing
│           └── methods_iter.py # iterator support
└── stdlib/
    ├── src/                   # Python source (LLVM IR generators)
    │   ├── common.py          # Shared utilities
    │   ├── conversions.py     # Type conversions
    │   ├── error_emission.py  # Error code helpers
    │   ├── ir_builders.py     # IR construction helpers
    │   ├── ir_common.py       # Common IR patterns
    │   ├── libc_declarations.py # Centralized libc declarations
    │   ├── string_helpers.py  # String operation helpers
    │   ├── type_converters.py # Type conversion utilities
    │   ├── type_definitions.py # Type definition helpers
    │   ├── collections/
    │   │   ├── strings/       # String operations (organized)
    │   │   │   ├── common.py
    │   │   │   ├── compiler/  # Built-in string ops
    │   │   │   ├── intrinsics/ # Low-level UTF-8 ops
    │   │   │   └── methods/   # High-level methods
    │   │   ├── list.py        # List@(T)
    │   │   └── hashmap.py     # HashMap@(K,V)
    │   ├── io/
    │   │   ├── stdio/         # Platform-specific stdio
    │   │   │   ├── common.py
    │   │   │   ├── darwin.py
    │   │   │   └── linux.py
    │   │   └── files/         # File operations
    │   │       ├── common.py
    │   │       ├── read.py
    │   │       ├── write.py
    │   │       ├── seek.py
    │   │       ├── status.py
    │   │       ├── iterators.py
    │   │       └── binary.py
    │   ├── math/              # Math operations
    │   │   └── operations.py
    │   ├── time/              # Time/sleep functions
    │   ├── random/            # Random number generation
    │   │   └── generators.py
    │   ├── sys/               # System modules
    │   │   ├── env/           # Environment variables
    │   │   └── process/       # Process control
    │   └── _platform/         # Platform-specific implementations
    │       ├── __init__.py    # get_platform_module() helper
    │       ├── posix/         # POSIX implementations
    │       ├── darwin/        # macOS implementations
    │       └── linux/         # Linux implementations
    └── dist/                  # Platform-organized precompiled .bc files
        ├── darwin/            # macOS
        │   ├── collections/strings.bc
        │   ├── io/stdio.bc
        │   ├── io/files.bc
        │   ├── math.bc
        │   ├── time.bc
        │   ├── random.bc
        │   └── sys/
        │       ├── env.bc
        │       └── process.bc
        └── linux/             # Linux (similar structure)
```

## Compilation Phases

### Phase 0: Headers and Constants

**Files:** `semantics/passes/collect/*.py`

**Responsibilities:**
- Parse constant definitions (`collect/constants.py`)
- Collect function signatures (`collect/functions.py`)
- Register struct definitions (`collect/structs.py`)
- Register enum definitions (`collect/enums.py`)
- Register perk definitions (`collect/perks.py`)
- Build initial symbol table

**Output:**
- Global constants map
- Function signature registry
- Generic type definitions

### Phase 1: Scope and Variables

**File:** `semantics/passes/scope.py`

**Responsibilities:**
- Variable declaration and usage tracking
- Scope analysis
- Move semantics validation
- Borrow tracking initialization

**Output:**
- Variable scopes
- Move analysis results
- Borrow tracking data

### Phase 1.5: Generic Instantiation Collection

**Files:** `semantics/generics/instantiate/*.py`

**Responsibilities:**
- Detect generic type usage (`instantiate/types.py`)
- Detect generic method calls (`instantiate/functions.py`)
- Infer type arguments from usage (`instantiate/expressions.py`)
- Collect all required instantiations

**Example:**
```sushi
let List@(i32) nums = List.new()  # Collect: List@(i32)
nums.push(42)                     # Collect: List@(i32).push
```

### Phase 1.6: Monomorphization

**Files:** `semantics/generics/monomorphize/*.py`

**Responsibilities:**
- Substitute generic type parameters (`monomorphize/transformer.py`)
- Create concrete types from generic definitions (`monomorphize/types.py`)
- Generate specialized function/method instances (`monomorphize/functions.py`)

**Example:**
```
extend Box@(T) unwrap() T
    ↓
extend Box@(i32) unwrap() i32
extend Box@(string) unwrap() string
```

### Phase 1.7: AST Transformation

**File:** `semantics/passes/ast_transform.py`

**Responsibilities:**
- Resolve extension methods to function calls
- Transform UFCS (Uniform Function Call Syntax)
- Type inference for expressions
- Simplify AST for backend

**Example:**
```sushi
arr.len()  # Method call
    ↓
array_len(arr)  # Function call
```

### Phase 1.8: Hash Function Derivation

**File:** `semantics/passes/hash_registration.py`

**Responsibilities:**
- Auto-generate `.hash()` methods for all types
- Compose hash functions for structs
- Validate hashability

**Derived for:**
- Primitives (FxHash for ints, FNV-1a for strings)
- Structs (field-wise hashing)
- Enums (discriminant + variant data hashing)
- Arrays (element-wise hashing)

### Phase 2: Type Validation

**Files:** `semantics/passes/types/*.py`

**Responsibilities:**
- Type checking all expressions
- Result@(T) handling validation
- Pattern match exhaustiveness
- Type compatibility checking

**Modular type checking:**
- `types/resolution.py` - Type resolution (Result@(T) wrapping)
- `types/propagation.py` - Type propagation to constructors
- `types/result_validation.py` - Result.Ok/Err validation
- `types/expressions.py` - Expression type checking
- `types/statements.py` - Statement validation
- `types/calls/*.py` - Function call validation (user-defined, methods, structs, enums, generics)
- `types/matching.py` - Pattern match validation
- `types/compatibility.py` - Type compatibility checking
- `types/inference.py` - Type inference
- `types/perks.py` - Perk constraint checking
- `types/field_matcher.py` - Struct field matching

### Phase 3: Borrow Checking

**File:** `semantics/passes/borrow.py`

**Responsibilities:**
- Ensure single active borrow per variable
- Prevent move-while-borrowed
- Prevent use-after-destroy
- Track reference lifetimes

**Errors detected:**
- CE2405: Use of moved variable
- CE1007: Cannot rebind while borrowed
- CE2406: Use of destroyed variable

## Incremental Compilation

Multi-unit projects use per-unit `.o` file caching to avoid redundant LLVM codegen.

### Architecture

```
Parse all .sushi files                           (always)
  → Whole-program semantic analysis              (always, fast Python)
  → Compute per-unit semantic fingerprints       (always, fast)
  → Per-unit LLVM codegen → .o file              (only if fingerprint changed)
  → Link all .o files → executable               (always)
```

Semantic analysis (passes 0-3) remains whole-program because generic instantiation
collection and monomorphization need the complete call graph. This is pure Python
and runs in well under a second. The expensive part — LLVM codegen + optimization +
object emission — is cached per-unit.

### Cache Structure

```
__sushi_cache__/
  cache.json                    ← manifest (compiler version, platform, opt level)
  units/
    main.o                      ← cached object file
    main.o.fingerprint          ← semantic fingerprint
    helpers/math.o
    helpers/math.o.fingerprint
  stdlib/
    io_stdio.o                  ← compiled stdlib bitcode
  libs/
    mylib.o                     ← compiled library bitcode
```

### Fingerprint Computation

Each unit's fingerprint is a SHA-256 hash of:
- Source file content
- Public symbol signatures from dependencies
- AST structure (structs, enums, extensions, perk impls, use statements)
- Monomorphized extensions consumed by this unit

### Linkage Rules

- Public functions/constants: `external` linkage
- Private functions/constants: `internal` linkage
- Monomorphized generics: `linkonce_odr` linkage (linker deduplicates across units)
- Inline runtime functions (`llvm_strlen`, `llvm_strcmp`, `utf8_char_count`): `linkonce_odr`

### Key Files

- `compiler/pipeline.py` — orchestrates monolithic vs incremental compilation paths
- `compiler/cache.py` — `CacheManager` class: directory management, manifest, staleness detection
- `compiler/fingerprint.py` — `compute_unit_fingerprint()`, `compute_stdlib_fingerprint()`, `compute_lib_fingerprint()`
- `backend/codegen_llvm.py` — `build_module_single_unit()`, `compile_single_unit_to_object()`, `link_object_files()`

## Backend Architecture

### LLVM Code Generation

**Main file:** `backend/codegen_llvm.py`

**Process:**
1. Create LLVM module and function declarations
2. Emit standard library linkage
3. Generate code for each function
4. Apply optimization passes
5. Link with clang

**Key classes:**
- `LLVMCodeGenerator` - Main orchestrator
- `ExpressionEmitter` - Expression code generation
- `StatementEmitter` - Statement code generation
- `TypeManager` - LLVM type creation
- `MemoryManager` - RAII and cleanup

### Expression Emission

Located in `backend/expressions/`:

- **literals.py** - Constants, strings, arrays
- **operators.py** - Binary/unary operations
- **memory.py** - Loads, stores, references
- **casts.py** - Type conversions
- **structs.py** - Struct field access
- **enums.py** - Enum construction/matching
- **calls/** - Function/method calls, subdivided by concern: `dispatcher.py`
  (main call routing), `generics.py`, `intrinsics.py`, `file_open.py`,
  `utils.py`, and a `stdlib/` subpackage with one module per stdlib area

Array *expression* codegen (construction, indexing, methods) lives under
`backend/types/arrays/` instead — see [Type System](#type-system) below.

### Statement Emission

Located in `backend/statements/`:

- **io.py** - println, print, stdin/stdout
- **loops.py** - while, foreach
- **control_flow.py** - if/elif/else, break, continue
- **returns.py** - Result.Ok/Err returns
- **variables.py** - let, rebind
- **matching.py** - match expressions

### Type System

Located in `backend/types/`:

- **primitives/** - i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool
  (`bit_reinterpret.py`, `hashing.py`, `to_str.py`)
- **arrays/** - Fixed and dynamic arrays: construction, indexing, bounds checks,
  and a `methods/` subpackage for `.len()`/`.get()`/`.push()`/etc
- **structs.py** - Struct layout and field access
- **enums.py** - Enum discriminant and variant data
- **hash_utils.py** - Hash function generation

### Runtime Support

Located in `backend/runtime/`:

- **strings.py** - String operations (len, size, find, split, trim)
- **formatting.py** - String interpolation
- **errors.py** - Runtime error messages
- **libc_externs.py** - malloc, free, printf, etc.

## Standard Library

### Structure

```
stdlib/
├── src/           # Python code that generates LLVM IR
└── dist/          # Precompiled .bc files
```

### Import Mechanism

```sushi
use <collections/strings>
```

Maps to: `stdlib/dist/collections/strings.bc`

### Building stdlib

```bash
# Rebuild stdlib modules
cd stdlib/src/collections
python strings.py  # Generates ../../dist/collections/strings.bc
```

## Optimization Pipeline

### Levels

- **none** - No optimization
- **mem2reg** - SROA only (default)
- **O1** - Basic (CFG simplification, DCE, instruction combining)
- **O2** - Moderate (+ loop opts, GVN, SCCP, jump threading)
- **O3** - Aggressive (+ loop unrolling, strength reduction, inlining)

### LLVM Passes

Applied in `backend/codegen_llvm.py:apply_optimizations()`:

```python
pm = llvm.ModulePassManager()

if opt_level == 'O1':
    pm.add_promote_memory_to_register_pass()
    pm.add_cfg_simplification_pass()
    pm.add_instruction_combining_pass()
    pm.add_dead_code_elimination_pass()

elif opt_level == 'O2':
    # O1 passes + ...
    pm.add_sccp_pass()
    pm.add_loop_rotation_pass()
    pm.add_gvn_pass()
    # ... etc

pm.run(module)
```

## Recent Architectural Improvements

### Breaking Circular Imports: Deferred Local Imports

There used to be a `backend/interfaces.py` module of `Protocol` classes for this
purpose; it was deleted (Tier 4.5) and no `Protocol`-based scheme has replaced it
(`grep -rl Protocol sushi_lang/backend` now returns nothing). The mechanism that
actually breaks circular dependencies between backend components today is simpler:
an import that would cycle at module-load time is written **inside the function
or method that needs it** instead of at the top of the file, so the cycle only
has to resolve at call time, by which point both modules have finished loading.

**Example** (`backend/destructors.py`, `_emit_list_value_destructor`):
```python
def _emit_list_value_destructor(codegen, builder, value_ptr, value_type):
    from sushi_lang.backend.generics.list.types import extract_element_type
    element_type = extract_element_type(value_type, codegen)
    ...
```

`generics/list/types.py` needs destructor helpers, and `destructors.py` needs to
inspect a `List@(T)`'s element type — a top-level import on either side would
cycle. Deferring the import into the function body sidesteps it.

A second, narrower use of `TYPE_CHECKING`-guarded imports covers pure type
annotations (no runtime import at all), e.g. `backend/enum_utils.py`:
```python
if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
```

**Benefits (unchanged from the old Protocol approach):**
- Eliminates circular import issues
- Reduces coupling between modules — a module only pays the import cost for the
  functions it actually calls

### Centralized Utilities (DRY Principle)

Recent refactors have extracted common patterns into reusable utility modules:

#### LLVM Constants Module

**File:** `backend/constants/llvm_values.py`

Eliminates duplication of LLVM constant creation across 100+ call sites.

**Provides:**
```python
# Boolean constants
FALSE_I1 = ir.Constant(ir.IntType(1), 0)
TRUE_I1 = ir.Constant(ir.IntType(1), 1)

# Integer constants
ZERO_I8 = ir.Constant(ir.IntType(8), 0)
ONE_I8 = ir.Constant(ir.IntType(8), 1)
ZERO_I32 = ir.Constant(ir.IntType(32), 0)
ONE_I32 = ir.Constant(ir.IntType(32), 1)
TWO_I32 = ir.Constant(ir.IntType(32), 2)
ZERO_I64 = ir.Constant(ir.IntType(64), 0)
ONE_I64 = ir.Constant(ir.IntType(64), 1)

# Factory functions
def make_i8_const(value: int) -> ir.Constant: ...
def make_i32_const(value: int) -> ir.Constant: ...
def make_i64_const(value: int) -> ir.Constant: ...
```

#### GetElementPtr Utilities

**File:** `backend/gep_utils.py`

Type-safe helpers for GEP (GetElementPtr) operations.

**Functions:**
```python
def gep_struct_field(builder, struct_ptr, field_index):
    """Access struct field by index"""
    return builder.gep(struct_ptr, [ZERO_I32, make_i32_const(field_index)])

def gep_array_element(builder, array_ptr, index):
    """Access array element"""
    return builder.gep(array_ptr, [ZERO_I32, index])

def gep_dynamic_array_data(builder, dynarray_ptr):
    """Get data pointer from dynamic array struct"""
    return builder.gep(dynarray_ptr, [ZERO_I32, ZERO_I32])

def gep_dynamic_array_len(builder, dynarray_ptr):
    """Get length from dynamic array struct"""
    return builder.gep(dynarray_ptr, [ZERO_I32, ONE_I32])

def gep_dynamic_array_capacity(builder, dynarray_ptr):
    """Get capacity from dynamic array struct"""
    return builder.gep(dynarray_ptr, [ZERO_I32, TWO_I32])
```

#### Enum Utilities

**File:** `backend/enum_utils.py`

Centralized enum discriminant and variant data operations.

**Functions:**
```python
def extract_enum_tag(builder, enum_ptr):
    """Load discriminant tag from enum"""
    tag_ptr = gep_struct_field(builder, enum_ptr, 0)
    return builder.load(tag_ptr)

def extract_enum_data(builder, enum_ptr, variant_type):
    """Extract variant data from enum"""
    data_ptr = gep_struct_field(builder, enum_ptr, 1)
    typed_ptr = builder.bitcast(data_ptr, variant_type.as_pointer())
    return builder.load(typed_ptr)

def compare_enum_variant(builder, enum_ptr, expected_tag):
    """Check if enum matches variant tag"""
    actual_tag = extract_enum_tag(builder, enum_ptr)
    return builder.icmp_signed('==', actual_tag, make_i32_const(expected_tag))
```

#### Unified Destructor Logic

**File:** `backend/destructors.py`

Replaces scattered destruction code with single recursive implementation.

**Function:**
```python
def emit_value_destructor(codegen, builder, value, llvm_type, ast_type):
    """
    Recursively destroy value based on type.

    Handles:
    - Primitives: no-op
    - Strings: no-op (immutable)
    - Dynamic arrays: destroy elements, free buffer
    - Structs: destroy each field recursively
    - Enums: switch on discriminant, destroy variant data
    - Own@(T): destroy owned value, free pointer
    """
    # Type-aware dispatch...
```

**Benefits:**
- Eliminates duplicated destruction logic (previously in 10+ files)
- Ensures consistent cleanup behavior
- Single point of maintenance for RAII
- Powers HashMap.free(), List.destroy(), and error propagation cleanup

#### PassErrorReporter Helper

**File:** `semantics/error_reporter.py`

Reduces boilerplate in semantic passes by binding Reporter instance.

**Before:**
```python
def validate_expression(expr, reporter):
    if not is_valid(expr):
        reporter.error("CE2001", f"Invalid expression: {expr}")
    # reporter passed to every function...
```

**After:**
```python
error_reporter = PassErrorReporter(reporter)

def validate_expression(expr):
    if not is_valid(expr):
        error_reporter.error("CE2001", f"Invalid expression: {expr}")
    # No need to pass reporter around
```

### Modularized Backend Structure

The backend has been organized into logical subdirectories for better maintainability:

- **`backend/expressions/calls/`** - Subdivided due to complexity; stdlib call
  routing is itself a `stdlib/` subpackage, one module per stdlib area
- **`backend/generics/`** - Complete generic type system with HashMap and List implementations
- **`backend/memory/`** - Separated scope management, dynamic arrays, and heap operations
- **`backend/runtime/externs/`** - Organized libc bindings by category (stdio, strings, ctype, process)
- **`backend/types/arrays/methods/`** - Array method implementations organized by category

## Key Design Patterns

### Deferred Local Imports

There is no shared protocol/interface module. Circular dependencies between
backend components are avoided by importing inside the function that needs the
dependency rather than at module top level — see
[Breaking Circular Imports: Deferred Local Imports](#breaking-circular-imports-deferred-local-imports)
above for the mechanism and a concrete example from `backend/destructors.py`.

### Recursive Destructors

`MemoryManager.emit_value_destructor()` handles cleanup for all types:
- Primitives: no-op
- Strings: no-op (immutable)
- Arrays: iterate and destroy elements, free buffer
- Structs: destroy each field, free struct
- Enums: switch on discriminant, destroy variant data
- Own@(T): destroy owned value, free pointer

### Move Tracking

Variables marked as moved:
```python
self.moved_variables.add(var_name)
```

Checked on every use:
```python
if var_name in self.moved_variables:
    raise CompilerError(f"CE2405: Use of moved variable '{var_name}'")
```

### Borrow Tracking

Active borrows tracked per variable:
```python
self.active_borrows[var_name] = borrow_id
```

Prevents rebinding while borrowed:
```python
if var_name in self.active_borrows:
    raise CompilerError(f"CE1007: Cannot rebind '{var_name}' while borrowed")
```

## Error Handling

### Error Code Ranges

- **CE0xxx** - Internal errors (function-related)
- **CE1xxx** - Scope/variable errors (undefined, moved, borrowed)
- **CE2xxx** - Type errors (incompatible types, array bounds)
- **CE3xxx** - Unit errors (module system)
- **CWxxxx** - Warnings (unused Result, etc.)
- **RExxxx** - Runtime errors (bounds check, malloc failure)

### Error Reporting

Located in each pass file. Example:

```python
raise CompilerError(
    f"CE2505: Cannot assign Result@({inner}) to {target_type} without handling. "
    f"Use .realise(default) to unwrap the Result."
)
```

## Testing Strategy

### Test Types

- `test_*.sushi` - Must compile (exit 0)
- `test_warn_*.sushi` - Compile with warnings (exit 1)
- `test_err_*.sushi` - Must fail (exit 2)

### Test Runner

```bash
python tests/run_tests.py
```

Compiles all tests and verifies expected exit codes.

## Development Workflow

### Adding a New Feature

1. Update grammar (`grammar.lark`)
2. Update AST builder (`semantics/ast_builder/` — pick the matching module under
   `declarations/`, `expressions/`, `statements/`, or `types/`)
3. Add semantic analysis (appropriate phase)
4. Add code generation (`backend/`)
5. Write tests (`tests/test_feature.sushi`)
6. Run test suite

### Debugging Compiler Issues

```bash
# See full traceback
./sushic --traceback program.sushi

# View AST
./sushic --dump-ast program.sushi

# View generated IR
./sushic --dump-ll program.sushi

# Save IR to file
./sushic --write-ll program.sushi
```

---

**See also:**
- [Semantic Passes](semantic-passes.md) - Detailed pass-by-pass analysis
- [Backend](backend.md) - LLVM code generation details
