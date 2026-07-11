"""
Type-specific functionality for Sushi Lang.

This package contains extension methods and type-specific utilities for:
- Primitive types (integers, floats, boolean, string)
- Array types (fixed and dynamic arrays)
- Struct and enum types

This is separate from backend/expressions/ which handles expression emission.

Importing this package populates the shared builtin-method registry
(sushi_stdlib/src/common.py) with everything the backend needs to *emit*:

- the primitive methods (to_str/hash/to_bits), with their emitters. Pass 2 does
  not read these -- it decides which primitive methods exist from
  semantics/generics/primitives.py, since the pipeline imports the backend only
  after semantic analysis. tests/unit/test_primitive_methods.py keeps the two
  tables in sync;
- the hash() emitter factories for structs, enums and arrays, which
  semantics/generics/hashing.py resolves at emission time. Pass 1.8 decides
  hashability and registers the method; these modules supply its LLVM body.
"""

from sushi_lang.backend.types import primitives  # noqa: F401
from sushi_lang.backend.types import structs  # noqa: F401
from sushi_lang.backend.types import enums  # noqa: F401
from sushi_lang.backend.types.arrays.methods import hashing  # noqa: F401
