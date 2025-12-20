"""
Extension methods for primitive types (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool, string).

Implemented methods:
- to_str() -> string: Convert primitive values to string representation
- hash() -> u64: Compute hash value for use in hash tables/collections

All methods are implemented as LLVM IR for optimal performance.
"""

# Import hash methods to register them
import sushi_lang.backend.types.primitives.hashing  # noqa: F401

# Re-export all public APIs from submodules
from sushi_lang.backend.types.primitives.to_str import (
    validate_builtin_primitive_method_with_validator,
    generate_module_ir,
)


def is_builtin_primitive_method(method_name: str) -> bool:
    """Check if a method name is a builtin primitive method."""
    return method_name in ("to_str", "hash")


__all__ = [
    'is_builtin_primitive_method',
    'validate_builtin_primitive_method_with_validator',
    'generate_module_ir',
]
