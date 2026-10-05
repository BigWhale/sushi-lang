# Compiler Architecture

[← Back to Documentation](../index.md)

This page is a map of the Sushi compiler. It names each stage, the directory that holds
it, and the rules that keep the stages apart. [Semantic Passes](semantic-passes.md)
describes each pass of the analysis. [Backend](backend.md) describes code generation.

All paths on this page are relative to the package directory `sushi_lang/`.

## Overview

```
Source code (.sushi)
    ↓
Parser (internals/parser.py over grammar.lark)
    ↓
AST builder (semantics/ast_builder/)
    ↓
Semantic analysis: 19 passes (semantics/)            ← always the whole program
    ↓
IR generation (backend/codegen_llvm.py)              ← one module, or one module per unit
    ↓
Optimization (backend/llvm_optimization.py)
    ↓
Object emission and link with `cc` (backend/driver.py)
    ↓
Native executable, or a .slib library
```

The command line is `compiler/cli.py:main`. It reads the flags once into a frozen
`BuildOptions` (`compiler/options.py`), and the pipeline (`compiler/pipeline.py`) reads
that record, never the argparse namespace.

A program of more than one unit uses **incremental compilation**: each unit becomes its
own object file in `__sushi_cache__/`, and a unit whose fingerprint did not change is not
compiled again. A single-file program, a library build (`--lib`), `--no-incremental` and
`--dump-ll` use the monolithic path: one LLVM module for the whole program.

## Directory map

| Directory | What it holds |
|---|---|
| `compiler/` | The driver of a build: `cli.py` (flags, `main`), `options.py` (`BuildOptions`), `pipeline.py` (unit loading, stdlib and library injection, the monolithic and the incremental path), `loader.py`, `cache.py`, `fingerprint.py`, `lib_info.py` (the Python half of `--lib-info`) |
| `grammar.lark` | The Lark grammar |
| `internals/` | What every layer shares: `parser.py`, `report.py` (the diagnostic channel), `diagnostics.py` (`SushiError`, internal errors), `errors/` (the error registry, one module per code family), `version.py`, `semver.py` |
| `semantics/ast_builder/` | Lark tree to typed AST: `builder.py` orchestrates; `declarations/`, `expressions/`, `statements/`, `types/`; the internal-error helpers are in `utils/tree_navigation.py` |
| `semantics/` | The AST (`ast.py`), the type system (`typesys.py`), the symbol tables (`tables.py`), the analyzer (`semantic_analyzer.py`), and the one-seam modules of the analysis (see below) |
| `semantics/passes/` | The passes: `collect/`, `docs.py`, `namespaces.py`, `resolve.py`, `finite_types.py`, `derive.py`, `scope.py`, `types/` (the `typecheck` pass), `lift.py`, `borrow/` |
| `semantics/generics/` | Instantiation (`instantiate/`), monomorphization (`monomorphize/`), the Result and Maybe interning, derived `hash` and `clone`, name mangling, type display |
| `backend/` | LLVM IR generation. `codegen_llvm.py` GENERATES a module; `driver.py` compiles, links and builds library bitcode |
| `backend/expressions/`, `backend/statements/` | Tree-to-IR translation, one module per node family |
| `backend/types/` | The LLVM types (`core/`), and the code for arrays, primitives, structs and enums |
| `backend/generics/` | `List@(T)`, `HashMap@(K, V)`, `Maybe@(T)`, `Result@(T, E)`, `Own@(T)`, the container walk and the derived hashes |
| `backend/memory/` | Scopes and scope exit, stack slots, move tracking, heap calls, print frames |
| `backend/functions/` | Function declarations, bodies and the C `main` wrapper |
| `backend/runtime/` | Runtime support: strings, formatting, runtime errors, closures, libc and user externs |
| `sushi_stdlib/src/` | Python generators that emit the IR of the bitcode stdlib modules |
| `sushi_stdlib/src_sushi/` | Stdlib modules written in Sushi, compiled as ordinary units |
| `packager/` | `nori`, the package manager |

The prebuilt bitcode goes to `sushi_stdlib/dist/<platform>/`. The build of that directory
is in [Stdlib Build](stdlib-build.md).

## Parser

`internals/parser.py:build_parser` makes ONE LALR parser per process, with two start
symbols: `start` for a unit, and `expr` for the text of an interpolation hole
(`parse_hole`). The doc-block lexer callbacks ([CE6011](../error-catalog.md#ce6011), [CE6012](../error-catalog.md#ce6012), [CE6013](../error-catalog.md#ce6013)) do not fire while
a hole is parsed, because the hole text comes from inside a string literal.

`cached_lark` keeps the tables in the user cache directory (see the Grammar Cache
section of `docs/compiler-reference.md`). It writes a temporary file in the same
directory and renames it over the cache file, so parallel compiles read a whole file or
no file. A missing, broken or foreign file gives a plain build, and a plain build gives
the same parser. The options that hold Python objects (`postlex`, `lexer_callbacks`)
are not in the file; each load gives them again.

## Semantic passes

There are 19 passes. The passes have NAMES, not numbers, because a number goes out of order
when a pass is inserted between two others. The docstring of `SemanticAnalyzer.check()`
(`semantics/semantic_analyzer.py`) is the authority on the order, and
`_check_multi_file` runs that order, one call per pass.

```
whole program, once:
  collect -> docs -> unused -> externs -> libraries -> namespaces -> ffi-clash
     -> entrypoint -> instantiate -> monomorphize -> resolve -> finite-types
     -> derive -> shadowing -> effects

then per unit, in one loop:
  scope -> typecheck -> lift -> borrow
```

| Pass | What it does | Where |
|---|---|---|
| `collect` | constants and unit variables, function headers, generic types, externs, perks, extensions; one holder for each name of a unit ([CE1005](../error-catalog.md#ce1005)) | `semantics/passes/collect/` |
| `docs` | doc blocks against their declarations (CE70xx, [CW7001](../error-catalog.md#cw7001)); completeness under `--warn-missing-docs` | `semantics/passes/docs.py` |
| `unused` | under `--warn-unused`: dead private declarations ([CW1004](../error-catalog.md#cw1004)), unused imports ([CW3006](../error-catalog.md#cw3006)) | `semantics/unused.py` |
| `externs` | extern signatures ([CE5003](../error-catalog.md#ce5003)) and the `ptr` unit gate ([CE5009](../error-catalog.md#ce5009)) | `semantics/passes/types/externals.py` |
| `libraries` | the symbols of each linked `.slib` | `semantics/library_registration.py` |
| `namespaces` | `use ... as`, `public use`, one scope per unit | `semantics/passes/namespaces.py` |
| `ffi-clash` | an extern that names a symbol this build defines ([CE5013](../error-catalog.md#ce5013)) | `semantics/passes/types/externals.py` |
| `entrypoint` | the rule for `main` ([CE3007](../error-catalog.md#ce3007), [CE3501](../error-catalog.md#ce3501), [CE0106](../error-catalog.md#ce0106), [CE0138](../error-catalog.md#ce0138)) | `semantics/semantic_analyzer.py` |
| `instantiate` | every generic instantiation the program names | `semantics/generics/instantiate/` |
| `monomorphize` | a concrete copy for each instantiation | `semantics/generics/monomorphize/` |
| `resolve` | field, variant and constant types; each spelled `Result@(T, E)` return | `semantics/passes/resolve.py` |
| `finite-types` | a type that holds itself inline ([CE2095](../error-catalog.md#ce2095)) | `semantics/passes/finite_types.py` |
| `derive` | the derived `hash()` and `clone()` | `semantics/passes/derive.py` |
| `shadowing` | an extension method with the name of a built-in ([CE2097](../error-catalog.md#ce2097)) | `semantics/semantic_analyzer.py` |
| `effects` | which functions destroy a `poke` parameter, over all units | `semantics/passes/borrow/destroy_effects.py` |
| `scope` | declarations, scopes and the bare-name ladder ([CE1001](../error-catalog.md#ce1001), [CE2105](../error-catalog.md#ce2105)) | `semantics/passes/scope.py` |
| `typecheck` | types, inference, return paths ([CE0107](../error-catalog.md#ce0107), [CE0140](../error-catalog.md#ce0140)) | `semantics/passes/types/` |
| `lift` | each lambda becomes a top-level function and an environment | `semantics/passes/lift.py` |
| `borrow` | moves, borrows and destroyed values (CE24xx) | `semantics/passes/borrow/` |

The word "phase" names only the three steps of the `typecheck` pass for each statement:
resolution, then propagation, then validation. It never names a pass.

`semantics/const_eval.py` is a helper and not a pass. It has three callers: the AST
builder (for a fixed array's size), the `typecheck` pass and the backend.

## The layering invariant

**`semantics` never imports `backend`.** The command below must print nothing:

```bash
grep -rn "sushi_lang\.backend" sushi_lang/semantics/
```

A fact that both layers need lives in `semantics/` (for example `semantics/param_modes.py`,
`semantics/drop_set.py`, `semantics/externs_manifest.py`), and the backend reads it from
there.

## The backend rule

A backend subsystem that keeps STATE is a manager class. `LLVMCodegen`
(`backend/codegen_llvm.py`) composes the managers and each one is an attribute:

| Attribute | Class | File |
|---|---|---|
| `codegen.types` | `LLVMTypeSystem` | `backend/types/core/__init__.py` |
| `codegen.runtime` | `LLVMRuntime` | `backend/runtime/core.py` |
| `codegen.memory` | `ScopeManager` | `backend/memory/scopes.py` |
| `codegen.moves` | `MoveTracker` | `backend/memory/moves.py` |
| `codegen.expressions` | `ExpressionEmitter` | `backend/expressions/__init__.py` |
| `codegen.statements` | `StatementEmitter` | `backend/statements/__init__.py` |
| `codegen.functions` | `LLVMFunctionManager` | `backend/functions/__init__.py` |
| `codegen.optimizer` | `LLVMOptimizer` | `backend/llvm_optimization.py` |
| `codegen.stdlib` | `StdlibLinker` | `backend/stdlib_linker.py` |
| `codegen.print_frames` | `PrintFrames` | `backend/memory/print_frames.py` |

A translation from the tree to IR that keeps NO state is a free function, and it takes
`codegen` as its first parameter. Most of `backend/expressions/`, `backend/statements/`,
`backend/destructors.py`, `backend/gep_utils.py` and `backend/enum_utils.py` have this
shape. `LLVMDriver` (`backend/driver.py`) wraps a codegen and does the compile and the
link.

No backend invariant is an `assert`, because `python -O` removes it. The backend calls
`raise_internal_error` (`internals/errors/__init__.py`) with a registered code.

## One-seam modules

A seam is ONE module that answers ONE question for every caller. A second path for the
same question is not permitted, and most seams have a CI gate under `tests/unit/`. The
tables below are a map; each module's docstring holds the full rule.

### Semantic analysis

| Question | Where |
|---|---|
| May unit U name declaration D (`DeclOrigin`); did a library take this name | `semantics/visibility.py` |
| What kind a bare name is (local, constant, function, namespace, type) | `semantics/name_ladder.py` |
| The mode of each parameter, for every callee kind (`CalleeKind`); a borrow marker | `semantics/param_modes.py` |
| Which names are type names, and which static `Type.name(...)` calls | `semantics/statics.py`, `semantics/passes/types/calls/statics.py` |
| The family of a built-in method call (`METHOD_TYPE_REGISTRY`) | `semantics/passes/types/method_registry.py` |
| What `X.Y(args)` names (`resolve_dotcall`) | `semantics/passes/types/calls/dotcall.py` |
| Every argument check (`check_arguments`) | `semantics/passes/types/arguments.py` |
| Declared-type propagation at a method boundary | `semantics/passes/types/propagation.py` |
| The root of a place (`walk_place`) | `semantics/places.py` |
| A write through a read-only receiver (`reject_readonly_write`) | `semantics/passes/borrow/writes.py` |
| What a borrow of a constant may do (`reject_borrow_of_constant`) | `semantics/constant_borrow.py` |
| What a method does to its receiver (`effect_of`, `METHOD_EFFECTS`) | `semantics/method_effects.py` |
| The `Drop` set (`drop_type_names`) | `semantics/drop_set.py` |
| Interning a `Result@(T, E)` or a `Maybe@(T)` (`intern_wrapper_enum`) | `semantics/generics/results.py` |
| An interned generic name (`interned_name`); "is this an instance of X" | `semantics/generics/interned.py`, `semantics/type_predicates.py` |
| The element types of a container instance (`instance_type_arguments`) | `semantics/generics/list.py` |
| Leading type-argument inference (`solve_leading_type_args`) | `semantics/generics/pack_inference.py` |
| A wrong type-argument count ([CE2062](../error-catalog.md#ce2062)) | `semantics/generics/explicit_type_args.py` |
| Type substitution (`substitute_type_params`) | `semantics/generics/types.py` |
| A perk constraint on a type argument (`validate_all_constraints`) | `semantics/generics/constraints.py` |
| A built-in method's argument count; a derived method's registration | `semantics/generics/builtin_methods.py` |
| The copy of an extension or perk-implementation signature (`substitute_signature`) | `semantics/generics/extensions.py` |
| A bare name in an extension target (`DeclaredTypeNamer`) | `semantics/generics/extension_targets.py` |
| Which types hash, and what a container hashes (`hashability_of`, `CONTAINER_HASH_KINDS`) | `semantics/generics/hashing.py` |
| The HashMap key rules (`reject_unusable_key`) | `semantics/generics/hashmap.py` |
| The unit of a generic instance | `semantics/generics/synthesis.py` |
| A stdlib function's signature row (`stdlib_signature`) | `semantics/stdlib_registry.py` |
| The perks the compiler predefines (`Drop`, `Hashable`, `Eq`, `Ord`, `Display`) | `semantics/passes/collect/perks.py` |
| Compile-time integer operations | `semantics/const_eval.py` |
| One walk over a type; one walk over the AST nodes | `semantics/type_walk.py`, `semantics/ast_walk.py` |
| A condition, an arithmetic operand, a comparison operand | `semantics/passes/types/expressions.py` |
| An index, a count or a range bound is `i32` (`reject_non_i32`) | `semantics/passes/types/arrays.py` |
| Every public-signature rule (the `ptr` fence, the leak fence) | `semantics/passes/types/public_signatures.py` |
| Inference in the early passes writes no stamp (`ReadOnlyInferrer`) | `semantics/passes/types/__init__.py` |

### Backend

| Question | Where |
|---|---|
| Every ownership transfer (`consume`, `bind`, `relinquish`); the one deep clone (`copy_out`); the `Drop` set (`drops_of`) | `backend/ownership.py` |
| The clone and destroy pair for each type kind | `backend/lifecycle.py` |
| Destroy a value; the one cleanup predicate (`needs_cleanup`); a declared `drop()` | `backend/destructors.py` |
| An owning temporary (`own_temporary`); `llvm.mem*` and buffer growth | `backend/expressions/memory.py` |
| Every stack slot, in the entry block (`entry_alloca`) | `backend/memory/allocas.py` |
| Scope exit, newest first, for a block end, `return`, `??`, `break` and `continue` | `backend/memory/scopes.py` |
| The address of a dynamic array (`as_array_address`) | `backend/types/arrays/addressing.py` |
| A dynamic-array descriptor field (`{len, cap, data}`) | `backend/gep_utils.py` |
| A bounds check, and the Some/None merge | `backend/types/arrays/bounds.py` |
| C-string marshalling for a stdlib or FFI callee (`emit_cstr_arg`) | `backend/expressions/calls/utils.py` |
| The semantic type of a Result or Maybe receiver (`infer_generic_enum_type`) | `backend/expressions/calls/utils.py` |
| The method-call dispatch, and the checked-call tail | `backend/expressions/calls/dispatcher.py` |
| The emitter of a stdlib call (`STDLIB_EMITTERS`) | `backend/expressions/calls/stdlib/__init__.py` |
| A container method's emitter (`emit_from_table`) | `backend/generics/container_table.py` |
| The counted walk over a container; the HashMap probe loop | `backend/generics/container_walk.py`, `backend/generics/hashmap/probe.py` |
| A `Maybe` value (`emit_maybe_some`, `emit_maybe_none`) | `backend/generics/maybe.py` |
| The hash of one held value (`emit_value_hash`) | `backend/types/value_hash.py` |
| A loop frame; a merge block; a `match` scrutinee type | `backend/statements/loops.py`, `backend/statements/control_flow.py`, `backend/statements/matching.py` |
| The enum payload layout (`payload_field_offsets`) | `backend/types/core/sizing.py` |
| An extension or perk method symbol (`extension_symbol`) | `semantics/generics/name_mangling.py` |

### Stdlib generators

| Question | Where |
|---|---|
| A checked `malloc` in a generated body ([RE2021](../error-catalog.md#re2021) on null) | `sushi_stdlib/src/string_helpers.py` |
| A `Result` or `Maybe` tag, by name | `sushi_stdlib/src/results.py` |
| An extern declaration, once per module | `sushi_stdlib/src/libc_declarations.py` |
| errno to an error-enum tag | `sushi_stdlib/src/errno_tags.py` |

## Diagnostics

Every failure goes through the channel in `internals/report.py` as a structured
diagnostic. The analysis reports through `PassErrorReporter` (`semantics/error_reporter.py`):
`emit(entry, span, **params)` takes a registry entry and the parameters of its text. A
relational note takes a span (`note_at`); a prose note takes none (`note`).

Codes live in `internals/errors/`, one module for each family, and a code is added only in
the module that owns its range:

| Range | Module | Family |
|---|---|---|
| CE0xxx | `internal.py`, `func.py` | internal errors; function and variadic errors |
| CE1xxx | `scope.py` | scope and names |
| CE2xxx | `types.py`, `result.py` | types, arrays, structs; Result checks |
| CE24xx | `borrow.py` | moves and borrows |
| CE3xxx | `unit.py`, `library.py` | units; libraries and `.slib` |
| CE4xxx | `perk.py` | perks |
| CE5xxx | `ffi.py` | FFI and the `ptr` fence |
| CE6xxx | `syntax.py` | the parser |
| CE7xxx | `docs.py` | documentation blocks |
| CWxxxx | `warnings.py` | warnings |
| RExxxx | `runtime.py` | runtime errors |
| NExxxx | `nori.py` | the `nori` package manager |

No Python traceback reaches the user; `--traceback` adds one after the diagnostic. A
compiler crash is [CE0000](../error-catalog.md#ce0000) and exits with status 2. See
[Error Handling](../error-handling.md) for the user view.

## Stdlib and libraries

A stdlib module is one of two kinds:

- **Bitcode.** A Python generator under `sushi_stdlib/src/` emits the IR, and the build
  writes `sushi_stdlib/dist/<platform>/<module>.bc`. `./sushic --build-stdlib` rebuilds
  it at once. The compiler also rebuilds a stale stdlib itself, and the cache key covers
  the generators.
- **Sushi source.** A module under `sushi_stdlib/src_sushi/` (for example `<io/fs>`,
  `<io/buf>`, `<net/tcp>`, `<collections/iter>`) is injected as an ordinary unit. Its
  name is in `SOURCE_STDLIB_MODULES` (`semantics/stdlib_registry.py`) and in
  `_virtual_units` (`backend/stdlib_linker.py`).

A `.slib` library has one of three kinds: source (the default), binary or hybrid. The
pipeline injects the units of a source library as ordinary units, and the `libraries`
pass registers the manifest symbols of a binary library. A library definition that a
consumer can also hold gets `weak_odr` linkage (`backend/library_linkage.py`). See
[Libraries](../libraries.md) and [Library Format](../library-format.md).

**Toolchain tools.** A Sushi program that reads Sushi artefacts lives in `toolchain/`
(repository only). `sushic` runs a built tool when there is one (`--lib-info` runs
`slib-info`) and uses the Python code in the other case. `SUSHI_TOOLCHAIN=off` skips the
tools, and `SUSHI_TOOLCHAIN_BIN` names another directory.

## Incremental compilation

```
Parse every unit                                  (always)
  -> whole-program semantic analysis              (always)
  -> a fingerprint for each unit                  (always)
  -> IR, optimization and object for each unit    (only if the fingerprint changed)
  -> link every object                            (always)
```

The analysis is always whole-program, because instantiation and monomorphization need
every call. The cost that the cache removes is IR generation, optimization and object
emission.

### Cache layout

```
__sushi_cache__/
  units/
    main.<global key>.<fp>.o            a unit of the program
    helpers/math.<global key>.<fp>.o
    lib/mylib/mylib.<global key>.<fp>.o a unit of a SOURCE library
  stdlib/
    io_files.<global key>.<fp>.o        a bitcode stdlib module
  libs/
    mylib.<global key>.<fp>.o           a BINARY library's bitcode
  libsrc/
    mylib/                              the written-out units of a source library
```

An object file is named `<name>.<global key>.<fingerprint>.o` (`compiler/cache.py`). The
global key digests the compiler version, the target triple, the optimization level and
a fingerprint of the compiler source. The fingerprint is the first 12 characters of the
unit's digest. The cache has no manifest: a cached object is valid if and only if its
name matches. An object is written to a temporary file and renamed into place
(`publish_atomically`).

### Fingerprint

`compute_unit_fingerprint` (`compiler/fingerprint.py`) is a SHA-256 digest of these
blocks:

- `SOURCE`: the unit's source text (a unit with no source is [CE0142](../error-catalog.md#ce0142)).
- `OWN_SYMBOLS`: the unit's public symbols and their signatures.
- `DEP_SYMBOLS`: the full interface (struct layouts, variant tags, constant values) of
  every unit in the dependency closure, through the dependency graph.
- The AST structure: structs, enums, extensions, perk implementations and `use`
  statements.
- `HELD_EXTENSIONS` and `PROGRAM_INTERFACES`: the template copies this unit holds
  (generic function instances, generic-target extension copies, perk-implementation
  copies), and then the interface of every unit of the program.
- `LIB_TEMPLATES`: the digest of each imported `.slib`.
- `DROP_TYPES`: the whole-program set of types that implement `Drop`.

Thus `--clean-cache` is never necessary for a correct build.

### Linkage

| Definition | Linkage |
|---|---|
| a public function, and `main` | `external` |
| a private function | `internal` |
| a monomorphized generic function | the linkage of its visibility, in the module of the unit that declares the template, with the unit prefix in its symbol |
| a constant | `internal`, read-only; each module has its own copy |
| a unit variable (`var`) | defined once in the declaring unit's module; every other module declares it `external` |
| a library definition that a consumer can also hold | `weak_odr` (`backend/library_linkage.py`, `backend/driver.py`) |
| an inline runtime symbol (`GENERATED_INLINE_SYMBOLS` in `semantics/externs_manifest.py`) | `linkonce_odr` |
| an out-of-line destroy or clone body (`backend/lifecycle.py`) | `linkonce_odr` |

### Key files

- `compiler/pipeline.py`: chooses the monolithic or the incremental path.
- `compiler/cache.py`: `CacheManager`, the cache directories and the object names.
- `compiler/fingerprint.py`: `compute_unit_fingerprint`, `compute_stdlib_fingerprint`,
  `compute_lib_fingerprint`.
- `backend/codegen_llvm.py`: `LLVMCodegen.build_module_single_unit`.
- `backend/driver.py`: `LLVMDriver.compile_multi_unit`,
  `compile_single_unit_to_object`, `link_object_files`.

## Optimization levels

`--opt` takes one of five levels (`compiler/cli.py`). `LLVMOptimizer.optimize`
(`backend/llvm_optimization.py`) applies them with the new pass manager. The `_PIPELINES`
table holds the function passes and the module passes of each level.

| Level | Passes |
|---|---|
| `none` | no pass |
| `mem2reg` (default) | SROA only |
| `O1` | SROA, CFG simplification, instruction combining, dead-code elimination; global DCE |
| `O2` | adds SCCP, reassociation, jump threading, loop simplification and rotation, loop deletion, GVN, memcpy optimization, dead-store elimination, aggressive DCE, tail-call elimination; interprocedural global optimization, IPSCCP, dead-argument elimination |
| `O3` | adds loop unrolling, loop strength reduction, aggressive instruction combining, sinking; argument promotion and function merging |

No level adds an inliner pass.

## Testing

A fixture is a `.sushi` file under `tests/<area>/<feature>/`:

- `test_*.sushi` must compile (exit 0).
- `test_warn_*.sushi` compiles with warnings (exit 1).
- `test_err_*.sushi` must fail (exit 2).

`# EXPECT_*` directives in the leading comment block state what the runner checks. The
runner is `python tests/run_tests.py`. pytest (`tests/unit/`) never runs the Sushi
compiler; it checks the Python source (the seam gates), the registries and the runner.

## Adding a feature

1. Write the failing fixtures first, under `tests/<area>/<feature>/`.
2. Change the grammar (`grammar.lark`) if the syntax is new.
3. Change the AST builder (`semantics/ast_builder/`), in the module under
   `declarations/`, `expressions/`, `statements/` or `types/` that matches.
4. Change the pass that owns the rule. Use the seam if one exists.
5. Change the backend (`backend/`). Keep the backend rule.
6. Run the fixtures.

## Debugging the compiler

```bash
./sushic --traceback program.sushi     # the Python traceback after the diagnostic
./sushic --dump-parse program.sushi    # the parse tree
./sushic --dump-ast program.sushi      # the AST
./sushic --dump-ll program.sushi       # the IR, printed
./sushic --write-ll program.sushi      # the IR, written beside the output
```

The emitted IR depends on the Python hash seed. Set `PYTHONHASHSEED` before you compare
two dumps.

---

**See also:**
- [Semantic Passes](semantic-passes.md) - each pass in detail
- [Backend](backend.md) - LLVM code generation
- [Stdlib Build](stdlib-build.md) - how the bitcode stdlib is built
