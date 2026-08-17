"""Extension methods for primitive types (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool,
string).
"""

# Import to_str methods to register them
import sushi_lang.backend.types.primitives.to_str  # noqa: F401
# Import hash methods to register them
import sushi_lang.backend.types.primitives.hashing  # noqa: F401
# Import bit-reinterpret methods (f32/f64 .to_bits()) to register them
import sushi_lang.backend.types.primitives.bit_reinterpret  # noqa: F401
# Import clone methods to register them
import sushi_lang.backend.types.primitives.cloning  # noqa: F401

from sushi_lang.backend.types.primitives.to_str import generate_module_ir


__all__ = [
    'generate_module_ir',
]
