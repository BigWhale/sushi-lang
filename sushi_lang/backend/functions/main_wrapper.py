"""Main function wrapper handling for C compatibility."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef
from sushi_lang.semantics.typesys import Type as Ty
from sushi_lang.backend import enum_utils
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.runtime.constants import IONBF

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
    ) -> tuple[ir.Value, ir.Value]:
        """Extract the Ok value from a Result<T> enum."""
        is_ok = enum_utils.check_enum_variant(
            self.codegen, result_enum, variant_index=0, signed=True, name="is_ok"
        )

        # Read the payload back through a typed pointer INTO the byte buffer. Do NOT memcpy
        # the [N x i8] blob into a value-typed alloca and load that: the round trip
        # miscompiled the trailing field of a payload holding nested string fat pointers at
        # -O1/-O2 (#119). One typed load is both robust and cheaper.
        # The slot is scratch: the payload is loaded straight back out, so one slot per
        # site is enough. Where it goes is `backend/memory/allocas.py`'s to say (#589).
        data_array = enum_utils.extract_enum_data(self.codegen, result_enum, name="result_data")
        data_alloca = self.codegen.memory.entry_alloca(data_array.type, "result_data_slot")
        self.codegen.builder.store(data_array, data_alloca)

        value_ptr = self.codegen.builder.bitcast(data_alloca, value_type.as_pointer())
        # Natural alignment: the payload slot is a [K x i64] array (#300 phase 2), so
        # the alloca is 8-aligned and the packed-layout `align=1` (#145) is gone.
        value = self.codegen.builder.load(value_ptr, name="result_value")

        return (is_ok, value)

    def _unbuffer_libc_stdio(self) -> None:
        """Take the buffer off libc stdout, so a stdio write keeps its place in line.

        Sushi's own output goes to the descriptor and never through stdio (Phase 5), so
        this call is not for `print` or `println`. It is for the OTHER writer: a program
        that binds libc `printf` through an `unsafe external` block. A buffered stdio
        byte and a descriptor byte arrive in flush order rather than in call order when
        the stream is a pipe, and the program that wrote them cannot see why.

        `_IONBF` is 2 on macOS and on glibc alike, verified in both headers rather than
        remembered. A program that wants the buffer back may ask for it: this runs
        before the first user statement.
        """
        stdio = self.codegen.runtime.libc_stdio
        i32 = self.codegen.types.i32
        stream = self.codegen.builder.load(stdio.stdout_handle, name="stdio_stdout")
        self.codegen.builder.call(stdio.setvbuf, [
            stream,
            ir.Constant(self.codegen.types.i8.as_pointer(), None),
            ir.Constant(i32, IONBF),
            ir.Constant(self.codegen.types.i64, 0),
        ])

    def emit_main(self, fn: FuncDef) -> ir.Function:
        """Emit the C `main` that calls the user's main and returns its exit code."""
        c_main = self.codegen.funcs.get('main')
        if c_main is None:
            raise_internal_error("CE0064")

        user_main = self.create_user_main_function(fn)

        self.codegen.functions.helpers.begin_function(c_main)
        self._unbuffer_libc_stdio()

        # The entrypoint pass admits `string[] args` or nothing (CE0138), so argv is the
        # one possible argument.
        user_main_args: list[ir.Value] = []
        if self.codegen.main_expects_args:
            argc, argv = c_main.args[0], c_main.args[1]
            args_array = self.codegen._generate_argc_argv_conversion(argc, argv)
            user_main_args.append(self.codegen.builder.load(args_array, name="args_struct"))

        self._return_exit_code(fn, user_main, user_main_args)

        self.codegen.functions.helpers.end_function()
        return c_main

    def _return_exit_code(self, fn: FuncDef, user_main: ir.Function,
                          user_main_args: list[ir.Value]) -> None:
        """Call the user's main and return its Ok value as an i32, or 1 for an Err."""
        result_struct = self.codegen.builder.call(user_main, user_main_args, name="user_main_result")

        value_type = self.codegen.types.ll_type(fn.ret)
        is_ok, value = self.extract_value_from_result_enum(result_struct, value_type, fn.ret)

        i32 = self.codegen.types.i32
        if value.type == i32:
            converted_value = value
        elif value.type == self.codegen.types.i8:  # i8/u8 -> i32
            converted_value = self.codegen.builder.zext(value, i32, name="i8_to_int")
        elif value.type == self.codegen.types.i16:  # i16/u16 -> i32
            converted_value = self.codegen.builder.sext(value, i32, name="i16_to_int")
        elif value.type == self.codegen.types.i64:  # i64/u64 -> i32 (truncate)
            converted_value = self.codegen.builder.trunc(value, i32, name="i64_to_int")
        else:
            converted_value = ir.Constant(i32, 0)

        one = ir.Constant(i32, 1)
        result = self.codegen.builder.select(is_ok, converted_value, one, name="main_exit_code")

        cmd_args_desc = self.codegen.dynamic_arrays._array("cmd_args")
        if cmd_args_desc is not None:
            self.codegen.dynamic_arrays.emit_array_destructor("cmd_args")
            cmd_args_desc.destroyed = True

        self.codegen.builder.ret(result)

    def create_user_main_function(self, fn: FuncDef) -> ir.Function:
        """Create a separate function for the user's main function body."""
        params = self.codegen.functions.helpers.params_of(fn)
        ll_param_tys = [self.codegen.types.ll_type(ty) for _, ty in params]
        from sushi_lang.backend.functions.helpers import declared_result_of
        ll_ret = self.codegen.types.ll_type(declared_result_of(self.codegen, fn))

        fnty = ir.FunctionType(ll_ret, ll_param_tys)
        user_main = ir.Function(self.codegen.module, fnty, name="user_main")
        user_main.linkage = 'internal'

        for i, (pname, _) in enumerate(params):
            user_main.args[i].name = pname

        self.codegen.functions.definitions.emit_body(user_main, fn)
        return user_main
