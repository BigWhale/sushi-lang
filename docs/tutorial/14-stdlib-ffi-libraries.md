# 14. The Standard Library, FFI & Libraries

You have a language. Now you need batteries. This final chapter is a guided tour of the
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
importing your own source files (which uses quotes — more on that later). Each module is a
precompiled unit that the compiler links into your binary.

### Time

The `<time>` module gives you POSIX-precision sleep functions. They all return
`Result<i32>` (0 on success, or the remaining microseconds if a signal interrupts the
sleep), so you unwrap them like any other `Result`. We keep the duration tiny here so the
program returns almost instantly.

```
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/timing.sushi"
```

Output:

```
Improbability drive warming up...
Drive online. Anything is now infinitely probable.
```

!!! note "No `??` in `main`"
    We unwrap with `.realise(default)` rather than `??`. The `??` operator is wonderful
    inside ordinary functions, but using it in `main` triggers a CW2511 warning — and a
    warning means a non-zero compile exit, which we treat as failure. In `main`, prefer
    `match`, `if (result)`, or `.realise(default)`.

### Math

The `<math>` module wraps LLVM's numeric intrinsics. Alongside the type-suffixed forms
(`abs_i32`, `min_f64`, …) there are **polymorphic** helpers — `abs`, `min`, `max`, `sqrt`,
`hypot` — that pick the right instruction for whatever numeric type you hand them.

```
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

```
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

The `<io/files>` module opens files with `open(path, mode)`, which returns a
`FileResult<file>`. Instead of the usual `Result`, file operations use a dedicated
`FileResult`/`FileError` pair so you can match on specific failures like
`FileError.NotFound()` or `FileError.PermissionDenied()`. Here we write a file under `/tmp`
and read it straight back.

```
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/files.sushi"
```

Output:

```
Entry filed.
Entry reads: Earth: Mostly Harmless
```

Other modules you will reach for include `<env>` for environment variables, `<io/stdio>`
for stream access, and `<collections/strings>` for UTF-8-aware string utilities. The
[Standard Library reference](https://github.com/yourusername/sushi/blob/main/docs/standard-library.md)
lists them all.

## Variadic functions

Sometimes you don't know in advance how many arguments a function will get. Sushi has a
**native, safe** variadic mechanism: a trailing parameter written `...T name` collects all
the trailing call arguments into an owned dynamic array `T[]`, which the callee iterates
with `.iter()`, `foreach`, and `.len()` — and which is RAII-destroyed at scope exit. The
marker `...` is a **prefix on the element type**, and the variadic parameter must be last.

```
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

```
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

```
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/ffi-strlen.sushi"
```

Output:

```
len = 15
```

Two things are doing quiet work here. First, the `string` argument is automatically
marshalled to a C `char*` for the call and the copy is freed at scope exit — no leak.
Second, and crucially: **externals return raw C values, not `Result`.** That is the single
exception to Sushi's implicit-`Result` rule. So `libc.strlen(s)` yields a bare `i64`, and
we *wrap it ourselves* in the `length` safe wrapper. Trying to use `??` directly on a raw
external would be a CE2507 error.

!!! note "Wall off the foreign world"
    The guiding rule is *"FFI is not Sushi."* Keep the `unsafe external` block thin, and
    immediately wrap each foreign call in an ordinary Sushi function that folds the raw
    value back into a `Result`. After that wrapper, all four guarantees — borrow checking,
    RAII, `Result`/`Maybe`, and bounds/null safety — are back in force for callers.

The unsafe block is also the *only* place a bare `...` variadic is allowed, which is how
you bind C's variadic functions like `printf`:

```
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/ffi-printf.sushi"
```

Output:

```
answer = 42
printf reported 12 bytes written
```

The full FFI guide — including the `ptr` type, the diagnostic codes CE5001–CE5005, and the
C argument-promotion rules — lives in
[the FFI documentation](https://github.com/yourusername/sushi/blob/main/docs/ffi.md).

## Building a library

Finally, your own code. There are two ways to reuse Sushi across files.

The simplest is **source import**: `use "path"` (quotes, no extension) pulls another `.sushi`
file in directly and compiles it together with yours. The path is relative to the importing
file. Here is a small `guidelib.sushi` whose API functions are marked `public` so other files
can see them:

```
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/guidelib.sushi"
```

A program imports it by path and calls those functions as if they were local:

```
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/use-library.sushi"
```

Compile and run it with the usual one-liner — the compiler finds `guidelib.sushi` next to
it automatically:

```
./sushic use-library.sushi -o use-library
./use-library
```

Output:

```
add(40, 2) = 42
answer() = 42
```

For genuine reuse you compile the library once into a `.slib` — a single file holding
bitcode plus the metadata the compiler needs for type information — and link it by name.
Build the library with `--lib`, then point `SUSHI_LIB_PATH` at it and import it with
`use <lib/...>`:

```
./sushic --lib guidelib.sushi -o /tmp/guidelib.slib
export SUSHI_LIB_PATH=/tmp
./sushic use-slib.sushi -o use-slib
./use-slib
```

where the program imports the precompiled library rather than the source:

```
--8<-- "docs/tutorial/examples/14-stdlib-ffi-libraries/use-slib.sushi"
```

Output:

```
add(40, 2) = 42
answer() = 42
```

!!! note "Sharing further afield"
    For distributing libraries beyond your own machine there is the **`nori`** packager and
    the central **Omakase** repository at `omakase.lubica.net`. Be aware of the current
    limits: libraries have no transitive dependencies, are not portable across platforms,
    and do not share generic instantiations across the boundary. See the
    [libraries guide](https://github.com/yourusername/sushi/blob/main/docs/libraries.md)
    for the details.

## What you learned

- Standard-library modules are imported with `use <name>`: `<time>` for sleeping,
  `<math>` for numeric intrinsics (with polymorphic `abs`/`min`/`max`/`sqrt`/`hypot`),
  `<random>` for seedable pseudo-randomness, and `<io/files>` for file I/O via
  `FileResult`/`FileError`.
- A native variadic parameter `...T name` collects trailing arguments into an owned `T[]`;
  zero arguments is valid, and it must be the last parameter.
- FFI lets you call C from inside an `unsafe external "C" as <ns> because "<reason>"` block;
  externals return **raw** C values (the one exception to implicit `Result`), so you wrap
  them in a safe Sushi function — and `string` arguments are marshalled and freed for you.
- C varargs (`printf`-style bare `...`) are bound only inside the unsafe external block,
  kept strictly apart from safe native `...T` variadics.
- Reuse your own code with source `use "path"` imports, or compile a reusable `.slib` with
  `--lib` and link it via `SUSHI_LIB_PATH` and `use <lib/...>`.

## Where to go next

That is the end of the tutorial — you have travelled from "Mostly Harmless" all the way to
linking C and shipping libraries. So long, and thanks for all the fish. From here, the
reference documentation goes deeper than any tutorial can:

- [Language Reference](https://github.com/yourusername/sushi/blob/main/docs/language-reference.md)
  — the complete grammar, types, and operators.
- [Standard Library Reference](https://github.com/yourusername/sushi/blob/main/docs/standard-library.md)
  — every module and function.
- [FFI Guide](https://github.com/yourusername/sushi/blob/main/docs/ffi.md) and the
  [Variadics Design Note](https://github.com/yourusername/sushi/blob/main/docs/design/variadics.md)
  — the full story behind this chapter.
- [Libraries Guide](https://github.com/yourusername/sushi/blob/main/docs/libraries.md)
  — building, distributing, and the `nori` packager.

The compiler is still on your side. Go build something improbable.
