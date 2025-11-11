# Backend: LLVM Code Generation

[← Back to Documentation](../README.md) | [Architecture](architecture.md)

Detailed documentation of Sushi's LLVM backend and code generation.

## Overview

The backend translates type-checked AST into LLVM IR, applies optimizations, and links to produce native executables.

## Main Components

### LLVMCodeGenerator

**File:** `backend/codegen_llvm.py`

Main orchestrator for code generation.

**Key responsibilities:**
- Create LLVM module
- Generate function declarations
- Emit function bodies
- Apply optimization passes
- Link with clang

**Workflow:**
```python
def compile(ast, opt_level):
    # 1. Initialize LLVM module
    module = ir.Module(name="sushi_program")

    # 2. Declare all functions
    for func in ast.functions:
        declare_function(func)

    # 3. Generate function bodies
    for func in ast.functions:
        emit_function(func)

    # 4. Apply optimizations
    apply_optimizations(module, opt_level)

    # 5. Link with clang
    link_executable(module, output_name)
```

## Type System

### TypeManager

**File:** `backend/types/`

Manages LLVM type creation and mapping.

**Primitive types:**
```python
'i8': ir.IntType(8)
'i16': ir.IntType(16)
'i32': ir.IntType(32)
'i64': ir.IntType(64)
'u8': ir.IntType(8)   # Same as i8 in LLVM
'u16': ir.IntType(16)
'u32': ir.IntType(32)
'u64': ir.IntType(64)
'f32': ir.FloatType()
'f64': ir.DoubleType()
'bool': ir.IntType(1)
'string': ir.IntType(8).as_pointer()  # i8*
```

**Array types:**
```python
# Fixed array: [5 x i32]
ir.ArrayType(ir.IntType(32), 5)

# Dynamic array struct: { i32*, i32, i32 }
#                        ^ptr  ^len ^cap
ir.LiteralStructType([
    ir.IntType(32).as_pointer(),  # data
    ir.IntType(32),                # length
    ir.IntType(32)                 # capacity
])
```

**Struct types:**
```sushi
struct Point:
    i32 x
    i32 y
```

```python
# LLVM: { i32, i32 }
ir.LiteralStructType([
    ir.IntType(32),  # x
    ir.IntType(32)   # y
])
```

**Enum types:**
```sushi
enum Status:
    Idle()
    Running(i32 task_id)
    Error(string message)
```

```python
# LLVM: { i32, [largest_variant_size x i8] }
#        ^tag  ^variant data (union-style)
ir.LiteralStructType([
    ir.IntType(32),                    # discriminant tag
    ir.ArrayType(ir.IntType(8), size)  # variant data buffer
])
```

## Expression Emission

### Literals

**File:** `backend/expressions/literals.py`

```python
# Integer
ir.Constant(ir.IntType(32), 42)

# Float
ir.Constant(ir.DoubleType(), 3.14)

# Boolean
ir.Constant(ir.IntType(1), 1)  # true
ir.Constant(ir.IntType(1), 0)  # false

# String
string_const = ir.GlobalVariable(module, ir.ArrayType(ir.IntType(8), len + 1), name)
string_const.initializer = ir.Constant(ir.ArrayType(ir.IntType(8), len + 1), bytearray(text, 'utf-8'))
string_ptr = builder.bitcast(string_const, ir.IntType(8).as_pointer())
```

### Binary Operators

**File:** `backend/expressions/operators.py`

**Arithmetic:**
```python
# Addition (int)
builder.add(left, right)

# Addition (float)
builder.fadd(left, right)

# Subtraction (int)
builder.sub(left, right)

# Multiplication (int)
builder.mul(left, right)

# Division (signed int)
builder.sdiv(left, right)

# Division (unsigned int)
builder.udiv(left, right)

# Division (float)
builder.fdiv(left, right)

# Modulo (signed)
builder.srem(left, right)

# Modulo (unsigned)
builder.urem(left, right)
```

**Comparison:**
```python
# Integer comparison
builder.icmp_signed('==', left, right)  # ==
builder.icmp_signed('!=', left, right)  # !=
builder.icmp_signed('<', left, right)   # <
builder.icmp_signed('<=', left, right)  # <=
builder.icmp_signed('>', left, right)   # >
builder.icmp_signed('>=', left, right)  # >=

# Float comparison (ordered)
builder.fcmp_ordered('==', left, right)
```

**Logical:**
```python
# AND
builder.and_(left, right)

# OR
builder.or_(left, right)

# NOT
builder.not_(operand)
```

**Bitwise:**
```python
# Bitwise AND
builder.and_(left, right)

# Bitwise OR
builder.or_(left, right)

# Bitwise XOR
builder.xor(left, right)

# Bitwise NOT (complement)
builder.not_(operand)

# Left shift (zero-fill right side)
builder.shl(left, right)

# Right shift (type-dependent, matches Go/Rust behavior)
builder.ashr(left, right)  # Arithmetic shift for signed types (i8, i16, i32, i64) - sign-extends
builder.lshr(left, right)  # Logical shift for unsigned types (u8, u16, u32, u64) - zero-fills
```

**Note:** The `>>` operator in Sushi automatically selects between `ashr` and `lshr` based on the operand's semantic type, ensuring type-safe shift behavior without requiring separate operator syntax.

### Type Casting

**File:** `backend/expressions/casts.py`

```python
# Integer to float
builder.sitofp(value, target_type)  # Signed int to float
builder.uitofp(value, target_type)  # Unsigned int to float

# Float to integer
builder.fptosi(value, target_type)  # Float to signed int (truncate)
builder.fptoui(value, target_type)  # Float to unsigned int

# Integer extension/truncation
builder.zext(value, target_type)    # Zero-extend (unsigned)
builder.sext(value, target_type)    # Sign-extend (signed)
builder.trunc(value, target_type)   # Truncate

# Integer to integer (same size, different signedness)
# No operation needed - just reinterpret
```

### Array Operations

**File:** `backend/expressions/arrays.py`

**Array literal:**
```python
# Fixed array: [1, 2, 3]
array_type = ir.ArrayType(ir.IntType(32), 3)
array_alloca = builder.alloca(array_type)
for i, elem in enumerate([1, 2, 3]):
    ptr = builder.gep(array_alloca, [ir.Constant(ir.IntType(32), 0),
                                      ir.Constant(ir.IntType(32), i)])
    builder.store(ir.Constant(ir.IntType(32), elem), ptr)
```

**Dynamic array:**
```python
# from([1, 2, 3])
# 1. Allocate struct { i32*, i32, i32 }
arr_struct = builder.alloca(dynarray_type)

# 2. Malloc buffer
size = 3
malloc_size = builder.mul(ir.Constant(ir.IntType(32), 4), size)  # 4 bytes per i32
buffer = builder.call(malloc_fn, [malloc_size])
buffer_typed = builder.bitcast(buffer, ir.IntType(32).as_pointer())

# 3. Store elements
for i, elem in enumerate([1, 2, 3]):
    ptr = builder.gep(buffer_typed, [ir.Constant(ir.IntType(32), i)])
    builder.store(ir.Constant(ir.IntType(32), elem), ptr)

# 4. Initialize struct
builder.store(buffer_typed, builder.gep(arr_struct, [zero, zero]))  # data ptr
builder.store(size, builder.gep(arr_struct, [zero, one]))            # length
builder.store(size, builder.gep(arr_struct, [zero, two]))            # capacity
```

### Function Calls

**File:** `backend/expressions/calls.py`

```python
# Load function from registry
func = module.get_global(func_name)

# Evaluate arguments
args = [emit_expression(arg) for arg in call_args]

# Call function
result = builder.call(func, args)
```

### Method Calls (Extension Methods)

After AST transformation, method calls become function calls:

```sushi
# Source:
arr.len()

# After transformation:
array_len(arr)

# LLVM:
call @array_len(%arr_type* %arr)
```

## Statement Emission

### Variable Declaration

**File:** `backend/statements/variables.py`

```python
# let i32 x = 42
x_alloca = builder.alloca(ir.IntType(32), name='x')
value = ir.Constant(ir.IntType(32), 42)
builder.store(value, x_alloca)
variables['x'] = x_alloca
```

### Variable Rebinding

```python
# x := 50
x_ptr = variables['x']
value = ir.Constant(ir.IntType(32), 50)
builder.store(value, x_ptr)
```

### If-Elif-Else

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

# Then block
builder.position_at_end(then_block)
emit_println("big")
builder.branch(merge_block)

# Else block
builder.position_at_end(else_block)
emit_println("small")
builder.branch(merge_block)

# Merge
builder.position_at_end(merge_block)
```

### While Loop

**File:** `backend/statements/loops.py`

```python
# while (x > 0):
#     x := x - 1

loop_cond = func.append_basic_block('while.cond')
loop_body = func.append_basic_block('while.body')
loop_end = func.append_basic_block('while.end')

builder.branch(loop_cond)

# Condition
builder.position_at_end(loop_cond)
x_val = builder.load(x_ptr)
cond = builder.icmp_signed('>', x_val, zero)
builder.cbranch(cond, loop_body, loop_end)

# Body
builder.position_at_end(loop_body)
x_val = builder.load(x_ptr)
new_val = builder.sub(x_val, one)
builder.store(new_val, x_ptr)
builder.branch(loop_cond)

# End
builder.position_at_end(loop_end)
```

### Pattern Matching

**File:** `backend/statements/matching.py`

```python
# match status:
#     Status.Idle() -> ...
#     Status.Running(task_id) -> ...

# 1. Load discriminant tag
tag_ptr = builder.gep(status_ptr, [zero, zero])
tag = builder.load(tag_ptr)

# 2. Switch on tag
switch = builder.switch(tag, default_block)

# 3. Case for Idle (tag = 0)
idle_block = func.append_basic_block('match.idle')
switch.add_case(ir.Constant(ir.IntType(32), 0), idle_block)

builder.position_at_end(idle_block)
# Emit idle case body
builder.branch(merge_block)

# 4. Case for Running (tag = 1)
running_block = func.append_basic_block('match.running')
switch.add_case(ir.Constant(ir.IntType(32), 1), running_block)

builder.position_at_end(running_block)
# Extract task_id from variant data
data_ptr = builder.gep(status_ptr, [zero, one])
task_id = builder.load(builder.bitcast(data_ptr, ir.IntType(32).as_pointer()))
# Emit running case body
builder.branch(merge_block)

builder.position_at_end(merge_block)
```

## Memory Management

### MemoryManager

**File:** `backend/expressions/memory.py`

Handles RAII (automatic cleanup) for dynamic resources.

**Key method:** `emit_value_destructor(value, type_)`

**Dispatch by type:**

```python
def emit_value_destructor(self, value, type_):
    if is_primitive(type_):
        return  # No-op
    elif type_ == 'string':
        return  # No-op (immutable)
    elif is_dynamic_array(type_):
        self.destroy_array(value, type_)
    elif is_struct(type_):
        self.destroy_struct(value, type_)
    elif is_enum(type_):
        self.destroy_enum(value, type_)
    elif is_own(type_):
        self.destroy_own(value, type_)
```

**Array destruction:**
```python
def destroy_array(self, arr_ptr, elem_type):
    # 1. Load data pointer, length
    data_ptr = builder.load(builder.gep(arr_ptr, [zero, zero]))
    length = builder.load(builder.gep(arr_ptr, [zero, one]))

    # 2. Destroy each element (if needed)
    if needs_destruction(elem_type):
        loop_destroy_elements(data_ptr, length, elem_type)

    # 3. Free buffer
    builder.call(free_fn, [data_ptr])
```

**Struct destruction:**
```python
def destroy_struct(self, struct_ptr, struct_type):
    # Destroy each field recursively
    for i, field_type in enumerate(struct_fields):
        field_ptr = builder.gep(struct_ptr, [zero, ir.Constant(ir.IntType(32), i)])
        field_val = builder.load(field_ptr)
        emit_value_destructor(field_val, field_type)
```

**Enum destruction:**
```python
def destroy_enum(self, enum_ptr, enum_type):
    # 1. Load discriminant
    tag_ptr = builder.gep(enum_ptr, [zero, zero])
    tag = builder.load(tag_ptr)

    # 2. Switch on tag
    switch = builder.switch(tag, default_block)

    # 3. For each variant, destroy variant-specific data
    for variant_tag, variant_fields in enumerate(variants):
        variant_block = func.append_basic_block(f'destroy.variant{variant_tag}')
        switch.add_case(ir.Constant(ir.IntType(32), variant_tag), variant_block)

        builder.position_at_end(variant_block)
        # Extract and destroy variant data
        data_ptr = builder.gep(enum_ptr, [zero, one])
        for field_type in variant_fields:
            emit_value_destructor(field_value, field_type)
        builder.branch(merge_block)
```

### Scope-Based Cleanup

At end of function or block:

```python
def emit_scope_cleanup(self):
    for var_name in scope.variables:
        if needs_cleanup(var_name):
            var_ptr = variables[var_name]
            var_value = builder.load(var_ptr)
            emit_value_destructor(var_value, var_type)
```

## Runtime Support

### String Operations

**File:** `backend/runtime/strings.py`

Implements string methods by emitting LLVM calls to libc or custom runtime functions.

```python
# strlen
len_fn = declare_libc_strlen(module)
result = builder.call(len_fn, [string_ptr])

# strcmp
strcmp_fn = declare_libc_strcmp(module)
cmp_result = builder.call(strcmp_fn, [str1, str2])
is_equal = builder.icmp_signed('==', cmp_result, zero)
```

### String Interpolation

**File:** `backend/runtime/formatting.py`

```sushi
let i32 x = 42
println("Answer: {x}")
```

**Generated LLVM:**
```python
# 1. Format string (without interpolations)
format_str = "Answer: %d\n"

# 2. Call printf
printf_fn = declare_libc_printf(module)
builder.call(printf_fn, [format_str_ptr, x_value])
```

### Error Messages

**File:** `backend/runtime/errors.py`

Runtime errors emit formatted messages:

```python
def emit_bounds_check(builder, index, length):
    # if (index >= length) { error }
    cond = builder.icmp_unsigned('>=', index, length)

    error_block = func.append_basic_block('bounds.error')
    continue_block = func.append_basic_block('bounds.ok')

    builder.cbranch(cond, error_block, continue_block)

    builder.position_at_end(error_block)
    # fprintf(stderr, "Runtime error RE2020: Array bounds check failed (index %d, length %d)\n", index, length)
    builder.call(fprintf, [stderr, error_msg, index, length])
    builder.call(exit_fn, [ir.Constant(ir.IntType(32), 1)])
    builder.unreachable()

    builder.position_at_end(continue_block)
```

## Optimization Pipeline

### Pass Management

**File:** `backend/codegen_llvm.py`

**Requirements:** llvmlite 0.43.0+ (uses New Pass Manager API)

The compiler uses LLVM's New Pass Manager for optimization, which provides better performance and more flexible pass composition than the legacy PassManager.

```python
def apply_optimizations(module, opt_level):
    """
    Apply LLVM optimization passes using New Pass Manager.

    Args:
        module: LLVM IR module
        opt_level: Optimization level (0-3)
    """
    import llvmlite.binding as llvm

    # Initialize LLVM
    llvm.initialize()
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()

    # Create target machine for platform-specific optimizations
    target = llvm.Target.from_default_triple()
    target_machine = target.create_target_machine()

    # Configure pipeline tuning options
    pto = llvm.PipelineTuningOptions(
        speed_level=opt_level,  # 0-3 for O0-O3
        size_level=0            # 0 = no size optimization, 1 = optimize for size
    )

    # Create pass builder
    pass_builder = llvm.PassBuilder(target_machine, pto)

    # Create pass managers
    module_pass_manager = llvm.create_module_pass_manager()
    function_pass_manager = llvm.create_function_pass_manager()

    # Populate pass managers based on optimization level
    if opt_level == 0:
        # No optimization
        pass
    elif opt_level == 1:
        # Basic optimizations (O1)
        pass_builder.populate_module_pass_manager(module_pass_manager)
        pass_builder.populate_function_pass_manager(function_pass_manager)
    elif opt_level == 2:
        # Moderate optimizations (O2)
        pass_builder.populate_module_pass_manager(module_pass_manager)
        pass_builder.populate_function_pass_manager(function_pass_manager)
    elif opt_level == 3:
        # Aggressive optimizations (O3)
        pass_builder.populate_module_pass_manager(module_pass_manager)
        pass_builder.populate_function_pass_manager(function_pass_manager)

    # Run optimization passes
    llvm_module = llvm.parse_assembly(str(module))
    function_pass_manager.run(llvm_module)
    module_pass_manager.run(llvm_module)

    return llvm_module
```

### Optimization Levels

The compiler supports the following optimization levels:

#### O0 (None)

No optimization. Fastest compilation, largest code size, slowest execution.

**Use case:** Development, debugging

**Passes:** None

#### O1 (Basic)

Basic optimizations with minimal compilation time impact.

**Use case:** Development with reasonable performance

**Typical passes:**
- Promote memory to register (mem2reg/SROA)
- CFG simplification
- Dead code elimination (DCE)
- Instruction combining
- Basic inlining (small functions only)

#### O2 (Moderate)

Moderate optimizations, good balance of compilation time and runtime performance.

**Use case:** Production builds

**Typical passes (includes all O1 passes plus):**
- Sparse conditional constant propagation (SCCP)
- Loop optimizations:
  - Loop rotation
  - Loop unswitch
  - Loop-invariant code motion (LICM)
- Global value numbering (GVN)
- MemCpy optimization
- Jump threading
- Tail call elimination
- Aggressive DCE

**Configuration:**
- `speed_level=2`
- `size_level=0`
- Loop vectorization: enabled
- SLP vectorization: enabled

#### O3 (Aggressive)

Aggressive optimizations, longest compilation time, best runtime performance.

**Use case:** Performance-critical production builds

**Typical passes (includes all O2 passes plus):**
- Aggressive inlining
- Loop unrolling
- Vectorization (both loop and SLP)
- More aggressive constant propagation
- Interprocedural optimizations

**Configuration:**
- `speed_level=3`
- `size_level=0`
- Loop vectorization: enabled
- SLP vectorization: enabled
- Inline threshold: increased

### PipelineTuningOptions

The `PipelineTuningOptions` class configures the optimization pipeline:

```python
llvm.PipelineTuningOptions(
    speed_level=2,      # 0-3: Higher = more aggressive optimization
    size_level=0,       # 0-2: Higher = optimize for code size over speed
    loop_interleaving=True,     # Enable loop interleaving
    loop_vectorization=True,    # Enable loop vectorization
    slp_vectorization=True,     # Enable superword-level parallelism vectorization
    loop_unrolling=True,        # Enable loop unrolling
    forget_scev_in_loop_unroll=True,  # Improved unroll analysis
    licm_mssa_opt_cap=None,     # LICM optimization limit
    licm_mssa_no_acc_for_promotion_cap=None,  # LICM promotion limit
    call_graph_profile=False,   # Use call graph profiling (requires profile data)
    merge_functions=False       # Merge identical functions (breaks debug info)
)
```

### Pass Manager Architecture

The New Pass Manager uses a two-level architecture:

1. **Module Pass Manager** - Operates on entire LLVM module
   - Interprocedural optimizations
   - Global analysis
   - Function inlining decisions

2. **Function Pass Manager** - Operates on individual functions
   - Intraprocedural optimizations
   - Local analysis
   - Instruction-level transformations

### Example: Custom Optimization Pipeline

```python
def apply_custom_optimizations(module):
    """Apply custom optimization pipeline"""
    import llvmlite.binding as llvm

    llvm.initialize()
    llvm.initialize_native_target()

    target = llvm.Target.from_default_triple()
    tm = target.create_target_machine()

    # Create pass builder with custom tuning
    pto = llvm.PipelineTuningOptions(
        speed_level=2,
        size_level=1,  # Balance speed and size
        loop_vectorization=True,
        slp_vectorization=False  # Disable SLP for smaller code
    )

    pb = llvm.PassBuilder(tm, pto)

    # Create pass managers
    mpm = llvm.create_module_pass_manager()
    fpm = llvm.create_function_pass_manager()

    # Populate with O2-level passes
    pb.populate_module_pass_manager(mpm)
    pb.populate_function_pass_manager(fpm)

    # Parse and optimize
    llvm_module = llvm.parse_assembly(str(module))
    fpm.run(llvm_module)
    mpm.run(llvm_module)

    return llvm_module
```

### Vectorization

The optimizer can automatically vectorize loops and arithmetic operations:

**Loop Vectorization:**
```sushi
# Original code
let i32[] arr = from([1, 2, 3, 4, 5, 6, 7, 8])
foreach(i in range(0, 8)):
    arr[i] = arr[i] * 2

# Vectorized (4-wide SIMD on x86-64)
# Processes 4 elements at once using SSE/AVX instructions
```

**SLP Vectorization (Superword-Level Parallelism):**
```sushi
# Original code
let i32 a1 = x1 + y1
let i32 a2 = x2 + y2
let i32 a3 = x3 + y3
let i32 a4 = x4 + y4

# Vectorized (combined into single SIMD operation)
# Uses packed addition instruction
```

### Debugging Optimization Issues

View generated LLVM IR at different stages:

```bash
# Unoptimized IR
./sushic --dump-ll --opt=0 program.sushi

# Optimized IR (O2)
./sushic --dump-ll --opt=2 program.sushi

# Compare optimization impact
diff unoptimized.ll optimized.ll
```

View pass execution with LLVM debug output:

```bash
# Set LLVM debug environment variable
export LLVM_DEBUG=1
./sushic --opt=2 program.sushi
```

## Linking

### Clang Invocation

```python
def link_executable(module, output_name, stdlib_modules):
    # 1. Write LLVM IR to temp file
    with open('temp.ll', 'w') as f:
        f.write(str(module))

    # 2. Collect stdlib .bc files
    stdlib_files = [f'stdlib/dist/{mod}.bc' for mod in stdlib_modules]

    # 3. Link with clang
    cmd = ['clang', 'temp.ll'] + stdlib_files + ['-o', output_name]
    subprocess.run(cmd, check=True)

    # 4. Cleanup
    os.remove('temp.ll')
```

---

**See also:**
- [Architecture](architecture.md) - Overall compiler design
- [Semantic Passes](semantic-passes.md) - Type checking and analysis
