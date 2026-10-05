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
| `compile_single_unit_to_object` | the incremental path: one unit to one object |
| `compile_stdlib_to_object`, `compile_library_to_object` | a bitcode stdlib module or a binary library to an object, for the cache |
| `compile_to_bitcode` | the bitcode of a `.slib` library |
| `link_object_files` | the link of the objects with `cc` |

The emit order in a module is: the declarations of the runtime and the libc externs, the
user externs (`declare_user_externs`, `backend/runtime/externs/user_externs.py`), the
constants and unit variables, the function declarations, and then the function bodies.
`backend/functions/` holds the function manager: `declarations.py`, `definitions.py`, and
`main_wrapper.py`, whose `emit_main` emits the C `main`. The C `main` calls the Sushi
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

A `T[]` is its descriptor BY VALUE everywhere: `emit_expr` yields the descriptor, and
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

A `Result@(T, E)` and a `Maybe@(T)` are ordinary interned enums. The semantic type of a
Result or Maybe receiver comes from `infer_generic_enum_type`
(`backend/expressions/calls/utils.py`), never from a match on the LLVM layout; an
unknown type is [CE0019](../error-catalog.md#ce0019).

**Function values:** a 4-word fat pointer `{fn_ptr, env_ptr, drop_ptr, clone_ptr}`, all
`i8*` (`backend/runtime/closures.py`). A value with no captures has a null `env_ptr`,
`drop_ptr` and `clone_ptr`. See `docs/design/closures.md`.

## Expression emission

### Literals

**File:** `backend/expressions/literals.py`

```python
# Integer: at the type of its context (i32 when there is no context)
ir.Constant(ir.IntType(32), 42)

# Float: at the type of its context (f64 when there is no context)
ir.Constant(ir.DoubleType(), 3.14)

# Boolean: i8 in storage, i1 only when a condition asks for it (to_i1)
ir.Constant(ir.IntType(8), 1)  # true
ir.Constant(ir.IntType(1), 1)  # true, as a condition

# String: a private constant global, and a fat pointer with owned = 0
```

`StringConstantManager` (`backend/string_constants.py`) keeps one private global for
each distinct string text.

### Binary operators

**File:** `backend/expressions/operators.py`

**Arithmetic:**
```python
builder.add(left, right)    # int;   builder.fadd for a float
builder.sub(left, right)
builder.mul(left, right)
builder.sdiv(left, right)   # signed; builder.udiv for unsigned, builder.fdiv for a float
builder.srem(left, right)   # signed; builder.urem for unsigned
```

Mixed widths are refused before the backend ([CE2510](../error-catalog.md#ce2510)). The backend folds nothing: every
compile-time integer operation is `semantics/const_eval.py`.

**Comparison:**
```python
builder.icmp_signed('<', left, right)     # a signed integer operand
builder.icmp_unsigned('<', left, right)   # an unsigned integer operand
builder.fcmp_ordered('<', left, right)    # a float operand
```

The result is `i1` for a condition and `i8` in any other position. A string comparison
reads bytes: equality compares the sizes and then `memcmp` over the data; an order
compares with `memcmp` over the common prefix and then the lengths
(`backend/runtime/strings.py`).

**Logical:** `emit_logic` (`backend/expressions/operators.py`)
```python
# a and b: short circuit
#   evaluate a; cbranch(a, rhs_block, end_block)
#   rhs_block: evaluate b in a scope of its own; branch(end_block)
#   end_block: phi(false from the a block, b from rhs_block)
# a or b: the same, with cbranch(a, end_block, rhs_block)
# a xor b: both sides are evaluated, then builder.xor
# not a: builder.not_ on the i1 value
```

**Bitwise:**
```python
builder.and_(left, right)
builder.or_(left, right)
builder.xor(left, right)
builder.not_(operand)       # ~
builder.shl(value, count)   # <<
builder.ashr(value, count)  # >> on a signed type: fills with the sign bit
builder.lshr(value, count)  # >> on an unsigned type: fills with zeros
```

A shift count the compiler can read must be in the range 0 to width-1 ([CE2512](../error-catalog.md#ce2512)). A
computed count at or past the width has a defined result: 0, or the sign fill for a
signed `>>`. The emitter tests the count and selects that result; it does not mask the
count.

### Type casting

**File:** `backend/expressions/casts.py`

```python
# Integer to float
builder.sitofp(value, target_type)  # Signed int to float
builder.uitofp(value, target_type)  # Unsigned int to float

# Float to integer
builder.fptosi(value, target_type)  # Float to signed int (truncate)
builder.fptoui(value, target_type)  # Float to unsigned int

# Integer extension/truncation
builder.zext(value, target_type)    # Zero-extend (unsigned source)
builder.sext(value, target_type)    # Sign-extend (signed source)
builder.trunc(value, target_type)   # Truncate

# Integer to integer (same size, different signedness): no instruction
```

### Arrays

**Files:** `backend/types/arrays/` (literals, indexing, bounds, `methods/`)

```python
# from([1, 2, 3])
# 1. malloc a buffer of 3 * sizeof(i32) bytes (the allocation is checked)
# 2. store each element into the buffer
# 3. build the descriptor by value: { len = 3, cap = 3, data = buffer }
```

`arr[i]` is bounds-checked by `emit_bounds_check(codegen, index, size, ...)`
(`backend/types/arrays/bounds.py`). It tests `index >= 0` and `index < size`. A failure
prints `Runtime Error RE2020: array index 5 out of bounds for array of size 2` and exits
with status 1. `.get(i)` answers a `Maybe@(T)` through the same check
(`emit_checked_maybe`). An index, a count and a range bound are `i32`; `require_i32`
(`backend/llvm_utils.py`) is an internal error, never a widening.

### Function calls

**Files:** `backend/expressions/calls/` (the entry is `dispatcher.py`)

A named callee is resolved through the same per-unit ladder that the `typecheck` pass
walked, so two units that declare one name do not share a callee. The parameter mode of
each argument comes from `semantics/param_modes.py`, the same resolver that the `borrow`
pass reads. A `peek` or `poke` argument goes by pointer.

A stdlib call goes to its emitter through `STDLIB_EMITTERS`
(`backend/expressions/calls/stdlib/__init__.py`). A C-string argument of a stdlib or FFI
callee is marshalled by `emit_cstr_arg` (`backend/expressions/calls/utils.py`), which
also registers the copy for a free at scope exit.

### Method calls

`emit_method_call` (`backend/expressions/calls/dispatcher.py`) is a table dispatch. The
AST is not rewritten into a function call.

1. `PRE_RECEIVER_HANDLERS` run before the receiver is emitted: an FFI call, a namespace
   call, a static, an enum or struct constructor, and the Result, Maybe, Own, HashMap and
   List methods.
2. The receiver is emitted once.
3. `RECEIVER_HANDLERS` run in order: the array and string methods, a perk method, the
   derived `hash` and `clone`, and the primitive methods.
4. An extension method call goes to `emit_checked_call`: the arity guard, the casts, the
   call and the `i1` conversion.

A built-in array method such as `arr.len()` is emitted inline (`backend/types/arrays/`);
there is no `array_len` function. A container method goes through its row in
`LIST_EMITTERS` or `HASHMAP_EMITTERS` (`emit_from_table`,
`backend/generics/container_table.py`). An extension method symbol comes from
`extension_symbol` (`semantics/generics/name_mangling.py`), for example
`Pair__i32_string_swapped`.

## Statement emission

### Variable declaration

**File:** `backend/statements/variables.py`

```python
# let i32 x = 42
x_slot = codegen.memory.entry_alloca(ir.IntType(32), 'x')   # in the ENTRY block
builder.store(ir.Constant(ir.IntType(32), 42), x_slot)
```

Every stack slot goes in the function's entry block through `entry_alloca`
(`backend/memory/allocas.py`), so a `let` inside a loop does not grow the frame. A `let`
of an owning value registers it for scope exit through `register_owning_value`
(`backend/memory/scopes.py`). An owning value that no name holds is registered as a scope
temporary by `own_temporary` (`backend/expressions/memory.py`).

### Variable rebinding

```python
# x := 50
builder.store(ir.Constant(ir.IntType(32), 50), x_slot)
```

A rebind of an owning value first destroys the old value (`destroy_old_value`,
`backend/destructors.py`), and then stores the new one.

### If-elif-else

**File:** `backend/statements/control_flow.py`

```python
# if (x > 5):
#     println("big")
# else:
#     println("small")

cond = builder.icmp_signed('>', x, five)

then_block = func.append_basic_block('if.then')
else_block = func.append_basic_block('if.else')
merge_block = func.append_basic_block('if.merge')

builder.cbranch(cond, then_block, else_block)

builder.position_at_end(then_block)
emit_println("big")
builder.branch(merge_block)

builder.position_at_end(else_block)
emit_println("small")
builder.branch(merge_block)

builder.position_at_end(merge_block)
```

When every arm returns, `close_merge_block` leaves no merge block.

### Loops

**File:** `backend/statements/loops.py`

```python
# while (x > 0):
#     x := x - 1

loop_cond = func.append_basic_block('while.cond')
loop_body = func.append_basic_block('while.body')
loop_end = func.append_basic_block('while.end')

builder.branch(loop_cond)

builder.position_at_end(loop_cond)
cond = builder.icmp_signed('>', builder.load(x_slot), zero)
builder.cbranch(cond, loop_body, loop_end)

builder.position_at_end(loop_body)
builder.store(builder.sub(builder.load(x_slot), one), x_slot)
builder.branch(loop_cond)

builder.position_at_end(loop_end)
```

`loop_frame` is the one frame of a loop: it pushes the loop entry and the scope, runs the
exit actions, pops them, and emits the back edge. A `foreach` over a protocol iterator
destroys the iterator on every exit path.

### Pattern matching

**File:** `backend/statements/matching.py`

```python
# match status:
#     Status.Idle -> ...
#     Status.Running(task_id) -> ...

tag = extract_enum_tag(codegen, status_value)       # field 0
switch = builder.switch(tag, default_block)
switch.add_case(ir.Constant(ir.IntType(32), 0), idle_block)
switch.add_case(ir.Constant(ir.IntType(32), 1), running_block)

builder.position_at_end(running_block)
# read task_id at its offset in the data buffer (payload_field_offsets), bind it, emit the arm
builder.branch(merge_block)
```

`emit_match` reads the scrutinee type from the stamp of the `typecheck` pass alone
([CE0121](../error-catalog.md#ce0121) when the stamp is missing). An integer match switches on the value itself. A
tuple match and a string match have no value to switch on: `_emit_sequential_match` tests
the arms in source order, and a string literal test is one `emit_value_eq` (the size, then
`memcmp`).

## Memory management

### Ownership transfers

`backend/ownership.py` is the one way to give a value to a new owner: `consume`, `bind`
and `relinquish`. `copy_out` is the one deep-clone entry. A consuming use with no
decision is a fatal [CE0129](../error-catalog.md#ce0129); there is no fallback. `drops_of(codegen)` gives the set of
types that implement `Drop`.

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

### Scope-based cleanup

`ScopeManager` (`backend/memory/scopes.py`) does all scope-exit cleanup in one walk.
The walk goes through the names of a scope in reverse declaration order, and it sends
each name to the registry that holds it:

```python
def _emit_scope_exit(self, depth):
    for name in reversed(self._scope_vars[depth]):
        for slot, emit_free in self._exit_actions(name, depth):
            self.codegen.moves.emit_free_unless_moved(slot, emit_free)
    self._free_cstr_list(self._cstr_cleanup[depth])
    self._free_closure_temp_list(self._closure_temp_cleanup[depth])
```

- `pop_scope` walks the innermost scope on the fall-through path, then removes its
  entries.
- `emit_exit_cleanup(lowest_depth)` walks each scope from the innermost down to
  `lowest_depth` on an early exit, and removes no entries: `0` for a `return` or a `??`
  (through `statements/utils.py:emit_scope_cleanup`), the first scope of the loop for a
  `break` or a `continue`.

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

**Files:** `backend/runtime/externs/user_externs.py`,
`backend/expressions/calls/dispatcher.py`, `backend/expressions/calls/utils.py`,
`backend/memory/scopes.py`.

- `declare_user_externs(codegen, external_table)` emits one `ir.Function` for each
  foreign declaration, with `string` parameters as `i8*`, a `~` return as `void`, and
  `ptr` as `i8*`. The results are on `codegen.external_funcs` and
  `codegen.external_sigs`, keyed by `(namespace, name)`.
- `_try_emit_external_call` is the first entry of `PRE_RECEIVER_HANDLERS`. It reads the
  `external_ref` stamp of the `typecheck` pass and emits a direct call that gives back the
  raw C value. A `string` argument is marshalled by `marshal_cstr`, through the seam
  `emit_cstr_arg`. A `string` return is copied into an owned fat pointer by
  `emit_cstr_to_owned_fat_pointer` (`backend/runtime/strings.py`).
- Each marshalled `char*` goes on a list of its scope in `ScopeManager`. Scope exit frees
  each pointer exactly once.

`RESERVED_EXTERNS` (`semantics/externs_manifest.py`) holds `malloc`, `free` and `exit`,
read from the libc table. Each one is the first declaration of its name for [`CE5001`](../error-catalog.md#ce5001) (#1099).
The compiler's declarations of a libc symbol and a user's declarations of it are
independent (#1099). Every libc signature that the compiler declares is in one table,
`LIBC_SIGNATURES` (`sushi_stdlib/src/libc_declarations.py`), and the generators and the
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
