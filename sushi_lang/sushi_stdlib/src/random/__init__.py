"""
Random module for Sushi standard library.

Provides basic pseudo-random number generation for non-cryptographic use cases.

Available Functions:
    rand() -> u64
        Returns a random unsigned 64-bit integer.
        Range: [0, 2^64-1]

    rand_range(i32 min, i32 max) -> i32
        Returns a random integer in the range [min, max).
        Range: [min, max) (inclusive min, exclusive max)

    srand(u64 seed) -> ~
        Seeds the random number generator.
        Calling with the same seed produces the same sequence.

    rand_f64() -> f64
        Returns a random floating-point value in the range [0.0, 1.0).
        Range: [0.0, 1.0) (inclusive 0.0, exclusive 1.0)

Example Usage:
    use <random>

    fn main() i32:
        # Seed for reproducibility
        srand(42 as u64)

        # Generate random u64
        let u64 big_value = rand()
        println("Random u64: {big_value}")

        # Roll a die (1-6)
        let i32 die = rand_range(1, 7)
        println("Die roll: {die}")

        # Generate probability
        let f64 prob = rand_f64()
        println("Probability: {prob}")

        return Result.Ok(0)

Implementation Notes:
    - All functions return bare types (wrapping in Result<T> happens at semantic level)
    - Uses POSIX random() and srandom() from libc
    - NOT cryptographically secure (use crypto library for security-sensitive code)
    - NOT thread-safe (libc random() uses global state)
    - Adequate quality for games, simulations, testing
"""
from __future__ import annotations
import typing
from llvmlite import ir

if typing.TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.symbols import Signature

from sushi_lang.sushi_stdlib.src import type_converters


def is_builtin_random_function(name: str) -> bool:
    """Check if name is a built-in random module function."""
    return name in {
        'rand',
        'rand_range',
        'srand',
        'rand_f64',
    }


def get_builtin_random_function_return_type(name: str) -> Type:
    """Get the return type for a built-in random function."""
    from sushi_lang.semantics.typesys import BuiltinType

    if name == 'rand':
        return BuiltinType.U64
    elif name == 'rand_range':
        return BuiltinType.I32
    elif name == 'srand':
        return BuiltinType.BLANK  # ~ (void/blank)
    elif name == 'rand_f64':
        return BuiltinType.F64

    raise ValueError(f"Unknown random function: {name}")


def validate_random_function_call(name: str, signature: Signature) -> None:
    """Validate a call to a built-in random function."""
    from sushi_lang.semantics.typesys import BuiltinType

    if name == 'rand':
        # rand() -> u64 (no parameters)
        if len(signature.params) != 0:
            raise TypeError(f"rand expects 0 arguments, got {len(signature.params)}")

    elif name == 'rand_range':
        # rand_range(i32 min, i32 max) -> i32
        if len(signature.params) != 2:
            raise TypeError(f"rand_range expects 2 arguments, got {len(signature.params)}")

        param1_type = signature.params[0].type
        param2_type = signature.params[1].type

        if param1_type != BuiltinType.I32:
            raise TypeError(f"rand_range expects i32 for min, got {param1_type}")
        if param2_type != BuiltinType.I32:
            raise TypeError(f"rand_range expects i32 for max, got {param2_type}")

        # TODO: Add compile-time validation that min < max (requires constant evaluation)

    elif name == 'srand':
        # srand(u64 seed) -> ~
        if len(signature.params) != 1:
            raise TypeError(f"srand expects 1 argument, got {len(signature.params)}")

        param_type = signature.params[0].type
        if param_type != BuiltinType.U64:
            raise TypeError(f"srand expects u64, got {param_type}")

    elif name == 'rand_f64':
        # rand_f64() -> f64 (no parameters)
        if len(signature.params) != 0:
            raise TypeError(f"rand_f64 expects 0 arguments, got {len(signature.params)}")


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for random functions."""
    from sushi_lang.sushi_stdlib.src.random import generators
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("random")

    # Generate all random functions
    generators.generate_rand(module)
    generators.generate_rand_range(module)
    generators.generate_srand(module)
    generators.generate_rand_f64(module)

    return module
