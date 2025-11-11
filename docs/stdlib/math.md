# Math Module

[← Back to Standard Library](../standard-library.md)

Mathematical functions for numeric types.

## Import

```sushi
use <math>
```

## Overview

The math module provides mathematical operations for all numeric types, including absolute value, min/max, and floating-point operations. All functions use LLVM intrinsics for optimal performance.

## Absolute Value Functions

Get the absolute value of a number.

### Integer Absolute Value

Available for all signed integer types: `i8`, `i16`, `i32`, `i64`

```sushi
use <math>

fn main() i32:
    let i32 x = -42
    let i32 abs_x = abs_i32(x)  # 42

    let i64 y = -100 as i64
    let i64 abs_y = abs_i64(y)  # 100

    return Result.Ok(0)
```

**Functions:**
- `abs_i8(i8 value) -> i8`
- `abs_i16(i16 value) -> i16`
- `abs_i32(i32 value) -> i32`
- `abs_i64(i64 value) -> i64`

### Floating-Point Absolute Value

Available for: `f32`, `f64`

```sushi
use <math>

fn main() i32:
    let f64 x = -3.14
    let f64 abs_x = abs_f64(x)  # 3.14

    return Result.Ok(0)
```

**Functions:**
- `abs_f32(f32 value) -> f32`
- `abs_f64(f64 value) -> f64`

## Min/Max Functions

Find minimum or maximum of two values.

### Integer Min/Max

Available for all numeric types: `i8`, `i16`, `i32`, `i64`, `u8`, `u16`, `u32`, `u64`

```sushi
use <math>

fn main() i32:
    let i32 a = 10
    let i32 b = 20

    let i32 smaller = min_i32(a, b)  # 10
    let i32 larger = max_i32(a, b)   # 20

    return Result.Ok(0)
```

**Functions:**
- `min_i8(i8 a, i8 b) -> i8` / `max_i8(i8 a, i8 b) -> i8`
- `min_i16(i16 a, i16 b) -> i16` / `max_i16(i16 a, i16 b) -> i16`
- `min_i32(i32 a, i32 b) -> i32` / `max_i32(i32 a, i32 b) -> i32`
- `min_i64(i64 a, i64 b) -> i64` / `max_i64(i64 a, i64 b) -> i64`
- `min_u8(u8 a, u8 b) -> u8` / `max_u8(u8 a, u8 b) -> u8`
- `min_u16(u16 a, u16 b) -> u16` / `max_u16(u16 a, u16 b) -> u16`
- `min_u32(u32 a, u32 b) -> u32` / `max_u32(u32 a, u32 b) -> u32`
- `min_u64(u64 a, u64 b) -> u64` / `max_u64(u64 a, u64 b) -> u64`

### Floating-Point Min/Max

Available for: `f32`, `f64`

```sushi
use <math>

fn main() i32:
    let f64 a = 3.14
    let f64 b = 2.71

    let f64 smaller = min_f64(a, b)  # 2.71
    let f64 larger = max_f64(a, b)   # 3.14

    return Result.Ok(0)
```

**Functions:**
- `min_f32(f32 a, f32 b) -> f32` / `max_f32(f32 a, f32 b) -> f32`
- `min_f64(f64 a, f64 b) -> f64` / `max_f64(f64 a, f64 b) -> f64`

## Floating-Point Operations

Advanced mathematical functions for floating-point types.

### Square Root

```sushi
use <math>

fn main() i32:
    let f64 x = 16.0
    let f64 root = sqrt_f64(x)  # 4.0

    let f32 y = 9.0 as f32
    let f32 root_y = sqrt_f32(y)  # 3.0

    return Result.Ok(0)
```

**Functions:**
- `sqrt_f32(f32 value) -> f32`
- `sqrt_f64(f64 value) -> f64`

**Note:** Negative inputs produce NaN (Not a Number).

### Power

Raise a number to a power.

```sushi
use <math>

fn main() i32:
    let f64 base = 2.0
    let f64 exponent = 3.0
    let f64 result = pow_f64(base, exponent)  # 8.0

    return Result.Ok(0)
```

**Functions:**
- `pow_f32(f32 base, f32 exponent) -> f32`
- `pow_f64(f64 base, f64 exponent) -> f64`

### Floor

Round down to nearest integer.

```sushi
use <math>

fn main() i32:
    let f64 x = 3.7
    let f64 floored = floor_f64(x)  # 3.0

    let f64 y = -2.3
    let f64 floored_y = floor_f64(y)  # -3.0

    return Result.Ok(0)
```

**Functions:**
- `floor_f32(f32 value) -> f32`
- `floor_f64(f64 value) -> f64`

### Ceiling

Round up to nearest integer.

```sushi
use <math>

fn main() i32:
    let f64 x = 3.2
    let f64 ceiled = ceil_f64(x)  # 4.0

    let f64 y = -2.8
    let f64 ceiled_y = ceil_f64(y)  # -2.0

    return Result.Ok(0)
```

**Functions:**
- `ceil_f32(f32 value) -> f32`
- `ceil_f64(f64 value) -> f64`

### Round

Round to nearest integer (half-way cases round away from zero).

```sushi
use <math>

fn main() i32:
    let f64 x = 3.5
    let f64 rounded = round_f64(x)  # 4.0

    let f64 y = 3.4
    let f64 rounded_y = round_f64(y)  # 3.0

    let f64 z = -3.5
    let f64 rounded_z = round_f64(z)  # -4.0

    return Result.Ok(0)
```

**Functions:**
- `round_f32(f32 value) -> f32`
- `round_f64(f64 value) -> f64`

### Truncate

Round toward zero (remove decimal part).

```sushi
use <math>

fn main() i32:
    let f64 x = 3.9
    let f64 truncated = trunc_f64(x)  # 3.0

    let f64 y = -3.9
    let f64 truncated_y = trunc_f64(y)  # -3.0

    return Result.Ok(0)
```

**Functions:**
- `trunc_f32(f32 value) -> f32`
- `trunc_f64(f64 value) -> f64`

## Type-Specific Function Naming

All functions use type-specific suffixes to indicate which type they operate on:

- `_i8`, `_i16`, `_i32`, `_i64` - Signed integers
- `_u8`, `_u16`, `_u32`, `_u64` - Unsigned integers
- `_f32`, `_f64` - Floating-point numbers

This ensures type safety and avoids ambiguity at compile time.

## Performance

All math functions are implemented using LLVM intrinsics, which:
- Compile to native CPU instructions when available
- Provide optimal performance without function call overhead
- Enable vectorization opportunities in loops

## IEEE 754 Behavior

Floating-point operations follow IEEE 754 standard:

- **NaN propagation:** Operations with NaN inputs produce NaN
- **Infinity:** `1.0 / 0.0` produces infinity, `sqrt(-1.0)` produces NaN
- **Denormals:** Handled per platform default
- **Rounding:** Round to nearest, ties to even (except for `round()` which rounds away from zero)

## Example: Computing Distance

```sushi
use <math>

fn distance(f64 x1, f64 y1, f64 x2, f64 y2) f64:
    let f64 dx = x2 - x1
    let f64 dy = y2 - y1

    let f64 dx_squared = pow_f64(dx, 2.0)
    let f64 dy_squared = pow_f64(dy, 2.0)

    let f64 sum = dx_squared + dy_squared
    let f64 dist = sqrt_f64(sum)

    return Result.Ok(dist)

fn main() i32:
    let f64 d = distance(0.0, 0.0, 3.0, 4.0).realise(0.0)
    println("Distance: {d}")  # Distance: 5.0

    return Result.Ok(0)
```

## See Also

- [Standard Library Reference](../standard-library.md) - Complete stdlib reference
- [Language Reference](../language-reference.md) - Numeric types and operators
