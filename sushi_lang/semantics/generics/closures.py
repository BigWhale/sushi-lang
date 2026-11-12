"""Validation and pure helpers for the built-in methods on a function value."""
from __future__ import annotations




def is_builtin_function_method(method_name: str) -> bool:
    """Is `method_name` a built-in method on a function value?"""
    return method_name == "clone"


