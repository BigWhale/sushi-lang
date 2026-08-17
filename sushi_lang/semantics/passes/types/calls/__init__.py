"""Call validation facade - maintains backward compatibility."""
from __future__ import annotations

from .user_defined import (
    validate_function_call,
    validate_open_function,
)
from .structs import validate_struct_constructor
from .enums import validate_enum_constructor
from .methods import validate_method_call

__all__ = [
    'validate_function_call',
    'validate_struct_constructor',
    'validate_enum_constructor',
    'validate_method_call',
    'validate_open_function',
]
