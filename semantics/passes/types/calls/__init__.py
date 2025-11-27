# semantics/passes/types/calls/__init__.py
"""
Call validation facade - maintains backward compatibility.

This module provides the public API for call validation while delegating
to specialized modules for different call types.

Public API:
- validate_function_call: User-defined and stdlib function calls
- validate_struct_constructor: Struct constructor calls
- validate_enum_constructor: Enum variant constructor calls
- validate_method_call: Method calls (extension, builtin, perk)
- validate_open_function: Built-in open() function

Internal modules:
- user_defined: User-defined and stdlib function calls
- structs: Struct constructor validation
- enums: Enum constructor validation
- generics: Generic function call validation
- methods: Method call validation
"""
from __future__ import annotations

# Re-export public API for backward compatibility
from .user_defined import (
    validate_function_call,
    validate_open_function,
    check_stdlib_function as _check_stdlib_function,
    validate_stdlib_function as _validate_stdlib_function,
)
from .structs import validate_struct_constructor
from .enums import validate_enum_constructor
from .methods import validate_method_call
from .generics import (
    validate_generic_function_call as _validate_generic_function_call,
    validate_call_arguments as _validate_call_arguments,
)

__all__ = [
    'validate_function_call',
    'validate_struct_constructor',
    'validate_enum_constructor',
    'validate_method_call',
    'validate_open_function',
]
