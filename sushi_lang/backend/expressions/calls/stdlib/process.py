"""Standard library sys/process function call emission."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend.utils import require_builder
from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg, emit_cstr_arg

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_process_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a sys/process module function."""
    require_builder(codegen)

    i32 = ir.IntType(INT32_BIT_WIDTH)
    void = ir.VoidType()

    stdlib_func_name = f"sushi_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    string_type = codegen.types.ll_type(BuiltinType.STRING)

    if func_name == "getcwd":
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method="getcwd", expected=0, got=len(expr.args))

        # Result<string, ProcessError> type (#300 phase 2): {i32 tag, [2 x i64] data}
        # string (fat pointer) = 16 bytes; ProcessError (unit enum {i32, [1 x i64]}) = 16 bytes
        # K = max(16, 16)/8 = 2. Shared helper byte-matches the .bc.
        from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
        result_string_type = get_result_type(string_type, get_unit_enum_type())

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_string_type, [])
        result = codegen.builder.call(stdlib_func, [], name="getcwd_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "run":
        # run(string cmd, ...string args) -> Result<ProcessOutput, ProcessError>
        # Variadic: the trailing string arguments are collected (or a single `arr...`
        # bloomed) into the argv string[], via the same machinery as user variadics.
        if len(expr.args) < 1:
            raise_internal_error("CE0023", method="run", expected=1, got=len(expr.args))

        from sushi_lang.backend.expressions.calls.variadic import build_variadic_array
        from sushi_lang.semantics.typesys import DynamicArrayType

        cmd_value = emit_borrowed_arg(codegen, expr.args[0])   # string {i8*,i32}
        if isinstance(cmd_value.type, ir.PointerType):
            cmd_value = codegen.builder.load(cmd_value, name="run_cmd_val")

        args_value = build_variadic_array(
            codegen, expr.args[1:], DynamicArrayType(BuiltinType.STRING), "run",
            callee_owns=False)

        # Build Result<ProcessOutput, ProcessError> from the shared aligned-layout helper
        # so the returned type matches both the .bc and the caller's variable type
        # ({i32, [5 x i64]} -- aligned ProcessOutput size, 40 bytes -> K=5).
        from sushi_lang.sushi_stdlib.src.type_definitions import get_process_output_result_type
        result_type = get_process_output_result_type()
        argv_type = ir.LiteralStructType([i32, i32, string_type.as_pointer()])

        stdlib_func = declare_stdlib_function(
            codegen.module, stdlib_func_name, result_type, [string_type, argv_type]
        )
        result = codegen.builder.call(stdlib_func, [cmd_value, args_value], name="run_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "chdir":
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method="chdir", expected=1, got=len(expr.args))

        # Marshalled HERE and freed at scope exit, like an FFI argument (#292).
        path_cstr = emit_cstr_arg(codegen, expr.args[0])

        # Result<i32, ProcessError> type (#300 phase 2): {i32 tag, [2 x i64] data}
        # ProcessError is a unit enum {i32 tag, [1 x i64] data} = 16 bytes; i32 = 4 bytes
        # K = max(4, 16)/8 = 2. Shared helper byte-matches the .bc.
        from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
        result_i32_type = get_result_type(i32, get_unit_enum_type())

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_i32_type,
                                              [codegen.types.i8.as_pointer()])
        result = codegen.builder.call(stdlib_func, [path_cstr], name="chdir_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "exit":
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method="exit", expected=1, got=len(expr.args))

        code_value = codegen.expressions.emit_expr(expr.args[0])

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, void, [i32])
        codegen.builder.call(stdlib_func, [code_value], name="exit_call")

        # exit() never returns, so emit unreachable
        codegen.builder.unreachable()

        return ir.Constant(i32, ir.Undefined)

    elif func_name == "getpid":
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method="getpid", expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [])
        result = codegen.builder.call(stdlib_func, [], name="getpid_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "getuid":
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method="getuid", expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [])
        result = codegen.builder.call(stdlib_func, [], name="getuid_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    else:
        raise_internal_error("CE0024", type="sys/process", method=func_name)
