"""Standard library sys/env function call emission."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import FALSE_I1
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend.utils import require_builder
from sushi_lang.backend.expressions.calls.utils import emit_cstr_arg

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_env_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a sys/env module function."""
    require_builder(codegen)

    i32 = ir.IntType(INT32_BIT_WIDTH)

    stdlib_func_name = f"sushi_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    string_type = codegen.types.ll_type(BuiltinType.STRING)
    i8_ptr = codegen.types.i8.as_pointer()

    if func_name == "getenv":
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method="getenv", expected=1, got=len(expr.args))

        # The C string is marshalled HERE and freed at scope exit, like an FFI argument
        # (#292). The callee takes `i8*` and frees nothing.
        key_cstr = emit_cstr_arg(codegen, expr.args[0])

        # Maybe<string> type (#300 phase 2): {i32 tag, [2 x i64] data}
        # (string fat pointer = 16 bytes -> K=2). Shared helper byte-matches the .bc.
        from sushi_lang.sushi_stdlib.src.type_definitions import get_maybe_type
        maybe_string_type = get_maybe_type(string_type)

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, maybe_string_type, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [key_cstr], name="getenv_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "setenv":
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method="setenv", expected=2, got=len(expr.args))

        key_cstr = emit_cstr_arg(codegen, expr.args[0])
        value_cstr = emit_cstr_arg(codegen, expr.args[1])

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i8_ptr, i8_ptr])
        result = codegen.builder.call(stdlib_func, [key_cstr, value_cstr], name="setenv_result")

        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.results import ensure_result_type_in_table

        ok_type = BuiltinType.I32
        err_type = UnknownType("EnvError")
        result_enum = ensure_result_type_in_table(codegen.enum_table, ok_type, err_type, struct_table=codegen.struct_table.by_name)

        if result_enum:
            result_llvm_type = codegen.types.ll_type(result_enum)
            ok_variant_index = result_enum.get_variant_index("Ok")

            ok_result = ir.Constant(result_llvm_type, ir.Undefined)
            tag = ir.Constant(codegen.types.i32, ok_variant_index)
            ok_result = codegen.builder.insert_value(ok_result, tag, 0, name="ok_tag")

            data_array_type = result_llvm_type.elements[1]

            value_alloca = codegen.builder.alloca(i32, name="setenv_result_value")
            codegen.builder.store(result, value_alloca)

            data_alloca = codegen.builder.alloca(data_array_type, name="data_array")

            src_ptr = codegen.builder.bitcast(value_alloca, codegen.types.i8.as_pointer())
            dest_ptr = codegen.builder.bitcast(data_alloca, codegen.types.i8.as_pointer())

            # Copy i32 value into data array (4 bytes). i64-length llvm.memcpy so the
            # length register is never fed a value with garbage upper bits (#149/#151).
            size_const = ir.Constant(codegen.types.i64, 4)
            memcpy_fn = codegen.module.declare_intrinsic('llvm.memcpy', [
                ir.PointerType(codegen.types.i8),
                ir.PointerType(codegen.types.i8),
                codegen.types.i64
            ])
            is_volatile = FALSE_I1
            codegen.builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

            data_value = codegen.builder.load(data_alloca, name="data_value")
            ok_result = codegen.builder.insert_value(ok_result, data_value, 1, name="ok_result")

            return codegen.utils.as_i1(ok_result) if to_i1 else ok_result
        else:
            raise_internal_error("CE0091", type="Result<i32>")

    else:
        raise_internal_error("CE0024", type="sys/env", method=func_name)
