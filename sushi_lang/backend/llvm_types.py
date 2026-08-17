"""LLVM type system management for the Sushi language compiler."""
from __future__ import annotations

from sushi_lang.backend.types.core import LLVMTypeSystem


class TypeSystemWrapper:
    """Minimal codegen wrapper for generic type helpers."""
    def __init__(self, types_system, struct_table, enum_table):
        self.types = types_system
        self.struct_table = struct_table
        self.enum_table = enum_table


__all__ = ['LLVMTypeSystem', 'TypeSystemWrapper']
