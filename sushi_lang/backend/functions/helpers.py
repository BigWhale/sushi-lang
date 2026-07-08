"""
Helper functions for LLVM function management.

This module contains utility functions used across function declaration
and definition: parameter validation, scope management, default returns, etc.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Tuple

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef, Param, ExtendDef
from sushi_lang.semantics.typesys import Type as Ty, BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType, UnknownType, ReferenceType, ResultType, ForeignPtrType
from sushi_lang.backend import enum_utils
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class FunctionHelpers:
    """Utility functions for function emission."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize helpers with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance.
        """
        self.codegen = codegen

    def is_valid_param_type(self, param_type: Ty) -> bool:
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

        # Opaque foreign pointer (FFI handle) - valid in non-public signatures
        if isinstance(param_type, ForeignPtrType):
            return True

        # First-class function value (bare function pointer)
        from sushi_lang.semantics.typesys import FunctionType
        if isinstance(param_type, FunctionType):
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
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(param_type, GenericTypeRef):
            # GenericTypeRef is valid - ll_type() will resolve it to monomorphized enum
            return True

        return False

    def params_of(self, fn: FuncDef) -> List[Tuple[str, Ty]]:
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

            if not self.is_valid_param_type(p.ty):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] '{p.name}' has invalid type {p.ty!r}")

            out.append((p.name, p.ty))
        return out

    def get_extension_method_name(self, ext: ExtendDef) -> str:
        """Generate unique function name for extension method.

        Args:
            ext: The extension method definition.

        Returns:
            The mangled function name.

        Examples:
            - extend i32 add() → "i32_add"
            - extend Box<i32> unwrap() → "Box__i32_unwrap"
            - extend HashMap<string, i32> get() → "HashMap__string_i32_get"

        Mirrored by ``backend/library_templates.py:impl_method_symbol`` for
        the symbols recorded in shipped perk-impl manifest records (C4a).
        """
        if ext.target_type and isinstance(ext.target_type, BuiltinType):
            target_type_name = ext.target_type.value
        else:
            target_type_name = str(ext.target_type) if ext.target_type else "unknown"

        # Sanitize generic type names for valid LLVM identifiers
        # Replace < with __, > with nothing, and ", " with _
        target_type_name = target_type_name.replace("<", "__").replace(">", "").replace(", ", "_")

        return f"{target_type_name}_{ext.name}"

    def emit_default_return(self, ret_type: Ty | None) -> None:
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
        from sushi_lang.backend.statements import utils
        utils.emit_scope_cleanup(self.codegen, cleanup_type='all')

        # With Result<T, E>, create an Err(error) result using enum constructor logic
        # Get the monomorphized Result<T, E> enum type
        std_error = self.codegen.enum_table.by_name.get("StdError")
        result_type = ResultType(ok_type=ret_type, err_type=std_error if std_error else ret_type)
        result_llvm_type = self.codegen.types.ll_type(result_type)

        # Look up the Result<T, E> enum in the enum table
        result_enum_name = str(result_type)
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

    def emit_default_return_for_extension(self, ret_type: Ty | None) -> None:
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
        from sushi_lang.backend.statements import utils
        utils.emit_scope_cleanup(self.codegen, cleanup_type='all')

        # Extension methods return bare types - return zero/default value
        value_llvm_type = self.codegen.types.ll_type(ret_type)
        zero_value = self.codegen.utils.get_zero_value(value_llvm_type)
        self.codegen.builder.ret(zero_value)

    def begin_function(self, llvm_fn: ir.Function, fn_def: FuncDef | None = None) -> None:
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
        from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager
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
            current_scope_level = self.codegen.memory._scope_depth
            self.codegen.memory._scope_vars[current_scope_level].add(pname)

            # Update flat cache for O(1) lookup
            if pname not in self.codegen.memory._locals:
                self.codegen.memory._locals[pname] = []
            self.codegen.memory._locals[pname].append((current_scope_level, slot))

            # IMPORTANT: Register semantic type for pattern matching support
            if semantic_type is not None:
                # Update flat cache for semantic types
                if pname not in self.codegen.memory._types:
                    self.codegen.memory._types[pname] = []
                self.codegen.memory._types[pname].append((current_scope_level, semantic_type))

            param_slots.append((arg, slot))

        for arg, slot in param_slots:
            val = arg
            # A by-value `string` parameter is a BORROW: the caller's binding retains
            # ownership and frees the buffer. Clear the callee's copy's owned bit to 0 so
            # anything the callee does with it (notably `return self` in an extension method,
            # or forwarding it onward) is treated as a borrow and never frees the caller's
            # buffer -- no double-free (#145). The callee never frees a string param anyway
            # (params are not registered in _string_cleanup); this makes RETURNING one safe.
            if self.codegen.types.is_string_type(arg.type):
                val = self.codegen.builder.insert_value(arg, ir.Constant(self.codegen.i8, 0), 2)
            self.codegen.builder.store(val, slot)

        # Register owning value parameters (moved-in T[] / List<T> / Own<T>, and native
        # variadic '...T' arrays) for RAII cleanup: the callee owns them and frees them
        # at scope exit.
        #
        # Exception: the user `main`'s `args` parameter is a BORROWED view of C argv --
        # its string elements point directly at process argv memory, not heap-owned
        # copies -- so it must never be freed. Skip cleanup registration for main's
        # parameters entirely (callers must borrow `args`, not move it by value).
        is_user_main = fn_def is not None and fn_def.name == "main"
        if fn_def is not None and not is_user_main:
            slot_by_name = {arg.name or f"arg{i}": slot
                            for i, (arg, slot) in enumerate(param_slots)}
            for param in fn_def.params:
                # Reference parameters (&peek/&poke) are borrows -- never owned/freed.
                if isinstance(param.ty, ReferenceType):
                    continue
                slot = slot_by_name.get(param.name)
                if slot is None:
                    continue

                # A native variadic '...T' array parameter: the callee owns the
                # caller-collected T[] and frees it at scope exit.
                if getattr(param, "is_variadic", False) and isinstance(param.ty, DynamicArrayType):
                    self.codegen.dynamic_arrays.register_param_array(
                        param.name, param.ty.base_type, slot
                    )
                    continue

                # Move-by-value owning parameters (#131): a bare owning value (dynamic
                # array / List<T> / Own<T>) passed by value is MOVED into the callee,
                # which now owns it and must free it exactly once at scope exit (the
                # caller marked its source moved and skips its own free). Registration
                # mirrors what a `let` local of the same type gets.
                if isinstance(param.ty, DynamicArrayType):
                    self.codegen.dynamic_arrays.register_param_array(
                        param.name, param.ty.base_type, slot
                    )
                    continue

                resolved = param.ty
                if isinstance(resolved, UnknownType):
                    resolved = self.codegen.struct_table.by_name.get(resolved.name, resolved)
                if isinstance(resolved, StructType):
                    if self.codegen.dynamic_arrays.is_own_type(resolved):
                        self.codegen.dynamic_arrays.register_own(param.name, resolved)
                    elif self.codegen.dynamic_arrays.is_list_type(resolved):
                        self.codegen.dynamic_arrays.register_list(param.name, resolved, slot)
                    elif self.codegen.dynamic_arrays.struct_needs_cleanup(resolved):
                        # By-value owning USER struct (#60): copy semantics, callee frees
                        # its independent deep copy.
                        self.codegen.memory.register_struct_cleanup(param.name, resolved, slot)

    def end_function(self) -> None:
        """Clean up function emission context.

        Clears per-function state including builders, scopes, and references.
        """
        self.codegen.func = None
        self.codegen.builder = None
        self.codegen.alloca_builder = None
        self.codegen.entry_block = None
        self.codegen.memory.reset_scope_stack()
        self.codegen.entry_branch = None


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
