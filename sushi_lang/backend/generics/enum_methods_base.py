"""Unified generic enum method implementations for Result<T> and Maybe<T>."""
from __future__ import annotations

from typing import TYPE_CHECKING
import llvmlite.ir as ir

from sushi_lang.backend import enum_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_enum_tag_check(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    expected_tag: int,
    check_name: str
) -> ir.Value:
    """Extract enum tag and compare to expected value."""
    return enum_utils.check_enum_variant(
        codegen, enum_value, expected_tag, signed=False, name=check_name
    )

