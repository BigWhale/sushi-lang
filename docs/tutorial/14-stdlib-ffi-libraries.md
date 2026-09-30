# 14. The Standard Library, FFI & Libraries

You have a language. Now you need batteries. This chapter is a guided tour of the
parts of Sushi that connect your programs to the wider universe: the **standard library**
of ready-made modules, **variadic functions** for flexible argument lists, the **foreign
function interface** for calling C, and the **library system** for sharing your own code
across projects.

If you come from Python, think of this as `import time`, `import math`, `ctypes`, and
`pip`-installable packages — except everything here is compiled, statically typed, and
checked ahead of time. Java programmers will recognise the standard packages, JNI, and
JARs. We will meet a small piece of each, and every example below is a complete program
that really compiles and runs.

## A tour of the standard library

Standard-library modules are pulled in with `use <name>` (angle brackets), distinct from
importing your own source files (which uses quotes — more on that later). Some modules are
precompiled; others (for example `<io/fs>`, `<io/buf>`, `<io/contracts>` and
`<collections/iter>`) are Sushi source that the compiler adds to your program as ordinary
units. For you, the two kinds are the same: write `use <name>`, and the compiler links what
you use into your binary.

### Time

The `<time>` module gives you POSIX-precision sleep functions. They all have the `StdError`
error channel and return `Result@(i32, StdError)` (0 on success, or the remaining microseconds if a signal interrupts the
sleep), so you unwrap them like any other `Result`. We keep the duration tiny here so the
program returns almost instantly.

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/timing.sushi"
```

Output:

```
Improbability drive warming up...
Drive online. Anything is now infinitely probable.
```

!!! note "No `??` in `main`"
    We unwrap with `.realise(default)` rather than `??`. The `??` operator is wonderful
    inside a function with an error channel, but `main` is bare, so `??` in `main` is
    **CE0131**. In `main`, use `match`, `if (result.is_ok())`, or `.realise(default)`.

### Math

The `<math>` module gives numeric functions. Three of them are **polymorphic**: `abs`,
`min` and `max` accept any numeric type, and the result has the type of the arguments.
There are no type-suffixed forms such as `abs_i32`. The other functions (`sqrt`, `hypot`,
`pow`, `sin`, `log` and more) take and return `f64` only. For an `f32` or an integer, cast
the argument with `as f64`.

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/math-tour.sushi"
```

Output:

```
max(7, 42) = 42
abs(-42) = 42
sqrt(1764) = 42
hypot(3, 4) = 5
```

### Random

The `<random>` module offers a non-cryptographic pseudo-random generator. Seeding it with
`srand` makes a run reproducible, which is exactly what you want in a tutorial whose output
must match every time.

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/dice.sushi"
```

Output:

```
Rolling three dice for the Total Perspective Vortex...
roll 1: d6 -> 1
roll 2: d6 -> 2
roll 3: d6 -> 5
```

!!! note "Reproducible, not secret"
    The same seed produces the same sequence on the same platform — great for tests and
    procedural generation, but `<random>` is explicitly *not* cryptographically secure.
    Do not use it for anything a Vogon might try to break.

### Files

`<io/fs>` opens files with `open(path, mode)`, which answers a `File` on an `IoError`
channel. A `File` OWNS its operating-system descriptor: it moves to exactly one owner,
and when that owner leaves scope the descriptor closes. So there is no `close()` to
remember, and no way to leak one -- and the same `IoError` carries every read, write and
seek, so one `??` chain covers a whole function.

Buffering is a separate type you opt into. `<io/buf>` gives `BufReader@(R)` over
anything that can read and `BufWriter@(W)` over anything that can write. Each makes one
system call per window of bytes, not one per line. The constructor takes the handle
(`BufReader.new(nom f, 4096)`), so nothing can use the unbuffered `File` behind the reader.

Here we write a file under `/tmp` and read the first line back through a buffer.

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/files.sushi"
```

Output:

```
Entry filed.
Entry reads: Earth: Mostly Harmless
```

To read a file line by line, use `r.lines()` on a `BufReader`. It takes the reader, and
each item is a `Result@(string, IoError)`. `foreach(line?? in r.lines())` is the line loop:
the `??` on the binder passes an error to the caller. A `BufWriter` ends with
`w.finish()`, which sends the last bytes and reports an error. (A `BufWriter` that goes out
of scope also sends its bytes, but it cannot report an error.)

`<io/contracts>` holds three perks: `Reader`, `Writer` and `Seek`. `File` implements all
three, and `BufReader` and `BufWriter` implement the contract that they wrap. A function
that names the perk, not the type, accepts all of them. `<io/fs>` also declares the console
handles `stdin`, `stdout` and `stderr` as `File` values, so a `Writer` function can write to
the terminal too:

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/buffered.sushi"
```

Output:

```
log written
read: Arthur: towel packed
read: Ford: thumb ready
2 lines
```

A `File` can do more: `f.read_at(offset, count)` and `f.write_at(offset, data)` read and
write at a position and do not move the file offset, and `f.share()` gives a second `File`
for the same open file.

`<io/files>` is the layer under `<io/fs>`: the path utilities (`exists`, `remove`,
`read_dir`) and the `fd_*` descriptor primitives that `File` uses. It has no `open()`.
Other modules you will use include `<sys/env>` for environment variables and
`<collections/strings>` for UTF-8-aware string utilities. The
[Standard Library reference](../standard-library.md)
lists them all.

## Variadic functions

Sometimes you don't know in advance how many arguments a function will get. Sushi has a
**native, safe** variadic mechanism: a trailing parameter written `...T name` collects all
the trailing call arguments into an owned dynamic array `T[]`, which the callee iterates
with `.iter()`, `foreach`, and `.len()` — and which is RAII-destroyed at scope exit. The
marker `...` is a **prefix on the element type**, and the variadic parameter must be last.

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/variadic-sum.sushi"
```

Output:

```
sum(1, 2, 3, 4) = 10
sum(40, 2) = 42
sum() = 0
```

Notice the last call, `sum()`: **zero trailing arguments is valid**, and the callee simply
receives an empty array. A variadic parameter can also follow fixed parameters:

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/variadic-log.sushi"
```

Output:

```
readings (3 values):
  10
  20
  30
empty (0 values):
```

!!! note "Two kinds of variadic, kept apart"
    The `...T` form above is the *safe, native* one: homogeneous element type, owned array,
    full RAII. There is a second, *unsafe* form — a bare `...` — that exists only inside an
    `unsafe external "C"` block for binding C varargs like `printf`. We meet it next. The
    two are deliberately separated so C's untyped varargs never leak into safe Sushi code.

## Calling C (FFI)

When the standard library doesn't have what you need, you can reach down into C. The
foreign function interface lets you declare an external C function and call it. This is the
escape hatch toward self-hosting, and Sushi makes you acknowledge that you are stepping
outside its guarantees.

You declare externals inside an `unsafe external "C" as <namespace>` block. The
`because "<reason>"` clause documents *why* the unsafety is acceptable and silences the
CW5001 four-guarantees warning so the build stays clean. Each declaration is bodyless, and
`= "symbol"` names the actual C link symbol.

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/ffi-strlen.sushi"
```

Output:

```
len = 15
```

Two things are doing quiet work here. First, the `string` argument is automatically
marshalled to a C `char*` for the call and the copy is freed at scope exit — no leak.
Second, and crucially: **externals return raw C values, not `Result`.** An external never
has an error channel. So `libc.strlen(s)` yields a bare `i64`, and
we *wrap it ourselves* in the `length` safe wrapper. `strlen` cannot fail, so `length` is
bare; this is one of the few correct uses of the bare form. Trying to use `??` directly on a raw
external would be a CE2507 error.

!!! note "Wall off the foreign world"
    The guiding rule is *"FFI is not Sushi."* Keep the `unsafe external` block thin, and
    immediately wrap each foreign call in an ordinary Sushi function. When the call can
    fail, the wrapper writes an error channel and folds the raw value into a `Result`.
    After that wrapper, all four guarantees — borrow checking, RAII, `Result`/`Maybe`, and
    bounds safety — are back in force for callers. A NULL never reaches Sushi as a value:
    a C function that can answer one is declared `Maybe@(string)` or `Maybe@(ptr)`, and
    its NULL arrives as `Maybe.None`.

The unsafe block is also the *only* place a bare `...` variadic is allowed, which is how
you bind C's variadic functions like `printf`:

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/ffi-printf.sushi"
```

Output:

```
answer = 42
printf reported 12 bytes written
```

The story continues in [Chapter 16](16-foreign-pointers.md), which covers the `ptr` type —
the opaque handle C functions like `malloc` return — how to read the memory behind it, and
the fences that keep it inside the unsafe realm. The full FFI guide, including all diagnostic codes and the C
argument-promotion rules, lives in [the FFI documentation](../ffi.md).

## Building a library

Finally, your own code. There are two ways to reuse Sushi across files.

The simplest is **source import**: `use "path"` (quotes, no extension) pulls another `.sushi`
file in directly and compiles it together with yours. The path is relative to the importing
file. Here is a small `guidelib.sushi` whose API functions are marked `public` so other files
can see them. Everything a unit exports carries the marker -- a `const`, a `struct`, an
`enum` and a `perk` as much as a `fn` -- and everything unmarked stays that unit's own:

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/guidelib.sushi"
```

A program imports it by path and calls those functions as if they were local:

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/use-library.sushi"
```

Compile and run it with the usual one-liner — the compiler finds `guidelib.sushi` next to
it automatically:

```bash
./sushic use-library.sushi -o use-library
./use-library
```

Output:

```
add(40, 2) = 42
answer() = 42
```

### Imports behind a name, and re-exports

`use "guidelib" as guide` puts the public names of `guidelib` behind the name `guide`. You
write `guide.add(40, 2)`, and your own `add` stays free:

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/use-namespace.sushi"
```

Output:

```
guide.add(40, 2) = 42
add(40, 2) = 4002
```

The dot works in every place where you write a name: a type (`let geo.Vec v`), a
constructor, a match arm, a perk constraint, a value and a call. The same form works for a
standard module: `use <collections/hashmap> as hm`.

The scope of a unit contains only its own declarations and the names that its own `use`
lines bring. An import is **not** transitive: if `relay.sushi` has `use "guidelib"`, a
program that imports `relay` does not see `add`. To pass the public names on, `relay`
writes `public use "guidelib"`. Then the importers of `relay` get the public names of
`guidelib` as if `relay` declared them.

### Compiled libraries

For genuine reuse you compile the library once into a `.slib` — a single file holding the
library's source text plus the index the compiler needs for type information — and link it
by name. Build the library with `--lib`, then point `SUSHI_LIB_PATH` at it and import it
with `use <lib/...>`:

```bash
./sushic --lib --lib-version 1.0.0 guidelib.sushi -o /tmp/guidelib.slib
export SUSHI_LIB_PATH=/tmp
./sushic use-slib.sushi -o use-slib
./use-slib
```

where the program imports the library by name rather than compiling its source alongside:

```sushi
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/use-slib.sushi"
```

Output:

```
add(40, 2) = 42
answer() = 42
```

!!! note "Sharing further afield"
    For distributing libraries beyond your own machine there is the **`nori`** packager and
    the central **Omakase** repository at `omakase.lubica.net`. The default `.slib` holds
    source text, so it is portable, and its generic functions and types work in the
    program that imports it. A `--lib-kind binary` library is not portable across
    platforms (`CE3504`) and exports no extension methods. A library has no transitive
    dependencies. See the
    [libraries guide](../libraries.md)
    for the details.

## What you learned

- Standard-library modules are imported with `use <name>`: `<time>` for sleeping,
  `<math>` for numeric functions (polymorphic `abs`/`min`/`max`; `f64` for the others),
  `<random>` for seedable pseudo-randomness, and `<io/fs>` for files: `open()` gives a
  `File` on the `IoError` channel.
- `<io/buf>` adds buffering and the line loop `foreach(line?? in r.lines())`, and
  `<io/contracts>` lets a function accept any `Reader` or `Writer`.
- A native variadic parameter `...T name` collects trailing arguments into an owned `T[]`;
  zero arguments is valid, and it must be the last parameter.
- FFI lets you call C from inside an `unsafe external "C" as <ns> because "<reason>"` block;
  externals return **raw** C values and never have an error channel, so you wrap
  them in a safe Sushi function — and `string` arguments are marshalled and freed for you.
- C varargs (`printf`-style bare `...`) are bound only inside the unsafe external block,
  kept strictly apart from safe native `...T` variadics.
- Reuse your own code with source `use "path"` imports, or compile a reusable `.slib` with
  `--lib` and link it via `SUSHI_LIB_PATH` and `use <lib/...>`.
- `use "x" as ns` puts the names behind `ns.`; an import is not transitive, and
  `public use "x"` re-exports.

## Where to go next

You have travelled from "Mostly Harmless" all the way to linking C and shipping libraries.
Five more chapters follow: [variadic functions](15-variadic-functions.md),
[foreign pointers](16-foreign-pointers.md),
[first-class functions](17-first-class-functions.md), [closures](18-closures.md) and
[higher-order combinators](19-higher-order-combinators.md). The reference documentation
goes deeper than any tutorial can:

- [Language Reference](../language-reference.md)
  — the complete grammar, types, and operators.
- [Standard Library Reference](../standard-library.md)
  — every module and function.
- [FFI Guide](../ffi.md) and the
  [Variadics Design Note](../design/variadics.md)
  — the full story behind this chapter.
- [Libraries Guide](../libraries.md)
  — building, distributing, and the `nori` packager.

The compiler is still on your side. Go build something improbable.
