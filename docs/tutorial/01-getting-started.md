# 1. Getting Started

Every journey starts somewhere. Ours starts with a program that prints two words and
exits cleanly. By the end of this short chapter you'll have compiled and run your first
Sushi program and you'll understand every character in it.

## What Sushi is

Sushi is a **compiled** language. Unlike Python, where you run your source directly
through an interpreter, Sushi source is translated ahead of time into a native executable
by the `sushic` compiler (built on LLVM). You compile once, then run the resulting binary
as many times as you like.

It is also **statically typed** (every value's type is known at compile time, like Java),
and it takes **error handling** and **memory safety** seriously enough that the compiler
will refuse to build a program with whole categories of bugs. We'll meet those ideas
gradually; for now, just know that the compiler is on your side.

## Setting up the compiler

This tutorial assumes you have a working `sushic`. If you don't yet, follow the
[Getting Started installation guide](../getting-started.md)
in the main documentation — it covers installing LLVM, `uv`, and building the standard
library. When `./sushic --version` prints a version banner, you are ready.

## Mostly Harmless

Here is the traditional first program. By tradition in the Sushi world it prints
**"Mostly Harmless"** rather than "Hello, World":

```sushi
--8<-- "docs/tutorial/examples/01-getting-started/mostly-harmless.sushi"
```

## Compiling and running it

Save that as `mostly-harmless.sushi`, then compile it:

```bash
./sushic mostly-harmless.sushi
```

The compiler produces a native executable named after the source file — here,
`mostly-harmless`. Run it:

```bash
./mostly-harmless
```

Output:

```
Mostly Harmless
```

That's a real, native binary. There's no interpreter and no virtual machine involved when
it runs.

## Anatomy of the program

Three lines, and every one of them matters.

```sushi
fn main() i32:
```

- `fn` declares a function.
- `main` is special: it's the **entry point**, where every Sushi program begins. (Java
  programmers will find this familiar; Python programmers used to top-level script code
  will need to put their code inside `main`.)
- `i32` is the **return type** — a 32-bit signed integer. `main` returns an integer to the
  operating system as the program's *exit code*, where `0` conventionally means success.
- The line ends in a colon, and the body is **indented** beneath it. Sushi uses
  indentation for blocks, just like Python.

```sushi
    println("Mostly Harmless")
```

- `println` prints its argument followed by a newline. (There's also `print`, without the
  newline.)
- Text in double quotes is a **string**. Sushi strings are fully UTF-8, so
  `println("Hello, galaxy! 42")` works fine.

```sushi
    return 0
```

- `return 0` ends `main` and gives the value `0` back. `main` returns the exit code of the
  program, and `0` means success.

`main` is a **bare** function: it returns its value directly. Most other functions that
you write do not. A function that can fail writes an **error channel** in its signature,
for example `fn load() i32 | IoError:`. Such a function returns `Result.Ok(value)` on
success and `Result.Err(error)` on failure, and the caller must handle both.
[Chapter 4](04-functions.md) shows the two forms, and [Chapter 6](06-error-handling.md)
is about errors.

!!! note "Why an error channel?"
    The error channel puts failure in the type system. The compiler then makes sure that
    each caller handles the error. Languages that let you ignore errors (a forgotten
    exception, an unchecked return code) are where a lot of real-world bugs hide.

## Exit codes

The integer `main` returns becomes the process exit code. Try changing the program to
`return 42`, recompile, run it, and then check the code your shell saw:

```bash
./mostly-harmless
echo $?
```

You'll see `42`. This is how command-line programs signal success or failure to whatever
launched them. `0` means everything went fine; any non-zero value signals a specific
problem.

## What you learned

- Sushi compiles source to a native binary with `./sushic file.sushi`.
- Every program has an entry point: `fn main() i32:`.
- `println` prints a line; strings are UTF-8 and use double quotes.
- `main` is bare and returns the exit code (`return 0`). A function that can fail writes
  an error channel (`| E`) and returns `Result.Ok(value)` or `Result.Err(error)`.

Next up: storing and naming data. On to [Variables & Types](02-variables-and-types.md).
