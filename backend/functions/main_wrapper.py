"""
Main function wrapper handling for C compatibility.

This module handles the special case of the main() function, which needs
a C-compatible signature (int main() or int main(int argc, char** argv))
but internally calls a user_main() function that follows Sushi's Result<T>
conventions.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Tuple

from llvmlite import ir
from semantics.ast import FuncDef
from semantics.typesys import Type as Ty, ResultType
from backend import enum_utils
from backend.llvm_constants import FALSE_I1
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class MainFunctionWrapper:
    """Handles main function wrapping for C interoperability."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize wrapper with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance.
        """
        self.codegen = codegen

    def extract_value_from_result_enum(
        self,
        result_enum: ir.Value,
        value_type: ir.Type,
        semantic_type: Ty
    ) -> Tuple[ir.Value, ir.Value]:
        """Extract the Ok value from a Result<T> enum.

        Result<T> enum layout: {i32 tag, [N x i8] data}
        - tag = 0 for Ok variant, 1 for Err variant
        - data contains the packed value bytes

        Args:
            result_enum: The Result<T> enum value
            value_type: The LLVM type of T (e.g., i32 for Result<i32>)
            semantic_type: The semantic type of T (for accurate size calculation)

        Returns:
            A tuple (is_ok, value) where:
            - is_ok: i1 flag (true if Ok, false if Err)
            - value: The extracted value of type T (undefined if Err)
        """
        # Extract tag and check if Ok variant (tag == 0)
        is_ok = enum_utils.check_enum_variant(
            self.codegen, result_enum, variant_index=0, signed=True, name="is_ok"
        )

        # Extract data field (array of bytes)
        data_array = enum_utils.extract_enum_data(self.codegen, result_enum, name="result_data")

        # Create stack allocation for the value
        value_alloca = self.codegen.builder.alloca(value_type, name="result_value_temp")

        # Get pointer to data array
        # Note: data_array is a value, not a pointer, so we need to store it first
        data_alloca = self.codegen.builder.alloca(data_array.type, name="data_temp")
        self.codegen.builder.store(data_array, data_alloca)

        # Bitcast both pointers to i8*
        data_ptr = self.codegen.builder.bitcast(data_alloca, self.codegen.types.i8.as_pointer())
        dest_ptr = self.codegen.builder.bitcast(value_alloca, self.codegen.types.i8.as_pointer())

        # Get size of value type in bytes using the type system's authoritative calculation
        # This correctly handles all types including structs, dynamic arrays, and nested types
        size = self.codegen.types.get_type_size_bytes(semantic_type)
        size_const = ir.Constant(self.codegen.types.i32, size)

        # Copy bytes from data to value using LLVM memcpy intrinsic
        # Signature: void @llvm.memcpy.p0i8.p0i8.i32(i8* dest, i8* src, i32 len, i1 is_volatile)
        memcpy_fn = self.codegen.module.declare_intrinsic('llvm.memcpy', [ir.PointerType(self.codegen.types.i8), ir.PointerType(self.codegen.types.i8), self.codegen.types.i32])
        is_volatile = FALSE_I1  # Not volatile
        self.codegen.builder.call(memcpy_fn, [dest_ptr, data_ptr, size_const, is_volatile])

        # Load the unpacked value
        value = self.codegen.builder.load(value_alloca, name="result_value")

        return (is_ok, value)

    def emit_main_with_args(self, fn: FuncDef, begin_function_fn, end_function_fn, create_user_main_fn) -> ir.Function:
        """
        Emit the main function when command line arguments are expected.

        Creates a C-style main(int argc, char** argv) that converts arguments
        to a Sushi string[] and calls the user's main function.

        Args:
            fn: The user's main function definition.
            begin_function_fn: Function to begin function emission.
            end_function_fn: Function to end function emission.
            create_user_main_fn: Function to create user_main.

        Returns:
            The generated C-style main function.
        """
        # Get the C-style main function that was already declared
        c_main = self.codegen.funcs.get('main')
        if c_main is None:
            raise_internal_error("CE0064")

        # Create a user_main function with the original Sushi signature
        user_main = create_user_main_fn(fn)

        # Generate the C-style main function body
        begin_function_fn(c_main)

        # Get argc and argv parameters
        argc = c_main.args[0]  # int argc
        argv = c_main.args[1]  # char** argv

        # Convert argc/argv to Sushi string[] using the conversion utility
        args_array = self.codegen._generate_argc_argv_conversion(argc, argv)

        # Call the user_main function with the converted arguments
        # Find the args parameter position in the user's main function
        args_param_index = None
        for i, param in enumerate(fn.params):
            if param.name == "args":
                args_param_index = i
                break

        if args_param_index is None:
            raise_internal_error("CE0065")

        # Create argument list for user_main call
        user_main_args = []
        for i, param in enumerate(fn.params):
            if param.name == "args":
                # args_array is a pointer to the struct, but we need to load the struct value
                args_struct = self.codegen.builder.load(args_array, name="args_struct")
                user_main_args.append(args_struct)
            else:
                # For other parameters, create zero/null values
                param_type = self.codegen.types.ll_type(param.ty)
                if hasattr(param_type, 'intrinsic_name') and param_type.intrinsic_name.startswith('i'):
                    # Integer type
                    zero_val = ir.Constant(param_type, 0)
                elif str(param_type).endswith('*'):
                    # Pointer type
                    zero_val = ir.Constant(param_type, None)
                else:
                    # Other types - use zero
                    zero_val = ir.Constant(param_type, 0)
                user_main_args.append(zero_val)

        # Call user_main (returns Result<T>)
        result_struct = self.codegen.builder.call(user_main, user_main_args, name="user_main_result")

        # Extract the value from Result<T> enum using helper
        # Get the expected value type (T from Result<T>)
        value_type = self.codegen.types.ll_type(fn.ret)

        # Extract (is_ok, value) from Result enum (pass semantic type for accurate size calculation)
        is_ok, value = self.extract_value_from_result_enum(result_struct, value_type, fn.ret)

        # Convert value to i32 for C main() return
        if value.type != self.codegen.types.i32:
            if value.type == self.codegen.types.i8:  # i8/u8 -> i32
                # Use zext for unsigned types (u8), sext for signed (i8)
                # Since we can't distinguish here, use zext (zero-extend)
                converted_value = self.codegen.builder.zext(value, self.codegen.types.i32, name="i8_to_int")
            elif value.type == self.codegen.types.i16:  # i16/u16 -> i32
                converted_value = self.codegen.builder.sext(value, self.codegen.types.i32, name="i16_to_int")
            elif value.type == self.codegen.types.i64:  # i64/u64 -> i32 (truncate)
                converted_value = self.codegen.builder.trunc(value, self.codegen.types.i32, name="i64_to_int")
            else:
                # For other types (shouldn't happen after validation), use 0 as fallback
                converted_value = ir.Constant(self.codegen.types.i32, 0)
        else:
            converted_value = value

        # Return converted_value if Ok, 1 if Err
        # In shell conventions: 0 = success, non-zero = error
        # So Err() should return 1 (generic error), not 0
        one = ir.Constant(self.codegen.types.i32, 1)
        result = self.codegen.builder.select(is_ok, converted_value, one, name="main_exit_code")

        # Clean up cmd_args array before returning
        # (Must happen before return to avoid terminated block issue)
        if "cmd_args" in self.codegen.dynamic_arrays.arrays:
            self.codegen.dynamic_arrays._emit_array_destructor("cmd_args")
            self.codegen.dynamic_arrays.arrays["cmd_args"].destroyed = True

        # Return the result
        self.codegen.builder.ret(result)

        end_function_fn()
        return c_main

    def emit_main_without_args(self, fn: FuncDef, begin_function_fn, end_function_fn, create_user_main_fn) -> ir.Function:
        """
        Emit the main function without command line arguments.

        Creates a C-style main() that calls user_main and extracts the value
        from the Result struct.

        Args:
            fn: The user's main function definition.
            begin_function_fn: Function to begin function emission.
            end_function_fn: Function to end function emission.
            create_user_main_fn: Function to create user_main.

        Returns:
            The generated C-style main function.
        """
        # Get the C-style main function that was already declared
        c_main = self.codegen.funcs.get('main')
        if c_main is None:
            raise_internal_error("CE0064")

        # Create a user_main function with the original Sushi signature
        user_main = create_user_main_fn(fn)

        # Generate the C-style main function body
        begin_function_fn(c_main)

        # Create argument list for user_main call
        # If user's main has parameters, we need to provide zero/default values
        user_main_args = []
        for param in fn.params:
            param_type = self.codegen.types.ll_type(param.ty)
            # Create zero/default value for the parameter
            zero_val = self.codegen.utils.get_zero_value(param_type)
            user_main_args.append(zero_val)

        # Call user_main (returns Result<T>)
        result_struct = self.codegen.builder.call(user_main, user_main_args, name="user_main_result")

        # Extract the value from Result<T> enum using helper
        # Get the expected value type (T from Result<T>)
        value_type = self.codegen.types.ll_type(fn.ret)

        # Extract (is_ok, value) from Result enum (pass semantic type for accurate size calculation)
        is_ok, value = self.extract_value_from_result_enum(result_struct, value_type, fn.ret)

        # Convert value to i32 for C main() return
        if value.type != self.codegen.types.i32:
            if value.type == self.codegen.types.i8:  # i8/u8 -> i32
                # Use zext for unsigned types (u8), sext for signed (i8)
                # Since we can't distinguish here, use zext (zero-extend)
                converted_value = self.codegen.builder.zext(value, self.codegen.types.i32, name="i8_to_int")
            elif value.type == self.codegen.types.i16:  # i16/u16 -> i32
                converted_value = self.codegen.builder.sext(value, self.codegen.types.i32, name="i16_to_int")
            elif value.type == self.codegen.types.i64:  # i64/u64 -> i32 (truncate)
                converted_value = self.codegen.builder.trunc(value, self.codegen.types.i32, name="i64_to_int")
            else:
                # For other types (shouldn't happen after validation), use 0 as fallback
                converted_value = ir.Constant(self.codegen.types.i32, 0)
        else:
            converted_value = value

        # Return converted_value if Ok, 1 if Err
        # In shell conventions: 0 = success, non-zero = error
        # So Err() should return 1 (generic error), not 0
        one = ir.Constant(self.codegen.types.i32, 1)
        result = self.codegen.builder.select(is_ok, converted_value, one, name="main_exit_code")

        # Return the result
        self.codegen.builder.ret(result)

        end_function_fn()
        return c_main

    def create_user_main_function(self, fn: FuncDef, params_of_fn, begin_function_fn, end_function_fn, emit_default_return_fn) -> ir.Function:
        """
        Create a separate function for the user's main function body.

        Args:
            fn: The user's main function definition.
            params_of_fn: Function to extract parameters.
            begin_function_fn: Function to begin function emission.
            end_function_fn: Function to end function emission.
            emit_default_return_fn: Function to emit default return.

        Returns:
            The user_main LLVM function.
        """
        # Create function signature matching the user's main function
        params = params_of_fn(fn)
        ll_param_tys = [self.codegen.types.ll_type(ty) for _, ty in params]
        # Use ll_type with ResultType to get the monomorphized enum type
        std_error = self.codegen.enum_table.by_name.get("StdError")
        result_type = ResultType(ok_type=fn.ret, err_type=std_error if std_error else fn.ret)
        ll_ret = self.codegen.types.ll_type(result_type)

        fnty = ir.FunctionType(ll_ret, ll_param_tys)
        user_main = ir.Function(self.codegen.module, fnty, name="user_main")
        user_main.linkage = 'internal'  # Internal function

        # Set parameter names
        for i, (pname, _) in enumerate(params):
            user_main.args[i].name = pname

        # Emit the user's main function body
        begin_function_fn(user_main, fn)

        # Track current function AST for ?? operator (needs return type info)
        self.codegen.current_function_ast = fn

        # Track parameter types in variable_types for struct member access resolution
        for param in fn.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(fn.body)

        if self.codegen.builder.block.terminator is None:
            emit_default_return_fn(fn.ret)

        end_function_fn()

        # Clear function AST after emission
        self.codegen.current_function_ast = None

        return user_main
