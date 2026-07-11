"""
Extension methods for primitive types (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool, string).

Implemented methods:
- to_str() -> string: Convert primitive values to string representation
- hash() -> u64: Compute hash value for use in hash tables/collections
- to_bits() -> u32/u64: Raw IEEE-754 encoding (f32/f64 only)

All methods are implemented as LLVM IR for optimal performance.

Importing this package registers those methods -- validator and emitter both --
into the shared builtin-method registry (sushi_stdlib/src/common.py). Pass 2
dispatches primitive-method validation through that registry, so the registration
must have happened before semantic analysis runs; backend/types/__init__.py
imports this package to guarantee it.
"""

# Import to_str methods to register them
import sushi_lang.backend.types.primitives.to_str  # noqa: F401
# Import hash methods to register them
import sushi_lang.backend.types.primitives.hashing  # noqa: F401
# Import bit-reinterpret methods (f32/f64 .to_bits()) to register them
import sushi_lang.backend.types.primitives.bit_reinterpret  # noqa: F401

from sushi_lang.backend.types.primitives.to_str import generate_module_ir


__all__ = [
    'generate_module_ir',
]
