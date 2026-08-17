"""Validation utilities for common checks in code generation."""

from typing import TYPE_CHECKING, TypeVar

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

T = TypeVar('T')


def require_builder(codegen: 'LLVMCodegen') -> ir.IRBuilder:
    """Validate builder is initialized or raise CE0009."""
    if codegen.builder is None:
        raise_internal_error("CE0009")
    return codegen.builder


def require_function(codegen: 'LLVMCodegen') -> ir.Function:
    """Validate current function is set or raise CE0010."""
    if codegen.func is None:
        raise_internal_error("CE0010")
    return codegen.func


def require_non_empty(items: list[T], error_code: str) -> list[T]:
    """Validate list is non-empty or raise specified error."""
    if not items:
        raise_internal_error(error_code)
    return items


def require_both_initialized(codegen: 'LLVMCodegen') -> tuple[ir.IRBuilder, ir.Function]:
    """Validate both builder and function are initialized."""
    builder = require_builder(codegen)
    func = require_function(codegen)
    return builder, func
