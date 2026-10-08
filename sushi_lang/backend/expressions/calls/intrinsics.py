"""Core type method call handlers (arrays, enums, structs, primitives, strings)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Union

from llvmlite import ir
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.ast import DotCall, MethodCall, Name
from sushi_lang.semantics.typesys import EnumType, StructType, BuiltinType
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def try_emit_enum_constructor(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                              to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as enum constructor. Returns None if not an enum constructor."""
    from sushi_lang.backend.expressions.calls.utils import get_resolved_type

    receiver = expr.receiver
    method = expr.method
    args = expr.args

    resolved_type = get_resolved_type(expr, 'resolved_enum_type')
    if resolved_type is not None:
        from sushi_lang.semantics.typesys import EnumType
        if isinstance(resolved_type, EnumType) and resolved_type.get_variant(method) is not None:
            from sushi_lang.backend.expressions import enums
            return enums.emit_enum_constructor_from_method_call(codegen, resolved_type, method, args)
        return None

    if isinstance(receiver, Name) and hasattr(codegen, 'enum_table'):
        # Local-wins (#296): a local named after the enum shadows it.
        if (receiver.id in codegen.enum_table.by_name
                and codegen.memory.find_semantic_type(receiver.id) is None):
            from sushi_lang.backend.expressions import enums
            enum_type = codegen.enum_table.by_name[receiver.id]
            return enums.emit_enum_constructor_from_method_call(codegen, enum_type, method, args)

    # Priority 3: Defensive check for generic enum constructors without type info
    # This should never be reached if semantic analysis properly sets resolved_enum_type
    if isinstance(receiver, Name) and hasattr(codegen, 'enum_table'):
        base_name = receiver.id

        for enum_name, enum_type in codegen.enum_table.by_name.items():
            if is_instance_of(enum_type, base_name):
                raise_internal_error("CE0113",
                    message=f"Generic enum constructor {base_name}.{method}() requires "
                            f"type annotation. Found monomorphized instance {enum_name}. "
                            f"This is a compiler bug - semantic analysis should have set "
                            f"resolved_enum_type on this DotCall node.")

    return None


def try_emit_struct_constructor(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                                to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as struct constructor (e.g., Own.alloc()). Returns None if not a struct constructor."""
    from sushi_lang.backend.expressions.calls.utils import get_resolved_type

    receiver = expr.receiver
    method = expr.method

    if method != "alloc":
        return None

    resolved_type = get_resolved_type(expr, 'resolved_struct_type')
    if resolved_type is not None:
        from sushi_lang.semantics.generics.own import is_builtin_own_method
        from sushi_lang.backend.generics.own import emit_builtin_own_method

        if isinstance(resolved_type, StructType) and is_instance_of(resolved_type, "Own"):
            if is_builtin_own_method(method):
                return emit_builtin_own_method(codegen, expr, None, resolved_type)

    if isinstance(receiver, Name):
        if hasattr(codegen, 'generic_structs') and receiver.id in codegen.generic_structs.by_name:
            return None

    return None


def try_emit_array_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                           receiver_value: ir.Value, receiver_type: ir.Type, semantic_type: 'Type', to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as array method. Returns None if not an array method.

    `hash` is not asked here: it yields to a `Hashable` implementation, so it is asked
    after the perk rung (`try_emit_array_hash`).
    """
    from sushi_lang.backend.types.arrays import is_builtin_array_method, emit_array_method

    if expr.method == "hash" or not is_builtin_array_method(expr.method):
        return None
    if not _is_array_receiver(codegen, receiver_type, semantic_type):
        return None
    return emit_array_method(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)


def try_emit_array_hash(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                        receiver_value: ir.Value, receiver_type: ir.Type, semantic_type: 'Type',
                        to_i1: bool) -> Optional[ir.Value]:
    """The built-in `hash` of an array, when no `Hashable` implementation answered it."""
    from sushi_lang.backend.types.arrays import emit_array_method

    if expr.method != "hash" or not _is_array_receiver(codegen, receiver_type, semantic_type):
        return None
    return emit_array_method(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)


def _is_array_receiver(codegen: 'LLVMCodegen', receiver_type: ir.Type,
                       semantic_type: 'Type') -> bool:
    """A fixed or a dynamic array receiver, and not a container of the same shape."""
    from sushi_lang.backend.expressions import type_utils

    is_dynamic_array = (codegen.types.is_dynamic_array_type(receiver_type) or
                       type_utils.is_dynamic_array_pointer(codegen, receiver_type))

    # A `peek` / `poke` fixed array arrives as `[N x T]*`: `_deref_borrowed_receiver` loads
    # through a borrow only when the referent is a BuiltinType, and an array is not one. The
    # pointer arm is what stops dispatch falling through to the user-extension fallback,
    # which mangled the type name into `i32[3]_len` and raised CE0000 (#480).
    is_fixed_array = isinstance(receiver_type, ir.ArrayType) or (
        isinstance(receiver_type, ir.PointerType)
        and isinstance(receiver_type.pointee, ir.ArrayType))

    if not is_fixed_array and not is_dynamic_array:
        return False

    # A `List@(T)` is `{i32 len, i32 capacity, T* data}`, which is the dynamic-array
    # descriptor's own shape, so the LLVM type alone cannot tell the two apart. The
    # SEMANTIC type can, and it is already here. Without this the array path claimed
    # `List@(i32).hash()` and then refused its own receiver with CE0042 (#628).
    from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
    return not (isinstance(semantic_type, StructType)
                and is_instance_of(semantic_type, *CONTAINER_BASES))


def try_emit_string_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                            receiver_value: ir.Value, receiver_type: ir.Type,
                            semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as string method. Returns None if not a string method."""
    from sushi_lang.backend.expressions.calls.stdlib import emit_stdlib_string_call

    if not codegen.types.is_string_type(receiver_type):
        return None

    if expr.method == "is_empty":
        from sushi_lang.sushi_stdlib.src.collections.strings.compiler import emit_string_is_empty_intrinsic

        is_empty_func = emit_string_is_empty_intrinsic(codegen.module)

        require_builder(codegen)
        result = codegen.builder.call(is_empty_func, [receiver_value], name="is_empty_result")

        if to_i1:
            result = codegen.builder.trunc(result, ir.IntType(1), name="to_i1")

        return result

    # `clone` is the escape CE2411 names, so it must work without
    # `use <collections/strings>` (#242). Routed through the seam's `copy_out`, the ONE deep
    # clone in the backend.
    if expr.method == "clone":
        from sushi_lang.backend.ownership import copy_out
        from sushi_lang.semantics.typesys import BuiltinType
        require_builder(codegen)
        return copy_out(codegen, receiver_value, BuiltinType.STRING)

    from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method
    if not is_builtin_string_method(expr.method):
        return None

    return emit_stdlib_string_call(codegen, expr.method, receiver_value, expr.args, to_i1)


def _try_emit_auto_derived(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                           receiver_value: ir.Value, receiver_type: ir.Type,
                           semantic_type, to_i1: bool, *, method: str, kind: type,
                           exclude_containers: bool = False) -> Optional[ir.Value]:
    """The ONE shape behind the four auto-derived hash/clone dispatchers (11b)."""
    if semantic_type is None or expr.method != method:
        return None

    # Normalize the receiver in two steps: DEREF, because the methods on `&T` are the
    # methods on `T` (#301, #308), and RESOLVE, because a parameter's type arrives as the
    # declared spelling -- `UnknownType('Holder')` is no `StructType` (#312). Both misses
    # fell through to the extension lookup and raised a CE0000 for a plain `.clone()`.
    # This is the ONE body behind all four auto-derived dispatchers.
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
        from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
        if is_instance_of(semantic_type, *CONTAINER_BASES):
            return None

    derived = codegen.derived_methods.get_method(semantic_type, method)
    if derived is None:
        return None

    return derived.llvm_emitter(codegen, expr, receiver_value, receiver_type, to_i1)


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


def try_emit_contract_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                             receiver_value: ir.Value, receiver_type: ir.Type,
                             semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """`a.eq(b)`, `a.compare(b)` and `a.to_str()`: the derived contract methods.

    An implementation of the contract answered the call at the perk rung already, so
    what reaches here is the derived method of a struct or an enum, or a primitive's
    `eq` / `compare`. Both operands only READ, so a temporary gets an owner.
    """
    from sushi_lang.semantics.generics.contracts import (
        DISPLAY, EQ, METHOD_CONTRACT, is_contract_receiver)
    contract = METHOD_CONTRACT.get(expr.method)
    if contract is None or semantic_type is None:
        return None

    from sushi_lang.backend.destructors import resolve_named_type
    from sushi_lang.semantics.typesys import BuiltinType, deref_type
    ty = resolve_named_type(codegen, deref_type(semantic_type))
    if isinstance(ty, BuiltinType):
        if contract == DISPLAY:
            return None
    elif not is_contract_receiver(ty):
        return None

    from sushi_lang.backend.expressions.memory import own_temporary
    from sushi_lang.backend.types import contracts
    receiver = (contracts.load_operand(codegen, receiver_value, ty)
                if isinstance(receiver_type, ir.PointerType) else receiver_value)
    own_temporary(codegen, expr.receiver, receiver, ty)
    if contract == DISPLAY:
        from sushi_lang.backend.types.display import emit_value_to_str
        return emit_value_to_str(codegen, receiver, ty)
    argument = contracts.load_operand(
        codegen, codegen.expressions.emit_expr(expr.args[0]), ty)
    own_temporary(codegen, expr.args[0], argument, ty)
    if contract == EQ:
        equal = contracts.emit_value_eq(codegen, receiver, argument, ty)
        return equal if to_i1 else codegen.builder.zext(equal, codegen.i8)
    return contracts.emit_value_compare(codegen, receiver, argument, ty)


def try_emit_function_clone(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                            receiver_value: ir.Value, receiver_type: ir.Type,
                            semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit clone() on a function value. None if not applicable."""
    from sushi_lang.semantics.typesys import FunctionType, deref_type

    if expr.method != "clone":
        return None

    resolved = deref_type(semantic_type)
    if not isinstance(resolved, FunctionType):
        return None

    from sushi_lang.backend.ownership import copy_out
    return copy_out(codegen, receiver_value, resolved)


def try_emit_enum_clone(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                        receiver_value: ir.Value, receiver_type: ir.Type,
                        semantic_type, to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as auto-derived enum clone method (#134). None if not applicable."""
    return _try_emit_auto_derived(codegen, expr, receiver_value, receiver_type,
                                  semantic_type, to_i1, method="clone", kind=EnumType)


def try_emit_primitive_static(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                              to_i1: bool) -> Optional[ir.Value]:
    """A static on a primitive type name: `f64.from_bits`, `f32.from_bits`, `string.from_bytes`."""
    receiver = expr.receiver
    if isinstance(receiver, Name) and receiver.id == "string" and expr.method == "from_bytes":
        return _emit_string_from_bytes(codegen, expr)
    if not (isinstance(receiver, Name) and receiver.id in ("f64", "f32")
            and expr.method == "from_bits"):
        return None

    if len(expr.args) != 1:
        raise_internal_error("CE0078", got=len(expr.args))

    builder = require_builder(codegen)
    arg_value = codegen.expressions.emit_expr(expr.args[0])
    float_ll = ir.DoubleType() if receiver.id == "f64" else ir.FloatType()
    return builder.bitcast(arg_value, float_ll, name="from_bits")


def _emit_string_from_bytes(codegen: 'LLVMCodegen', expr) -> ir.Value:
    """`string.from_bytes(nom b)` (#1091): the array's buffer becomes the string's.

    No byte is copied. The array is spent, so its scope exit frees nothing, and the
    string owns the buffer (`owned` is 1) and frees it as a string does.
    """
    from sushi_lang.backend.ownership import ConsumingUse, consume
    from sushi_lang.backend.types.arrays.addressing import as_array_address
    from sushi_lang.backend.gep_utils import gep_dynamic_array_data, gep_dynamic_array_len
    from sushi_lang.semantics.typesys import DynamicArrayType

    if len(expr.args) != 1:
        raise_internal_error("CE0078", got=len(expr.args))
    builder = require_builder(codegen)
    arg = expr.args[0]
    value = codegen.expressions.emit_expr(arg)
    value = consume(codegen, arg, value, DynamicArrayType(BuiltinType.U8),
                    ConsumingUse.CALL_ARG)
    address = as_array_address(codegen, value)
    data = builder.load(gep_dynamic_array_data(codegen, address), name="from_bytes_data")
    size = builder.load(gep_dynamic_array_len(codegen, address), name="from_bytes_size")
    string = ir.Constant(codegen.types.string_struct, ir.Undefined)
    string = builder.insert_value(string, data, 0)
    string = builder.insert_value(string, size, 1)
    return builder.insert_value(string, ir.Constant(codegen.i8, 1), 2, name="from_bytes")


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
        type_map = {
            'i8': BuiltinType.I8, 'i16': BuiltinType.I16, 'i32': BuiltinType.I32, 'i64': BuiltinType.I64,
            'u8': BuiltinType.U8, 'u16': BuiltinType.U16, 'u32': BuiltinType.U32, 'u64': BuiltinType.U64,
            'f32': BuiltinType.F32, 'f64': BuiltinType.F64, 'bool': BuiltinType.BOOL, 'string': BuiltinType.STRING
        }
        builtin_type = type_map[str(semantic_type)]

        builtin_method = codegen.derived_methods.get_method(builtin_type, expr.method)
        if builtin_method is not None:
            return builtin_method.llvm_emitter(codegen, expr, receiver_value, receiver_type, to_i1)

    return None


def try_emit_perk_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                         receiver_value: ir.Value, receiver_type: ir.Type,
                         semantic_type: Optional['Type'], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as perk method. Returns None if not a perk method."""
    if semantic_type is None:
        return None

    perk_method = codegen.perk_impl_table.get_method(semantic_type, expr.method)
    if perk_method is None:
        return None

    from sushi_lang.semantics.generics.name_mangling import extension_symbol
    func_name = extension_symbol(str(semantic_type), expr.method,
                                 getattr(expr, "callee_method_type_args", None) or ())

    llvm_fn = codegen.funcs.get(func_name)
    if llvm_fn is None:
        raise_internal_error("CE0024", method=expr.method, type=str(semantic_type))

    # A `poke self` / `peek self` perk method (#327) takes the receiver by POINTER --
    # the same rule as the extension call site (dispatcher.py), read from the same
    # the typecheck pass stamp.
    from sushi_lang.semantics.param_modes import receiver_mode
    self_mode = receiver_mode(getattr(expr, "callee_self_mode", None))
    if self_mode.by_pointer:
        from sushi_lang.backend.expressions.calls.utils import emit_receiver_as_pointer
        receiver_value = emit_receiver_as_pointer(codegen, expr.receiver)

    from sushi_lang.backend.expressions.calls.dispatcher import (
        consume_receiver, emit_checked_call, emit_method_call_arguments,
        settle_method_call_arguments)
    arg_values = emit_method_call_arguments(codegen, expr)
    if self_mode.consumes:
        receiver_value = consume_receiver(codegen, expr, receiver_value)
    settle_method_call_arguments(codegen, expr, arg_values)
    return emit_checked_call(codegen, llvm_fn, [receiver_value, *arg_values], to_i1)
