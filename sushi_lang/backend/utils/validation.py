"""Validation utilities for common checks in code generation."""

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def require_builder(codegen: 'LLVMCodegen') -> ir.IRBuilder:
    """Validate builder is initialized or raise CE0009."""
    if codegen.builder is None:
        raise_internal_error("CE0009")
    return codegen.builder

