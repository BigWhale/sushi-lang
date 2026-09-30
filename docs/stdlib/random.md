# Random Module

[← Back to Standard Library](../standard-library.md)

Provides basic pseudo-random number generation for non-cryptographic use cases.

## Import

```sushi
use <random>
```

Each function returns a bare value, not a `Result`. The signature lines below give the
parameter and return types; they are not Sushi declarations (a declared `fn` returns a
`Result`).

## Functions

### rand()

Returns a random unsigned 64-bit integer.

**Signature:**
```text
rand() u64
```

**Returns:** A random value in the range [0, 2^62-1]. The value comes from two 31-bit
`random()` calls, so the two high bits are always zero.

**Example:**
```sushi
use <random>

fn main() i32:
    let u64 value = rand()
    println("Random u64: {value}")
    return 0
```

### rand_range()

Returns a random integer in the range [min, max).

**Signature:**
```text
rand_range(i32 min, i32 max) i32
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

    return 0
```

### rand_f64()

Returns a random floating-point value.

**Signature:**
```text
rand_f64() f64
```

**Returns:** A value where `0.0 <= result < 0.25`. The function divides the value of
`rand()` by 2^64, and `rand()` gives only 62 bits, so the result never reaches 0.25. Do
not scale it to a range on the assumption that it covers `[0.0, 1.0)`.

**Example:**
```sushi
use <random>

fn main() i32:
    let f64 sample = rand_f64()
    println("Sample: {sample}")

    return 0
```

### srand()

Seeds the random number generator for reproducible sequences.

**Signature:**
```text
srand(u64 seed) ~
```

**Parameters:**
- `seed` - Seed value (same seed produces same sequence). Only the low 32 bits of the
  seed are used, so two seeds that differ only in the high 32 bits give the same sequence.

**Returns:** Blank type (`~`)

**Example:**
```sushi
use <random>

fn main() i32:
    # Seed for reproducibility
    srand(42)

    # These will be the same every run with seed 42
    let i32 a = rand_range(1, 100)
    let i32 b = rand_range(1, 100)
    let i32 c = rand_range(1, 100)

    println("Sequence: {a}, {b}, {c}")

    return 0
```

## Implementation Notes

**Algorithm:**
- Uses POSIX `random()` and `srandom()` from libc. The algorithm is the one that the C
  library of the platform uses (on macOS and glibc, an additive feedback generator)
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
- `rand()` gives 62 random bits (two 31-bit `random()` calls)
- `rand_f64()` gives values in `[0.0, 0.25)` (see above)

**Portability:**
- POSIX-compliant systems only (Unix, Linux, macOS, BSD)
- Not available on Windows (requires POSIX compatibility layer)

## Common Patterns

### Random Boolean

```sushi
use <random>

fn coin_flip() bool:
    return rand_range(0, 2) == 1

fn main() i32:
    if (coin_flip()):
        println("Heads")
    else:
        println("Tails")
    return 0
```

### Random Element from Array

```sushi
use <random>

fn main() i32:
    let string[] choices = from(["Rock", "Paper", "Scissors"])
    let i32 index = rand_range(0, choices.len())
    let string choice = choices[index]
    println("Choice: {choice}")
    return 0
```

### Reproducible Random Sequences

```sushi
use <random>

fn generate_level(u64 level_seed) i32[] | StdError:
    # Same seed always generates same level
    srand(level_seed)

    let i32[] terrain = from([])
    foreach(i in 0..100):
        terrain.push(rand_range(0, 10))

    return Result.Ok(terrain)

fn main() i32:
    # Level 1 will always have the same terrain
    match generate_level(1):
        Result.Ok(level1) -> println("{level1.len()} tiles")
        Result.Err(_) -> println("no level")
    return 0
```

## See Also

- [Math Module](math.md) - Mathematical operations
- [Time Module](time.md) - Sleep and clocks
- [Arrays](collections/arrays.md) - Array operations
