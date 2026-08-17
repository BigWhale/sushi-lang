"""Function and method call emission for LLVM IR generation."""
from sushi_lang.backend.expressions.calls.dispatcher import emit_function_call, emit_method_call, emit_fn_field_call

__all__ = ['emit_function_call', 'emit_method_call', 'emit_fn_field_call']
