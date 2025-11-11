"""
LLVM function management for the Sushi language compiler.

This module handles function declaration, definition, parameter processing,
and extension method support. It manages the function creation lifecycle
from declaration through body emission and cleanup.
"""
from __future__ import annotations
from typing import List, Tuple

from llvmlite import ir
from semantics.ast import FuncDef, ExtendDef, Param
from semantics.typesys import Type as Ty, BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType, UnknownType, ReferenceType
from backend import enum_utils
from backend.llvm_constants import FALSE_I1
from internals.errors import raise_internal_error


def declare_stdlib_function(
    module: ir.Module,
    func_name: str,
    return_type: ir.Type,
    param_types: list[ir.Type]
) -> ir.Function:
    """Declare an external stdlib function.

    This declares a function that will be linked from a stdlib .bc file.
    If the function already exists in the module, returns the existing declaration.

    Args:
        module: The LLVM module to declare the function in.
        func_name: Name of the stdlib function (e.g., "sushi_i32_to_str")
        return_type: LLVM return type
        param_types: List of LLVM parameter types

    Returns:
        The declared function (or existing if already declared)
    """
    # Check if already declared
    if func_name in module.globals:
        existing = module.globals[func_name]
        if isinstance(existing, ir.Function):
            return existing

    # Declare new external function
    fn_type = ir.FunctionType(return_type, param_types)
    func = ir.Function(module, fn_type, name=func_name)
    # External linkage is default, no need to set explicitly
    return func


class LLVMFunctionManager:
    """Handles LLVM function declaration, definition, and management."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize function manager with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

    def _is_valid_param_type(self, param_type: Ty) -> bool:
        """Check if a type is valid for function parameters.

        Args:
            param_type: The type to validate.

        Returns:
            True if the type can be used as a function parameter.
        """
        # Check for builtin types
        if param_type in (
            BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
            BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
            BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
        ):
            return True

        # Check for array types
        if isinstance(param_type, (ArrayType, DynamicArrayType)):
            return True

        # Check for struct types
        if isinstance(param_type, StructType):
            return True

        # Check for enum types
        if isinstance(param_type, EnumType):
            return True

        # Check for reference types
        if isinstance(param_type, ReferenceType):
            return True

        # Check for UnknownType that could be a struct or enum
        if isinstance(param_type, UnknownType):
            # Check if this unknown type is in the struct table
            if hasattr(self.codegen, 'struct_table') and param_type.name in self.codegen.struct_table.by_name:
                return True
            # Check if this unknown type is in the enum table
            if hasattr(self.codegen, 'enum_table') and param_type.name in self.codegen.enum_table.by_name:
                return True

        # Check for generic type references (should be monomorphized by type checker)
        from semantics.generics.types import GenericTypeRef
        if isinstance(param_type, GenericTypeRef):
            # GenericTypeRef is valid - ll_type() will resolve it to monomorphized enum
            return True

        return False

    def emit_func_decl(self, fn: FuncDef) -> ir.Function:
        """Create LLVM function prototype for regular function.

        Args:
            fn: The function definition AST node.

        Returns:
            The LLVM function with declared signature (no body).
        """
        existing = self.codegen.funcs.get(fn.name)
        if existing is not None:
            return existing

        # Special handling for main function - it needs C-compatible signature
        # Main always needs a wrapper because Sushi functions return Result<T>
        # but C expects int main()
        if fn.name == 'main':
            if self.codegen.main_expects_args:
                # Generate C-style main signature: int main(int argc, char** argv)
                ll_param_tys = [
                    self.codegen.types.i32,                                    # argc: int
                    ir.PointerType(ir.PointerType(self.codegen.types.i8))     # argv: char**
                ]
            else:
                # Generate C-style main signature: int main()
                ll_param_tys = []

            ll_ret = self.codegen.types.i32  # main always returns int in C
            fnty = ir.FunctionType(ll_ret, ll_param_tys)
            llvm_fn = ir.Function(self.codegen.module, fnty, name=fn.name)

            if self.codegen.main_expects_args:
                # Set parameter names for clarity
                llvm_fn.args[0].name = "argc"
                llvm_fn.args[1].name = "argv"
        else:
            # Normal Sushi function signature
            # All functions now return Result<T> where T is fn.ret
            params = self._params_of(fn)
            ll_param_tys = [self.codegen.types.ll_type(ty) for _, ty in params]
            # Use ll_type with ResultType to get the monomorphized enum type
            from semantics.typesys import ResultType
            ll_ret = self.codegen.types.ll_type(ResultType(ok_type=fn.ret))

            fnty = ir.FunctionType(ll_ret, ll_param_tys)
            llvm_fn = ir.Function(self.codegen.module, fnty, name=fn.name)

            # Set Sushi parameter names
            for i, (pname, _) in enumerate(params):
                llvm_fn.args[i].name = pname

        # Set linkage based on visibility:
        # - main function: always external linkage (required by linker)
        # - public functions: external linkage (accessible across units and for linking)
        # - private functions: internal linkage (only accessible within this module)
        if fn.name == 'main':
            llvm_fn.linkage = 'external'
        else:
            llvm_fn.linkage = 'external' if fn.is_public else 'internal'

        self.codegen.funcs[fn.name] = llvm_fn

        # Store the semantic return type for Result<T> type inference
        # This helps when inferring Result<T> types from function call expressions
        if fn.name != 'main' and fn.ret is not None:
            from semantics.typesys import ResultType
            self.codegen.function_return_types[fn.name] = ResultType(ok_type=fn.ret)

        return llvm_fn

    def emit_func_def(self, fn: FuncDef) -> ir.Function:
        """Define the body of a regular function.

        Args:
            fn: The function definition AST node.

        Returns:
            The LLVM function with body emitted.

        Raises:
            TypeError: If the return type is not supported.
        """
        # Special handling for main function - needs wrapper for C compatibility
        if fn.name == 'main':
            if self.codegen.main_expects_args:
                return self._emit_main_with_args(fn)
            else:
                return self._emit_main_without_args(fn)

        # Normal function emission
        llvm_fn = self.codegen.funcs.get(fn.name) or self.emit_func_decl(fn)
        # Pass fn to _begin_function so it can register parameter semantic types
        self._begin_function(llvm_fn, fn)

        # Track current function AST for ?? operator (needs return type info)
        self.codegen.current_function_ast = fn

        # Track parameter types in variable_types for struct member access resolution
        for param in fn.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(fn.body)

        if self.codegen.builder.block.terminator is None:
            self._emit_default_return(fn.ret)

        self._end_function()

        # Clear function AST after emission
        self.codegen.current_function_ast = None

        return llvm_fn

    def emit_extension_method_decl(self, ext: ExtendDef) -> ir.Function:
        """Create LLVM function prototype for extension method.

        Args:
            ext: The extension method definition AST node.

        Returns:
            The LLVM function with declared signature (no body).
        """
        func_name = self._get_extension_method_name(ext)

        param_types = []
        param_names = []

        if ext.target_type:
            param_types.append(self.codegen.types.ll_type(ext.target_type))
            param_names.append("self")

        for param in ext.params:
            if param.ty:
                param_types.append(self.codegen.types.ll_type(param.ty))
                param_names.append(param.name)

        # Extension methods return bare types (not Result<T>)
        # This matches built-in extension methods and provides zero-cost abstraction
        if ext.ret:
            ret_type = self.codegen.types.ll_type(ext.ret)
        else:
            ret_type = ir.VoidType()

        func_type = ir.FunctionType(ret_type, param_types)
        llvm_fn = ir.Function(self.codegen.module, func_type, name=func_name)

        for i, name in enumerate(param_names):
            if i < len(llvm_fn.args):
                llvm_fn.args[i].name = name

        self.codegen.funcs[func_name] = llvm_fn
        return llvm_fn

    def emit_extension_method_def(self, ext: ExtendDef) -> ir.Function:
        """Define the body of an extension method.

        Args:
            ext: The extension method definition AST node.

        Returns:
            The LLVM function with body emitted.

        Raises:
            RuntimeError: If the extension method was not declared.
            TypeError: If the return type is not supported.
        """
        func_name = self._get_extension_method_name(ext)
        llvm_fn = self.codegen.funcs.get(func_name)
        if not llvm_fn:
            raise_internal_error("CE0025", name=func_name)

        # Extension methods use ExtendDef, so we can't pass it as FuncDef
        # For now, don't pass semantic types for extension methods
        # (they typically don't pattern match on their parameters)
        self._begin_function(llvm_fn)

        # Mark that we're compiling an extension method (for return handling)
        self.codegen.in_extension_method = True

        # Track 'self' and parameter types in variable_types for struct member access resolution
        if ext.target_type is not None:
            self.codegen.variable_types["self"] = ext.target_type
        for param in ext.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(ext.body)

        if self.codegen.builder.block.terminator is None:
            self._emit_default_return_for_extension(ext.ret)

        self.codegen.in_extension_method = False
        self._end_function()
        return llvm_fn

    def _get_extension_method_name(self, ext: ExtendDef) -> str:
        """Generate unique function name for extension method.

        Args:
            ext: The extension method definition.

        Returns:
            The mangled function name.

        Examples:
            - extend i32 add() → "i32_add"
            - extend Box<i32> unwrap() → "Box__i32__unwrap"
            - extend HashMap<string, i32> get() → "HashMap__string_i32__get"
        """
        if ext.target_type and isinstance(ext.target_type, BuiltinType):
            target_type_name = ext.target_type.value
        else:
            target_type_name = str(ext.target_type) if ext.target_type else "unknown"

        # Sanitize generic type names for valid LLVM identifiers
        # Replace < with __, > with nothing, and ", " with _
        target_type_name = target_type_name.replace("<", "__").replace(">", "").replace(", ", "_")

        return f"{target_type_name}_{ext.name}"

    def _emit_default_return(self, ret_type: Ty | None) -> None:
        """Emit default return value for function without explicit return.

        With Result<T>, all functions now return Result structs. The default
        return is Err() which is {0, zero_value}.

        Args:
            ret_type: The function's return type.

        Raises:
            TypeError: If the return type is not supported.
        """
        if ret_type is None:
            return

        # RAII: Emit cleanup for all resources before returning
        from backend.statements import utils
        utils.emit_scope_cleanup(self.codegen, cleanup_type='all')

        # With Result<T>, create an Err() result using enum constructor logic
        # Get the monomorphized Result<T> enum type
        from semantics.typesys import ResultType
        result_llvm_type = self.codegen.types.ll_type(ResultType(ok_type=ret_type))

        # Look up the Result<T> enum in the enum table
        result_enum_name = f"Result<{ret_type}>"
        if result_enum_name in self.codegen.enum_table.by_name:
            result_enum = self.codegen.enum_table.by_name[result_enum_name]
            # Use enum constructor emission for Err()
            # Result.Err() has no arguments, variant index is 1 (Ok=0, Err=1)
            variant_index = result_enum.get_variant_index("Err")

            # Create enum value with Err tag
            err_result = enum_utils.construct_enum_variant(
                self.codegen, result_llvm_type, variant_index=variant_index,
                data=None, name_prefix="Result_Err"
            )

            # No data for Err variant, just return with tag set
            self.codegen.builder.ret(err_result)
        else:
            # Fallback to old Result struct format
            value_llvm_type = self.codegen.types.ll_type(ret_type)
            zero_value = self.codegen.utils.get_zero_value(value_llvm_type)
            err_result = ir.Constant(result_llvm_type, [
                ir.Constant(self.codegen.i1, 0),  # is_ok = 0 (Err)
                zero_value                         # value = zero/default
            ])
            self.codegen.builder.ret(err_result)

    def _emit_default_return_for_extension(self, ret_type: Ty | None) -> None:
        """Emit default return value for extension method without explicit return.

        Extension methods return bare types (not Result<T>), so we return
        a zero/default value directly.

        Args:
            ret_type: The extension method's return type.

        Raises:
            TypeError: If the return type is not supported.
        """
        if ret_type is None:
            return

        # RAII: Emit cleanup for all resources before returning
        from backend.statements import utils
        utils.emit_scope_cleanup(self.codegen, cleanup_type='all')

        # Extension methods return bare types - return zero/default value
        value_llvm_type = self.codegen.types.ll_type(ret_type)
        zero_value = self.codegen.utils.get_zero_value(value_llvm_type)
        self.codegen.builder.ret(zero_value)

    def _begin_function(self, llvm_fn: ir.Function, fn_def: FuncDef | None = None) -> None:
        """Initialize function emission context.

        Sets up entry and start blocks, alloca builder, fresh scope,
        and parameter handling for the function.

        Args:
            llvm_fn: The LLVM function to begin emitting.
            fn_def: Optional function definition for parameter semantic type registration.
        """
        self.codegen.func = llvm_fn
        self.codegen.entry_branch = None

        entry = llvm_fn.append_basic_block(name="entry")
        start = llvm_fn.append_basic_block(name="start")

        self.codegen.entry_block = entry
        self.codegen.builder = ir.IRBuilder(start)
        self.codegen.alloca_builder = ir.IRBuilder(entry)
        self.codegen.alloca_builder.position_at_start(entry)

        self.codegen.memory.reset_scope_stack()
        self.codegen.memory.push_scope()

        # Initialize dynamic array memory manager with the builder
        from backend.memory.dynamic_arrays import DynamicArrayManager
        self.codegen.dynamic_arrays = DynamicArrayManager(self.codegen.builder, self.codegen)
        self.codegen.dynamic_arrays.push_scope()

        self.codegen.entry_branch = self.codegen.alloca_builder.branch(start)

        # Build parameter name -> semantic type mapping if fn_def is provided
        param_semantic_types = {}
        if fn_def is not None:
            for param in fn_def.params:
                if param.ty is not None:
                    param_semantic_types[param.name] = param.ty

        param_slots = []
        for i, arg in enumerate(llvm_fn.args):
            pname = arg.name or f"arg{i}"

            # Get semantic type for this parameter
            semantic_type = param_semantic_types.get(pname)

            # For reference parameters, the arg is already a pointer, so we store the pointer itself
            # rather than loading through it. This allows us to use the reference transparently.
            # When the parameter is used (in _emit_name), we'll load through this pointer.
            slot = self.codegen.memory.entry_alloca(arg.type, pname)
            current_scope_level = len(self.codegen.memory.locals) - 1
            self.codegen.memory.locals[-1][pname] = slot

            # Update flat cache for O(1) lookup
            if pname not in self.codegen.memory._flat_locals_cache:
                self.codegen.memory._flat_locals_cache[pname] = []
            self.codegen.memory._flat_locals_cache[pname].append((current_scope_level, slot))

            # IMPORTANT: Register semantic type for pattern matching support
            if semantic_type is not None:
                self.codegen.memory.semantic_types[-1][pname] = semantic_type

                # Update flat cache for semantic types
                if pname not in self.codegen.memory._flat_types_cache:
                    self.codegen.memory._flat_types_cache[pname] = []
                self.codegen.memory._flat_types_cache[pname].append((current_scope_level, semantic_type))

            param_slots.append((arg, slot))

        for arg, slot in param_slots:
            self.codegen.builder.store(arg, slot)

    def _end_function(self) -> None:
        """Clean up function emission context.

        Clears per-function state including builders, scopes, and references.
        """
        self.codegen.func = None
        self.codegen.builder = None
        self.codegen.alloca_builder = None
        self.codegen.entry_block = None
        self.codegen.memory.reset_scope_stack()
        self.codegen.entry_branch = None

    def _params_of(self, fn: FuncDef) -> List[Tuple[str, Ty]]:
        """Extract parameter information from function definition.

        Args:
            fn: The function definition AST node.

        Returns:
            List of (name, type) tuples for function parameters.

        Raises:
            TypeError: If parameter format is invalid or type is missing.
        """
        out: List[Tuple[str, Ty]] = []
        for idx, p in enumerate(getattr(fn, "params", ())):
            if not isinstance(p, Param):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] must be Param, got {type(p).__name__}")

            if not isinstance(p.name, str):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] name must be str, got {type(p.name).__name__}")

            if p.ty is None:
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] '{p.name}' has no type")

            if not self._is_valid_param_type(p.ty):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] '{p.name}' has invalid type {p.ty!r}")

            out.append((p.name, p.ty))
        return out

    def _extract_value_from_result_enum(
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

    def _emit_main_with_args(self, fn: FuncDef) -> ir.Function:
        """
        Emit the main function when command line arguments are expected.

        Creates a C-style main(int argc, char** argv) that converts arguments
        to a Sushi string[] and calls the user's main function.

        Args:
            fn: The user's main function definition.

        Returns:
            The generated C-style main function.
        """
        # Get the C-style main function that was already declared
        c_main = self.codegen.funcs.get('main')
        if c_main is None:
            raise_internal_error("CE0064")

        # Create a user_main function with the original Sushi signature
        user_main = self._create_user_main_function(fn)

        # Generate the C-style main function body
        self._begin_function(c_main)

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
        is_ok, value = self._extract_value_from_result_enum(result_struct, value_type, fn.ret)

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

        self._end_function()
        return c_main

    def _emit_main_without_args(self, fn: FuncDef) -> ir.Function:
        """
        Emit the main function without command line arguments.

        Creates a C-style main() that calls user_main and extracts the value
        from the Result struct.

        Args:
            fn: The user's main function definition.

        Returns:
            The generated C-style main function.
        """
        # Get the C-style main function that was already declared
        c_main = self.codegen.funcs.get('main')
        if c_main is None:
            raise_internal_error("CE0064")

        # Create a user_main function with the original Sushi signature
        user_main = self._create_user_main_function(fn)

        # Generate the C-style main function body
        self._begin_function(c_main)

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
        is_ok, value = self._extract_value_from_result_enum(result_struct, value_type, fn.ret)

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

        self._end_function()
        return c_main

    def _create_user_main_function(self, fn: FuncDef) -> ir.Function:
        """
        Create a separate function for the user's main function body.

        Args:
            fn: The user's main function definition.

        Returns:
            The user_main LLVM function.
        """
        # Create function signature matching the user's main function
        params = self._params_of(fn)
        ll_param_tys = [self.codegen.types.ll_type(ty) for _, ty in params]
        # Use ll_type with ResultType to get the monomorphized enum type
        from semantics.typesys import ResultType
        ll_ret = self.codegen.types.ll_type(ResultType(ok_type=fn.ret))

        fnty = ir.FunctionType(ll_ret, ll_param_tys)
        user_main = ir.Function(self.codegen.module, fnty, name="user_main")
        user_main.linkage = 'internal'  # Internal function

        # Set parameter names
        for i, (pname, _) in enumerate(params):
            user_main.args[i].name = pname

        # Emit the user's main function body
        self._begin_function(user_main, fn)

        # Track current function AST for ?? operator (needs return type info)
        self.codegen.current_function_ast = fn

        # Track parameter types in variable_types for struct member access resolution
        for param in fn.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(fn.body)

        if self.codegen.builder.block.terminator is None:
            self._emit_default_return(fn.ret)

        self._end_function()

        # Clear function AST after emission
        self.codegen.current_function_ast = None

        return user_main