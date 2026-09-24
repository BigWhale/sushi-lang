"""Call emission for the <sys/process> module functions.

Every function is emitted from its row in `PROCESS_SIGNATURES` through the one signature
seam (#827), except two named special cases: `exit` never returns, so the block ends in
`unreachable`, and `run` is variadic, so its trailing arguments are collected into argv.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend.utils import require_builder
from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_registry_call
from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg
from sushi_lang.sushi_stdlib.src.sys.process import PROCESS_SIGNATURES

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_process_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a sys/process module function."""
    require_builder(codegen)

    if func_name == "run":
        return _emit_run(codegen, expr, to_i1)
    if func_name == "exit":
        return _emit_exit(codegen, expr)

    sig = PROCESS_SIGNATURES.get(func_name)
    if sig is None:
        raise_internal_error("CE0024", type="sys/process", method=func_name)
    return emit_registry_call(codegen, expr, func_name, f"sushi_{func_name}", sig, to_i1)


def _emit_run(codegen: 'LLVMCodegen', expr, to_i1: bool) -> ir.Value:
    """run(string cmd, ...string args) -> Result<ProcessOutput, ProcessError>.

    The trailing string arguments are collected (or a single `arr...` bloomed) into the
    argv string[], through the same machinery as user variadics.
    """
    from sushi_lang.backend.expressions.calls.variadic import build_variadic_array
    from sushi_lang.backend.functions import declare_stdlib_function
    from sushi_lang.semantics.typesys import DynamicArrayType
    from sushi_lang.sushi_stdlib.src.type_definitions import get_process_output_result_type

    if len(expr.args) < 1:
        raise_internal_error("CE0023", method="run", expected=1, got=len(expr.args))

    i32 = ir.IntType(INT32_BIT_WIDTH)
    string_type = codegen.types.ll_type(BuiltinType.STRING)

    cmd_value = emit_borrowed_arg(codegen, expr.args[0])
    if isinstance(cmd_value.type, ir.PointerType):
        cmd_value = codegen.builder.load(cmd_value, name="run_cmd_val")

    args_value = build_variadic_array(
        codegen, expr.args[1:], DynamicArrayType(BuiltinType.STRING), "run",
        callee_owns=False)

    # The shared aligned-layout helper, so the type matches both the .bc and the
    # caller's variable type ({i32, [5 x i64]}).
    result_type = get_process_output_result_type()
    argv_type = ir.LiteralStructType([i32, i32, string_type.as_pointer()])

    stdlib_func = declare_stdlib_function(
        codegen.module, "sushi_run", result_type, [string_type, argv_type])
    result = codegen.builder.call(stdlib_func, [cmd_value, args_value], name="run_result")
    return codegen.utils.as_i1(result) if to_i1 else result


def _emit_exit(codegen: 'LLVMCodegen', expr) -> ir.Value:
    """exit(i32 code) never returns, so the block ends in `unreachable`."""
    from sushi_lang.backend.functions import declare_stdlib_function

    if len(expr.args) != 1:
        raise_internal_error("CE0023", method="exit", expected=1, got=len(expr.args))

    i32 = ir.IntType(INT32_BIT_WIDTH)
    code_value = codegen.expressions.emit_expr(expr.args[0])
    stdlib_func = declare_stdlib_function(codegen.module, "sushi_exit", ir.VoidType(), [i32])
    codegen.builder.call(stdlib_func, [code_value], name="exit_call")
    codegen.builder.unreachable()
    return ir.Constant(i32, ir.Undefined)
