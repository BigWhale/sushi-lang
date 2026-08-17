"""Extension methods for primitive types (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool,
string).
"""

import sushi_lang.backend.types.primitives.to_str  # noqa: F401
import sushi_lang.backend.types.primitives.hashing  # noqa: F401
import sushi_lang.backend.types.primitives.bit_reinterpret  # noqa: F401
import sushi_lang.backend.types.primitives.cloning  # noqa: F401

from sushi_lang.backend.types.primitives.to_str import generate_module_ir


__all__ = [
    'generate_module_ir',
]
