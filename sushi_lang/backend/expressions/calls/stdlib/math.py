"""Standard library math function call emission."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_math_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a math module function."""
    require_builder(codegen)

    from sushi_lang.backend.functions import declare_stdlib_function

    args = [codegen.expressions.emit_expr(arg) for arg in expr.args]

    if func_name in {'abs', 'min', 'max'} and args:
        arg_type = args[0].type
        type_suffix = _get_math_type_suffix(arg_type, _is_unsigned_argument(codegen, expr.args[0]))
        stdlib_func_name = f"sushi_{func_name}_{type_suffix}"

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, arg_type, [arg_type] * len(args))
    elif func_name in {
        'sqrt', 'floor', 'ceil', 'round', 'trunc',
        'sin', 'cos', 'tan',
        'asin', 'acos', 'atan',
        'sinh', 'cosh', 'tanh',
        'log', 'log2', 'log10',
        'exp', 'exp2',
    }:
        f64 = ir.DoubleType()
        stdlib_func_name = f"sushi_{func_name}"
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, f64, [f64])
        if args and args[0].type != f64:
            from sushi_lang.backend.expressions.casts import cast_int_to_float, cast_float_to_float
            if isinstance(args[0].type, ir.IntType):
                args[0] = cast_int_to_float(codegen, args[0], f64)
            else:
                args[0] = cast_float_to_float(codegen, args[0], f64)
    elif func_name in {'pow', 'atan2', 'hypot'}:
        f64 = ir.DoubleType()
        stdlib_func_name = f"sushi_{func_name}"
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, f64, [f64, f64])
        from sushi_lang.backend.expressions.casts import cast_int_to_float, cast_float_to_float
        for i in range(len(args)):
            if args[i].type != f64:
                if isinstance(args[i].type, ir.IntType):
                    args[i] = cast_int_to_float(codegen, args[i], f64)
                else:
                    args[i] = cast_float_to_float(codegen, args[i], f64)
    else:
        raise ValueError(f"Unknown math function: {func_name}")

    result = codegen.builder.call(stdlib_func, args, name=f"{func_name}_result")
    return codegen.utils.as_i1(result) if to_i1 else result


def _is_unsigned_argument(codegen: 'LLVMCodegen', arg) -> bool:
    """An LLVM integer carries no sign, so the signedness comes from the semantic type."""
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type, is_unsigned_type
    return is_unsigned_type(infer_expr_semantic_type(codegen, arg))


def _get_math_type_suffix(llvm_type, unsigned: bool) -> str:
    """Get the type suffix for polymorphic math functions."""
    if isinstance(llvm_type, ir.IntType) and llvm_type.width in (8, 16, 32, 64):
        return f"{'u' if unsigned else 'i'}{llvm_type.width}"
    elif isinstance(llvm_type, ir.FloatType):
        return 'f32'
    elif isinstance(llvm_type, ir.DoubleType):
        return 'f64'

    raise ValueError(f"Unsupported type for math function: {llvm_type}")
