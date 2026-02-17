# Random Module

[← Back to Standard Library](../standard-library.md)

Provides basic pseudo-random number generation for non-cryptographic use cases.

## Import

```sushi
use <random>
```

## Functions

### rand()

Returns a random unsigned 64-bit integer.

**Signature:**
```sushi
fn rand() u64
```

**Returns:** Random value in range [0, 2^64-1]

**Example:**
```sushi
use <random>

fn main() i32:
    let u64 value = rand()
    println("Random u64: {value}")
    return Result.Ok(0)
```

### rand_range()

Returns a random integer in the range [min, max).

**Signature:**
```sushi
fn rand_range(i32 min, i32 max) i32
```

**Parameters:**
- `min` - Inclusive lower bound
- `max` - Exclusive upper bound

**Returns:** Random value where `min <= result < max`

**Example:**
```sushi
use <random>

fn main() i32:
    # Simulate rolling a die (1-6)
    let i32 die = rand_range(1, 7)
    println("Die roll: {die}")

    # Random index for array of size 10
    let i32 index = rand_range(0, 10)

    return Result.Ok(0)
```

### rand_f64()

Returns a random floating-point value in the range [0.0, 1.0).

**Signature:**
```sushi
fn rand_f64() f64
```

**Returns:** Random value where `0.0 <= result < 1.0`

**Example:**
```sushi
use <random>

fn main() i32:
    let f64 probability = rand_f64()
    println("Probability: {probability}")

    # Generate random float in range [min, max)
    let f64 min = 10.0
    let f64 max = 20.0
    let f64 value = min + (rand_f64() * (max - min))
    println("Random in [10, 20): {value}")

    return Result.Ok(0)
```

### srand()

Seeds the random number generator for reproducible sequences.

**Signature:**
```sushi
fn srand(u64 seed) ~
```

**Parameters:**
- `seed` - Seed value (same seed produces same sequence)

**Returns:** Blank type (`~`)

**Example:**
```sushi
use <random>

fn main() i32:
    # Seed for reproducibility
    srand(42 as u64)

    # These will be the same every run with seed 42
    let i32 a = rand_range(1, 100)
    let i32 b = rand_range(1, 100)
    let i32 c = rand_range(1, 100)

    println("Sequence: {a}, {b}, {c}")

    return Result.Ok(0)
```

## Implementation Notes

**Algorithm:**
- Uses POSIX `random()` and `srandom()` from libc
- Linear congruential generator (LCG)
- State size: 128 bytes (on most platforms)

**Quality:**
- Adequate for games, simulations, and testing
- NOT cryptographically secure
- NOT suitable for security-sensitive applications (use crypto library instead)

**Thread Safety:**
- NOT thread-safe (uses global state)
- Different threads share the same generator
- For multi-threaded use, external synchronization required

**Precision:**
- `rand_f64()` precision limited by `random()` output (typically 31 bits)
- Full 64-bit precision not guaranteed

**Portability:**
- POSIX-compliant systems only (Unix, Linux, macOS, BSD)
- Not available on Windows (requires POSIX compatibility layer)

## Common Patterns

### Random Boolean

```sushi
use <random>

fn coin_flip() bool:
    return Result.Ok(rand_range(0, 2) == 1)

fn main() i32:
    if (coin_flip()??):
        println("Heads")
    else:
        println("Tails")
    return Result.Ok(0)
```

### Random Element from Array

```sushi
use <random>

fn main() i32:
    let string[] choices = from(["Rock", "Paper", "Scissors"])
    let i32 index = rand_range(0, choices.len() as i32)
    let string choice = choices[index]
    println("Choice: {choice}")
    return Result.Ok(0)
```

### Random Float in Range

```sushi
use <random>

fn rand_f64_range(f64 min, f64 max) f64:
    return Result.Ok(min + (rand_f64() * (max - min)))

fn main() i32:
    let f64 temp = rand_f64_range(-10.0, 35.0)
    println("Temperature: {temp}°C")
    return Result.Ok(0)
```

### Reproducible Random Sequences

```sushi
use <random>

fn generate_level(u64 level_seed) i32[]:
    # Same seed always generates same level
    srand(level_seed)

    let i32[] terrain = from([])
    foreach(i in 0..100):
        terrain.push(rand_range(0, 10))

    return Result.Ok(terrain)

fn main() i32:
    # Level 1 will always have the same terrain
    let i32[] level1 = generate_level(1 as u64)??
    return Result.Ok(0)
```

## See Also

- [Math Module](math.md) - Mathematical operations
- [Time Module](time.md) - High-precision timing
- [Arrays](collections/arrays.md) - Array operations
