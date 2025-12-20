"""
Random number generator implementations for Sushi stdlib.

Implements RNG functions with varying types and ranges:
- rand: Core implementation returning u64
- rand_range: Convenience wrapper for bounded i32 range
- srand: Seed the generator
- rand_f64: Floating-point random in [0.0, 1.0)

All functions use POSIX random() and srandom() under the hood.
"""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types

# Get platform-specific random module (darwin, linux, etc.)
_platform_random = get_platform_module('random')

if typing.TYPE_CHECKING:
    pass


def generate_rand(module: ir.Module) -> None:
    """Generate rand function: rand() -> u64

    Returns a random unsigned 64-bit integer using libc random().

    Implementation Strategy:
        libc random() returns 31-bit values [0, 2^31-1]
        To get 64 bits, we call random() twice and combine:
        1. Call random() to get low 31 bits
        2. Call random() to get high 31 bits
        3. Combine: (high << 31) | low
        4. This gives us 62 bits of randomness (good enough for u64)

    Note: We could call random() three times for full 64 bits, but
    62 bits provides adequate randomness for non-crypto use cases.
    """
    # Get common types
    _, _, i32, i64 = get_basic_types()

    # Declare external libc random (returns long/i64)
    libc_random = _platform_random.declare_random(module)

    # Define OUR function signature: sushi_rand() -> u64
    # Note: We use sushi_ prefix to avoid name collision with libc rand()
    func_type = ir.FunctionType(i64, [])
    func = ir.Function(module, func_type, name="sushi_rand")

    # Create entry block
    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call random() for low 31 bits
    low_i64 = builder.call(libc_random, [], name="random_low")

    # Call random() for high 31 bits
    high_i64 = builder.call(libc_random, [], name="random_high")

    # Shift high bits left by 31: high << 31
    shift_amount = ir.Constant(i64, 31)
    high_shifted = builder.shl(high_i64, shift_amount, name="high_shifted")

    # Combine: (high << 31) | low
    result = builder.or_(high_shifted, low_i64, name="combined")

    builder.ret(result)


def generate_rand_range(module: ir.Module) -> None:
    """Generate rand_range function: rand_range(i32 min, i32 max) -> i32

    Returns a random integer in the range [min, max).

    Implementation:
        result = min + (rand() % (max - min))

    Algorithm Limitations:
        - Uses modulo bias (slightly non-uniform distribution)
        - For small ranges, bias is negligible
        - For large ranges approaching 2^32, bias becomes noticeable
        - Future enhancement: Use rejection sampling for unbiased results

    Validation:
        - Compile-time check: min < max (enforced by validator)
        - Runtime: Assumes valid range (no bounds checks for performance)
    """
    _, _, i32, i64 = get_basic_types()

    # Get sushi_rand function (should already be defined)
    rand_func = module.globals.get("sushi_rand")
    if rand_func is None:
        raise RuntimeError("sushi_rand must be defined before rand_range")

    # Define function signature: sushi_rand_range(i32 min, i32 max) -> i32
    func_type = ir.FunctionType(i32, [i32, i32])
    func = ir.Function(module, func_type, name="sushi_rand_range")

    min_param = func.args[0]
    max_param = func.args[1]
    min_param.name = "min"
    max_param.name = "max"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Calculate range: max - min
    range_i32 = builder.sub(max_param, min_param, name="range")

    # Get random u64
    rand_u64 = builder.call(rand_func, [], name="rand_value")

    # Truncate to i32 (we only need 32 bits for the modulo)
    rand_i32 = builder.trunc(rand_u64, i32, name="rand_i32")

    # Calculate: rand_i32 % range
    # Use unsigned remainder to avoid issues with negative values
    remainder = builder.urem(rand_i32, range_i32, name="remainder")

    # Calculate: min + remainder
    result = builder.add(min_param, remainder, name="result")

    builder.ret(result)


def generate_srand(module: ir.Module) -> None:
    """Generate srand function: srand(u64 seed) -> ~

    Seeds the random number generator with the given value.
    Calling this function with the same seed produces the same sequence.

    Implementation:
        Truncate u64 seed to u32 (unsigned int) and pass to libc srandom()

    Note: libc srandom() takes unsigned int (32 bits), but Sushi uses
    u64 for consistency. We truncate the high bits.
    """
    _, _, i32, i64 = get_basic_types()
    void = ir.VoidType()

    # Declare external libc srandom (takes unsigned int/i32)
    libc_srandom = _platform_random.declare_srandom(module)

    # Define OUR function signature: sushi_srand(u64 seed) -> void
    func_type = ir.FunctionType(void, [i64])
    func = ir.Function(module, func_type, name="sushi_srand")

    seed_param = func.args[0]
    seed_param.name = "seed"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Truncate u64 to u32 (i64 to i32)
    seed_i32 = builder.trunc(seed_param, i32, name="seed_i32")

    # Call libc srandom(seed_i32)
    builder.call(libc_srandom, [seed_i32])

    builder.ret_void()


def generate_rand_f64(module: ir.Module) -> None:
    """Generate rand_f64 function: rand_f64() -> f64

    Returns a random floating-point value in the range [0.0, 1.0).

    Implementation:
        1. Get random u64
        2. Convert to f64
        3. Divide by 2^64 to normalize to [0.0, 1.0)

    Precision:
        - f64 has 53-bit mantissa (IEEE 754 double precision)
        - u64 has 64 bits, so we lose 11 bits of precision
        - Resulting distribution has 2^53 distinct values in [0.0, 1.0)
        - This is adequate for most applications

    Note: For cryptographic applications, use a dedicated crypto library.
    """
    _, _, _, i64 = get_basic_types()
    f64 = ir.DoubleType()

    # Get sushi_rand function (should already be defined)
    rand_func = module.globals.get("sushi_rand")
    if rand_func is None:
        raise RuntimeError("sushi_rand must be defined before rand_f64")

    # Define function signature: sushi_rand_f64() -> f64
    func_type = ir.FunctionType(f64, [])
    func = ir.Function(module, func_type, name="sushi_rand_f64")

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Get random u64
    rand_u64 = builder.call(rand_func, [], name="rand_value")

    # Convert u64 to f64 (unsigned conversion)
    rand_f64 = builder.uitofp(rand_u64, f64, name="rand_f64")

    # Divide by 2^64 to normalize to [0.0, 1.0)
    # 2^64 = 18446744073709551616.0
    max_u64_plus_1 = ir.Constant(f64, 18446744073709551616.0)
    result = builder.fdiv(rand_f64, max_u64_plus_1, name="normalized")

    builder.ret(result)
