# Compiler Reference

[← Back to Documentation](index.md)

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
./sushic [options] <source-file> [-o output]
```

### Examples

```bash
# Compile single file (output: hello)
./sushic hello.sushi

# Specify output name
./sushic program.sushi -o myprogram

# Multi-unit project (units are pulled in via `use` imports, not extra CLI args)
./sushic main.sushi -o app

# With optimization
./sushic --opt O3 program.sushi -o optimized
```

### Options

| Option              | Description                                        |
|---------------------|----------------------------------------------------|
| `-h`, `--help`      | Print the usage text and exit                      |
| `--version`         | Print the version banner and exit                  |
| `-o NAME`           | Specify output executable name                     |
| `--opt LEVEL`       | Set optimization level (none, mem2reg, O1, O2, O3) |
| `--no-verify`       | Do not verify the LLVM IR before and after optimization |
| `--keep-object`     | Keep the `.o` file after the link (add `--no-incremental` for a program of more than one unit) |
| `--build-stdlib`    | Build the standard library bitcode again, from its generators |
| `--lib`             | Compile to a library (`.slib`) instead of an executable |
| `--lib-kind KIND`   | What the library ships: `source` (default), `binary`, `hybrid` |
| `--lib-version X.Y.Z` | Version of the library being built                |
| `--lib-info FILE`   | Print the metadata report of a `.slib` file        |
| `--docs`            | With `--lib-info`: print each symbol's documentation block |
| `--color WHEN`      | `always`, `never` or `auto` (the default)          |
| `--ignore-compiler-version` | Load libraries this compiler does not satisfy ([CE3503](error-catalog.md#ce3503)) |
| `--warn-missing-docs` | Warn about anything with no documentation block ([CW7002](error-catalog.md#cw7002)-[CW7006](error-catalog.md#cw7006)) |
| `--warn-unused`     | Warn about a dead private declaration ([CW1004](error-catalog.md#cw1004)) and an unused import ([CW3006](error-catalog.md#cw3006)) |
| `--dont-panic`      | Allow the `dont_panic` marker in this build, which removes the bounds check of `[]` in the marked function ([CE0152](error-catalog.md#ce0152)) |
| `--traceback`       | Show full Python traceback on errors               |
| `--dump-parse`      | Print the raw Lark parse tree                      |
| `--dump-ast`        | Print abstract syntax tree                         |
| `--dump-ll`         | Print LLVM IR to terminal                          |
| `--write-ll`        | Write LLVM IR to `<output>.ll` file                |
| `--no-incremental`  | Force full rebuild, ignoring cached object files   |
| `--clean-cache`     | Remove `__sushi_cache__/` directory and exit       |
| `--cache-dir PATH`  | Custom cache directory location                    |

### Documentation Completeness

```bash
./sushic --warn-missing-docs main.sushi
```

The compiler always checks what a doc block CLAIMS against the declaration beside it. This
flag adds the other half: it reports what is missing.

```
mymodule.sushi: warning [CW7006]: this unit has no documentation block.
mymodule.sushi:3:8: warning [CW7002]: this struct has no documentation block: 'Ship'.
  | struct Ship:
  `        --+-
mymodule.sushi:9:4: warning [CW7004]: 'hull_of' returns a value, and no '- Returns:' tag says what it is.
  | fn hull_of(Ship ship) i32:
  `    ---+---
```

Every declaration is asked, public and private. `fn main()` and the `unsafe external` seam
are the only exemptions, and a library's units are never linted. The five codes and the
rules behind them are in
[Documentation Blocks](documentation-blocks.md#completeness-warn-missing-docs).

### Unchecked Indexes

```bash
./sushic --dont-panic main.sushi
```

`--dont-panic` lets the build accept functions marked `dont_panic because "<reason>"`. In a
marked body, an index on an array or a string is not bounds-checked. Without the flag, the
marker in a user unit is [CE0152](error-catalog.md#ce0152). The flag is also the consent of
a consumer to the marked functions of a source library, and to the marked generic templates
of any library kind. A concrete marked body in a binary or hybrid library is compiled
already and needs no consent. A bundled stdlib unit needs no flag. A build that compiles no marked
function outside the stdlib gets [CW0003](error-catalog.md#cw0003) for the flag. The build
prints no list of what it accepted. See the
[language reference](language-reference.md#unchecked-indexes-dont_panic).

### Unused Declarations and Imports

```bash
./sushic --warn-unused main.sushi
```

The flag adds two warnings. It is off by default.

```
main.sushi:3:1: warning [CW3006]: '<time>' brings nothing this unit names.
main.sushi:7:4: warning [CW1004]: private function 'helper' is never used in this unit.
```

**[CW1004](error-catalog.md#cw1004)** is a private top-level declaration that nothing in its unit reaches. A private
name is visible only in its own unit, so the lint checks each unit alone. The roots are
every `public` declaration, every `extend` block (an extension method and a perk
implementation), the `unsafe external` blocks, and `fn main()`. A private declaration that
only another dead declaration names is also dead, so the lint reports both. A public
declaration is API, and the lint never reports it.

**[CW3006](error-catalog.md#cw3006)** is a `use` line whose unit names nothing that the import brings. The import
brings the names it declares or re-exports, the member names behind its alias, the
extension and perk methods of the imported unit, and for `<collections/strings>` the
string methods that the module enables. A `public use` re-exports to the importers of the
unit, so the lint never reports it. The lint does not report an import of a library.

The lint does not check the units of a library, and it does not check the bundled stdlib
units. The test runner sets the hidden variable `SUSHI_STDLIB_DEAD_GATE=1` to check the
bundled stdlib units too.

### Library Compilation

```bash
# Compile source to a reusable library
./sushic --lib --lib-version 1.0.0 mylib.sushi -o mylib.slib

# Inspect library metadata
./sushic --lib-info mylib.slib

# The same report, with every documentation block in it
./sushic --lib-info mylib.slib --docs
```

A `.slib` ships **source** by default: the consumer compiles its units and caches the
objects, so one file works on every platform. `--lib-kind binary` ships LLVM bitcode
instead, which binds the library to the platform that built it, and `--lib-kind hybrid`
ships both.

Every library states its own version. `--lib-version` supplies it, unless the `nori.toml`
in the current directory does; neither is **[CE3505](error-catalog.md#ce3505)**, and a `nori.toml` there that cannot be
read (**[CE3518](error-catalog.md#ce3518)**) or is not valid (**[CE3517](error-catalog.md#ce3517)**) stops the build. A build also stamps `requires_compiler` --
`~<major>.<minor>` of the building compiler -- because a source library is compiled by the
consumer's compiler and a later one may reject it. A consumer outside that range is
**[CE3503](error-catalog.md#ce3503)**, and `--ignore-compiler-version` overrides the check for the whole build.

The plain report is the API surface: one line per symbol, plus a parameter's `nom` mode
beside its type, which is the one mode a type cannot spell for itself. A perk prints with
each method's signature, receiver mode included (`fn read(poke self, u8[] buf) i32 |
IoError`), and `Perk Implementations` lists every type that implements it, a generic-target
template (`extend Box@(T) with Show`) beside the concrete ones. `Extension Methods` lists
each extension method as it was declared (`extend Vec static at(i32 x, i32 y) Vec`), the
templates beside the concrete ones. An enum declared with `error` prints with that keyword
(`error ParseError:`), a generic one too (`error DecodeError@(T):`). `Conversions` lists
each conversion between two error types that the library declares, one line per pair, in
the form of its declaration without the colon:

```text
Conversions (1):
  extend LowError as LibError
```

A binary or hybrid library carries its conversions in its manifest, so a consumer's `??`
and `as` call them with no source.

`--docs` prints each symbol's documentation block under its signature. It is opt-in
because prose is what makes a report long -- a library of forty documented functions runs
to ten screens with the blocks in and one and a half without. A `.slib` carries the doc
text of every symbol it exports, so this answers what a library holds without its
sources -- see
[Documentation Blocks](documentation-blocks.md#what-travels-in-a-slib).

In a repository checkout, `--lib-info` runs the Sushi-written `toolchain/bin/slib-info`
binary when it exists (build it with `./toolchain/build.py`) and returns its exit code;
without the binary the built-in Python reader prints the same report. `--docs` and
`--color` reach the tool as themselves, and the tool answers `--help` on its own. Set
`SUSHI_TOOLCHAIN=off` to force the Python path, or `SUSHI_TOOLCHAIN_BIN=DIR` to point at
a different tool directory.

### Colour

Everything the compiler prints to a terminal -- a diagnostic, the version banner and the
`--lib-info` report -- makes one colour decision. Highest precedence first:

| # | Rung | Answer |
|---|---|---|
| 1 | `--color=always` / `--color=never` | as asked |
| 2 | `NO_COLOR` set to **anything**, the empty string included | off |
| 3 | `CLICOLOR_FORCE` set to anything but `0` | on, terminal or not |
| 4 | `TERM=dumb` | off |
| 5 | the stream is a terminal | on, else off |

Rung 2 is [no-color.org](https://no-color.org)'s rule: the variable's presence is the
signal, never its value. Rung 3 is what lets a script capture a coloured report, and what
lets this project's own gates compare one.

Colour changes no text. Strip the escapes from a coloured report and the plain report
comes back, byte for byte.

Libraries are used via `use <lib/...>` statements in source code:

<!-- docs-sweep: skip (needs a .slib library built from the page's earlier example) -->
```sushi
use <lib/mylib>

fn main() i32:
    # Library functions/types are now available
    return 0
```

See [Libraries](libraries.md) for complete documentation.

### Incremental Compilation

Multi-unit projects (those with `use` statements to other `.sushi` files) automatically use incremental compilation. Each unit is compiled to its own `.o` file and cached in `__sushi_cache__/`. Only units whose content or dependencies change are recompiled.

```bash
# First build: compiles all units
./sushic main.sushi

# Second build: reuses cached .o files (near-instant codegen)
./sushic main.sushi

# Force full rebuild
./sushic --no-incremental main.sushi

# Clean cache and exit
./sushic --clean-cache

# Custom cache directory
./sushic --cache-dir /tmp/my_cache main.sushi
```

**How it works:**
- Semantic analysis always runs whole-program (fast, pure Python)
- After analysis, a content-based fingerprint is computed per unit
- A unit's fingerprint covers its own source and declarations, and the **interface** of
  every unit in its dependency closure: each declaration's shape (a struct's field order
  and field types, an enum's variant order and payloads, a signature's parameter modes
  and error channel) and each public constant's **value**, because a dependent bakes all
  of those into its own object. The closure is transitive, so a type reached through a
  `public use` counts, and it includes a source library's injected units
- It also covers the name of the unit's file as a diagnostic prints it, relative to the
  working directory of the build, because an `assert` prints that name at run time. The
  same source built from another directory with one `--cache-dir` is another object
- Each cached object is content-addressed: its filename is
  `{name}.{global_key}.{fingerprint}.o`, where `global_key` digests the compiler
  version, target triple, opt level, **and** a content digest of the compiler's own
  sources, and `fingerprint` digests the unit itself
- A hit therefore means exactly "an object built from this input, by this compiler,
  with these settings" — there is no separate staleness check to get wrong; a
  compiler upgrade, a compiler source edit, or a settings change simply names a
  different file and is a cache miss by construction
- Stdlib and library imports are cached as `.o` files the same way
- Publishing an object is atomic (write to a temp file, then `os.replace`), so a
  concurrent build sharing the same cache directory never links a truncated object
- All `.o` files are linked together at the end

Because invalidation is structural, `--clean-cache` is never needed for
correctness — it only prunes entries for settings/versions you no longer use.

**Output example:**
```
Code generation:
  main                           [cached]
  helpers/math                   [rebuilt]
  helpers/strings                [cached]

Codegen: 3 units (2 cached, 1 rebuilt) in 0.05s
Linking: 3 units + 1 stdlib in 0.03s
Success! Wrote native binary: main
```

**Notes:**
- Single-file programs skip incremental compilation entirely
- `--dump-ll` forces the monolithic (non-incremental) path
- `--write-ll` and `--keep-object` have no effect in incremental mode: the compiler
  gives the warning [CW0003](error-catalog.md#cw0003) and the build goes on. Add `--no-incremental` to get the
  `.ll` file or the object file
- The cache directory (`__sushi_cache__/`) is already in `.gitignore`

### Grammar Cache

The parser tables of `grammar.lark` are kept in one file in the user cache directory:
`~/Library/Caches/sushi` on macOS, `$XDG_CACHE_HOME/sushi` or `~/.cache/sushi` on Linux.
The first compile writes the file, and each later compile loads it instead of building
the tables again. Set `SUSHI_GRAMMAR_CACHE_DIR=DIR` to use a different directory, or
`SUSHI_GRAMMAR_CACHE_DIR=off` to build the tables in each process.

The cache never changes a result. The file name holds a hash of the grammar and of the
Lark and Python versions, so a changed grammar does not read an old file. A broken file
is built again. The compiler reads the file only when it is a regular file of the user
in a directory of the user that no other user can write, because the file is a Python
pickle. When the directory cannot be made or used, the compiler builds the tables in
the process and gives no message.

## Optimization Levels

Sushi provides a complete LLVM optimization pipeline with multiple levels.

### Overview

| Level         | Description              | Use Case                        | Compile Time |
|---------------|--------------------------|---------------------------------|--------------|
| `none`        | No optimization          | Debugging, development          | Fastest      |
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

### Optimization Example

At `O2` and `O3`, LLVM folds an expression whose operands are known at compile time:

```
let i32 x = 10
let i32 y = (x + 5) * 2    # the optimized IR stores the constant 30
```

Use `--write-ll` or `--dump-ll` (below) to compare the IR of your own program at each level.

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

### Dump the Parse Tree

Print the raw Lark parse tree, before the AST is built:

```bash
./sushic --dump-parse program.sushi
```

### Dump AST

Print the abstract syntax tree:

```bash
./sushic --dump-ast program.sushi
```

**Output includes:**
- The AST as the builder makes it, before the semantic analysis
- The written types of declarations. A resolved type is not filled in yet (`resolved_type=None`)

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

- **CE0xxx**: Internal errors, function and variadic errors
- **CE1xxx**: Scope/variable errors
- **CE2xxx**: Type/array/struct errors, Result validation
- **CE24xx**: Borrow and reference errors
- **CE3xxx**: Units, libraries and `.slib` files
- **CE4xxx**: Perks
- **CE5xxx**: FFI and the `ptr` quarantine
- **CE6xxx**: Syntax (the parser's own diagnostics)
- **CE7xxx**: Documentation blocks
- **CWxxxx**: Warnings
- **RExxxx**: Runtime errors

The text, the reason and the escape of each code are in the [Error Catalog](error-catalog.md).

### Driver Diagnostics

The driver reads the command line and the source files before the analysis starts.
It checks the output path ([CE3019](error-catalog.md#ce3019), [CE3020](error-catalog.md#ce3020), [CE3500](error-catalog.md#ce3500)) in one place, after the analysis and
before code generation. It gives these diagnostics:

| Code | Text | Cause |
|------|------|-------|
| [CE3017](error-catalog.md#ce3017) | `cannot read '<path>': <reason>` | The main source or an imported unit cannot be read: the path is a directory, the file cannot be opened, or the text is not valid UTF-8 (the reason names the first bad byte and its line) |
| [CE3018](error-catalog.md#ce3018) | `no source file to compile` | The command line names no `.sushi` file, and the run is not `--build-stdlib` or `--clean-cache` alone |
| [CE3019](error-catalog.md#ce3019) | `cannot write '<path>': '<directory>' is not a directory` | The `-o` path is in a directory that does not exist. The compiler does not create it |
| [CE3020](error-catalog.md#ce3020) | `cannot write '<path>': <reason>` | The compiler cannot write a file that the command line asks for: the `-o` path is a directory or is in a directory that you cannot write, or a write of the object file, the `.slib` or the `--cache-dir` cache fails. The reason is the operating system's |
| [CE3500](error-catalog.md#ce3500) | `library output path must have .slib extension: '<path>'` | A `--lib` build names an `-o` path with no `.slib` extension |
| [CW0002](error-catalog.md#cw0002) | `cannot write LLVM IR to '<path>': <reason>` | `--write-ll` cannot write the `.ll` file. The build is written; the IR is not |
| [CE0152](error-catalog.md#ce0152) | `<function> is marked dont_panic, and this build does not allow it` | A user unit writes the `dont_panic` marker and the build has no `--dont-panic`, or a library that the build uses has the marker and the build has no `--dont-panic` |
| [CE6111](error-catalog.md#ce6111) | `'dont_panic' cannot stand on <position>: <reason>` | The marker is on a lambda, a perk contract method or an extern |
| [CW0003](error-catalog.md#cw0003) | `'<flag>' has no effect <reason>` | The build does not read the flag: `--docs` without `--lib-info`, `--lib-kind` or `--lib-version` without `--lib`, `--keep-object` with `--lib`, `--write-ll` / `--keep-object` on the incremental build, or `--dont-panic` with `--lib-info` or on a build with no function marked `dont_panic` outside the bundled stdlib |
| [CW0004](error-catalog.md#cw0004) | `<function> is marked dont_panic, but its body has no index to uncheck` | The marker removes no check. For an author only: a user unit and a bundled stdlib unit |

A warning makes the compiler exit 1; an error makes it exit 2.

### Common Errors

#### [CE1001](error-catalog.md#ce1001): Undeclared Identifier

<!-- docs-sweep: error CE1001 -->
```sushi
fn main() i32:
    # ERROR CE1001: use of undeclared identifier 'x'
    println(x)
    return 0
```

**Fix:** Declare variable with `let` before use.

#### [CE1002](error-catalog.md#ce1002): Rebind to Undeclared Variable

<!-- docs-sweep: error CE1002 -->
```sushi
fn main() i32:
    # ERROR CE1002: assignment to undeclared variable 'count'
    count := 5

    return 0
```

**Fix:** Declare with `let` first (`let i32 count = 0`) before rebinding with `:=`.

#### [CE2406](error-catalog.md#ce2406): Use of Destroyed Variable

<!-- docs-sweep: error CE2406 -->
```sushi
fn main() i32:
    let i32[] arr = from([1, 2, 3])
    arr.destroy()

    # ERROR CE2406: use of destroyed variable 'arr'
    println(arr.len())

    return 0
```

The `borrow` pass reports the use. It reads the flow of the function, and it covers a
value of every type. The use gives one diagnostic. (CE2024, the old array-only code, is
retired.)

**Fix:** Don't use a variable after `.destroy()`, or use `.free()` instead. After
`.free()` the array is empty (its length is 0) and you can use it again.

#### [CE2009](error-catalog.md#ce2009): Wrong Argument Count

A built-in method takes a fixed number of arguments, and a miscount is [CE2009](error-catalog.md#ce2009), as on
every other callee. `.realise()` is one example.

<!-- docs-sweep: error CE2009 -->
```sushi
fn get_value() i32 | StdError:
    return Result.Ok(42)

fn main() i32:
    let Result@(i32, StdError) r = get_value()

    # ERROR CE2009: wrong number of arguments: 'Result@(i32, StdError).realise' expects 1, got 0
    let i32 x = r.realise()

    return 0
```

**Fix:** Provide default value: `r.realise(0)`.

#### [CE2503](error-catalog.md#ce2503): .realise() Type Mismatch

<!-- docs-sweep: error CE2503 -->
```sushi
fn get_value() i32 | StdError:
    return Result.Ok(42)

fn main() i32:
    let Result@(i32, StdError) r = get_value()

    # ERROR CE2503: realise() default type mismatch: expected 'i32', got 'string'
    let i32 x = r.realise("wrong")

    return 0
```

**Fix:** Use correct type: `r.realise(0)`.

#### [CE2505](error-catalog.md#ce2505): Assigning Result Without Handling

Assigning a `Result`-returning call directly to a non-`Result` variable is refused, and
the code names the fix:

<!-- docs-sweep: error CE2505 -->
```sushi
fn get_value() i32 | StdError:
    return Result.Ok(42)

fn main() i32:
    # ERROR CE2505: cannot assign Result@(T, E) to non-Result variable without handling
    let i32 x = get_value()

    return 0
```

**Fix:** Use `.realise()`: `let i32 x = get_value().realise(0)`.

#### [CE2507](error-catalog.md#ce2507): Using ?? on an Operand That Is Not a Result

<!-- docs-sweep: error CE2507 -->
```sushi
fn run() i32 | StdError:
    let i32 x = 5

    # ERROR CE2507: `??` takes a `Result@(T, E)`, got 'i32'
    let i32 y = x??

    return Result.Ok(0)

fn main() i32:
    match run():
        Result.Ok(code) -> return code
        Result.Err(_) -> return 1
```

**Fix:** Use `??` only on a `Result@(T, E)`. A `Maybe@(T)` is refused too, because it holds
no error value: write one with `m.or_err(nom e)??`. A user enum with `Ok`/`Err` variants is
not a `Result`, because `??` reads the type and not the names of its variants.

#### [CE0131](error-catalog.md#ce0131): Using ?? in a Bare Body

A callable has an error channel only when its signature writes `| E`, or returns an
explicit `Result@(T, E)`. A BARE body (a function, an extension or perk method, or a lambda
with no `| E`) returns its value directly. It has no error channel, so `??` cannot
propagate an error out of it. `main` is bare, so a `??` in `main` is [CE0131](error-catalog.md#ce0131) too:

<!-- docs-sweep: error CE0131 -->
```sushi
fn might_fail() i32 | StdError:
    return Result.Ok(4)

extend i32 scaled() i32:
    # ERROR CE0131: '??' operator not allowed in an extension method
    let i32 x = might_fail()??
    return self * x

fn main() i32:
    println(3.scaled())
    return 0
```

**Fix:** Handle the Result in the body with `match` or `.realise(default)`, or give the
callable an error channel with `| E`. Then `??` is legal in the body, the call yields
`Result@(T, E)`, and the body spells its success with `return Result.Ok(...)` (a bare
`return value` in a channel body is [CE2030](error-catalog.md#ce2030)). In `main`, handle the Result and return an
exit code, because `main` cannot have a channel ([CE0106](error-catalog.md#ce0106)):

```sushi
fn might_fail() i32 | StdError:
    return Result.Ok(4)

extend i32 scaled() i32 | StdError:
    let i32 x = might_fail()??
    return Result.Ok(self * x)

fn main() i32:
    println(3.scaled().realise(0))
    return 0
```

#### [CE2091](error-catalog.md#ce2091): Result Constructor in a Bare Body

A bare body (a function, a lambda, or an extension or perk method with no `| E`) returns
the value itself. `return Result.Ok(...)` and `return Result.Err(...)` are refused there:

<!-- docs-sweep: error CE2091 -->
```sushi
fn double(i32 x) i32:
    # ERROR CE2091: function 'double' must use a bare 'return <value>'
    return Result.Ok(x * 2)

fn main() i32:
    return double(0)
```

**Fix:** Write `return x * 2`. If the function can fail, write `| E` in its signature, and
then both constructors are legal.

#### [CE0106](error-catalog.md#ce0106): main() Must Return a Bare Integer

`main` returns the exit code of the program. It returns a bare integer type and has no
error channel. A `| E` on `main`, or a `Result@(T, E)` return, is refused:

<!-- docs-sweep: error CE0106 -->
```sushi
# ERROR CE0106: main() function must return a bare integer type (i8-i64, u8-u64)
fn main() i32 | StdError:
    return Result.Ok(0)
```

**Fix:** Write `fn main() i32:` and `return 0`. Handle each failure in the body with
`match` or `.realise(default)`, and return a code for it.

#### [CE3007](error-catalog.md#ce3007): No main() Function

```sushi
# ERROR CE3007: no main() function: an executable needs an entry point
fn helper(i32 a) i32:
    return a + 1
```

**Fix:** Add `fn main() i32:`, or compile the unit as a library with `--lib`. A library must
not carry a `main()` -- that is the mirror error, [`CE3501`](error-catalog.md#ce3501).

#### [CE3008](error-catalog.md#ce3008): Linking Failed

Reported when the C compiler used as the linker exits non-zero -- most often an
`unsafe external` declaration naming a symbol that no linked library provides. The linker's
own output is attached to the diagnostic as notes.

**Fix:** Read the attached notes. An undefined symbol means the link name is wrong or the
library it lives in was not linked.

### Constant Expression Errors

#### [CE0108](error-catalog.md#ce0108): Expression Not Compile-Time Constant

```sushi
fn get_value() i32:
    return 42

# ERROR CE0108: a function call is not a compile-time constant
const i32 X = get_value()
```

**Fix:** Only use compile-time evaluable expressions (literals, arithmetic, bitwise, etc.).

#### [CE0109](error-catalog.md#ce0109): Circular Constant Dependency

```sushi
# ERROR CE0109: circular constant dependency detected: B -> A -> B
const i32 A = B + 1
const i32 B = A + 1
```

**Fix:** Remove circular dependencies between constants.

#### [CE0110](error-catalog.md#ce0110): Unsupported Operation in Constant

```sushi
# ERROR CE0110: unsupported operation 'bitwise & on non-integer type' in constant expression
const f64 INVALID = 3.14 & 2.0  # Bitwise AND on float
```

**Fix:** Use only supported operations for the type (bitwise only on integers).

#### [CE0111](error-catalog.md#ce0111): Invalid Type Cast in Constant

```sushi
# ERROR CE0111: invalid type cast in constant expression from string to i32
const i32 INVALID = "hello" as i32
```

**Fix:** Only cast between compatible numeric types.

#### [CE0112](error-catalog.md#ce0112): Division by Zero

The compiler reads the divisor and it is zero. One compile-time arithmetic answers a
constant and a body alike, so both spellings below are refused. A divisor the compiler
cannot read is ordinary code and is left alone.

```sushi
# ERROR CE0112: division by zero
const i32 INVALID = 100 / 0
```

```sushi
# ERROR CE0112: division by zero
let i32 x = 100 / 0
```

**Fix:** Ensure divisor is non-zero.

### Warnings

#### [CW2001](error-catalog.md#cw2001): Unused Result Value

```sushi
fn get_value() i32 | StdError:
    return Result.Ok(42)

fn main() i32:
    # WARNING CW2001: unused 'Result@(i32, StdError)' value
    get_value()

    return 0
```

**Fix:** Handle result or explicitly discard:
```sushi
let Result@(i32, StdError) r = get_value()  # Store for later
let i32 x = get_value().realise(0)  # Use immediately
```

### Runtime Errors

#### [RE2020](error-catalog.md#re2020): Array Bounds Check Failed

```sushi
fn main() i32:
    let i32[3] arr = [1, 2, 3]
    let i32 i = 10

    # Runtime error: direct indexing out of bounds
    let i32 x = arr[i]

    return 0
```

Direct indexing (`arr[i]`) is checked at runtime and aborts on an out-of-bounds
access. (Use `arr.get(i)`, which returns `Maybe@(i32)`, for safe access instead.)

**Runtime output:**
```
Runtime Error RE2020: array index 10 out of bounds for array of size 3
```

#### [RE2021](error-catalog.md#re2021): Memory Allocation Failed

```
Runtime Error RE2021: memory allocation failed
```

Occurs when system runs out of memory during dynamic allocation.

## Testing

### Test Runner

```bash
# Run all tests: every fixture, every directive
python tests/run_tests.py

# Run only the fixtures that never execute a binary (a fast gate on the diagnostics)
python tests/run_tests.py --compile-only

# Filter tests by the path under tests/
python tests/run_tests.py --filter hashmap
python tests/run_tests.py --filter test_result
```

Each flag SELECTS; none of them makes the checking weaker. A run that selects no fixture
fails, because a run that covered nothing must not report a pass.

### Test Types

**Positive tests** (`test_*.sushi`):
- Must compile successfully (exit code 0)
- The runner runs the executable when the fixture needs it, and checks every `EXPECT_*`
  directive

**Warning tests** (`test_warn_*.sushi`):
- Must compile with warnings (exit code 1)

**Error tests** (`test_err_*.sushi`):
- Must fail compilation (exit code 2)
- Used to verify error detection

### Writing Tests

A fixture lives in a feature directory, `tests/<area>/<feature>/`. Its directives are in
the leading comment block. A positive test that prints must state its output with an
`EXPECT_STDOUT_*` directive:

```sushi
# tests/basic/my_feature/test_my_feature.sushi
# EXPECT_RUNTIME_EXIT: 0
# EXPECT_STDOUT_EXACT: "Test passed: 42\n"
fn main() i32:
    let i32 x = 42
    println("Test passed: {x}")
    return 0
```

```bash
# Run your test
python tests/run_tests.py --filter basic/my_feature/
```

### Test Naming Conventions

- `test_<feature>.sushi` - Positive test
- `test_warn_<feature>.sushi` - Expected warning
- `test_err_<feature>.sushi` - Expected error

A file name must be unique across `tests/`, because the runner reports a fixture by its
name.

---

**See also:**
- [Getting Started](getting-started.md) - Installation and first program
- [Language Reference](language-reference.md) - Complete syntax
- [Compiler Internals](internals/architecture.md) - How the compiler works
