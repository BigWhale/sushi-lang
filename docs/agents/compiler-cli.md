# Compiler CLI: what an agent must know

This file holds the facts about `sushic` and its flags that the user reference does not
put in the front, plus the inspection traps. The full flag list, the library flags, the
`--warn-missing-docs` and `--warn-unused` rules and the optimization levels are in
`docs/compiler-reference.md`; `sushi_lang/compiler/cli.py` is the code. Read this file
when you add or change a flag, change the driver, compare IR, or build a library by hand.

## The wrapper and the options

- `./sushic <file.sushi>` is the main entry point. `-o <path>` chooses the output; a
  missing directory is an error.
- The wrapper changes into the checkout and passes the caller's directory in `SUSHI_CWD`.
  `cli.py:main()` changes back into it ONCE, so every relative path (`-o`, `--cache-dir`,
  `SUSHI_LIB_PATH`, a `.slib` beside the program, `--clean-cache`) is the user's.
- The pipeline takes a frozen `BuildOptions` (`compiler/options.py`), never the argparse
  namespace.
- A flag that has no effect gets a warning.
- There is no `--debug` flag, and `python -m compiler` does not exist.
- `--traceback` appends the Python traceback AFTER the diagnostic. The default output never
  shows one.

## Inspection

- `--dump-parse`, `--dump-ast`, `--write-ll` (a file), `--dump-ll` (stdout).
- Emitted IR is hash-nondeterministic: pin `PYTHONHASHSEED` before you diff it.

## Libraries

- `--lib` builds a `.slib`. `--lib-version X.Y.Z` is required unless a `nori.toml` states
  one; else it is an error.
- `--lib-kind {source,binary}`; source is the DEFAULT.
- `--lib-info FILE` prints the signatures; `--docs` adds every doc block.
- `--ignore-compiler-version` overrides the compiler-version check.

## Lints

- `--warn-missing-docs`: the doc-completeness lints, off by default. `fn main()` and the
  `unsafe external` seam are the only exemptions.
- `--warn-unused`: the dead-code lints, off by default. One warning for a private
  top-level declaration that nothing in its unit reaches, a private extension method
  included. The roots are every `public` declaration, every perk implementation and
  conversion, the `unsafe external` blocks and `main`. One warning for a `use` whose unit
  names nothing that it brings; a `public use` is never reported. A call of an R6
  extension of the imported unit uses the import; a method of a built-in type never uses
  a stdlib import (R2).
- Both lint flags ride on one `Lints` record.

## The overflow mode

- `--overflow {trap,wrap}`; `trap` is the default. It does not depend on `--opt`.
- `BuildOptions.overflow` is an `OverflowMode` (`semantics/overflow_mode.py`). The
  pipeline gives it to the `SemanticAnalyzer`, and `codegen_for` copies
  `analyzer.overflow` to `LLVMCodegen.overflow`. So SLIR lowering and the backend read one
  value.
- SLIR lowering leaves out the RE2031 check of `/` and `%` in `wrap` mode. The backend
  reads the `user` mark of a SLIR `Binary` or `Unary`: in `trap` mode a marked `+ - *` or
  negation goes through a `with.overflow` intrinsic and stops with RE2032; in `wrap` mode
  a marked signed `/` or `%` gives the wrapped answer for a divisor of -1.
- The compiler's own arithmetic has no mark and is never checked.
- The mode is in `CacheManager.global_key`. A stdlib Sushi module compiles with the mode
  of the program. The stdlib generators are prebuilt and do not follow it.
- With `--lib-info` the flag warns CW0003.

## Other flags

`--opt {none,mem2reg,O1,O2,O3}`, `--no-verify`, `--keep-object`, `--no-incremental`,
`--clean-cache`, `--cache-dir PATH`, `--build-stdlib`. The cache key makes `--clean-cache`
unnecessary for correctness (see the Backend line in `CLAUDE.md`, Architecture).

## The per-user cache root

- The one helper is `internals/user_cache.py`. `sushi_home()` gives `~/.sushi`
  (`$SUSHI_HOME` when it is an absolute path), and `packager/constants.py` reads it too.
  `compiler_cache_dir(name)` gives `~/.sushi/cache/compiler/<name>`, made with mode
  `0700`, or None when the directory is not the user's alone (`trusted_dir`). A new
  per-user cache of the compiler gets its directory from this helper and from nothing
  else.
- The grammar tables are in `grammar/` (`internals/parser.py:grammar_cache_dir`).
- The stdlib objects are in `stdlib/<global_key>/` (`CacheManager.user_stdlib_path`):
  the object and the record of each reused bundled stdlib unit (`semantics/unit_reuse.py`),
  and the objects of the stdlib generators. The user units stay in `__sushi_cache__/`.
  `SUSHI_GRAMMAR_CACHE_DIR` still overrides that one directory, and `off` turns it off.
- `SUSHI_HOME` moves the tree for a test or CI. The fork server compares it, with `HOME`,
  before it reuses a warm child (`tests/fork_server.py:IMPORT_TIME_ENV`).
- `--clean-cache` removes the project cache only. To clear the per-user cache, delete
  `~/.sushi/cache/compiler/`.
