"""Main function wrapper handling for C compatibility."""
from __future__ import annotations
from typing import TYPE_CHECKING, Tuple

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef
from sushi_lang.semantics.typesys import Type as Ty
from sushi_lang.backend import enum_utils
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class MainFunctionWrapper:
    """Handles main function wrapping for C interoperability."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize wrapper with reference to main codegen instance."""
        self.codegen = codegen

    def extract_value_from_result_enum(
        self,
        result_enum: ir.Value,
        value_type: ir.Type,
        semantic_type: Ty
    ) -> Tuple[ir.Value, ir.Value]:
        """Extract the Ok value from a Result<T> enum."""
        is_ok = enum_utils.check_enum_variant(
            self.codegen, result_enum, variant_index=0, signed=True, name="is_ok"
        )

        # Extract data field (array of bytes) and read the payload back through a typed
        # pointer into that byte buffer, mirroring the match-binding path
        # (statements/matching.py). We deliberately do NOT memcpy the [N x i8] blob into a
        # separate value-typed alloca and load that: the extra byte-array<->struct round
        # trip miscompiled the trailing field of a struct payload containing nested string
        # fat-pointers at -O1/-O2 (issue #119: `run()?? ProcessOutput.stderr_text` read a
        # garbage {data,len} and segfaulted, while the match/realise paths were unaffected).
        # A single typed load from the variant buffer is both robust and cheaper.
        data_array = enum_utils.extract_enum_data(self.codegen, result_enum, name="result_data")
        data_alloca = self.codegen.builder.alloca(data_array.type, name="result_data_slot")
        self.codegen.builder.store(data_array, data_alloca)

        value_ptr = self.codegen.builder.bitcast(data_alloca, value_type.as_pointer())
        # Natural alignment: the payload slot is a [K x i64] array (#300 phase 2), so
        # the alloca is 8-aligned and the packed-layout `align=1` (#145) is gone.
        value = self.codegen.builder.load(value_ptr, name="result_value")

        return (is_ok, value)

    def emit_main_with_args(self, fn: FuncDef, begin_function_fn, end_function_fn, create_user_main_fn) -> ir.Function:
        """Emit the main function when command line arguments are expected."""
        c_main = self.codegen.funcs.get('main')
        if c_main is None:
            raise_internal_error("CE0064")

        user_main = create_user_main_fn(fn)

        begin_function_fn(c_main)

        argc = c_main.args[0]  # int argc
        argv = c_main.args[1]  # char** argv

        args_array = self.codegen._generate_argc_argv_conversion(argc, argv)

        args_param_index = None
        for i, param in enumerate(fn.params):
            if param.name == "args":
                args_param_index = i
                break

        if args_param_index is None:
            raise_internal_error("CE0065")

        user_main_args = []
        for _i, param in enumerate(fn.params):
            if param.name == "args":
                args_struct = self.codegen.builder.load(args_array, name="args_struct")
                user_main_args.append(args_struct)
            else:
                param_type = self.codegen.types.ll_type(param.ty)
                if hasattr(param_type, 'intrinsic_name') and param_type.intrinsic_name.startswith('i'):
                    zero_val = ir.Constant(param_type, 0)
                elif str(param_type).endswith('*'):
                    zero_val = ir.Constant(param_type, None)
                else:
                    zero_val = ir.Constant(param_type, 0)
                user_main_args.append(zero_val)

        result_struct = self.codegen.builder.call(user_main, user_main_args, name="user_main_result")

        value_type = self.codegen.types.ll_type(fn.ret)

        is_ok, value = self.extract_value_from_result_enum(result_struct, value_type, fn.ret)

        if value.type != self.codegen.types.i32:
            if value.type == self.codegen.types.i8:  # i8/u8 -> i32
                converted_value = self.codegen.builder.zext(value, self.codegen.types.i32, name="i8_to_int")
            elif value.type == self.codegen.types.i16:  # i16/u16 -> i32
                converted_value = self.codegen.builder.sext(value, self.codegen.types.i32, name="i16_to_int")
            elif value.type == self.codegen.types.i64:  # i64/u64 -> i32 (truncate)
                converted_value = self.codegen.builder.trunc(value, self.codegen.types.i32, name="i64_to_int")
            else:
                converted_value = ir.Constant(self.codegen.types.i32, 0)
        else:
            converted_value = value

        # Return converted_value if Ok, 1 if Err
        # In shell conventions: 0 = success, non-zero = error
        # So Err() should return 1 (generic error), not 0
        one = ir.Constant(self.codegen.types.i32, 1)
        result = self.codegen.builder.select(is_ok, converted_value, one, name="main_exit_code")

        cmd_args_desc = self.codegen.dynamic_arrays._array("cmd_args")
        if cmd_args_desc is not None:
            self.codegen.dynamic_arrays._emit_array_destructor("cmd_args")
            cmd_args_desc.destroyed = True

        self.codegen.builder.ret(result)

        end_function_fn()
        return c_main

    def emit_main_without_args(self, fn: FuncDef, begin_function_fn, end_function_fn, create_user_main_fn) -> ir.Function:
        """Emit the main function without command line arguments."""
        c_main = self.codegen.funcs.get('main')
        if c_main is None:
            raise_internal_error("CE0064")

        user_main = create_user_main_fn(fn)

        begin_function_fn(c_main)

        user_main_args = []
        for param in fn.params:
            param_type = self.codegen.types.ll_type(param.ty)
            zero_val = self.codegen.utils.get_zero_value(param_type)
            user_main_args.append(zero_val)

        result_struct = self.codegen.builder.call(user_main, user_main_args, name="user_main_result")

        value_type = self.codegen.types.ll_type(fn.ret)

        is_ok, value = self.extract_value_from_result_enum(result_struct, value_type, fn.ret)

        if value.type != self.codegen.types.i32:
            if value.type == self.codegen.types.i8:  # i8/u8 -> i32
                converted_value = self.codegen.builder.zext(value, self.codegen.types.i32, name="i8_to_int")
            elif value.type == self.codegen.types.i16:  # i16/u16 -> i32
                converted_value = self.codegen.builder.sext(value, self.codegen.types.i32, name="i16_to_int")
            elif value.type == self.codegen.types.i64:  # i64/u64 -> i32 (truncate)
                converted_value = self.codegen.builder.trunc(value, self.codegen.types.i32, name="i64_to_int")
            else:
                converted_value = ir.Constant(self.codegen.types.i32, 0)
        else:
            converted_value = value

        # Return converted_value if Ok, 1 if Err
        # In shell conventions: 0 = success, non-zero = error
        # So Err() should return 1 (generic error), not 0
        one = ir.Constant(self.codegen.types.i32, 1)
        result = self.codegen.builder.select(is_ok, converted_value, one, name="main_exit_code")

        self.codegen.builder.ret(result)

        end_function_fn()
        return c_main

    def create_user_main_function(self, fn: FuncDef, params_of_fn, begin_function_fn, end_function_fn, emit_default_return_fn) -> ir.Function:
        """Create a separate function for the user's main function body."""
        params = params_of_fn(fn)
        ll_param_tys = [self.codegen.types.ll_type(ty) for _, ty in params]
        from sushi_lang.backend.generics.result_builder import intern_result
        std_error = self.codegen.enum_table.by_name.get("StdError")
        result_type = intern_result(self.codegen, fn.ret, std_error if std_error else fn.ret)
        ll_ret = self.codegen.types.ll_type(result_type)

        fnty = ir.FunctionType(ll_ret, ll_param_tys)
        user_main = ir.Function(self.codegen.module, fnty, name="user_main")
        user_main.linkage = 'internal'  # Internal function

        for i, (pname, _) in enumerate(params):
            user_main.args[i].name = pname

        begin_function_fn(user_main, fn)

        self.codegen.current_function_ast = fn

        for param in fn.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(fn.body)

        if self.codegen.builder.block.terminator is None:
            emit_default_return_fn(fn.ret)

        end_function_fn()

        self.codegen.current_function_ast = None

        return user_main
