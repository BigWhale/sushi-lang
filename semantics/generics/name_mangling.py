"""Name mangling utilities for generic functions.

This module provides canonical name mangling for monomorphized generic functions,
ensuring consistency between the monomorphizer and call validator.
"""

from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from semantics.types import Type


def mangle_function_name(base_name: str, type_args: Tuple['Type', ...]) -> str:
    """Generate mangled name for monomorphized generic function.

    Format: base_name + "__" + sanitized_type_args

    Examples:
        identity<i32> -> identity__i32
        swap<i32, string> -> swap__i32_string
        process<List<i32>> -> process__List_i32

    Args:
        base_name: Original function name
        type_args: Concrete type arguments

    Returns:
        Mangled function name (guaranteed unique)
    """
    if not type_args:
        return base_name

    # Convert type args to sanitized strings
    arg_strs = []
    for arg in type_args:
        # Get string representation
        type_str = str(arg)

        # Sanitize for use in identifier
        sanitized = (type_str
                     .replace('<', '_')
                     .replace('>', '')
                     .replace(',', '_')
                     .replace(' ', '')
                     .replace('[', '_arr')
                     .replace(']', '')
                     .replace('&', '_ref')
                     .replace('*', '_ptr'))

        arg_strs.append(sanitized)

    return f"{base_name}__{'_'.join(arg_strs)}"
