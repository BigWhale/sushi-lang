# Getting Started with Sushi Lang

[← Back to Documentation](README.md)

This guide will help you set up Sushi and write your first program.

## Prerequisites

- **Python 3.8+** (for the compiler)
- **LLVM/Clang** (for code generation and linking)
- **macOS, Linux, or WSL** (Windows support via WSL)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/sushi.git
cd sushi
```

### 2. Install Dependencies

Sushi uses `uv` for Python dependency management:

```bash
# Install uv if you don't have it
pip install uv

# Install dependencies
uv sync
```

### 3. Verify Installation

```bash
# The sushic compiler script should be executable
./sushic --help
```

## Your First Program

Let's write the traditional first program (with a twist):

### 1. Create a File

Create a file named `hello.sushi`:

```sushi
fn main() i32:
    println("Mostly Harmless")
    return Result.Ok(0)
```

### 2. Compile It

```bash
./sushic hello.sushi
```

This creates an executable named `hello` (or `hello.exe` on Windows).

### 3. Run It

```bash
./hello
```

Output:
```
Mostly Harmless
```

## Understanding the Program

Let's break down what's happening:

```sushi
fn main() i32:
```
- Every Sushi program starts with a `main` function
- It returns `i32` (a 32-bit integer)
- Actually, all functions implicitly return `Result<T>`, so a regular would return `Result<i32>`, however, 
  because this is `main`, it will automatically realise this Result and return an integer back to shell.

```sushi
    println("Mostly Harmless")
```
- `println` outputs text followed by a newline
- Strings are enclosed in double quotes
- Full UTF-8 Unicode support: `println("Hello 🌍!")`

```sushi
    return Result.Ok(0)
```
- Explicit error handling: functions return `Result.Ok(value)` for success
- Or `Result.Err()` for failure
- The compiler enforces that you handle all possible errors

## Compilation Options

### Basic Usage

```bash
# Compile with default name (same as source file)
./sushic program.sushi

# Specify output name
./sushic program.sushi -o myprogram
```

### Optimization Levels

```bash
# No optimization (fastest compilation, for debugging)
./sushic --opt none program.sushi

# Basic optimizations (quick builds)
./sushic --opt O1 program.sushi

# Moderate optimizations (recommended for most use)
./sushic --opt O2 program.sushi

# Aggressive optimizations (maximum performance)
./sushic --opt O3 program.sushi
```

**Recommendation**: Use `--opt O2` or `--opt O3` for production code.

### Debugging Options

```bash
# Show full Python traceback on compiler errors
./sushic --traceback program.sushi

# Print the abstract syntax tree to terminal
./sushic --dump-ast program.sushi

# Print LLVM IR to terminal
./sushic --dump-ll program.sushi

# Save LLVM IR to file
./sushic --write-ll program.sushi
cat program.ll  # View the generated IR
```

## A More Realistic Example

Let's write a program that demonstrates error handling:

Create `calculator.sushi`:

```sushi
fn divide(i32 numerator, i32 denominator) i32:
    if (denominator == 0):
        println("Error: Cannot divide by zero!")
        return Result.Err()
    return Result.Ok(numerator / denominator)

fn main() i32:
    let Result<i32> result1 = divide(42, 6)
    let Result<i32> result2 = divide(42, 0)

    # Check result1
    if (result1):
        let i32 value = result1.realise(0)
        println("42 / 6 = {value}")
    else:
        println("First division failed")

    # Check result2
    if (result2):
        let i32 value = result2.realise(0)
        println("42 / 0 = {value}")
    else:
        println("Second division failed (as expected)")

    return Result.Ok(0)
```

Compile and run:

```bash
./sushic calculator.sushi
./calculator
```

Output:
```
42 / 6 = 7
Error: Cannot divide by zero!
Second division failed (as expected)
```

## Testing Your Setup

Sushi includes a comprehensive test suite. Try running it:

```bash
# Run all tests (compilation only)
python tests/run_tests.py

# Run with runtime validation
python tests/run_tests.py --enhanced

# Run specific tests
python tests/run_tests.py --filter hashmap
```

## Common Issues

### "Command not found: ./sushic"

Make sure you're in the sushi directory and the script is executable:

```bash
chmod +x sushic
./sushic program.sushi
```

### "ModuleNotFoundError: No module named 'llvmlite'"

Install dependencies:

```bash
uv sync
```

### "clang: command not found"

Install LLVM/Clang:

- **macOS**: `brew install llvm`
- **Ubuntu/Debian**: `apt install clang llvm`
- **Arch**: `pacman -S clang llvm`

## Next Steps

Now that you have Sushi set up:

1. **Learn the language**: Read the [Language Guide](language-guide.md) for a friendly tour
2. **Explore examples**: Check out [examples/](examples/) for hands-on code
3. **Deep dive**: See the [Language Reference](language-reference.md) for complete details
4. **Write code**: Start building something!

## Quick Reference Card

```bash
# Compile and run
./sushic program.sushi && ./program

# Optimized build
./sushic --opt O3 program.sushi -o program

# Debug compiler issues
./sushic --traceback program.sushi

# Inspect generated code
./sushic --write-ll program.sushi && cat program.ll
```

---

**Next**: [Language Guide](language-guide.md) | [Examples](examples/)
