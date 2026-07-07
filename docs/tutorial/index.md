# The Sushi Tutorial

Welcome. This is a hands-on, start-to-finish guide to **Sushi** — a small compiled
language with static types, explicit error handling, compile-time memory safety, and an
LLVM backend.

## Who this is for

You already write code. This tutorial assumes you know a mainstream language such as
**Python** (and maybe a little **Java**), and it leans on that intuition: where Sushi
does something differently, we say so explicitly and explain why. You do **not** need any
prior experience with systems languages, LLVM, or manual memory management.

## What you'll learn

We start from the absolute basics and build up, one chapter at a time, to every major
feature the language has:

- **Foundations** — variables, types, control flow, functions, strings
- **Safety** — `Result<T, E>`, `Maybe<T>`, error propagation with `??`
- **Data** — arrays, structs, enums, pattern matching
- **Abstraction** — generics, perks (traits), extension methods
- **Systems** — RAII, references and borrow checking, `Own<T>`
- **Library** — `List<T>`, `HashMap<K, V>`, and the standard library
- **Interop** — variadic functions, calling C, foreign pointers, building libraries
- **Functions as values** — first-class functions, closures, and higher-order combinators
  (`map`/`filter`/`fold`/`compose`)

## How to read this

Read the chapters in order — each one builds on the last. Every code example on these
pages is a **real, complete program** that was compiled and run to produce the output you
see. You can copy any example, save it as a `.sushi` file, and run it yourself.

### Running an example

Every example is a standalone program. To compile and run one:

```bash
./sushic mostly-harmless.sushi
./mostly-harmless
```

The first command produces a native executable named after the source file; the second
runs it. If you haven't set up the compiler yet, start with the
[Getting Started](01-getting-started.md) chapter.

## A note on flavor

Sushi has a sense of humor, and so do its docs. Examples lean on *The Hitchhiker's Guide
to the Galaxy* — expect Arthur, Ford, Marvin, the occasional towel, and the number 42.
The traditional first program prints **"Mostly Harmless"** rather than "Hello, World".

Ready? Let's begin with [Getting Started](01-getting-started.md).
