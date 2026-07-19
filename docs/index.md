# Sushi Lang

**Sushi** is a compiled, statically typed language with a Lark parser frontend and an LLVM
backend. It pairs explicit error handling (`Result@(T, E)`, `Maybe@(T)`) and compile-time memory
safety (RAII, borrow checking) with zero-cost abstractions — generics, references, and extension
methods that compile away to native code.

```sushi
fn main() i32:
    println("Mostly Harmless")
    return Result.Ok(0)
```

This site collects the guided tutorial, the language and standard-library reference, and the
compiler internals in one place.

## Start here

- **[Tutorial](tutorial/index.md)** — a guided, start-to-finish course in 14 chapters; every
  example is compiled and run.
- **[Getting Started](getting-started.md)** — install the compiler and build your first program.
- **[Language Guide](language-guide.md)** — a friendly tour of Sushi's key features.
- **[Examples](examples/README.md)** — learn by example with annotated programs.

## Guides

- [Error Handling](error-handling.md) — `Result@(T, E)`, `Maybe@(T)`, and the `??` operator
- [Memory Management](memory-management.md) — RAII, references, borrowing, and `Own@(T)`
- [Generics](generics.md) — generic types, functions, and monomorphization
- [Perks](perks.md) — traits/interfaces for polymorphism with static dispatch
- [First-Class Functions](first-class-functions.md) — function types and function values
- [Closures](closures.md) — capturing lambda literals and escaping closures
- [Foreign Function Interface](ffi.md) — calling external C functions via `unsafe external`

## Reference

- [Language Reference](language-reference.md) — complete syntax and semantics
- [Standard Library](standard-library.md) — built-in types (`Result`, `Maybe`, `List`, `HashMap`)
- [Compiler Reference](compiler-reference.md) — CLI options, optimization levels, error codes
- [Libraries](libraries.md) — creating and linking precompiled libraries
- [Library Format](library-format.md) — the `.slib` binary format
- [Nori Package Manager](package-manager.md) — packaging, installing, and managing libraries

## Standard library

`Result` and `Maybe`; collections (`arrays`, `List`, `HashMap`, `strings`); I/O
(`console`, `files`); and `math`, `time`, `random`, `env`, `process`, `platform`. See the
**Standard library** section in the navigation.

## Internals

- [Architecture](internals/architecture.md) — the compiler pipeline and design
- [Semantic Passes](internals/semantic-passes.md) — pass-by-pass analysis
- [Backend](internals/backend.md) — LLVM code generation
- [Stdlib Build](internals/stdlib-build.md) — building the standard library
- [Variadics (design)](design/variadics.md) — the variadics design note

## Philosophy

Sushi combines **Rust's safety** (ownership, borrowing, explicit error handling),
**Python's simplicity** (clean, readable syntax), and **C's performance** (zero-cost
abstractions, native binaries).
