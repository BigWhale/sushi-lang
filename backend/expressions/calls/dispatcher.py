"""
Main call dispatcher for function and method calls.

This module orchestrates the dispatching of function and method calls to
appropriate handlers based on receiver type and method name.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Union

from llvmlite import ir
from semantics.ast import Call, MethodCall, DotCall, Name
from backend.expressions.calls.file_open import emit_open_function
from backend.expressions.calls.stdlib import emit_time_function, emit_math_function, emit_env_function
from backend.expressions.calls import intrinsics, generics
from backend.expressions.calls.utils import emit_receiver_value
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def emit_function_call(codegen: 'LLVMCodegen', expr: Call, to_i1: bool) -> ir.Value:
    """Emit function call with argument type casting.

    Args:
        codegen: The LLVM code generator.
        expr: The function call expression.
        to_i1: Whether to convert result to i1.

    Returns:
        The function call result.

    Raises:
        TypeError: If callee is not a name or function not found.
    """
    if not isinstance(expr.callee, Name):
        raise_internal_error("CE0027", type=type(expr.callee).__name__)

    callee = expr.callee.id

    # Check if this is a struct constructor
    if callee in codegen.struct_table.by_name:
        from backend.expressions import structs
        return structs.emit_struct_constructor(codegen, expr, to_i1)

    # Check if this is a generic struct constructor
    if hasattr(codegen, 'generic_structs') and callee in codegen.generic_structs.by_name:
        from backend.expressions import structs
        return structs.emit_struct_constructor(codegen, expr, to_i1)

    # Check for built-in global functions
    if callee == "open":
        return emit_open_function(codegen, expr, to_i1)

    # Check if this is a stdlib function (from registry)
    stdlib_func = _check_stdlib_function_codegen(codegen, callee)
    if stdlib_func is not None:
        return _emit_stdlib_function(codegen, expr, callee, stdlib_func, to_i1)

    llvm_fn = codegen.funcs.get(callee)
    if llvm_fn is None:
        raise KeyError(f"unknown function: {callee}")

    args = [codegen.expressions.emit_expr(a) for a in expr.args]

    params = list(llvm_fn.args)
    if len(args) != len(params):
        raise_internal_error("CE0026", expected=len(params), got=len(args))

    casted = [codegen.utils.cast_for_param(v, p.type) for v, p in zip(args, params)]
    result_struct = codegen.builder.call(llvm_fn, casted)

    # Functions now return Result<T> as enum: {i32 tag, [N x i8] data}
    # Return the full Result<T> struct - downstream code will handle extraction
    # (e.g., .realise() method, if (result) conditionals, etc.)
    return codegen.utils.as_i1(result_struct) if to_i1 else result_struct


def emit_method_call(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool = False, is_dotcall: bool = False) -> ir.Value:
    """Emit method call by rewriting as function call with receiver as first argument.

    This function implements UFCS (Uniform Function Call Syntax) for extension methods.
    Method calls are rewritten as function calls with the receiver as the first argument.

    The dispatching logic checks for built-in methods in the following order:
    1. Enum constructors (e.g., Color.Red(), Result.Ok())
    2. Struct constructors (e.g., Own.alloc())
    3. stdio methods (stdin/stdout/stderr)
    4. File methods
    5. Result<T> methods
    6. Maybe<T> methods
    7. Own<T> instance methods (get, destroy)
    8. HashMap<K, V> methods (new, insert, get, etc.)
    9. List<T> methods (new, push, pop, get, etc.)
    10. Array methods (both fixed and dynamic)
    11. String methods
    12. Perk methods (extend Type with Perk) - BEFORE auto-derived
    13. Auto-derived struct hash
    14. Auto-derived enum hash
    15. Primitive methods (numeric types, bool)
    16. Extension methods (user-defined)

    Args:
        codegen: The LLVM code generator.
        expr: The method call or DotCall expression.
        to_i1: Whether to convert result to i1.
        is_dotcall: If True, this is a DotCall node (fields are identical).

    Returns:
        The method call result.

    Raises:
        TypeError: If receiver type cannot be determined.
        KeyError: If extension method function not found.
    """
    # ========================================================================
    # Priority-ordered dispatcher for method calls
    # Each handler returns a value if it matches, or None to continue dispatch
    # ========================================================================

    # 1. Enum constructors (e.g., Color.Red(), Result.Ok())
    result = intrinsics.try_emit_enum_constructor(codegen, expr)
    if result is not None:
        return result

    # 2. Struct constructors (e.g., Own.alloc())
    result = intrinsics.try_emit_struct_constructor(codegen, expr)
    if result is not None:
        return result

    # 3. stdio methods (stdin/stdout/stderr)
    result = intrinsics.try_emit_stdio_method(codegen, expr, to_i1)
    if result is not None:
        return result

    # 4. File methods
    result = intrinsics.try_emit_file_method(codegen, expr, to_i1)
    if result is not None:
        return result

    # 5. Result<T> methods (realise, etc.)
    result = generics.try_emit_result_method(codegen, expr, to_i1)
    if result is not None:
        return result

    # 6. Maybe<T> methods (is_some, is_none, realise, expect)
    result = generics.try_emit_maybe_method(codegen, expr, to_i1)
    if result is not None:
        return result

    # 7. Own<T> instance methods (get, destroy)
    result = generics.try_emit_own_method(codegen, expr, to_i1)
    if result is not None:
        return result

    # 8. HashMap<K, V> methods (new, insert, get, etc.)
    result = generics.try_emit_hashmap_method(codegen, expr, to_i1)
    if result is not None:
        return result

    # 9. List<T> methods (new, push, pop, get, etc.)
    result = generics.try_emit_list_method(codegen, expr, to_i1)
    if result is not None:
        return result

    # ========================================================================
    # For remaining handlers, emit receiver and infer types
    # ========================================================================
    receiver_value, receiver_type, semantic_type = emit_receiver_value(codegen, expr.receiver)

    # 10. Array methods (len, get, push, pop, etc.)
    result = intrinsics.try_emit_array_method(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # 11. String methods
    result = intrinsics.try_emit_string_method(codegen, expr, receiver_value, receiver_type, to_i1)
    if result is not None:
        return result

    # 12. Perk methods (extension methods via perk implementations) - BEFORE auto-derived
    result = intrinsics.try_emit_perk_method(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # 13. Auto-derived struct hash
    result = intrinsics.try_emit_struct_hash(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # 14. Auto-derived enum hash
    result = intrinsics.try_emit_enum_hash(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # 15. Primitive methods (to_str, hash, etc.)
    result = intrinsics.try_emit_primitive_method(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # ========================================================================
    # Fallback: User-defined extension methods
    # ========================================================================
    # Use semantic type if available to distinguish bool from i8
    if semantic_type is not None:
        lang_type = str(semantic_type)
    else:
        lang_type = codegen.types.map_llvm_to_language_type(receiver_type)

    # Sanitize generic type names for valid LLVM identifiers (match declaration mangling)
    # Replace < with __, > with nothing, and ", " with _
    sanitized_lang_type = lang_type.replace("<", "__").replace(">", "").replace(", ", "_")
    func_name = f"{sanitized_lang_type}_{expr.method}"
    llvm_fn = codegen.funcs.get(func_name)

    # Fallback: Check module globals for stdlib extension methods
    if llvm_fn is None and func_name in codegen.module.globals:
        llvm_fn = codegen.module.globals[func_name]

    # Fallback: Declare stdlib string extension methods if not found
    if llvm_fn is None and lang_type == "string":
        from backend.llvm_functions import declare_stdlib_function
        from stdlib.src.collections.strings import get_builtin_string_method_return_type
        from semantics.typesys import BuiltinType

        # Get return type from method registry
        ret_sushi_type = get_builtin_string_method_return_type(expr.method, BuiltinType.STRING)
        if ret_sushi_type is not None:
            ret_llvm_type = codegen.types.ll_type(ret_sushi_type)
            # String methods take the string fat pointer as parameter
            llvm_fn = declare_stdlib_function(codegen.module, func_name, ret_llvm_type, [receiver_type])

    if llvm_fn is None:
        raise KeyError(f"Extension method not found: {func_name}")

    emitted_args = [receiver_value]
    emitted_args.extend(codegen.expressions.emit_expr(arg) for arg in expr.args)

    params = list(llvm_fn.args)
    if len(emitted_args) != len(params):
        raise_internal_error("CE0026", expected=len(params), got=len(emitted_args))

    casted = [codegen.utils.cast_for_param(v, p.type) for v, p in zip(emitted_args, params)]
    result_value = codegen.builder.call(llvm_fn, casted)

    # Extension methods return bare types (not Result<T>)
    # This matches built-in extension methods and provides zero-cost abstraction
    return codegen.utils.as_i1(result_value) if to_i1 else result_value


def _check_stdlib_function_codegen(codegen: 'LLVMCodegen', function_name: str) -> tuple | None:
    """
    Check if a function is a stdlib function during code generation.

    Args:
        codegen: LLVM code generator
        function_name: Name of the function to check

    Returns:
        Tuple of (module_path, StdlibFunction) if found, None otherwise
    """
    # Access the function table from the codegen
    func_table = codegen.func_table

    # Try common module paths
    possible_modules = ["time", "sys/env", "sys/process", "math", "random", "io/files"]

    for module_path in possible_modules:
        stdlib_func = func_table.lookup_stdlib_function(module_path, function_name)
        if stdlib_func is not None:
            return (module_path, stdlib_func)

    return None


def _emit_stdlib_function(codegen: 'LLVMCodegen', expr: Call, function_name: str, 
                          module_and_func: tuple, to_i1: bool) -> ir.Value:
    """
    Emit code for a stdlib function call.

    Dispatches to the appropriate emitter based on the module.

    Args:
        codegen: LLVM code generator
        expr: Function call expression
        function_name: Name of the function
        module_and_func: Tuple of (module_path, StdlibFunction)
        to_i1: Whether to convert result to i1

    Returns:
        LLVM IR value
    """
    module_path, stdlib_func = module_and_func
    
    # Dispatch to module-specific emitters
    if module_path == "time":
        return emit_time_function(codegen, expr, function_name, to_i1)
    elif module_path == "sys/env":
        return emit_env_function(codegen, expr, function_name, to_i1)
    elif module_path == "sys/process":
        from backend.expressions.calls.stdlib import emit_process_function
        return emit_process_function(codegen, expr, function_name, to_i1)
    elif module_path == "math":
        return emit_math_function(codegen, expr, function_name, to_i1)
    elif module_path == "random":
        from backend.expressions.calls.stdlib import emit_random_function
        return emit_random_function(codegen, expr, function_name, to_i1)
    elif module_path == "io/files":
        from backend.expressions.calls.stdlib import emit_files_function
        return emit_files_function(codegen, expr, function_name, to_i1)
    else:
        raise_internal_error("CE0055", name=f"{module_path}/{function_name}")
