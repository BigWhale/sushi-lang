# Getting Started with Sushi Lang

[← Back to Documentation](README.md)

This guide will help you set up Sushi and write your first program.

## Prerequisites

- **Python 3.13+** (managed by uv)
- **LLVM 20** (for code generation - llvmlite 0.45 requirement)
- **cmake** (required for building llvmlite)
- **macOS, Linux, or WSL** (Windows support via WSL)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/sushi.git
cd sushi
```

### 2. Install System Dependencies

#### macOS

```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install cmake and LLVM 20
brew install cmake llvm@20
```

**Note**: LLVM 20 is keg-only on macOS (not symlinked into `/usr/local`). The build process will automatically detect and use the correct version.

#### Linux (Ubuntu/Debian)

```bash
# Install cmake
sudo apt update
sudo apt install cmake

# Install LLVM 20 (check your distribution's package manager for llvm-20)
# For Ubuntu 22.04+:
sudo apt install llvm-20 llvm-20-dev
```

#### Linux (Arch)

```bash
# Install cmake and LLVM
sudo pacman -S cmake llvm
```

### 3. Install uv

uv is a fast Python package manager that handles dependency installation and virtual environments:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via pip
pip install uv

# Or via Homebrew (macOS)
brew install uv
```

### 4. Install Python Dependencies

```bash
# Install all dependencies (including dev dependencies)
uv sync --dev

# This will:
# - Create a virtual environment at .venv/
# - Install lark (parser)
# - Build and install llvmlite (LLVM bindings)
# - Install colorama (colored output)
# - Install dev tools (pytest, ruff, black, mypy)
```

**Note**: Building llvmlite may take a few minutes on first install.

### 5. Build Standard Library

```bash
# Build platform-specific standard library modules
uv run python stdlib/build.py

# This generates LLVM bitcode for:
# - collections/strings, io/stdio, io/files
# - time, math, sys/env, random
```

### 6. Verify Installation

```bash
# Test the compiler
./sushic --help

# You should see:
# 🍣 Sushi (すし) Lang Compiler  v0.0.11
# Python 3.x.x • llvmlite 0.45.1 • LLVM 20.x.x
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
uv run python tests/run_tests.py

# Run with runtime validation
uv run python tests/run_tests.py --enhanced

# Run specific tests
uv run python tests/run_tests.py --filter hashmap
```

## Common Issues

### "Command not found: ./sushic"

Make sure you're in the sushi directory and the script is executable:

```bash
chmod +x sushic
./sushic program.sushi
```

### "ModuleNotFoundError: No module named 'llvmlite'"

Install dependencies with uv:

```bash
uv sync --dev
```

### "llvmlite only officially supports LLVM 20"

You have the wrong LLVM version installed. llvmlite 0.45 requires LLVM 20:

```bash
# macOS - remove LLVM 21 and install LLVM 20
brew uninstall llvm
brew install llvm@20

# Then rebuild dependencies
uv sync --dev
```

### "FileNotFoundError: [Errno 2] No such file or directory: 'cmake'"

cmake is required to build llvmlite:

```bash
# macOS
brew install cmake

# Ubuntu/Debian
sudo apt install cmake

# Arch
sudo pacman -S cmake
```

### "clang: command not found" during linking

The system needs clang for linking the final binary:

- **macOS**: Install Xcode Command Line Tools: `xcode-select --install`
- **Ubuntu/Debian**: `sudo apt install clang`
- **Arch**: `sudo pacman -S clang`

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
