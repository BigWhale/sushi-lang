"""
Type-specific functionality for Sushi Lang.

This package contains extension methods and type-specific utilities for:
- Primitive types (integers, floats, boolean, string)
- Array types (fixed and dynamic arrays)
- Struct and enum types

This is separate from backend/expressions/ which handles expression emission.

Importing this package registers the hash() emitter factories that
semantics/generics/hashing.py resolves at emission time. Pass 1.8 decides
hashability and registers the method; these modules supply its LLVM body.
"""

from sushi_lang.backend.types import structs  # noqa: F401
from sushi_lang.backend.types import enums  # noqa: F401
from sushi_lang.backend.types.arrays.methods import hashing  # noqa: F401
