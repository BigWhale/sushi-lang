# Math Module

[← Back to Standard Library](../standard-library.md)

Mathematical functions for numeric types.

## Import

```sushi
use <math>
```

## Overview

The math module provides mathematical operations for numeric types. All functions are
referred to by a single, **polymorphic** name — there are no type-suffixed variants such as
`abs_i32` or `sqrt_f64`. Two families exist:

- `abs`, `min`, and `max` accept any matching numeric type and return that same type.
- All other functions (`sqrt`, `pow`, the trigonometric, hyperbolic, logarithmic, and
  exponential functions, and `hypot`) operate on `f64` and return `f64`.

All functions are implemented with LLVM intrinsics for optimal performance.

## Constants

- `PI` — 3.141592653589793 (`f64`)
- `E` — 2.718281828459045 (`f64`)
- `TAU` — 6.283185307179586 (`f64`)

## Polymorphic Functions: abs, min, max

These three functions adapt to the type of their arguments.

```sushi
use <math>

fn main() i32:
    let i32 a = abs(-42)        # 42 (i32)
    let f64 b = abs(-3.14)      # 3.14 (f64)
    let i32 c = min(10, 20)     # 10 (i32)
    let f64 d = max(1.5, 2.5)   # 2.5 (f64)

    return Result.Ok(0)
```

**Functions:**
- `abs(T value) -> T` — absolute value. `T` must be a **signed integer** (`i8`, `i16`,
  `i32`, `i64`) or a **float** (`f32`, `f64`).
- `min(T a, T b) -> T` — smaller of two values. `T` may be any numeric type; both arguments
  must have the same type.
- `max(T a, T b) -> T` — larger of two values. Same typing rules as `min`.

## Floating-Point Functions

The remaining functions take `f64` arguments and return `f64`. (Pass an `f32` and you will
get a type-mismatch error; cast to `f64` first with `as f64`.)

### Square Root and Power

```sushi
use <math>

fn main() i32:
    let f64 root = sqrt(16.0)        # 4.0
    let f64 result = pow(2.0, 3.0)   # 8.0

    return Result.Ok(0)
```

**Functions:**
- `sqrt(f64 value) -> f64` — square root. Negative inputs produce NaN.
- `pow(f64 base, f64 exponent) -> f64` — `base` raised to `exponent`.

### Rounding: floor, ceil, round, trunc

```sushi
use <math>

fn main() i32:
    let f64 a = floor(3.7)    # 3.0  (round down)
    let f64 b = ceil(3.2)     # 4.0  (round up)
    let f64 c = round(3.5)    # 4.0  (nearest, ties away from zero)
    let f64 d = trunc(3.9)    # 3.0  (toward zero)

    return Result.Ok(0)
```

**Functions:**
- `floor(f64 value) -> f64` — largest integer not greater than `value`.
- `ceil(f64 value) -> f64` — smallest integer not less than `value`.
- `round(f64 value) -> f64` — nearest integer; half-way cases round away from zero.
- `trunc(f64 value) -> f64` — drop the fractional part (round toward zero).

### Trigonometric Functions

All trigonometric functions operate on radians.

```sushi
use <math>

fn main() i32:
    let f64 angle = PI / 4.0

    let f64 s = sin(angle)   # ~0.707
    let f64 c = cos(angle)   # ~0.707
    let f64 t = tan(angle)   # ~1.0

    return Result.Ok(0)
```

**Functions:**
- `sin(f64 x) -> f64` — sine of x (radians)
- `cos(f64 x) -> f64` — cosine of x (radians)
- `tan(f64 x) -> f64` — tangent of x (radians)

### Inverse Trigonometric: asin, acos, atan, atan2

```sushi
use <math>

fn main() i32:
    let f64 a = asin(1.0)         # PI/2
    let f64 b = acos(0.0)         # PI/2
    let f64 c = atan(1.0)         # PI/4
    let f64 d = atan2(1.0, 1.0)   # PI/4 (y/x with quadrant)

    return Result.Ok(0)
```

**Functions:**
- `asin(f64 x) -> f64` — arc sine, returns radians in [-PI/2, PI/2]
- `acos(f64 x) -> f64` — arc cosine, returns radians in [0, PI]
- `atan(f64 x) -> f64` — arc tangent, returns radians in [-PI/2, PI/2]
- `atan2(f64 y, f64 x) -> f64` — arc tangent of y/x, using signs to determine quadrant

### Hyperbolic Functions

```sushi
use <math>

fn main() i32:
    let f64 x = 1.0

    let f64 s = sinh(x)   # ~1.175
    let f64 c = cosh(x)   # ~1.543
    let f64 t = tanh(x)   # ~0.762

    return Result.Ok(0)
```

**Functions:**
- `sinh(f64 x) -> f64` — hyperbolic sine
- `cosh(f64 x) -> f64` — hyperbolic cosine
- `tanh(f64 x) -> f64` — hyperbolic tangent

### Logarithmic Functions

```sushi
use <math>

fn main() i32:
    let f64 ln_e = log(E)           # 1.0 (natural log)
    let f64 log2_8 = log2(8.0)      # 3.0
    let f64 log_100 = log10(100.0)  # 2.0

    return Result.Ok(0)
```

**Functions:**
- `log(f64 x) -> f64` — natural logarithm (base e)
- `log2(f64 x) -> f64` — base-2 logarithm
- `log10(f64 x) -> f64` — base-10 logarithm

Logarithm of non-positive values produces NaN or -Infinity.

### Exponential Functions

```sushi
use <math>

fn main() i32:
    let f64 e_squared = exp(2.0)   # ~7.389 (e^2)
    let f64 two_cubed = exp2(3.0)  # 8.0 (2^3)

    return Result.Ok(0)
```

**Functions:**
- `exp(f64 x) -> f64` — e raised to power x
- `exp2(f64 x) -> f64` — 2 raised to power x

### hypot

```sushi
use <math>

fn main() i32:
    let f64 h = hypot(3.0, 4.0)   # 5.0 (classic 3-4-5 triangle)
    let f64 dist = hypot(6.0, 8.0)  # 10.0

    return Result.Ok(0)
```

**Function:**
- `hypot(f64 x, f64 y) -> f64` — equivalent to `sqrt(x*x + y*y)`, computed without overflow.

## IEEE 754 Behavior

Floating-point operations follow the IEEE 754 standard:

- **NaN propagation:** operations with NaN inputs produce NaN
- **Infinity:** `1.0 / 0.0` produces infinity, `sqrt(-1.0)` produces NaN
- **Rounding:** round to nearest, ties to even (except `round()`, which rounds away from zero)

## Example: Computing Distance

```sushi
use <math>

fn distance(f64 x1, f64 y1, f64 x2, f64 y2) f64:
    let f64 dx = x2 - x1
    let f64 dy = y2 - y1
    return Result.Ok(hypot(dx, dy))

fn main() i32:
    let f64 d = distance(0.0, 0.0, 3.0, 4.0).realise(0.0)
    println("Distance: {d}")   # Distance: 5

    return Result.Ok(0)
```

!!! note
    Whole-valued floats print without a trailing `.0` — `5.0` displays as `5`.

## See Also

- [Standard Library Reference](../standard-library.md) - Complete stdlib reference
- [Language Reference](../language-reference.md) - Numeric types and operators
