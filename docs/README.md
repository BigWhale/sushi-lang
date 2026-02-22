# Sushi Lang Documentation

Welcome to the Sushi Lang documentation. This guide will help you learn and master Sushi, from basic concepts to 
advanced features and compiler internals.

## Getting Started

**New to Sushi?** Start here:
- [Getting Started](getting-started.md) - Installation, setup, and your first program
- [Language Guide](language-guide.md) - Friendly tour of Sushi's key features
- [Examples](examples/) - Learn by example with 21 annotated programs

## Language Documentation

**Core language reference:**
- [Language Reference](language-reference.md) - Complete syntax and semantics reference
- [Standard Library](standard-library.md) - Built-in types (`Result<T>`, `Maybe<T>`, `List<T>`, `HashMap<K,V>`)
- [Error Handling](error-handling.md) - `Result<T>`, `Maybe<T>`, and the `??` operator
- [Memory Management](memory-management.md) - RAII, references, borrowing, and `Own<T>`
- [Generics](generics.md) - Generic types, functions, and monomorphization
- [Perks](perks.md) - Traits/interfaces for polymorphic behavior with static dispatch

## Tooling

**Package management and distribution:**
- [Nori Package Manager](package-manager.md) - Packaging, installing, and managing Sushi libraries

## Compiler Documentation

**Using and understanding the compiler:**
- [Compiler Reference](compiler-reference.md) - CLI options, optimization levels, error codes
- [Libraries](libraries.md) - Creating and linking precompiled libraries
- [Library Format](library-format.md) - `.slib` binary format specification
- [Internals: Architecture](internals/architecture.md) - Compiler pipeline and design
- [Internals: Semantic Passes](internals/semantic-passes.md) - Pass-by-pass analysis details
- [Internals: Backend](internals/backend.md) - LLVM code generation
- [Internals: Stdlib Build](internals/stdlib-build.md) - Building the standard library

## Examples

Browse [examples/](examples/) directory for hands-on learning:
- Basic programs (hello world, variables, functions)
- String handling and interpolation
- Error handling patterns
- Collections (arrays, lists, hashmaps)
- Pattern matching and control flow
- Generic types and extension methods
- File I/O and system interaction

Each example includes detailed comments explaining concepts and patterns.

## Quick Reference

### Essential Commands

```bash
# Compile and run
./sushic program.sushi -o program
./program

# Optimization levels
./sushic --opt O2 program.sushi    # Recommended
./sushic --opt O3 program.sushi    # Maximum performance

# Debugging
./sushic --traceback program.sushi # Full error traces
./sushic --dump-ll program.sushi   # Show LLVM IR

# Libraries
./sushic --lib mylib.sushi -o mylib.slib  # Compile library
./sushic --lib-info mylib.slib            # Inspect library
# In main.sushi: use <lib/mylib>          # Import library

# Package management (Nori)
nori init                                  # Create nori.toml manifest
nori build                                 # Build .nori package archive
nori install ./dist/pkg-1.0.0.nori         # Install a package
nori list                                  # List installed packages
nori remove my-package                     # Remove a package
```

### Quick Syntax

```sushi
# Functions return Result<T>
fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

# Error propagation
fn read_file() string:
    let file f = open("data.txt", FileMode.Read())??
    return Result.Ok(f.read())

# Pattern matching
match result:
    Result.Ok(value) -> println("Got: {value}")
    Result.Err() -> println("Failed")

# Generics
struct Pair<T, U>:
    T first
    U second
```

## Philosophy

Sushi combines:
- **Rust's safety** - Ownership, borrowing, explicit error handling
- **Python's simplicity** - Clean syntax, readable code
- **C's performance** - Zero-cost abstractions, native binaries

## Contributing

Interested in contributing? See the repository root for contribution guidelines.

---

**Tip**: Use your browser's search (Ctrl/Cmd+F) to find specific topics within each documentation page.
