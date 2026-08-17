"""Common infrastructure for standalone LLVM IR generation in stdlib modules."""

import llvmlite.ir as ir


def create_stdlib_module(name: str) -> ir.Module:
    """Create a new LLVM module for stdlib with standard naming and settings."""
    module = ir.Module(name=f"stdlib.{name}")
    module.triple = ""  # Use default target triple
    return module
