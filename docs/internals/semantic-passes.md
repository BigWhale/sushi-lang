# Semantic Analysis Passes

[← Back to Documentation](../README.md) | [Architecture](architecture.md)

Detailed documentation of Sushi's multi-pass semantic analysis pipeline.

## Pass Overview

```
Phase 0: Constants, Function Headers, Generic Types
    ↓
Phase 1: Scope and Variable Analysis
    ↓
Phase 1.5: Generic Instantiation Collection
    ↓
Phase 1.6: Monomorphization (Generic → Concrete)
    ↓
Phase 1.7: AST Transformation, Type Resolution
    ↓
Phase 1.8: Hash Function Auto-Derivation
    ↓
Phase 2: Type Validation
    ↓
Phase 3: Borrow Checking
```

## Phase 0: Headers and Constants

**Files:** `semantics/passes/collect/*.py`

### Purpose

Collect global definitions before analyzing function bodies.

### Responsibilities

1. **Constants**: Parse and register constant definitions
2. **Function Signatures**: Collect return types and parameters
3. **Generic Types**: Register struct and enum definitions
4. **Symbol Table**: Build initial global scope

### Example

```sushi
const i32 MAX = 100  # Register constant

struct Pair<T, U>:   # Register generic struct
    T first
    U second

fn add(i32 a, i32 b) i32:  # Register signature
    return Result.Ok(a + b)
```

**Output:**
- `constants = {'MAX': 100}`
- `functions = {'add': FunctionSignature(...)}`
- `generic_types = {'Pair': GenericStruct(...)}`

### Limitations

Constants can only be literal values (no expressions).

## Phase 1: Scope and Variable Analysis

**File:** `semantics/passes/scope.py`

### Purpose

Track variable lifetimes, scopes, and ownership.

### Responsibilities

1. **Variable Declarations**: Register all `let` declarations
2. **Scope Analysis**: Track block-level scopes
3. **Move Semantics**: Mark variables as moved
4. **Usage Tracking**: Detect undefined variables

### Variable States

- **Declared**: Variable exists in scope
- **Moved**: Ownership transferred, cannot use
- **Destroyed**: Explicitly destroyed via `.destroy()`
- **Borrowed**: Temporarily passed by reference

### Examples

**Valid:**
```sushi
let i32 x = 42
let i32 y = x  # OK: primitives copy
```

**Invalid:**
```sushi
let i32[] arr = from([1, 2, 3])
let i32[] moved = arr
println(arr.len())  # ERROR CE1004: Use of moved variable 'arr'
```

### Scope Tracking

```sushi
fn example() i32:
    let i32 x = 1  # Scope 0 (function)

    if (true):
        let i32 y = 2  # Scope 1 (if block)
        x := 3         # OK: x from outer scope

    # println(y)  # ERROR CE1003: Undefined variable 'y'

    return Result.Ok(0)
```

## Phase 1.5: Generic Instantiation Collection

**Files:** `semantics/generics/instantiate/*.py`

### Purpose

Detect which generic instantiations are needed.

### How It Works

1. Traverse AST looking for generic types
2. When `List<i32>` appears, record it
3. When `.push()` is called on `List<i32>`, record `List<i32>.push`
4. Build complete set of required instantiations

### Example

```sushi
let List<i32> nums = List.new()  # Collect: List<i32>, List<i32>.new
nums.push(42)                     # Collect: List<i32>.push

let List<string> names = List.new()  # Collect: List<string>, List<string>.new
names.push("Alice")                   # Collect: List<string>.push
```

**Collected instantiations:**
- `List<i32>`
- `List<i32>.new()`
- `List<i32>.push()`
- `List<string>`
- `List<string>.new()`
- `List<string>.push()`

## Phase 1.6: Monomorphization

**Files:** `semantics/generics/monomorphize/*.py`

### Purpose

Generate concrete types from generic definitions.

### Process

1. For each collected instantiation (e.g., `List<i32>`)
2. Substitute type parameters (`T` → `i32`)
3. Create specialized struct/function
4. Add to AST as concrete definition

### Example

**Generic definition:**
```sushi
struct Pair<T, U>:
    T first
    U second

extend Pair<T, U> swap<T, U>() Pair<U, T>:
    return Result.Ok(Pair(first: self.second, second: self.first))
```

**After monomorphization for `Pair<i32, string>`:**
```sushi
struct Pair__i32__string:
    i32 first
    string second

extend Pair__i32__string swap() Pair__string__i32:
    return Result.Ok(Pair__string__i32(first: self.second, second: self.first))
```

### Name Mangling

- `Pair<i32, string>` → `Pair__i32__string`
- `List<T>` → `List__i32`, `List__string`
- Nested: `Maybe<Maybe<i32>>` → `Maybe__Maybe__i32`

## Phase 1.7: AST Transformation

**File:** `semantics/passes/ast_transform.py`

### Purpose

Transform high-level constructs into simpler forms.

### Transformations

1. **Extension Method → Function Call**

```sushi
# Before:
arr.len()

# After:
array_len(arr)
```

2. **Type Inference**

```sushi
# Before:
let Result<i32> r = get_value()

# After: (type explicitly resolved)
let Result<i32> r = get_value()  # Type: Result<i32>
```

3. **UFCS (Uniform Function Call Syntax)**

```sushi
# Before:
"hello".len()

# After:
string_len("hello")
```

### Benefits

- Simpler backend (only handles function calls)
- Easier optimization
- Clearer semantics

## Phase 1.8: Hash Function Derivation

**File:** `semantics/passes/hash_registration.py`

### Purpose

Auto-generate `.hash() -> u64` for all types.

### Algorithm

**Primitives:**
- Integers: FxHash
- Floats: Normalized to u64, then FxHash
- Strings: FNV-1a
- Booleans: 0 or 1

**Structs:**
```python
hash = FNV_OFFSET_BASIS
for field in fields:
    hash ^= field.hash()
    hash *= FNV_PRIME
return hash
```

**Enums:**
```python
hash = discriminant.hash()
hash ^= variant_data.hash()
return hash
```

**Arrays:**
```python
hash = FNV_OFFSET_BASIS
for element in elements:
    hash ^= element.hash()
    hash *= FNV_PRIME
return hash
```

### Limitations

Nested arrays cannot be hashed (type system constraint).

## Phase 2: Type Validation

**Files:** `semantics/passes/types/*.py`

### Purpose

Ensure all expressions and statements are type-correct.

### Modular Type Checking

**types/utils.py** - Type utilities
- `is_numeric()`, `is_integer()`, `is_float()`
- Type comparison and normalization

**types/inference.py** - Type inference
- Infer types from literals
- Propagate types through expressions

**types/compatibility.py** - Type compatibility
- Check if type A can be assigned to type B
- Handle Result<T> unwrapping

**types/expressions.py** - Expression type checking
- Binary operators (+, -, *, /, %, ==, !=, <, >, and, or)
- Unary operators (-, not)
- Function calls
- Array access
- Struct field access

**types/matching.py** - Pattern match validation
- Exhaustiveness checking
- Variant data extraction
- Nested pattern support

**types/calls.py** - Function call validation
- Argument count matching
- Parameter type compatibility
- Return type inference

**types/statements.py** - Statement validation
- Variable declarations
- Rebinding
- Control flow (if, while, foreach)
- Return statements

### Type Checking Examples

**Valid:**
```sushi
let i32 x = 42
let i32 y = x + 10  # OK: i32 + i32 → i32
```

**Invalid:**
```sushi
let i32 x = 42
let i32 y = x + "hello"  # ERROR CE2xxx: Cannot add i32 and string
```

**Result Handling:**
```sushi
fn get_value() i32:
    return Result.Ok(42)

# ERROR CE2505: Cannot assign Result<i32> to i32
let i32 x = get_value()

# OK: Use .realise()
let i32 y = get_value().realise(0)
```

## Phase 3: Borrow Checking

**File:** `semantics/passes/borrow.py`

### Purpose

Enforce memory safety rules for references.

### Rules

1. **One active borrow per variable**

```sushi
let i32 x = 42
let &i32 r1 = &x
# let &i32 r2 = &x  # ERROR: x already borrowed
```

2. **Cannot move/rebind while borrowed**

```sushi
fn borrow(&i32 x) i32:
    return Result.Ok(x)

fn main() i32:
    let i32 num = 42
    let i32 borrowed = borrow(&num)
    # num := 50  # ERROR CE1007: Cannot rebind while borrowed
    return Result.Ok(0)
```

3. **Cannot borrow temporaries**

```sushi
# ERROR: Cannot borrow temporary expression
# let i32 x = func(&(5 + 3))

# OK: Use variable
let i32 temp = 5 + 3
let i32 x = func(&temp)
```

4. **Use-after-destroy detection**

```sushi
let i32[] arr = from([1, 2, 3])
arr.destroy()
# println(arr.len())  # ERROR CE2406: Use of destroyed variable 'arr'
```

### Borrow Tracking

**Data structures:**
```python
active_borrows: Dict[str, BorrowId] = {}
destroyed_variables: Set[str] = set()
```

**On borrow:**
```python
if var in active_borrows:
    raise BorrowError("Already borrowed")
active_borrows[var] = borrow_id
```

**On borrow end (function return):**
```python
del active_borrows[var]
```

**On destroy:**
```python
destroyed_variables.add(var)
```

**On usage:**
```python
if var in destroyed_variables:
    raise UseAfterDestroyError("CE2406")
if var in moved_variables:
    raise UseAfterMoveError("CE1004")
```

## Pass Interdependencies

```
Phase 0 → Phase 1 → Phase 1.5 → Phase 1.6 → Phase 1.7 → Phase 1.8 → Phase 2 → Phase 3
   ↓        ↓          ↓            ↓            ↓           ↓          ↓        ↓
Constants  Vars   Instantiate  Monomorphize  Transform    Hash     Types   Borrows
  +Sigs    +Moves    Generics    Generics      AST       Funcs    Check    Check
```

**Dependencies:**
- Phase 1 needs Phase 0 (function signatures)
- Phase 1.5 needs Phase 1 (variable types)
- Phase 1.6 needs Phase 1.5 (instantiations to generate)
- Phase 1.7 needs Phase 1.6 (concrete types for resolution)
- Phase 1.8 needs Phase 1.7 (resolved types for hashing)
- Phase 2 needs Phase 1.7 (transformed AST)
- Phase 3 needs Phase 2 (type-checked borrows)

## Error Examples by Pass

**Phase 1:**
- CE1003: Undefined variable
- CE1004: Use of moved variable

**Phase 2:**
- CE2xxx: Type mismatch
- CE2502: `.realise()` wrong argument count
- CE2505: Assigning Result<T> without handling

**Phase 3:**
- CE1007: Cannot rebind while borrowed
- CE2406: Use of destroyed variable

---

**See also:**
- [Architecture](architecture.md) - Overall compiler design
- [Backend](backend.md) - Code generation details
