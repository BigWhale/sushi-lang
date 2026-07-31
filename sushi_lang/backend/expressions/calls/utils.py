"""
Utility functions for method call emission.

This module contains helper functions for type inference, receiver emission,
and generic type resolution used by the method call dispatcher.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Tuple, Union

from llvmlite import ir
from sushi_lang.semantics.ast import Name, Call, Expr, MemberAccess, MethodCall, DotCall
from sushi_lang.semantics.typesys import EnumType, StructType
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def infer_generic_struct_type(codegen: 'LLVMCodegen', receiver: Expr, prefix: str) -> Optional[StructType]:
    """Infer generic struct type (Own<T>, HashMap<K,V>, List<T>) from receiver using multiple strategies."""
    from sushi_lang.semantics.typesys import ReferenceType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    # Strategy 1: Check if receiver is a Name (variable)
    if isinstance(receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(receiver.id)
        # Unwrap ReferenceType if present (for &peek T or &poke T parameters)
        if isinstance(semantic_type, ReferenceType):
            semantic_type = semantic_type.referenced_type

        if isinstance(semantic_type, StructType) and semantic_type.name.startswith(prefix):
            return semantic_type

        # Handle GenericTypeRef (e.g., HashMap<string, string> before resolution)
        # Resolve it to the actual StructType from the struct table
        if isinstance(semantic_type, GenericTypeRef):
            type_name = str(semantic_type)  # e.g., "HashMap<string, string>"
            if type_name.startswith(prefix) and type_name in codegen.struct_table.by_name:
                return codegen.struct_table.by_name[type_name]

    # Strategy 2: Receiver is a struct-field member access (e.g. a captured List<T> read
    # as `__closure_env.<name>` inside a lifted lambda body). Resolve the field's semantic
    # type so List/Own methods claim the call instead of falling through to the raw
    # dynamic-array dispatch (which crashes on a List backing struct).
    if isinstance(receiver, MemberAccess):
        from sushi_lang.backend.expressions.structs import infer_struct_type
        try:
            parent_struct = infer_struct_type(codegen, receiver.receiver)
            field_type = parent_struct.get_field_type(receiver.member)
        except InternalCompilerError:
            # infer_struct_type raise_internal_error()s when the receiver is not a
            # struct -- the "not a struct field" case here. A genuine bug (any other
            # exception) now propagates instead of being read as "no field type".
            field_type = None
        if isinstance(field_type, ReferenceType):
            field_type = field_type.referenced_type
        if isinstance(field_type, StructType) and field_type.name.startswith(prefix):
            return field_type
        if isinstance(field_type, GenericTypeRef):
            type_name = str(field_type)
            if type_name.startswith(prefix) and type_name in codegen.struct_table.by_name:
                return codegen.struct_table.by_name[type_name]

    # Strategy 3: For Own.new(), receiver might be a type name from let statement type annotation
    # This would be handled during type inference in semantic analysis

    return None


def infer_generic_enum_type(codegen: 'LLVMCodegen', receiver: Expr, receiver_value: ir.Value, prefix: str) -> Optional[EnumType]:
    """Infer generic enum type (Result<T> or Maybe<T>) from receiver using multiple strategies."""
    from sushi_lang.semantics.typesys import ReferenceType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    # Strategy 1: Check if receiver is a Name (variable)
    if isinstance(receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(receiver.id)
        # Unwrap ReferenceType if present (for &peek T or &poke T parameters)
        if isinstance(semantic_type, ReferenceType):
            semantic_type = semantic_type.referenced_type

        # A known concrete enum type is authoritative for the receiver: only the
        # generic handler whose prefix matches may claim it. Return None (rather than
        # falling through to the Strategy 3 LLVM-layout heuristic) when it does not
        # match, so a same-layout enum of the other family is never mis-selected --
        # e.g. Maybe<Color> and Result<i32, StdError> can share {i32, [N x i8]}.
        if isinstance(semantic_type, EnumType):
            return semantic_type if semantic_type.name.startswith(prefix) else None

        # Handle GenericTypeRef (e.g., Result<i32> before resolution). A known enum
        # ref is likewise authoritative.
        if isinstance(semantic_type, GenericTypeRef):
            type_name = str(semantic_type)  # e.g., "Result<i32>"
            if type_name in codegen.enum_table.by_name:
                return codegen.enum_table.by_name[type_name] if type_name.startswith(prefix) else None

    # Strategy 2: Infer from function call return type (for Call expressions).
    #
    # Both lookups here used to build a ONE-argument name -- f"Result<{ok}>" -- which can never
    # match the two-argument name a Result is interned under ("Result<i32, StdError>"). So they
    # always missed, and every call fell through to Strategy 3's LLVM-type matching, which picks
    # the first enum with a matching layout. Two Results with the same layout but different type
    # arguments are indistinguishable there. Both now look up the interned enum directly.
    if isinstance(receiver, Call):
        from sushi_lang.semantics.typesys import FunctionType
        if not isinstance(receiver.callee, Name):
            # Indirect call through a function value (env.f(x), arr[0]()): recover the
            # Result enum from the FunctionType the type checker annotated.
            fn_ty = getattr(receiver, 'callee_fn_type', None)
            if isinstance(fn_ty, FunctionType):
                from sushi_lang.backend.generics.result_builder import intern_result
                result_enum = intern_result(codegen, fn_ty.ok_type, fn_ty.err_type)
                if result_enum is not None and result_enum.name.startswith(prefix):
                    return result_enum
        else:
            func_name = receiver.callee.id
            if func_name in codegen.function_return_types:
                result_type = codegen.function_return_types[func_name]
                if isinstance(result_type, EnumType) and result_type.name.startswith(prefix):
                    return result_type

    # Strategy 3: Fallback to LLVM type matching (last resort, only when the
    # receiver's semantic type could not be determined above).
    for enum_name, enum_type in codegen.enum_table.by_name.items():
        if isinstance(enum_type, EnumType) and enum_name.startswith(prefix):
            expected_llvm_type = codegen.types.ll_type(enum_type)
            if receiver_value.type == expected_llvm_type:
                return enum_type

    return None


def _deref_borrowed_receiver(codegen: 'LLVMCodegen', value: ir.Value, ll_type: ir.Type,
                             semantic_type: Optional['Type'],
                             name: str) -> Tuple[ir.Value, ir.Type]:
    """Load through a borrow whose referent is not itself pointer-shaped.

    "The methods on `&T` are the methods on `T`." Semantics already says so
    (`semantics/generics/builtin_methods.py` unwraps `ReferenceType` before it decides),
    which is why a borrowed receiver produces no diagnostic at all. This dispatcher keys
    on the LLVM receiver instead, so it has to agree.

    A borrow is always a POINTER to `T`. For a dynamic array or a struct that is what the
    handlers already want, because this backend passes those by pointer anyway. For a
    `BuiltinType` -- a primitive, or a `string` fat pointer -- the by-value form is not a
    pointer, so the pointer misses every built-in test: `is_string_type` answers False and
    dispatch falls through to the user extension-method path. That path mangled the name
    to `string_len` or `i32_hash` and then either declared a symbol nobody defines (a
    link failure surfacing as CE0000) or raised a bare `KeyError`.
    """
    from sushi_lang.semantics.typesys import BuiltinType, ReferenceType, deref_type

    if not isinstance(semantic_type, ReferenceType):
        return value, ll_type
    if not isinstance(deref_type(semantic_type), BuiltinType):
        return value, ll_type
    if not isinstance(ll_type, ir.PointerType):
        return value, ll_type
    loaded = codegen.builder.load(value, name=f"{name}_deref")
    return loaded, loaded.type


def emit_receiver_value(codegen: 'LLVMCodegen', receiver: Expr) -> Tuple[ir.Value, ir.Type, Optional['Type']]:
    """Emit receiver value with special handling for dynamic arrays and references.

    Returns tuple of (receiver_value, receiver_type, semantic_type).
    """
    from sushi_lang.backend.expressions import type_utils

    semantic_type = None

    if isinstance(receiver, Name):
        # Local alloca, or the global backing a constant (#248): `PRIMES.hash()` and
        # `PRIMES.iter()` reached here and died on a constant receiver. The semantic
        # type has to come from the const table too -- a constant was never declared as
        # a local, so the memory manager has no type recorded for it.
        from sushi_lang.backend.expressions.names import (
            resolve_name_semantic_type, resolve_name_slot)
        slot = resolve_name_slot(codegen, receiver.id)
        if slot is None:
            raise_internal_error("CE0055", name=receiver.id)
        slot_type = slot.type.pointee
        semantic_type = resolve_name_semantic_type(codegen, receiver.id)

        # Check if this is a reference parameter
        if type_utils.is_reference_parameter(codegen, receiver.id):
            receiver_value = codegen.builder.load(slot, name=f"{receiver.id}_ref")
            receiver_type = receiver_value.type
            receiver_value, receiver_type = _deref_borrowed_receiver(
                codegen, receiver_value, receiver_type, semantic_type, receiver.id)
        elif codegen.types.is_dynamic_array_type(slot_type):
            receiver_value = slot  # Use the alloca pointer directly
            receiver_type = slot_type
        else:
            receiver_value = codegen.expressions.emit_expr(receiver)
            receiver_type = codegen.types.infer_llvm_type_from_value(receiver_value)
    elif isinstance(receiver, MemberAccess):
        # _emit_member_access() already returns a pointer for dynamic array fields
        receiver_value = codegen.expressions.emit_expr(receiver)
        receiver_type = codegen.types.infer_llvm_type_from_value(receiver_value)
        # Extract semantic type from struct field
        from sushi_lang.backend.expressions.structs import infer_struct_type
        try:
            struct_type = infer_struct_type(codegen, receiver.receiver)
            semantic_type = struct_type.get_field_type(receiver.member)
        except InternalCompilerError:
            # Not a struct receiver (infer_struct_type raise_internal_error()s); leave
            # semantic_type as None. A bare `except:` here also swallowed Ctrl-C.
            pass
    else:
        receiver_value = codegen.expressions.emit_expr(receiver)
        receiver_type = codegen.types.infer_llvm_type_from_value(receiver_value)
        # Recover the enum type for an inline variant construction receiver
        # (e.g. Suit.Hearts().hash()). Without this, semantic_type stays None and
        # downstream handlers fall back to mapping the enum's LLVM layout
        # ({i32, [N x i8]}) back to a language type, which fails with CE0019.
        semantic_type = _infer_enum_construction_type(codegen, receiver)

    return receiver_value, receiver_type, semantic_type


def _infer_enum_construction_type(codegen: 'LLVMCodegen', receiver: Expr) -> Optional['Type']:
    """Recover the EnumType for a receiver that constructs an enum variant.

    Handles inline constructions like ``Suit.Hearts()`` (a DotCall whose receiver
    is the enum name) and EnumConstructor nodes. Returns None when the receiver is
    not a recognised enum construction.
    """
    from sushi_lang.semantics.ast import EnumConstructor

    # A resolved annotation (set for generic enums) takes precedence.
    resolved = getattr(receiver, 'resolved_enum_type', None)
    if resolved is not None:
        return resolved

    enum_name = None
    if isinstance(receiver, EnumConstructor):
        enum_name = receiver.enum_name
    elif isinstance(receiver, DotCall) and isinstance(receiver.receiver, Name):
        enum_name = receiver.receiver.id

    if enum_name is not None:
        return codegen.enum_table.by_name.get(enum_name)
    return None


def get_resolved_type(expr: Union[MethodCall, DotCall], type_attr: str) -> Optional['Type']:
    """Extract resolved type from expr if present.

    Args:
        expr: Method call or DotCall expression
        type_attr: Attribute name ('resolved_enum_type' or 'resolved_struct_type')

    Returns:
        Resolved type if present, None otherwise
    """
    if hasattr(expr, type_attr):
        resolved_type = getattr(expr, type_attr)
        if resolved_type is not None:
            return resolved_type
    return None


def infer_semantic_type(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                        receiver_value: Optional[ir.Value], expected_prefix: str,
                        expected_type_class) -> Optional['Type']:
    """Unified type inference for generic types.

    This function unifies the type inference pattern repeated across all generic type handlers.
    It tries multiple strategies in priority order:
    1. Check for resolved type annotation (resolved_enum_type or resolved_struct_type)
    2. Call appropriate inference function based on type class

    Args:
        codegen: The LLVM code generator
        expr: Method call or DotCall expression
        receiver_value: Emitted receiver value (None if not yet emitted)
        expected_prefix: Type name prefix (e.g., "Result<", "Maybe<", "Own<", "HashMap<", "List<")
        expected_type_class: Expected type class (EnumType or StructType)

    Returns:
        Inferred type if successful, None otherwise
    """
    receiver = expr.receiver

    # Priority 1: Check for resolved type annotation
    if expected_type_class == EnumType:
        resolved_type = get_resolved_type(expr, 'resolved_enum_type')
        if resolved_type is not None:
            return resolved_type
    elif expected_type_class == StructType:
        resolved_type = get_resolved_type(expr, 'resolved_struct_type')
        if resolved_type is not None:
            return resolved_type

    # Priority 2: Use appropriate inference function
    if expected_type_class == EnumType:
        if receiver_value is None:
            # Need to emit receiver first
            receiver_value = codegen.expressions.emit_expr(receiver)
        return infer_generic_enum_type(codegen, receiver, receiver_value, expected_prefix)
    elif expected_type_class == StructType:
        return infer_generic_struct_type(codegen, receiver, expected_prefix)

    return None


def emit_receiver_as_pointer(codegen: 'LLVMCodegen', receiver: Expr) -> Optional[ir.Value]:
    """Emit receiver as pointer (alloca) for mutation methods.

    This is used by HashMap and List methods that need to mutate the receiver.

    For reference parameters (&peek T or &poke T), the slot contains a pointer
    to the actual variable, so we need to load that pointer first.

    Args:
        codegen: The LLVM code generator
        receiver: Receiver expression

    Returns:
        Pointer to receiver if receiver is a Name, None otherwise
    """
    from sushi_lang.backend.expressions import type_utils

    if isinstance(receiver, Name):
        # Local alloca, or the global backing a constant (#248). `len` and `get` are
        # also builtin List/HashMap method names, so the List/HashMap probes in
        # calls/generics.py run first and asked for the address of `PRIMES` before the
        # array dispatcher was ever reached. Handing them a constant's global is safe:
        # both gate on the receiver's semantic type being a `List<`/`HashMap<`, so an
        # array constant falls through to the array dispatcher untouched.
        from sushi_lang.backend.expressions.names import resolve_name_slot
        slot = resolve_name_slot(codegen, receiver.id)
        if slot is None:
            return None
        # Check if this is a reference parameter - if so, load the pointer
        if type_utils.is_reference_parameter(codegen, receiver.id):
            return codegen.builder.load(slot, name=f"{receiver.id}_ref_ptr")
        return slot

    # A captured collection read as `__closure_env.<name>` (any struct-field List/Own).
    # try_get_struct_alloca recurses through the env reference param and GEPs to the
    # field, yielding a pointer to the List/Own so mutating methods work in the body.
    if isinstance(receiver, MemberAccess):
        from sushi_lang.backend.expressions.structs import try_get_struct_alloca
        return try_get_struct_alloca(codegen, receiver)

    return None
