"""
Core type method call handlers (arrays, enums, structs, primitives, strings).

This module contains dispatcher helpers for built-in language features like
arrays, strings, and primitive types.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Union

from llvmlite import ir
from sushi_lang.semantics.ast import DotCall, MethodCall, Name
from sushi_lang.semantics.typesys import EnumType, StructType, BuiltinType
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def try_emit_enum_constructor(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall]) -> Optional[ir.Value]:
    """Try to emit as enum constructor. Returns None if not an enum constructor."""
    from sushi_lang.backend.expressions.calls.utils import get_resolved_type

    receiver = expr.receiver
    method = expr.method
    args = expr.args

    # Priority 1: Check if resolved_enum_type is set (for generic enums like Result<T>)
    resolved_type = get_resolved_type(expr, 'resolved_enum_type')
    if resolved_type is not None:
        # CRITICAL: Verify that method is actually a variant name, not a method name
        # This prevents treating Result.realise() as a constructor when resolved_enum_type is set
        from sushi_lang.semantics.typesys import EnumType
        if isinstance(resolved_type, EnumType) and resolved_type.get_variant(method) is not None:
            from sushi_lang.backend.expressions import enums
            return enums.emit_enum_constructor_from_method_call(codegen, resolved_type, method, args)
        # Not a variant - fall through to method dispatch
        return None

    # Priority 2: Check if receiver is in enum_table (for non-generic enums)
    if isinstance(receiver, Name) and hasattr(codegen, 'enum_table'):
        if receiver.id in codegen.enum_table.by_name:
            from sushi_lang.backend.expressions import enums
            enum_type = codegen.enum_table.by_name[receiver.id]
            return enums.emit_enum_constructor_from_method_call(codegen, enum_type, method, args)

    # Priority 3: Defensive check for generic enum constructors without type info
    # This should never be reached if semantic analysis properly sets resolved_enum_type
    if isinstance(receiver, Name) and hasattr(codegen, 'enum_table'):
        base_name = receiver.id
        prefix = base_name + "<"

        # Check if this looks like a generic enum base name
        for enum_name in codegen.enum_table.by_name:
            if enum_name.startswith(prefix):
                raise_internal_error("CE0113",
                    message=f"Generic enum constructor {base_name}.{method}() requires "
                            f"type annotation. Found monomorphized instance {enum_name}. "
                            f"This is a compiler bug - semantic analysis should have set "
                            f"resolved_enum_type on this DotCall node.")

    return None


def try_emit_struct_constructor(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall]) -> Optional[ir.Value]:
    """Try to emit as struct constructor (e.g., Own.alloc()). Returns None if not a struct constructor."""
    from sushi_lang.backend.expressions.calls.utils import get_resolved_type

    receiver = expr.receiver
    method = expr.method
    args = expr.args

    # Check if this is a known constructor method
    if method != "alloc":
        return None

    # Priority 1: Check if resolved_struct_type is set (for generic structs like Own<T>)
    resolved_type = get_resolved_type(expr, 'resolved_struct_type')
    if resolved_type is not None:
        from sushi_lang.semantics.generics.own import is_builtin_own_method
        from sushi_lang.backend.generics.own import emit_builtin_own_method

        # Check if this is Own<T>
        if isinstance(resolved_type, StructType) and resolved_type.name.startswith("Own<"):
            if is_builtin_own_method(method):
                temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
                return emit_builtin_own_method(codegen, temp_expr, None, resolved_type)

    # Priority 2: Check if receiver is a generic struct name (Own)
    if isinstance(receiver, Name):
        # Check if it's a known generic struct
        if hasattr(codegen, 'generic_structs') and receiver.id in codegen.generic_structs.by_name:
            # For Own<T>, we need to resolve T from context (handled by resolved_struct_type)
            # If we don't have resolved_struct_type, we can't proceed
            return None

    return None


def try_emit_stdio_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as stdio method (stdin/stdout/stderr). Returns None if not stdio."""
    from sushi_lang.backend.expressions.calls.stdlib import emit_stdlib_stdio_call

    receiver = expr.receiver
    method = expr.method
    args = expr.args

    if not isinstance(receiver, Name) or receiver.id not in ['stdin', 'stdout', 'stderr']:
        return None

    from sushi_lang.sushi_stdlib.src.io.stdio import is_builtin_stdio_method
    if not is_builtin_stdio_method(method):
        return None

    # Require stdlib unit - no fallback to inline emission
    if not codegen.has_stdlib_unit("io/stdio"):
        raise_internal_error("CE0096", operation="Missing stdlib unit: io/stdio. Add 'use <io/stdio>' to use {receiver.id}.{method}()"
        )

    return emit_stdlib_stdio_call(codegen, receiver.id, method, args, to_i1)


def try_emit_file_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as file method. Returns None if not a file method."""
    from sushi_lang.backend.expressions.calls.stdlib import emit_stdlib_file_call

    receiver = expr.receiver
    method = expr.method
    args = expr.args

    if not isinstance(receiver, Name):
        return None

    semantic_type = codegen.memory.find_semantic_type(receiver.id)
    if semantic_type != BuiltinType.FILE:
        return None

    from sushi_lang.sushi_stdlib.src.io.files import is_builtin_file_method
    if not is_builtin_file_method(method):
        return None

    # Emit the receiver (file handle) and get the FILE* pointer
    file_ptr = codegen.expressions.emit_expr(receiver)

    # Require stdlib unit - no fallback to inline emission
    if not codegen.has_stdlib_unit("io/files"):
        raise_internal_error("CE0096", operation="Missing stdlib unit: io/files. Add 'use <io/files>' to use file.{method}()"
        )

    return emit_stdlib_file_call(codegen, file_ptr, method, args, to_i1)


def try_emit_array_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                           receiver_value: ir.Value, receiver_type: ir.Type, semantic_type: 'Type', to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as array method. Returns None if not an array method."""
    from sushi_lang.backend.expressions import type_utils
    from sushi_lang.backend.types.arrays import is_builtin_array_method, emit_array_method

    # Check for built-in array methods (both fixed and dynamic arrays)
    is_dynamic_array = (codegen.types.is_dynamic_array_type(receiver_type) or
                       type_utils.is_dynamic_array_pointer(codegen, receiver_type))

    if not isinstance(receiver_type, ir.ArrayType) and not is_dynamic_array:
        return None

    if not is_builtin_array_method(expr.method):
        return None

    # Arrays are a CORE language feature, always use inline emission
    temp_expr = MethodCall(receiver=expr.receiver, method=expr.method, args=expr.args, loc=expr.loc)
    return emit_array_method(codegen, temp_expr, receiver_value, receiver_type, semantic_type, to_i1)


def try_emit_string_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                            receiver_value: ir.Value, receiver_type: ir.Type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as string method. Returns None if not a string method."""
    from sushi_lang.backend.expressions.calls.stdlib import emit_stdlib_string_call

    if not codegen.types.is_string_type(receiver_type):
        return None

    # Handle is_empty as inline intrinsic (doesn't require stdlib import)
    if expr.method == "is_empty":
        from sushi_lang.sushi_stdlib.src.collections.strings.compiler import emit_string_is_empty_intrinsic

        # Emit intrinsic function if not already present
        is_empty_func = emit_string_is_empty_intrinsic(codegen.module)

        # Call the intrinsic
        require_builder(codegen)
        result = codegen.builder.call(is_empty_func, [receiver_value], name="is_empty_result")

        # Convert i8 to i1 if needed
        if to_i1:
            result = codegen.builder.trunc(result, ir.IntType(1), name="to_i1")

        return result

    # `clone` is an inline intrinsic too. It is the explicit deep copy, and the escape
    # CE2411 names, so it must work without `use <collections/strings>` (#242, MM.md B4).
    # Routed through the seam's `copy_out`, the ONE deep clone in the backend, so a cloned
    # string duplicates exactly what the owned-bit-guarded free releases. A literal carries
    # owned=0 and clones to another owned=0, whose free is a no-op.
    if expr.method == "clone":
        from sushi_lang.backend.ownership import copy_out
        from sushi_lang.semantics.typesys import BuiltinType
        require_builder(codegen)
        return copy_out(codegen, receiver_value, BuiltinType.STRING)

    from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method
    if not is_builtin_string_method(expr.method):
        return None

    # Require stdlib unit - no fallback to inline emission
    if not codegen.has_stdlib_unit("collections/strings"):
        raise_internal_error("CE0096", operation="Missing stdlib unit: collections/strings. Add 'use <collections/strings>' to use string.{expr.method}()"
        )

    return emit_stdlib_string_call(codegen, expr.method, receiver_value, expr.args, to_i1)


def _try_emit_auto_derived(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                           receiver_value: ir.Value, receiver_type: ir.Type,
                           semantic_type, to_i1: bool, *, method: str, kind: type,
                           exclude_containers: bool = False) -> Optional[ir.Value]:
    """The ONE shape behind the four auto-derived hash/clone dispatchers (11b).

    Checks the method name and the receiver's semantic kind, looks the auto-derived
    method up in the builtin registry, and calls its registered LLVM emitter. For the
    enum kind a `Result@(T, E)` still spelled as a GenericTypeRef is interned first
    (every Result must go through `ensure_result_type_in_table` -- see CE0126).
    `exclude_containers` keeps Own/List/HashMap on their own method paths, keyed on the
    SAME prefix tuple their registration exclusion uses (semantics/generics/cloning.py).
    """
    if semantic_type is None or expr.method != method:
        return None

    # Normalize the receiver's type to the concrete struct/enum it names, in two steps:
    #
    #   deref  -- the methods on `&T` are the methods on `T` (the same unwrap
    #             `try_emit_function_clone` states below). Without it a reference receiver
    #             matched no `kind` (#301, #308).
    #   resolve -- a PARAMETER's type reaches the backend as the declared spelling, so a
    #             struct parameter arrives as `UnknownType('Holder')`, which is not a
    #             `StructType` and matched no `kind` either. That hit a by-value parameter
    #             just as hard as a reference one (#312).
    #
    # Both misses fell through to the extension-method lookup and raised
    # `Extension method not found: Holder_clone` -- a CE0000 for a plain `.clone()`. This is
    # the ONE body behind all four auto-derived dispatchers, so one normalization covers
    # clone and hash, struct and enum. `resolve_named_type` is the same helper every
    # destructor gate uses, and for the same reason: an unresolved name silently matches
    # nothing.
    from sushi_lang.backend.destructors import resolve_named_type
    from sushi_lang.semantics.typesys import deref_type
    semantic_type = resolve_named_type(codegen, deref_type(semantic_type))

    if kind is EnumType:
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(semantic_type, GenericTypeRef) and semantic_type.base_name == "Result":
            if len(semantic_type.type_args) >= 2:
                from sushi_lang.semantics.generics.results import ensure_result_type_in_table
                result_enum = ensure_result_type_in_table(
                    codegen.enum_table, semantic_type.type_args[0],
                    semantic_type.type_args[1], struct_table=codegen.struct_table.by_name)
                if result_enum is None:
                    return None
                semantic_type = result_enum

    if not isinstance(semantic_type, kind):
        return None

    if exclude_containers:
        from sushi_lang.semantics.generics.cloning import CONTAINER_PREFIXES
        if semantic_type.name.startswith(CONTAINER_PREFIXES):
            return None

    from sushi_lang.sushi_stdlib.src.common import get_builtin_method
    derived = get_builtin_method(semantic_type, method)
    if derived is None:
        return None

    temp_expr = MethodCall(receiver=expr.receiver, method=expr.method, args=expr.args, loc=expr.loc)
    return derived.llvm_emitter(codegen, temp_expr, receiver_value, receiver_type, to_i1)


def try_emit_struct_hash(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                         receiver_value: ir.Value, receiver_type: ir.Type,
                         semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as auto-derived struct hash method. Returns None if not applicable."""
    return _try_emit_auto_derived(codegen, expr, receiver_value, receiver_type,
                                  semantic_type, to_i1, method="hash", kind=StructType)


def try_emit_enum_hash(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                       receiver_value: ir.Value, receiver_type: ir.Type,
                       semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as auto-derived enum hash method. Returns None if not applicable."""
    return _try_emit_auto_derived(codegen, expr, receiver_value, receiver_type,
                                  semantic_type, to_i1, method="hash", kind=EnumType)


def try_emit_struct_clone(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                          receiver_value: ir.Value, receiver_type: ir.Type,
                          semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as auto-derived struct clone method (#134). None if not applicable."""
    return _try_emit_auto_derived(codegen, expr, receiver_value, receiver_type,
                                  semantic_type, to_i1, method="clone", kind=StructType,
                                  exclude_containers=True)


def try_emit_function_clone(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                            receiver_value: ir.Value, receiver_type: ir.Type,
                            semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit clone() on a function value. None if not applicable.

    No new IR: `emit_value_clone` already routes a `FunctionType` to
    `_clone_function_value`, which duplicates the heap environment through the fat
    pointer's `clone_ptr` slot and is the structural inverse of
    `emit_function_value_destructor`. A hand-written duplicate here would be free to break
    that pairing -- clone fewer buffers is a double free, clone more is a leak -- which is
    why the fixed-array arm delegates for the same reason.

    A non-capturing value carries a null `clone_ptr` and passes through unchanged, so this
    is free for a plain fn reference.
    """
    from sushi_lang.semantics.typesys import FunctionType, deref_type

    if expr.method != "clone":
        return None

    # The methods on `&T` are the methods on `T`.
    resolved = deref_type(semantic_type)
    if not isinstance(resolved, FunctionType):
        return None

    from sushi_lang.backend.expressions.memory import emit_value_clone
    return emit_value_clone(codegen, receiver_value, resolved)


def try_emit_enum_clone(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                        receiver_value: ir.Value, receiver_type: ir.Type,
                        semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as auto-derived enum clone method (#134). None if not applicable."""
    return _try_emit_auto_derived(codegen, expr, receiver_value, receiver_type,
                                  semantic_type, to_i1, method="clone", kind=EnumType)


def try_emit_primitive_static(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                              to_i1: bool) -> Optional[ir.Value]:
    """Try to emit f64.from_bits(u64) / f32.from_bits(u32) static reinterpret.

    The receiver is a primitive float type NAME (not a value), so this MUST run before
    emit_receiver_value in the dispatcher (which would try to evaluate `f64` as a value).
    Emits a single LLVM bitcast from the integer bit pattern to the float type. Returns
    None if this is not a from_bits static call.
    """
    receiver = expr.receiver
    if not (isinstance(receiver, Name) and receiver.id in ("f64", "f32")
            and expr.method == "from_bits"):
        return None

    if len(expr.args) != 1:
        raise_internal_error("CE0078", got=len(expr.args))

    builder = require_builder(codegen)
    arg_value = codegen.expressions.emit_expr(expr.args[0])
    float_ll = ir.DoubleType() if receiver.id == "f64" else ir.FloatType()
    return builder.bitcast(arg_value, float_ll, name="from_bits")


def try_emit_primitive_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                              receiver_value: ir.Value, receiver_type: ir.Type,
                              semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as primitive type method. Returns None if not applicable."""
    from sushi_lang.backend.expressions.calls.stdlib import emit_stdlib_primitive_call

    # The methods on `&T` are the methods on `T`. Without this a borrowed primitive or
    # string matched no name below, fell through to the user extension-method path, and
    # died there as a bare KeyError rather than a diagnostic.
    from sushi_lang.semantics.typesys import deref_type
    semantic_type = deref_type(semantic_type)

    if semantic_type is None:
        return None

    if str(semantic_type) not in ['i8', 'i16', 'i32', 'i64', 'u8', 'u16', 'u32', 'u64', 'f32', 'f64', 'bool', 'string']:
        return None

    from sushi_lang.semantics.generics.primitives import is_builtin_primitive_method
    if not is_builtin_primitive_method(expr.method):
        return None

    # Check if stdlib unit is imported - if so, emit external call.
    # Only to_str() has a precompiled stdlib body; hash()/to_bits() are inline-only,
    # so route to the stdlib path solely for to_str (otherwise they'd hit CE0028).
    if codegen.has_stdlib_unit("core/primitives") and expr.method == "to_str":
        return emit_stdlib_primitive_call(codegen, expr.method, receiver_value, receiver_type, str(semantic_type))
    else:
        # Fall back to inline emission (backward compatibility)
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        # Map semantic type string to BuiltinType
        type_map = {
            'i8': BuiltinType.I8, 'i16': BuiltinType.I16, 'i32': BuiltinType.I32, 'i64': BuiltinType.I64,
            'u8': BuiltinType.U8, 'u16': BuiltinType.U16, 'u32': BuiltinType.U32, 'u64': BuiltinType.U64,
            'f32': BuiltinType.F32, 'f64': BuiltinType.F64, 'bool': BuiltinType.BOOL, 'string': BuiltinType.STRING
        }
        builtin_type = type_map[str(semantic_type)]

        # Look up the method in the registry
        builtin_method = get_builtin_method(builtin_type, expr.method)
        if builtin_method is not None:
            temp_expr = MethodCall(receiver=expr.receiver, method=expr.method, args=expr.args, loc=expr.loc)
            return builtin_method.llvm_emitter(codegen, temp_expr, receiver_value, receiver_type, to_i1)

    return None


def try_emit_perk_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                         receiver_value: ir.Value, receiver_type: ir.Type,
                         semantic_type: Optional['Type'], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as perk method. Returns None if not a perk method.

    Perk methods are extension methods defined via 'extend Type with Perk'.
    They take precedence over auto-derived methods like hash().

    Args:
        codegen: The LLVM code generator.
        expr: The method call expression.
        receiver_value: Already-emitted receiver LLVM value.
        receiver_type: LLVM type of receiver.
        semantic_type: Semantic type of receiver (if available).
        to_i1: Whether to convert result to i1.

    Returns:
        LLVM value if perk method found, None otherwise.
    """
    if semantic_type is None:
        return None

    # Check if this type has any perk implementations
    perk_method = codegen.perk_impl_table.get_method(semantic_type, expr.method)
    if perk_method is None:
        return None

    # Found a perk method - emit as extension method call
    # Perk methods are just extension methods, so use the same mangling
    lang_type = str(semantic_type)
    sanitized_lang_type = lang_type.replace("<", "__").replace(">", "").replace(", ", "_")
    func_name = f"{sanitized_lang_type}_{expr.method}"

    llvm_fn = codegen.funcs.get(func_name)
    if llvm_fn is None:
        # Function should have been generated - this is an internal error
        raise_internal_error("CE0027", method=expr.method, type=str(semantic_type))

    # Build argument list (receiver + explicit args)
    emitted_args = [receiver_value]
    emitted_args.extend(codegen.expressions.emit_expr(arg) for arg in expr.args)

    # Cast arguments to match function signature
    params = list(llvm_fn.args)
    casted = [codegen.utils.cast_for_param(v, p.type) for v, p in zip(emitted_args, params, strict=True)]

    # Call the perk method
    result_value = codegen.builder.call(llvm_fn, casted)

    # Perk methods return bare types (not Result<T>)
    return codegen.utils.as_i1(result_value) if to_i1 else result_value
