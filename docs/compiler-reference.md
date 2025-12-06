# Compiler Reference

[← Back to Documentation](README.md)

Complete reference for the Sushi compiler: CLI options, optimization levels, and error codes.

## Table of Contents

- [Command Line Interface](#command-line-interface)
- [Optimization Levels](#optimization-levels)
- [Debugging Options](#debugging-options)
- [Error Codes](#error-codes)
- [Testing](#testing)

## Command Line Interface

### Basic Usage

```bash
./sushic [options] <source-files> [-o output]
```

### Examples

```bash
# Compile single file (output: hello)
./sushic hello.sushi

# Specify output name
./sushic program.sushi -o myprogram

# Multiple files (multi-unit project)
./sushic main.sushi utils.sushi -o app

# With optimization
./sushic --opt O3 program.sushi -o optimized
```

### Options

| Option        | Description                                        |
|---------------|----------------------------------------------------|
| `-o NAME`     | Specify output executable name                     |
| `--opt LEVEL` | Set optimization level (none, mem2reg, O1, O2, O3) |
| `--lib`       | Compile to library bitcode instead of executable   |
| `--traceback` | Show full Python traceback on errors               |
| `--dump-ast`  | Print abstract syntax tree                         |
| `--dump-ll`   | Print LLVM IR to terminal                          |
| `--write-ll`  | Write LLVM IR to `<output>.ll` file                |

### Library Compilation

```bash
# Compile source to reusable library
./sushic --lib mylib.sushi -o mylib.bc
```

Libraries are used via `use <lib/...>` statements in source code:

```sushi
use <lib/mylib>

fn main() i32:
    # Library functions/types are now available
    return Result.Ok(0)
```

See [Libraries](libraries.md) for complete documentation.

## Optimization Levels

Sushi provides a complete LLVM optimization pipeline with multiple levels.

### Overview

| Level         | Description              | Use Case                        | Compile Time |
|---------------|--------------------------|---------------------------------|--------------|
| `none` / `O0` | No optimization          | Debugging, development          | Fastest      |
| `mem2reg`     | SROA only (default)      | Quick builds with SSA           | Very fast    |
| `O1`          | Basic optimizations      | Fast compilation + improvements | Fast         |
| `O2`          | Moderate optimizations   | **Recommended** for production  | Moderate     |
| `O3`          | Aggressive optimizations | Maximum performance             | Slower       |

### Default Behavior

If no `--opt` flag is specified, `mem2reg` is used (basic SROA for SSA form).

### mem2reg (Default)

**What it does:**
- SROA (Scalar Replacement of Aggregates) - promotes stack allocations to registers
- Converts code to SSA (Static Single Assignment) form
- Minimal overhead, fast compilation

**Use when:**
- Quick development builds
- Testing and iteration
- SSA form needed for debugging

```bash
./sushic program.sushi
# Equivalent to:
./sushic --opt mem2reg program.sushi
```

### O1 - Basic Optimizations

**What it does:**
- All of mem2reg, plus:
- CFG simplification - removes redundant branches
- Instruction combining - peephole optimizations
- Dead code elimination - removes unused code and variables
- Basic constant folding

**Use when:**
- Development with some performance
- Testing with realistic speed
- Fast CI/CD builds

```bash
./sushic --opt O1 program.sushi
```

**Performance impact:** 10-30% faster than `none`, minimal compile time increase.

### O2 - Moderate Optimizations (Recommended)

**What it does:**
- All of O1, plus:
- SCCP (Sparse Conditional Constant Propagation)
- Loop optimizations (rotation, simplification, deletion)
- GVN (Global Value Numbering) - eliminates redundant computations
- Memory optimizations (memcpy optimization, dead store elimination)
- Jump threading - optimizes conditional branches
- Tail call elimination
- Interprocedural optimizations (IPSCCP, dead argument elimination)

**Use when:**
- Production builds
- Benchmarking
- Deployment

```bash
./sushic --opt O2 program.sushi -o production
```

**Performance impact:** 50-200% faster than `none`, moderate compile time.

### O3 - Aggressive Optimizations

**What it does:**
- All of O2, plus:
- Aggressive loop unrolling - unrolls loops for better performance
- Loop strength reduction - optimizes loop induction variables
- Aggressive instruction combining
- Instruction sinking - moves instructions for better register allocation
- Argument promotion - converts by-reference to by-value where beneficial
- Function merging - combines identical functions

**Use when:**
- Maximum performance required
- Benchmarking
- Performance-critical production code

```bash
./sushic --opt O3 program.sushi -o maximum_performance
```

**Performance impact:** 100-300% faster than `none`, longest compile time.

### Optimization Examples

**Example program impact:**

```
Level       IR Lines    Binary Size    Relative Speed
none (O0)   278         34,016 bytes   1.0x (baseline)
mem2reg     245         34,000 bytes   1.1x
O1          220         33,980 bytes   1.3x
O2          195         33,968 bytes   1.8x
O3          181         33,960 bytes   2.2x
```

**Real optimizations observed:**

```sushi
# Source code
let i32 x = 10
let i32 y = (x + 5) * 2

# After O2/O3 constant folding
let i32 y = 30  # Computed at compile time
```

### Viewing Optimized Code

```bash
# Save LLVM IR to file
./sushic --opt O3 --write-ll program.sushi
cat program.ll

# Print IR to terminal
./sushic --opt O3 --dump-ll program.sushi
```

### Recommendations

**Development:**
```bash
# Fastest iteration
./sushic --opt none program.sushi

# Or default (mem2reg)
./sushic program.sushi
```

**Testing:**
```bash
# Balance of speed and compile time
./sushic --opt O1 program.sushi
```

**Production:**
```bash
# Recommended for most deployments
./sushic --opt O2 program.sushi -o app

# Maximum performance
./sushic --opt O3 program.sushi -o app
```

**Benchmarking:**
```bash
# Always use O3 for true performance measurement
./sushic --opt O3 benchmark.sushi -o bench
./bench
```

## Debugging Options

### Full Traceback

Show complete Python stack trace on compiler errors:

```bash
./sushic --traceback program.sushi
```

**When to use:**
- Reporting compiler bugs
- Understanding internal errors
- Debugging compiler itself

### Dump AST

Print the abstract syntax tree:

```bash
./sushic --dump-ast program.sushi
```

**Output includes:**
- Parsed AST structure
- Type annotations
- Scope information

**When to use:**
- Understanding parsing
- Debugging grammar issues
- Compiler development

### Print LLVM IR

Display generated LLVM IR:

```bash
./sushic --dump-ll program.sushi
```

**When to use:**
- Understanding code generation
- Verifying optimizations
- Learning LLVM IR
- Performance debugging

### Save LLVM IR to File

Save IR to `<output>.ll`:

```bash
./sushic --write-ll program.sushi
cat program.ll

# With custom output name
./sushic --write-ll program.sushi -o myapp
cat myapp.ll
```

**When to use:**
- Analyzing optimized code
- Sharing IR for debugging
- Comparing optimization levels

### Combining Debug Options

```bash
# Full debug output
./sushic --traceback --dump-ast --dump-ll program.sushi

# Save IR with optimization
./sushic --opt O3 --write-ll program.sushi
```

## Error Codes

Sushi uses structured error codes for diagnosing issues.

### Error Code Format

- **CE0xxx**: Internal/function errors
- **CE1xxx**: Scope/variable errors
- **CE2xxx**: Type/array/struct errors
- **CE3xxx**: Unit management errors
- **CWxxxx**: Warnings
- **RExxxx**: Runtime errors

### Common Errors

#### CE1003: Undefined Variable

```sushi
fn main() i32:
    # ERROR CE1003: Undefined variable 'x'
    println(x)
    return Result.Ok(0)
```

**Fix:** Declare variable with `let` before use.

#### CE1004: Use of Moved Variable

```sushi
fn main() i32:
    let i32[] arr = from([1, 2, 3])
    let i32[] moved = arr

    # ERROR CE1004: Use of moved variable 'arr'
    println(arr.len())

    return Result.Ok(0)
```

**Fix:** Use references (`&arr`) or clone (`arr.clone()`).

#### CE1007: Cannot Rebind While Borrowed

```sushi
fn borrow(&i32 x) i32:
    return Result.Ok(x)

fn main() i32:
    let i32 num = 42
    let i32 borrowed = borrow(&num)

    # ERROR CE1007: Cannot rebind 'num' while borrowed
    num := 50

    return Result.Ok(0)
```

**Fix:** Wait until borrow ends (function returns).

#### CE2406: Use of Destroyed Variable

```sushi
fn main() i32:
    let i32[] arr = from([1, 2, 3])
    arr.destroy()

    # ERROR CE2406: use of destroyed variable 'arr'
    println(arr.len())

    return Result.Ok(0)
```

**Fix:** Don't use variable after `.destroy()`, or use `.free()` instead.

#### CE2502: .realise() Wrong Argument Count

```sushi
fn main() i32:
    let Result<i32> r = get_value()

    # ERROR CE2502: .realise() requires exactly 1 argument
    let i32 x = r.realise()

    return Result.Ok(0)
```

**Fix:** Provide default value: `r.realise(0)`.

#### CE2503: .realise() Type Mismatch

```sushi
fn main() i32:
    let Result<i32> r = get_value()

    # ERROR CE2503: Default type 'string' doesn't match Result<i32>
    let i32 x = r.realise("wrong")

    return Result.Ok(0)
```

**Fix:** Use correct type: `r.realise(0)`.

#### CE2505: Assigning Result Without Handling

```sushi
fn get_value() i32:
    return Result.Ok(42)

fn main() i32:
    # ERROR CE2505: Cannot assign Result<i32> to i32
    let i32 x = get_value()

    return Result.Ok(0)
```

**Fix:** Use `.realise()`: `let i32 x = get_value().realise(0)`.

#### CE2507: Using ?? on Non-Result Type

```sushi
fn main() i32:
    let i32 x = 5

    # ERROR CE2507: ?? can only be used with Result<T> or Maybe<T>
    let i32 y = x??

    return Result.Ok(0)
```

**Fix:** Only use `??` with `Result<T>` or `Maybe<T>`.

#### CE2508: Using ?? Outside Result Function

```sushi
extend i32 squared() i32:
    # ERROR CE2508: ?? only works in Result-returning functions
    let i32 x = might_fail()??

    return Result.Ok(self * self)
```

**Fix:** Don't use `??` in extension methods (limitation).

### Constant Expression Errors

#### CE0108: Expression Not Compile-Time Constant

```sushi
fn get_value() i32:
    return Result.Ok(42)

# ERROR CE0108: Expression is not a compile-time constant
const i32 X = get_value()
```

**Fix:** Only use compile-time evaluable expressions (literals, arithmetic, bitwise, etc.).

#### CE0109: Circular Constant Dependency

```sushi
# ERROR CE0109: Circular constant dependency detected: A -> B -> A
const i32 A = B + 1
const i32 B = A + 1
```

**Fix:** Remove circular dependencies between constants.

#### CE0110: Unsupported Operation in Constant

```sushi
# ERROR CE0110: Unsupported operation '&' in constant expression
const f64 INVALID = 3.14 & 2.0  # Bitwise AND on float
```

**Fix:** Use only supported operations for the type (bitwise only on integers).

#### CE0111: Invalid Type Cast in Constant

```sushi
# ERROR CE0111: Invalid type cast in constant expression from string to i32
const i32 INVALID = "hello" as i32
```

**Fix:** Only cast between compatible numeric types.

#### CE0112: Division by Zero in Constant

```sushi
# ERROR CE0112: Division by zero in constant expression
const i32 INVALID = 100 / 0
```

**Fix:** Ensure divisor is non-zero.

### Warnings

#### CW2001: Unused Result Value

```sushi
fn get_value() i32:
    return Result.Ok(42)

fn main() i32:
    # WARNING CW2001: Unused Result<i32> value
    get_value()

    return Result.Ok(0)
```

**Fix:** Handle result or explicitly discard:
```sushi
let Result<i32> r = get_value()  # Store for later
let i32 x = get_value().realise(0)  # Use immediately
```

### Runtime Errors

#### RE2020: Array Bounds Check Failed

```sushi
fn main() i32:
    let i32[3] arr = [1, 2, 3]

    # Runtime error: index out of bounds
    let i32 x = arr.get(10)

    return Result.Ok(0)
```

**Runtime output:**
```
Runtime error RE2020: Array bounds check failed (index 10, length 3)
```

#### RE2021: Memory Allocation Failed

```
Runtime error RE2021: Memory allocation failed (malloc returned null)
```

Occurs when system runs out of memory during dynamic allocation.

## Testing

### Test Runner

```bash
# Run all tests (compilation only)
python tests/run_tests.py

# Run with runtime validation
python tests/run_tests.py --enhanced

# Filter tests by pattern
python tests/run_tests.py --filter hashmap
python tests/run_tests.py --filter test_result
```

### Test Types

**Positive tests** (`test_*.sushi`):
- Must compile successfully (exit code 0)
- Executable may or may not run

**Warning tests** (`test_warn_*.sushi`):
- Must compile with warnings (exit code 1)

**Error tests** (`test_err_*.sushi`):
- Must fail compilation (exit code 2)
- Used to verify error detection

### Writing Tests

```sushi
# tests/test_my_feature.sushi
fn main() i32:
    let i32 x = 42
    println("Test passed")
    return Result.Ok(0)
```

```bash
# Run your test
./sushic tests/test_my_feature.sushi
./test_my_feature
```

### Test Naming Conventions

- `test_<feature>.sushi` - Positive test
- `test_warn_<feature>.sushi` - Expected warning
- `test_err_<feature>.sushi` - Expected error
- `test_<category>_<specific>.sushi` - Organized by category

---

**See also:**
- [Getting Started](getting-started.md) - Installation and first program
- [Language Reference](language-reference.md) - Complete syntax
- [Compiler Internals](internals/architecture.md) - How the compiler works
