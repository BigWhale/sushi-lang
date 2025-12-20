"""
Function and method call emission for LLVM IR generation.

This package handles the emission of function calls and method calls,
including extension methods with UFCS (Uniform Function Call Syntax).
"""
from sushi_lang.backend.expressions.calls.dispatcher import emit_function_call, emit_method_call

__all__ = ['emit_function_call', 'emit_method_call']
