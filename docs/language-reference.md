# Language Reference

[← Back to Documentation](README.md)

Complete syntax and semantics reference for Sushi Lang. For a gentler introduction, see the [Language Guide](language-guide.md).

## Table of Contents

- [Program Structure](#program-structure)
- [Types](#types)
- [Variables](#variables)
- [Functions](#functions)
- [Operators](#operators)
- [Control Flow](#control-flow)
- [Arrays](#arrays)
- [Structs](#structs)
- [Enums](#enums)
- [Pattern Matching](#pattern-matching)
- [Module System](#module-system)

## Program Structure

Every Sushi program must have a `main` function that returns `i32`:

```sushi
fn main() i32:
    # Program entry point
    return Result.Ok(0)
```

## Types

### Primitive Types

**Integers (signed):**
- `i8` - 8-bit signed integer (-128 to 127)
- `i16` - 16-bit signed integer (-32,768 to 32,767)
- `i32` - 32-bit signed integer (-2,147,483,648 to 2,147,483,647)
- `i64` - 64-bit signed integer (-9,223,372,036,854,775,808 to 9,223,372,036,854,775,807)

**Integers (unsigned):**
- `u8` - 8-bit unsigned integer (0 to 255)
- `u16` - 16-bit unsigned integer (0 to 65,535)
- `u32` - 32-bit unsigned integer (0 to 4,294,967,295)
- `u64` - 64-bit unsigned integer (0 to 18,446,744,073,709,551,615)

**Floating-point:**
- `f32` - 32-bit IEEE 754 floating-point
- `f64` - 64-bit IEEE 754 floating-point

**Other:**
- `bool` - Boolean (`true` or `false`)
- `string` - UTF-8 null-terminated string
- `~` - Blank type (only for return types)

### Numeric Literals

**Decimal literals** (default):
```sushi
let i32 dec = 42
let i32 large = 1000000
```

**Hexadecimal literals** (base 16, prefix `0x` or `0X`):
```sushi
let i32 hex = 0xFF           # 255
let i32 addr = 0xDEAD_BEEF   # underscores allowed
let i32 mask = 0xFF00
```

**Binary literals** (base 2, prefix `0b` or `0B`):
```sushi
let i32 bin = 0b1111         # 15
let i32 flags = 0b1010_1010  # underscores allowed
let i32 byte = 0b11111111
```

**Octal literals** (base 8, prefix `0o` or `0O`):
```sushi
let i32 oct = 0o755          # 493 (Unix permissions)
let i32 perm = 0o644         # 420
```

**Note**: C-style octals with leading zeros (e.g., `077`) are **not supported** and will cause a compilation error. Use the explicit `0o` prefix instead.

**Common features**:
- All literal formats support underscore separators for readability
- All default to `i32` type
- Prefixes are case insensitive (`0xFF` == `0xff`, `0B1111` == `0b1111`)

### Type Conversion

All type conversions must be explicit using the `as` keyword:

```sushi
let i32 x = 42
let f64 y = x as f64        # int to float
let i16 small = y as i16    # float to int (truncates)
let u32 unsigned = x as u32 # signed to unsigned
```

**Rules:**
- Only numeric types can be cast
- Float-to-integer truncates toward zero
- No implicit conversions
- No casting to/from strings or arrays

### Array Types

**Fixed arrays:**
```sushi
let i32[5] fixed = [1, 2, 3, 4, 5]
```

**Dynamic arrays:**
```sushi
let i32[] dynamic = from([1, 2, 3])
let string[] empty = new()
```

## Variables

### Declaration

Variables must be declared with `let`:

```sushi
let i32 x = 42
let string name = "Arthur"
let bool flag = true
```

### Rebinding

Use `:=` to rebind variables (must be declared first):

```sushi
let i32 x = 10
x := 20     # OK
x := 30     # OK

# ERROR: Cannot rebind without prior declaration
# y := 5    # CE1003: Undefined variable 'y'
```

### Scope

Variables are block-scoped:

```sushi
fn main() i32:
    let i32 x = 1

    if (true):
        let i32 y = 2  # y scoped to if block
        x := 3         # OK: x from outer scope

    # ERROR: y not in scope
    # println(y)

    return Result.Ok(0)
```

## Functions

### Declaration

```sushi
fn function_name(param1_type param1_name, param2_type param2_name) return_type:
    # Function body
    return Result.Ok(value)
```

**Example:**

```sushi
fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

fn greet(string name) ~:
    println("Hello, {name}!")
    return Result.Ok(~)
```

### Return Types

All functions implicitly return `Result<T>`:

```sushi
fn divide(i32 a, i32 b) i32:  # Actually returns Result<i32>
    if (b == 0):
        return Result.Err()
    return Result.Ok(a / b)
```

### Parameters

**By value:**
```sushi
fn modify(i32 x) i32:
    x := x + 1
    return Result.Ok(x)
```

**Borrowed (by reference):**
```sushi
fn increment(&i32 counter) ~:
    counter := counter + 1
    return Result.Ok(~)
```

## Operators

### Arithmetic

- `+` - Addition
- `-` - Subtraction
- `*` - Multiplication
- `/` - Division (integer division for int types)
- `%` - Modulo (remainder)

### Comparison

- `==` - Equal
- `!=` - Not equal
- `<` - Less than
- `<=` - Less than or equal
- `>` - Greater than
- `>=` - Greater than or equal

### Logical

- `and` (or `&&`) - Logical AND (short-circuits)
- `or` (or `||`) - Logical OR (short-circuits)
- `xor` (or `^^`) - Logical XOR (evaluates both sides)
- `not` (or `!`) - Logical NOT

**Alternative syntax:** Sushi supports both keyword (`and`, `or`, `xor`, `not`) and symbolic (`&&`, `||`, `^^`, `!`) 
forms for all logical operators.

### Bitwise

- `&` - Bitwise AND
- `|` - Bitwise OR
- `^` - Bitwise XOR
- `~` - Bitwise NOT (complement)
- `<<` - Left shift (zero-fill)
- `>>` - Right shift (type-dependent, see below)

**Right shift behavior (matches Go/Rust):**
- **Signed types** (`i8`, `i16`, `i32`, `i64`): Arithmetic shift (sign-extends)
  ```sushi
  let i32 a = -16
  let i32 shifted = a >> 2  # Result: -4 (preserves sign bit)
  ```
- **Unsigned types** (`u8`, `u16`, `u32`, `u64`): Logical shift (zero-fills)
  ```sushi
  let u32 a = 3221225472 as u32
  let u32 shifted = a >> 2  # Result: 805306368 (zero-fill from left)
  ```

### String

- `+` - String concatenation

### Other

- `as` - Type casting
- `??` - Error propagation

## Control Flow

### If-Elif-Else

Parentheses required around conditions:

```sushi
if (condition):
    # Block
elif (other_condition):
    # Block
else:
    # Block
```

### While Loops

```sushi
while (condition):
    # Loop body
    if (done):
        break
    if (skip):
        continue
```

### For-Each Loops

```sushi
foreach(element in iterable.iter()):
    # Use element
```

Type annotation optional:

```sushi
foreach(i32 element in array.iter()):
    println(element)
```

## Arrays

See [Standard Library](standard-library.md) for complete array API.

### Fixed Arrays

Stack-allocated, compile-time size:

```sushi
let i32[5] arr = [1, 2, 3, 4, 5]
let i32 first = arr.get(0)  # Bounds checked
```

### Dynamic Arrays

Heap-allocated, runtime size:

```sushi
let i32[] arr = from([1, 2, 3])
let i32[] empty = new()

arr.push(4)
let i32 last = arr.pop()
```

## Structs

### Definition

```sushi
struct Name:
    type1 field1
    type2 field2
```

**Example:**

```sushi
struct Person:
    string name
    i32 age
    bool active
```

### Instantiation

Structs support both positional and named parameter construction:

**Positional (traditional):**
```sushi
let Person p = Person("Arthur", 42, true)
```

**Named (order-independent):**
```sushi
let Person p1 = Person(name: "Arthur", age: 42, active: true)
let Person p2 = Person(age: 42, active: true, name: "Arthur")  # Order doesn't matter
```

**Rules:**
- Named parameters provide clarity and prevent argument order mistakes
- All fields must be provided (no partial construction)
- Cannot mix positional and named arguments (all-or-nothing)
- Named parameters are resolved at compile-time (zero-cost abstraction)

### Field Access

```sushi
println(p.name)
p.age := 43
```

### Nested Structs

```sushi
struct Point:
    i32 x
    i32 y

struct Rectangle:
    Point top_left
    Point bottom_right

let Rectangle rect = Rectangle(
    top_left: Point(x: 0, y: 0),
    bottom_right: Point(x: 10, y: 10)
)

println(rect.top_left.x)
```

## Enums

### Definition

```sushi
enum Name:
    Variant1()
    Variant2(type1 field1)
    Variant3(type1 field1, type2 field2)
```

**Example:**

```sushi
enum Status:
    Idle()
    Running(i32 task_id)
    Error(string message)
```

### Construction

```sushi
let Status s1 = Status.Idle()
let Status s2 = Status.Running(task_id: 42)
let Status s3 = Status.Error(message: "Failed")
```

### Pattern Matching

Required to access enum data:

```sushi
match s2:
    Status.Idle() ->
        println("Idle")
    Status.Running(task_id) ->
        println("Running task {task_id}")
    Status.Error(msg) ->
        println("Error: {msg}")
```

## Pattern Matching

### Basic Match

```sushi
match expression:
    Pattern1 -> statement
    Pattern2 -> statement
```

### Wildcard

```sushi
match value:
    Status.Running(_) -> println("Running")
    _ -> println("Other")
```

### Nested Patterns

```sushi
match result:
    FileResult.Err(FileError.NotFound()) ->
        println("File not found")
    FileResult.Err(_) ->
        println("Other file error")
    FileResult.Ok(f) ->
        println("File opened")
```

### Exhaustiveness

The compiler enforces that all variants are matched:

```sushi
enum Color:
    Red()
    Green()
    Blue()

# ERROR: Non-exhaustive match (missing Blue)
match color:
    Color.Red() -> println("Red")
    Color.Green() -> println("Green")
```

## Module System

### Units

Sushi uses a unit system where each source file is a unit:

```sushi
# file: math.sushi
unit math

fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)
```

### Visibility

Functions are private by default. Use `public` for external access:

```sushi
unit utils

public fn helper() i32:
    return Result.Ok(private_helper())

fn private_helper() i32:
    return Result.Ok(42)
```

### Standard Library

Import stdlib modules with `use`:

```sushi
use <collections>        # List<T>
use <collections/strings> # String utilities
use <io/stdio>           # stdio functions
```

## Comments

Single-line comments only:

```sushi
# This is a comment
let i32 x = 42  # Inline comment
```

## Keywords

Reserved keywords:

- `fn` - Function declaration
- `let` - Variable declaration
- `struct` - Struct definition
- `enum` - Enum definition
- `if`, `elif`, `else` - Conditionals
- `while` - Loop
- `foreach`, `in` - For-each loop
- `break`, `continue` - Loop control
- `match` - Pattern matching
- `return` - Function return
- `and`, `or`, `not` - Logical operators
- `true`, `false` - Boolean literals
- `as` - Type casting
- `unit` - Unit declaration
- `public` - Visibility modifier
- `use` - Module import
- `extend` - Extension method
- `self` - Extension method receiver

## String Literals

Sushi supports two string literal syntaxes:

**Double-quote strings** (`"..."`):
- Support interpolation with `{expr}` syntax
- All escape sequences supported
- Use for: string constants, interpolated strings

**Single-quote strings** (`'...'`):
- Plain string literals, no interpolation
- Same escape sequences as double-quote strings
- Use for: string arguments in interpolation, literal strings

```sushi
let string s1 = "double quotes"    # Supports interpolation
let string s2 = 'single quotes'    # No interpolation
let string s3 = 'can\'t'           # Escape sequences work
```

**Both quote styles are equivalent** except for interpolation support. Use whichever is more convenient.

### Escape Sequences

Both quote styles support the same escape sequences:

- `\\` - Backslash
- `\"` - Double quote
- `\'` - Single quote
- `\n` - Newline
- `\t` - Tab
- `\r` - Carriage return
- `\0` - Null character
- `\xNN` - Hexadecimal escape (e.g., `\x41` = 'A')
- `\uNNNN` - Unicode escape (e.g., `\u0041` = 'A')

## String Interpolation

Embed expressions in double-quote strings with `{expression}`:

```sushi
let i32 x = 42
let string name = "Arthur"

println("Hello {name}")
println("Answer: {x}")
println("Next: {x + 1}")
println("Squared: {x * x}")
```

**Supported types:** All primitives, strings

### String Arguments in Interpolation

Use single-quote strings for string arguments inside interpolation expressions:

```sushi
use <collections/strings>

let string text = "hello"
println("{text.pad_left(10, '*')}")       # Padding character
println("{text.find('world')}")           # Search string
println("{text.replace('old', 'new')}")   # Multiple string args
println("{','.join(parts)}")              # Separator string
```

Single-quote strings work naturally in nested contexts where double quotes would require escaping.

## Constants

### Declaration

Constants are declared with `const` and evaluated at compile-time:

```sushi
const i32 MAX_SIZE = 100
const string VERSION = "1.0.0"
const bool DEBUG = true
const f64 PI = 3.14159
```

### Constant Expressions

Constants support compile-time expressions with arithmetic, bitwise, logical, and comparison operators:

```sushi
const i32 BASE = 10
const i32 DOUBLE = 2 * BASE              # 20
const i32 COMPLEX = (100 + 50) / 3       # 50
const u32 FLAGS = 0x01 | 0x02 | 0x04     # 7
const bool IS_VALID = (100 > 50) and true # true
```

**Supported operations:**
- **Arithmetic**: `+`, `-`, `*`, `/`, `%` (numeric types)
- **Bitwise**: `&`, `|`, `^`, `~`, `<<`, `>>` (integer types only)
- **Logical**: `and`, `or`, `xor`, `not` (boolean type only)
- **Comparison**: `==`, `!=`, `<`, `<=`, `>`, `>=` (compatible types)
- **Type casts**: `as` (between compatible types)

### Constant References

Constants can reference other constants:

```sushi
const i32 WIDTH = 100
const i32 HEIGHT = 50
const i32 AREA = WIDTH * HEIGHT  # 5000

const i32 BASE = 10
const i32 OFFSET = BASE * 2
const i32 TOTAL = OFFSET + BASE  # 30
```

The compiler detects circular dependencies:

```sushi
# ERROR: Circular constant dependency
const i32 A = B + 1
const i32 B = A + 1  # CE0109: circular dependency detected
```

### Array Constants

Fixed-size arrays with constant elements:

```sushi
const i32[3] PRIMES = [2, 3, 5]
const bool[2] FLAGS = [true, false]
const i32[4] POWERS = [1, 2, 4, 8]

# Can use expressions
const i32 BASE = 10
const i32[3] VALUES = [BASE, BASE * 2, BASE * 3]  # [10, 20, 30]
```

**Restrictions:**
- Array must be fixed-size (`T[N]`), not dynamic (`T[]`)
- All elements must be compile-time constant expressions

### Restrictions

Constants cannot use:
- Function calls (including constructors)
- Variable references (only other constants)
- String concatenation with `+` (not yet supported)
- Struct or enum construction
- Method calls
- Dynamic arrays

```sushi
# ERROR: Not allowed in constants
const i32 X = get_value()     # CE0108: function calls forbidden
const i32 Y = some_variable   # CE0108: variable references forbidden
const i32[] DYNAMIC = from([1, 2])  # CE2015: dynamic arrays forbidden
```

---

**See also:**
- [Standard Library](standard-library.md) - Built-in types and functions
- [Error Handling](error-handling.md) - Result<T> and Maybe<T>
- [Memory Management](memory-management.md) - RAII and ownership
- [Generics](generics.md) - Generic types and functions
