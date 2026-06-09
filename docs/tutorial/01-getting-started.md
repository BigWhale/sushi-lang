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
[Getting Started installation guide](https://github.com/yourusername/sushi/blob/main/docs/getting-started.md)
in the main documentation — it covers installing LLVM, `uv`, and building the standard
library. Once `./sushic --help` prints a version banner, you're ready.

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
    return Result.Ok(0)
```

This is the first place Sushi diverges sharply from Python or Java, so it's worth pausing.

In Sushi, functions don't just return a value — they return a value **or** an error,
wrapped in a type called `Result`. `Result.Ok(0)` means "this succeeded, and the value is
`0`". (The alternative is `Result.Err(...)`, for failure.) Because `main` returned
`Result.Ok(0)`, the program exits with code `0`: success.

You don't need to fully understand `Result` yet — [Chapter 6](06-error-handling.md) is
devoted to it. For now, just remember the shape: **a function that succeeds with value `v`
returns `Result.Ok(v)`.**

!!! note "Why wrap everything in `Result`?"
    Making success and failure explicit in the type system is what lets the Sushi compiler
    guarantee you've handled errors. Languages that let you ignore errors (a forgotten
    exception, an unchecked return code) are where a lot of real-world bugs hide. We'll see
    how ergonomic this becomes once the `??` operator enters the picture.

## Exit codes

The integer `main` returns becomes the process exit code. Try changing the program to
`return Result.Ok(42)`, recompile, run it, and then check the code your shell saw:

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
- Functions return `Result.Ok(value)` on success — and `main`'s integer becomes the exit
  code.

Next up: storing and naming data. On to [Variables & Types](02-variables-and-types.md).
