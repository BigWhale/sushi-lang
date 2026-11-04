# Backend: LLVM Code Generation

[← Back to Documentation](../index.md) | [Architecture](architecture.md)

The backend translates the type-checked AST into LLVM IR, optimizes the IR, emits object
code and links a native executable. All paths on this page are relative to
`sushi_lang/`. The code sketches show the shape of the emitted IR; the module that each
section names holds the real code.

## Main components

### `LLVMCodegen` and `LLVMDriver`

`LLVMCodegen` (`backend/codegen_llvm.py`) GENERATES a module. It composes the manager
classes (see "The backend rule" in [Architecture](architecture.md)) and holds the tables
that the analysis handed over. `build_module_single_unit` generates the module of one
unit.

`LLVMDriver` (`backend/driver.py`) wraps a codegen. It verifies, optimizes, emits objects
and links:

| Method | What it does |
|---|---|
| `compile_multi_unit` | the monolithic path: one module for the whole program, then the link |
| `compile_single_unit_to_object` | the incremental path: the module of one unit (`build_module_single_unit`) to one object |
| `compile_stdlib_to_object`, `compile_library_to_object` | a bitcode stdlib module or a binary library to an object, for the cache |
| `compile_to_bitcode` | the bitcode of a `.slib` library |
| `link_object_files` | the link of the objects with `cc` |

The emit order in a module is: the declarations of the runtime and the libc externs, the
user externs (`declare_user_externs`, `backend/runtime/externs/user_externs.py`), the
constants and unit variables, the function declarations, and then the function bodies.
The declarations come from the AST. Each body comes from the `SlirFunction` of the
analysis (`codegen_for` hands over the SLIR of the program as `SlirBodies`), through the
SLIR emitter in `backend/slir/` (`emit_function`): every local is a slot in the entry
block, and each SLIR block is one LLVM block. A drop goes through the one destroy seam
(`emit_value_destructor`), and a call of an intrinsic (`interp`, `print`, `println`)
through its row of `INTRINSICS` (`backend/slir/intrinsics.py`), which gives LLVM values to
the string builder or to the print emitter. A SLIR shape that the emitter does not
handle yet stops the compile with [`CE0155`](../error-catalog.md#ce0155) (`emit, <class>`)
before any output is written; the incremental path builds the module of every unit
before it writes the first object for this reason. `backend/functions/` holds the function
manager: `declarations.py`, `helpers.py`, and `main_wrapper.py`, whose `emit_main`
emits the C `main`. The C `main` calls the Sushi
`main` (emitted as the internal function `user_main`), converts `argc`/`argv` to a
`string[]` when `main` takes `args`, and returns the value of the Sushi `main` as the exit
code. The Sushi `main` is bare ([`CE0106`](../error-catalog.md#ce0106) refuses a channel), so its value IS the exit code.

## Type system

**File:** `backend/types/core/__init__.py` (`LLVMTypeSystem`, reached as `codegen.types`;
`TypeMapper`, `TypeSizing`, `TypeCache` and `TypeInference` are in `backend/types/core/`)

**Primitive types:**
```python
'i8': ir.IntType(8)
'i16': ir.IntType(16)
'i32': ir.IntType(32)
'i64': ir.IntType(64)
'u8': ir.IntType(8)   # LLVM has no sign on an integer type; the operation carries it
'u16': ir.IntType(16)
'u32': ir.IntType(32)
'u64': ir.IntType(64)
'f32': ir.FloatType()
'f64': ir.DoubleType()
'bool': ir.IntType(8)  # i1 is only a transient condition type; storage is i8
'string': ir.LiteralStructType([        # fat pointer, not a bare i8*
    ir.IntType(8).as_pointer(),          # data
    ir.IntType(32),                      # size
    ir.IntType(8),                       # owned: 1 = heap (RAII frees), 0 = literal/borrow
])
```

A string size is `i32`. A `mem*` call takes an `i64` length, so the backend zero-extends
the size first (`docs/design/string-representation.md`).

**Array types:**
```python
# Fixed array: [5 x i32]
ir.ArrayType(ir.IntType(32), 5)

# Dynamic array descriptor: { i32, i32, T* }
#                             ^len ^cap ^data
ir.LiteralStructType([
    ir.IntType(32),                # length (index 0)
    ir.IntType(32),                # capacity (index 1)
    ir.IntType(32).as_pointer(),   # data (index 2)
])
```

A `T[]` is its descriptor BY VALUE everywhere, and
`as_array_address` (`backend/types/arrays/addressing.py`) is the one place that makes an
address of it. The field addresses come from `gep_dynamic_array_len`,
`gep_dynamic_array_cap` and `gep_dynamic_array_data` (`backend/gep_utils.py`); each takes
`codegen` first and an optional `builder=`. See `docs/design/array-representation.md`.

**Struct types:**
```sushi
struct Point:
    i32 x
    i32 y
```

```python
# LLVM: an identified struct named after the type
# %"Point" = type { i32, i32 }
```

A generic instance carries its interned name, for example `%"Pair<i32, string>"`.

**Enum types:**
```sushi
enum Status:
    Idle
    Running(i32)
    Failed(string)
```

```python
# LLVM: { i32, [K x i64] }
#        ^tag  ^variant data (union-style), K = ceil(widest aligned payload / 8), min 1
ir.LiteralStructType([
    ir.IntType(32),                     # discriminant tag (4 bytes pad follow it)
    ir.ArrayType(ir.IntType(64), words) # variant data buffer, 8-aligned
])
```

The data member is an **i64 array on purpose**: it gives the struct 8-alignment, so the
payload starts at offset 8 and every payload field sits at a naturally aligned offset.
`TypeSizing.payload_field_offsets` (`backend/types/core/sizing.py`) is the one authority
on the offsets (C struct layout rules), and the `unpack_*` helpers of
`backend/enum_utils.py` read it. A payload access bitcasts the data pointer to `i8*` and
GEPs by byte offset, with natural alignment. `extract_enum_tag` and `extract_enum_data`
(`backend/enum_utils.py`) take `(codegen, enum_value)` and use `extract_value` on the
value; `check_enum_variant` compares the tag.

A `Result@(T, E)` and a `Maybe@(T)` are ordinary interned enums. The emitter reads the
semantic type of a value from its SLIR local, never from a match on the LLVM layout.

**Function values:** a 4-word fat pointer `{fn_ptr, env_ptr, drop_ptr, clone_ptr}`, all
`i8*` (`backend/runtime/closures.py`). A value with no captures has a null `env_ptr`,
`drop_ptr` and `clone_ptr`. See `docs/design/closures.md`.

## Body emission

**Files:** `backend/slir/` (`emit.py`, `values.py`, `intrinsics.py`, `state.py`,
`bodies.py`)

Each body comes from its `SlirFunction` (`docs/design/ir.md` 8.2), and there is no other
body emitter. `emit_function` defines the LLVM function that the declaration round made
for the symbol of the body:

- **Locals.** Each local is a slot in the entry block (`entry_alloca`,
  `backend/memory/allocas.py`), so a local in a loop does not grow the frame. A `peek` or
  a `poke` parameter has no slot: its local is the incoming address. A value parameter is
  stored into its slot at the entry. `_0` is the return slot.
- **Blocks and terminators.** Each SLIR block is one LLVM block, in the order of the
  function. `Goto` is a branch, `SwitchInt` a switch (with no `otherwise`, a block that
  holds `unreachable`), `Return` loads `_0` and returns it.
- **Statements.** `Assign` stores the value of its rvalue into its place. A `Call` goes to
  the row of its callee kind in `CALL_EMITTERS`: a `Direct` callee is a call of the
  declared symbol, and a `Builtin` callee is a row of `INTRINSICS` (`interp`, `print`,
  `println`). `Drop` goes through the one destroy seam (`emit_value_destructor`), and
  `DropIfSet` tests its flag local first. `StorageLive` and `StorageDead` emit nothing.
- **Rvalues and places.** `Use`, `Binary`, `Unary`, `Cast`, the `Aggregate` of an enum
  variant and `Discriminant`; a place is a local or the payload field of a downcast enum
  (`payload_field_offsets`).

A shape that the emitter does not handle yet stops the compile with
[`CE0155`](../error-catalog.md#ce0155) (`emit, <class>`). `and` and `or` are blocks and a
`SwitchInt` in SLIR, and so is `??`: no emitter evaluates them.

### The value families

The emitter gives LLVM values and semantic types to these functions. Each one holds the
rule of its construct, and the backend folds nothing: every compile-time integer
operation is the kernel `semantics/integer_ops.py`.

**Arithmetic, comparison and bitwise operators** (`emit_arithmetic`,
`emit_number_comparison`, `emit_bitwise`, `emit_xor`, `emit_unary_value`,
`backend/expressions/operators.py`):

```python
builder.add(left, right)    # int;   builder.fadd for a float
builder.sdiv(left, right)   # signed; builder.udiv for unsigned, builder.fdiv for a float
builder.srem(left, right)   # signed; builder.urem for unsigned
builder.icmp_signed('<', left, right)     # a signed integer operand
builder.icmp_unsigned('<', left, right)   # an unsigned integer operand
builder.fcmp_ordered('<', left, right)    # a float operand
builder.shl(value, count)   # <<
builder.ashr(value, count)  # >> on a signed type: fills with the sign bit
builder.lshr(value, count)  # >> on an unsigned type: fills with zeros
```

Mixed widths are refused before the backend ([CE2510](../error-catalog.md#ce2510)). A
comparison gives an `i1`, which the emitter widens to the `bool` type. A string comparison
reads bytes (`emit_string_relation`): equality compares the sizes and then `memcmp` over
the data; an order compares with `memcmp` over the common prefix and then the lengths
(`backend/runtime/strings.py`). A shift count the compiler can read must be in the range
0 to width-1 ([CE2512](../error-catalog.md#ce2512)). A computed count at or past the width
has a defined result: 0, or the sign fill for a signed `>>`. The emitter tests the count
and selects that result; it does not mask the count.

**Casts** (`emit_value_cast`, `backend/expressions/casts.py`): `sitofp` or `uitofp` from an
integer to a float, `fptosi` or `fptoui` back, `zext` or `sext` to widen an integer (by
the sign of the source), `trunc` to narrow it, and no instruction between two integers of
one width.

**Constants.** A scalar is `scalar_initializer` (`backend/constants/enum_initializers.py`):
an integer at the width of its type. A struct, a tuple and an enum variant go through the
one converter of an aggregate constant (`constant_initializer`, same module), which the
SLIR emitter and the global of a `const` both call. A string is a private constant global (`StringConstantManager`,
`backend/string_constants.py`, one global for each distinct text) and a fat pointer with
owned = 0.

**Arrays** (`backend/types/arrays/`): `arr[i]` is bounds-checked by
`emit_bounds_check(codegen, index, size, ...)` (`backend/types/arrays/bounds.py`). It tests
`index >= 0` and `index < size`. A failure prints
`Runtime Error RE2020: array index 5 out of bounds for array of size 2` and exits with
status 1. `.get(i)` answers a `Maybe@(T)` through the same check (`emit_checked_maybe`). An
index, a count and a range bound are `i32`; `require_i32` (`backend/llvm_utils.py`) is an
internal error, never a widening.

### The method families

A container method has its row in `LIST_EMITTERS` or `HASHMAP_EMITTERS` (`emit_from_table`,
`backend/generics/container_table.py`), a stdlib call its row in `STDLIB_EMITTERS`
(`backend/expressions/calls/stdlib/__init__.py`), and a built-in array method its row in
`backend/types/arrays/`. An extension method symbol comes from `extension_symbol`
(`semantics/generics/name_mangling.py`), for example `Pair__i32_string_swapped`. These
families still take AST nodes: each one gets operands from SLIR in the group that brings
its construct back (`docs/design/ir.md` Q7). The primitive family and the string family already take
operands (`backend/slir/methods.py`, `METHOD_FAMILIES`). The emitter stops on a `BuiltinMethod`
callee of any other family until then.

## Memory management

### Ownership transfers

SHIR-to-SLIR lowering places every move and every drop (`docs/design/ir.md` 8.10): a
`Move` operand, a `Drop` statement, and a `DropIfSet` with a flag local where the state of
a local differs between two paths. So the backend keeps no move state and no cleanup
registry. `copy_out` (`backend/ownership.py`) is the one deep-clone entry outside the clone
engine, and `drops_of(codegen)` gives the set of types that implement `Drop`.

### Destruction

**Files:** `backend/destructors.py`, `backend/lifecycle.py`

`emit_value_destructor(codegen, value_ptr, value_type)` destroys a value of any type. It
reads the ambient `codegen.builder`.

| Type | What the destructor does |
|---|---|
| primitive | nothing |
| `string` | `if owned: free(data)`, from the owned byte of the fat pointer (a literal has owned = 0) |
| dynamic array | destroys each element that needs it, then frees the buffer |
| fixed array | destroys each element that needs it |
| struct | calls the type's own `drop()` FIRST when the type implements `Drop` (`emit_declared_drop`), then destroys each owning field; the struct itself is not freed |
| enum | switches on the tag and destroys the payload of the live variant |
| `List@(T)`, `HashMap@(K, V)`, `Own@(T)` | destroys the elements, the entries or the payload, then frees the storage |
| function value | calls `drop_ptr(env_ptr)` when `drop_ptr` is not null |

`needs_cleanup(codegen, type)` is the ONE backend predicate for "does this value own
something to release". `backend/lifecycle.py` holds the clone and destroy handler of each
type kind as a pair, because a deep clone must copy exactly the heap that the destructor
frees. A self-referential type gets an out-of-line destroy body (`linkonce_odr`), so the
cleanup ends by recursion at run time.

### Unit variables

A unit variable (`var`) is a global. The module of the declaring unit defines it with its
initializer, and every other module declares it `external` with no initializer. A
constant is an `internal`, read-only global in each module that reads it. See
`docs/design/unit-storage.md`.

### Buffers and containers

`emit_memcpy_bytes`, `emit_memmove_bytes` and `emit_grow_to_fit`
(`backend/expressions/memory.py`) are the one route to an `llvm.mem*` intrinsic and to a
buffer growth. `emit_container_walk` (`backend/generics/container_walk.py`) is the one
counted walk over `data[0..count)`, and `emit_probe_loop`
(`backend/generics/hashmap/probe.py`) is the one HashMap probe loop. A `Maybe` value is
built only by `emit_maybe_some` and `emit_maybe_none` (`backend/generics/maybe.py`). The
hash of one held value is `emit_value_hash` (`backend/types/value_hash.py`).

## Runtime support

### Printing

**File:** `backend/runtime/formatting.py`

```sushi
fn main() i32:
    let i32 x = 42
    println("Answer: {x}")
    return 0
```

A print writes to the descriptor with `write(2)` through `emit_console_write`, the ONE
route from a print statement to a descriptor; nothing goes through stdio buffers. A
string value is written in place from its fat pointer. A scalar is formatted with
`sprintf` into a buffer in the entry block, and the buffer is written. A `bool` prints as
`true` or `false`.

### Runtime errors

**File:** `backend/runtime/errors.py`

A runtime error prints `Runtime Error RExxxx: <message>` to stderr and exits. The
registry text of each RExxxx code (`internals/errors/runtime.py`) is the format string.

### Foreign function interface (FFI)

**File:** `backend/runtime/externs/user_externs.py`

- `declare_user_externs(codegen, external_table)` emits one `ir.Function` for each
  foreign declaration, with `string` parameters as `i8*`, a `~` return as `void`, and
  `ptr` as `i8*`. The results are on `codegen.external_funcs` and
  `codegen.external_sigs`, keyed by `(namespace, name)`.
- A call of a foreign function comes back through SLIR with its group. The string
  family keeps the two conversions that it needs: `emit_to_cstr` makes the `char*` of a
  `string` argument, and `emit_cstr_to_owned_fat_pointer` copies a `string` return into an
  owned fat pointer (`backend/runtime/strings.py`).

`RESERVED_EXTERNS` (`semantics/externs_manifest.py`) holds `malloc`, `free` and `exit`,
read from the libc table. Each one is the first declaration of its name for [`CE5001`](../error-catalog.md#ce5001) (#1099).
The compiler's declarations of a libc symbol and a user's declarations of it are
independent (#1099). Every libc signature that the compiler declares is in one table,
`LIBC_SIGNATURES` (`sushi_stdlib/src/libc_signatures.py`), and the generators and the
runtime declare through `declare_libc`. A call goes through the type of its own
declaration: when the module already holds the symbol with another function type,
`declare_extern` gives a bitcast of it to the caller's type. `_declare_one` and
`_declare_variable` (`runtime/externs/user_externs.py`) do the same for a user
declaration.

## Optimization

**File:** `backend/llvm_optimization.py`

**Requirements:** llvmlite `>=0.45,<0.46` (`pyproject.toml`), with the new pass manager.

`LLVMOptimizer.optimize(llmod, mode)` takes the `--opt` level as a string: `none`,
`mem2reg` (the default), `O1`, `O2` or `O3`.

- `none` runs no pass.
- `mem2reg` runs SROA on each function, with `PipelineTuningOptions(speed_level=0)`.
- `O1`, `O2` and `O3` read their row of the `_PIPELINES` table: a speed level, a tuple of
  function passes and a tuple of module passes. The optimizer adds each pass explicitly
  to a function pass manager and a module pass manager, runs the function passes on each
  defined function, and then runs the module passes.

```python
pto = llvm.PipelineTuningOptions(speed_level=pipeline.speed_level, size_level=0)
pb = llvm.PassBuilder(tm, pto)

fpm = llvm.create_new_function_pass_manager()
for add_function_pass in pipeline.function_passes:
    add_function_pass(fpm)
mpm = llvm.create_new_module_pass_manager()
for add_module_pass in pipeline.module_passes:
    add_module_pass(mpm)
```

The passes of each level are listed in [Architecture](architecture.md#optimization-levels).
No level adds an inliner or a vectorizer pass.

To see the effect of a level, compare two dumps:

```bash
PYTHONHASHSEED=0 ./sushic --dump-ll --opt none program.sushi > unoptimized.ll
PYTHONHASHSEED=0 ./sushic --dump-ll --opt O2 program.sushi > optimized.ll
```

## Linking

**File:** `backend/driver.py`

- **Monolithic path.** `compile_multi_unit` builds one module. The stdlib bitcode is
  parsed with `llvm.parse_bitcode` and linked in with `link_in`. The target machine emits
  one object (`emit_object`), and `link_object_files` links it.
- **Incremental path.** Each unit, each bitcode stdlib module and each binary library is
  compiled to its own cached object (`compiler/cache.py`), and `link_object_files` links
  all of them.

The linker is `cc`. The command is `cc <objects> -o <output>`, with `-lm` on Linux. The stdlib linker is `StdlibLinker` (`backend/stdlib_linker.py`);
`TwoPhaseLinker` (`backend/module_linker.py`) resolves a duplicate symbol by its source on
the monolithic path. A library definition that a consumer can also hold is `weak_odr`
(`backend/library_linkage.py`).

---

**See also:**
- [Architecture](architecture.md) - Overall compiler design
- [Semantic Passes](semantic-passes.md) - Type checking and analysis
