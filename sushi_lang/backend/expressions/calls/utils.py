"""Utility functions for method call emission."""
from __future__ import annotations
import itertools
from typing import TYPE_CHECKING, Optional, Tuple, Union

from llvmlite import ir
from sushi_lang.semantics.ast import Name, Call, Expr, MemberAccess, MethodCall, DotCall
from sushi_lang.semantics.typesys import EnumType, StructType
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def _stamped_semantic_type(codegen: 'LLVMCodegen', expr: Expr) -> Optional['Type']:
    """The type Pass 2 recorded for a method call, or None."""
    from sushi_lang.semantics.ast import IndexAccess, TryExpr
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    from sushi_lang.semantics.typesys import UnknownType

    if isinstance(expr, TryExpr):
        stamped = getattr(expr, 'inferred_unwrapped_type', None)
    elif isinstance(expr, (MethodCall, DotCall)):
        stamped = getattr(expr, 'inferred_return_type', None)
    elif isinstance(expr, IndexAccess):
        stamped = getattr(expr, 'inferred_element_type', None)
    else:
        return None

    if stamped is None:
        return None

    struct_by_name = codegen.struct_table.by_name
    enum_by_name = codegen.enum_table.by_name
    resolved = resolve_unknown_type(stamped, struct_by_name, enum_by_name)

    # Never rebuild a named type -- if it has a name, the table is what that name means
    # (#240). A no-op whenever Pass 2 already handed over the interned instance, which it
    # does today for every shape this function serves.
    name = getattr(resolved, 'name', None)
    if isinstance(name, str):
        interned = struct_by_name.get(name) or enum_by_name.get(name)
        if interned is not None:
            return interned

    if isinstance(resolved, (GenericTypeRef, UnknownType)):
        return None
    return resolved


def infer_generic_struct_type(codegen: 'LLVMCodegen', receiver: Expr, prefix: str) -> Optional[StructType]:
    """Infer generic struct type (Own<T>, HashMap<K,V>, List<T>) from receiver using multiple strategies."""
    from sushi_lang.semantics.typesys import ReferenceType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(receiver.id)
        if isinstance(semantic_type, ReferenceType):
            semantic_type = semantic_type.referenced_type

        if isinstance(semantic_type, StructType) and semantic_type.name.startswith(prefix):
            return semantic_type

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

    # Strategy 3: the receiver is a chained method call (`outer.get().clone()`), so it is
    # neither a Name nor a MemberAccess and neither strategy above can see it. Pass 2
    # already typed it. The three strategies are disjoint by node type, so this one being
    # last is a matter of diff size, not of priority.
    stamped = _stamped_semantic_type(codegen, receiver)
    if isinstance(stamped, ReferenceType):
        stamped = stamped.referenced_type
    if isinstance(stamped, StructType) and stamped.name.startswith(prefix):
        return stamped

    return None


def _stdlib_call_return_enum(codegen: 'LLVMCodegen', func_name: str) -> Optional[EnumType]:
    """The Result/Maybe enum a direct stdlib-module call returns, or None."""
    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.backend.generics.result_builder import intern_result

    enums = codegen.enum_table.by_name
    if func_name == 'getenv':
        return enums.get('Maybe<string>')

    result_specs = {
        'sleep': (BuiltinType.I32, 'StdError'),
        'msleep': (BuiltinType.I32, 'StdError'),
        'usleep': (BuiltinType.I32, 'StdError'),
        'nanosleep': (BuiltinType.I32, 'StdError'),
        'setenv': (BuiltinType.I32, 'EnvError'),
        'file_size': (BuiltinType.I64, 'FileError'),
        'remove': (BuiltinType.I32, 'FileError'),
        'rename': (BuiltinType.I32, 'FileError'),
        'copy': (BuiltinType.I32, 'FileError'),
        'mkdir': (BuiltinType.I32, 'FileError'),
        'rmdir': (BuiltinType.I32, 'FileError'),
        'chdir': (BuiltinType.I32, 'ProcessError'),
        'getcwd': (BuiltinType.STRING, 'ProcessError'),
    }
    spec = result_specs.get(func_name)
    if spec is None:
        if func_name == 'run':
            out_struct = codegen.struct_table.by_name.get('ProcessOutput')
            err_enum = enums.get('ProcessError')
            if out_struct is not None and err_enum is not None:
                return intern_result(codegen, out_struct, err_enum)
        return None
    ok_type, err_name = spec
    err_enum = enums.get(err_name)
    if err_enum is None:
        return None
    return intern_result(codegen, ok_type, err_enum)


def infer_generic_enum_type(codegen: 'LLVMCodegen', receiver: Expr, receiver_value: ir.Value, prefix: str) -> Optional[EnumType]:
    """Infer generic enum type (Result<T> or Maybe<T>) from receiver using multiple strategies."""
    from sushi_lang.semantics.typesys import ReferenceType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(receiver.id)
        if isinstance(semantic_type, ReferenceType):
            semantic_type = semantic_type.referenced_type

        # A known concrete enum type is authoritative for the receiver: only the
        # generic handler whose prefix matches may claim it. Return None (rather than
        # falling through to the Strategy 3 LLVM-layout heuristic) when it does not
        # match, so a same-layout enum of the other family is never mis-selected --
        # e.g. Maybe<Color> and Result<i32, StdError> can share {i32, [N x i8]}.
        if isinstance(semantic_type, EnumType):
            return semantic_type if semantic_type.name.startswith(prefix) else None

        if isinstance(semantic_type, GenericTypeRef):
            type_name = str(semantic_type)  # e.g., "Result<i32>"
            if type_name in codegen.enum_table.by_name:
                return codegen.enum_table.by_name[type_name] if type_name.startswith(prefix) else None

    # Strategy 1b: a method-call (or `??`) receiver carries the type Pass 2 stamped
    # on the node (`inferred_return_type` / `inferred_unwrapped_type`, the chained-
    # receiver support). Authoritative like Strategy 1: a stamped enum of the other
    # family answers None instead of falling through to the layout heuristic, which
    # cannot tell same-shaped enums apart under the #300 phase 2 uniform layout
    # (e.g. `args.get(1).realise(d)` -- Maybe<string> == Result<i32, StdError> in LLVM).
    for stamp_attr in ('inferred_return_type', 'inferred_unwrapped_type'):
        stamped = getattr(receiver, stamp_attr, None)
        if isinstance(stamped, EnumType):
            return stamped if stamped.name.startswith(prefix) else None

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

            # Stdlib module functions (getenv, file_size, getcwd, ...) are in no
            # function table, so they used to fall through to Strategy 3. Under the
            # #300 phase 2 uniform {i32, [K x i64]} enum layout that is no longer
            # safe: Maybe<string> and Result<i32, StdError> share ONE LLVM type, so
            # the layout heuristic let the wrong family claim the receiver of
            # `getenv(x).realise(d)`. Resolve these calls from the same facts Pass
            # 1.5 registers (semantics/generics/instantiate/expressions.py); a
            # known stdlib return is authoritative, so a non-matching prefix
            # answers None rather than falling through.
            stdlib_ret = _stdlib_call_return_enum(codegen, func_name)
            if stdlib_ret is not None:
                return stdlib_ret if stdlib_ret.name.startswith(prefix) else None

    for enum_name, enum_type in codegen.enum_table.by_name.items():
        if isinstance(enum_type, EnumType) and enum_name.startswith(prefix):
            expected_llvm_type = codegen.types.ll_type(enum_type)
            if receiver_value.type == expected_llvm_type:
                return enum_type

    return None


def _deref_borrowed_receiver(codegen: 'LLVMCodegen', value: ir.Value, ll_type: ir.Type,
                             semantic_type: Optional['Type'],
                             name: str) -> Tuple[ir.Value, ir.Type]:
    """Load through a borrow whose referent is not itself pointer-shaped."""
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
    """Emit receiver value with special handling for dynamic arrays and references."""
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
        receiver_value = codegen.expressions.emit_expr(receiver)
        receiver_type = codegen.types.infer_llvm_type_from_value(receiver_value)
        from sushi_lang.backend.expressions.structs import infer_struct_type
        try:
            struct_type = infer_struct_type(codegen, receiver.receiver)
            semantic_type = struct_type.get_field_type(receiver.member)
        except InternalCompilerError:
            pass
    else:
        receiver_value = codegen.expressions.emit_expr(receiver)
        receiver_type = codegen.types.infer_llvm_type_from_value(receiver_value)
        # Recover the enum type for an inline variant construction receiver
        # (e.g. Suit.Hearts().hash()). Without this, semantic_type stays None and
        # downstream handlers fall back to mapping the enum's LLVM layout
        # ({i32, [N x i8]}) back to a language type, which fails with CE0019.
        semantic_type = _infer_enum_construction_type(codegen, receiver)
        if semantic_type is None:
            # Any other chained receiver -- `o.get().clone()`, `map.get(1).clone()`,
            # `make()??.clone()`. Enum construction keeps first place because it is the
            # narrower question and this stays purely additive.
            #
            # The order is only safe because the probe above now answers the question it
            # is named for. It used to claim ANY node carrying `resolved_enum_type`, which
            # Pass 2 also stamps on a Result/Maybe METHOD call -- so `go().realise("err")`
            # was typed as its receiver's enum instead of the `string` it produces, this
            # branch never ran, and the string temporary was registered nowhere and leaked
            # (#293). A non-variant method falls through to the stamp now.
            semantic_type = _stamped_semantic_type(codegen, receiver)
            _own_receiver_temp(codegen, receiver, receiver_value, semantic_type)

    return receiver_value, receiver_type, semantic_type


def _own_receiver_temp(codegen: 'LLVMCodegen', receiver: Expr, value: ir.Value,
                       semantic_type: Optional['Type']) -> None:
    """Give a receiver that nobody owns an owner, so it is freed exactly once."""
    from sushi_lang.backend.destructors import needs_cleanup, resolve_named_type
    from sushi_lang.backend.expressions.memory import expression_is_temporary

    if value is None or semantic_type is None:
        return
    resolved = resolve_named_type(codegen, semantic_type)
    if resolved is None or not needs_cleanup(resolved):
        return
    if not expression_is_temporary(codegen, receiver):
        return
    codegen.memory.create_local(
        f"__recv_temp_{next(_SPILL_SEQ)}", value.type, value, resolved)


def _infer_enum_construction_type(codegen: 'LLVMCodegen', receiver: Expr) -> Optional['Type']:
    """Recover the EnumType for a receiver that CONSTRUCTS an enum variant."""
    from sushi_lang.semantics.ast import EnumConstructor

    if isinstance(receiver, EnumConstructor):
        resolved = getattr(receiver, 'resolved_enum_type', None)
        if resolved is not None:
            return resolved
        return codegen.enum_table.by_name.get(receiver.enum_name)

    if not isinstance(receiver, DotCall):
        return None

    # A DotCall is `X.Y(args)`, and only SOME of those construct a variant. Pass 2 stamps
    # `resolved_enum_type` on a Result/Maybe METHOD call too, where it names the enum the
    # method was called ON rather than what the call RETURNS -- so claiming every node
    # that carries the annotation typed `go().realise("err")` as `Result<string, StdError>`
    # when its value is a `string`. That wrong answer then reached `_own_receiver_temp`'s
    # caller as a non-None semantic type, which skipped the temp registration entirely and
    # leaked the string (#293).
    #
    # The question this function is for is "does this node construct a variant", so ask it:
    # `Ok` and `Err` are variants of Result, `realise` is not. A method that is not a
    # variant falls through to the stamp, which carries the type of the VALUE.
    enum_type = getattr(receiver, 'resolved_enum_type', None)
    if enum_type is None and isinstance(receiver.receiver, Name):
        enum_type = codegen.enum_table.by_name.get(receiver.receiver.id)
    if enum_type is None:
        return None

    get_variant = getattr(enum_type, 'get_variant', None)
    if get_variant is None or get_variant(receiver.method) is None:
        return None
    return enum_type


def get_resolved_type(expr: Union[MethodCall, DotCall], type_attr: str) -> Optional['Type']:
    """Extract resolved type from expr if present."""
    if hasattr(expr, type_attr):
        resolved_type = getattr(expr, type_attr)
        if resolved_type is not None:
            return resolved_type
    return None


def infer_semantic_type(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                        receiver_value: Optional[ir.Value], expected_prefix: str,
                        expected_type_class) -> Optional['Type']:
    """Unified type inference for generic types."""
    receiver = expr.receiver

    if expected_type_class == EnumType:
        resolved_type = get_resolved_type(expr, 'resolved_enum_type')
        if resolved_type is not None:
            return resolved_type
    elif expected_type_class == StructType:
        resolved_type = get_resolved_type(expr, 'resolved_struct_type')
        if resolved_type is not None:
            return resolved_type

    if expected_type_class == EnumType:
        if receiver_value is None:
            receiver_value = codegen.expressions.emit_expr(receiver)
        return infer_generic_enum_type(codegen, receiver, receiver_value, expected_prefix)
    elif expected_type_class == StructType:
        return infer_generic_struct_type(codegen, receiver, expected_prefix)

    return None


def emit_receiver_as_pointer(codegen: 'LLVMCodegen', receiver: Expr,
                             semantic_type: Optional['Type'] = None) -> Optional[ir.Value]:
    """Emit receiver as pointer (alloca) for mutation methods."""
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
        if type_utils.is_reference_parameter(codegen, receiver.id):
            return codegen.builder.load(slot, name=f"{receiver.id}_ref_ptr")
        return slot

    # A captured collection read as `__closure_env.<name>` (any struct-field List/Own).
    # try_get_struct_alloca recurses through the env reference param and GEPs to the
    # field, yielding a pointer to the List/Own so mutating methods work in the body.
    if isinstance(receiver, MemberAccess):
        from sushi_lang.backend.expressions.structs import try_get_struct_alloca
        return try_get_struct_alloca(codegen, receiver)

    return _spill_receiver(codegen, receiver, semantic_type)


_SPILL_SEQ = itertools.count()


def _spill_receiver(codegen: 'LLVMCodegen', receiver: Expr,
                    semantic_type: Optional['Type']) -> Optional[ir.Value]:
    """Park a receiver that names no storage in a slot, and give it an owner if it needs one."""
    from sushi_lang.backend.destructors import needs_cleanup, resolve_named_type
    from sushi_lang.backend.expressions.memory import expression_is_temporary

    value = codegen.expressions.emit_expr(receiver)
    if value is None:
        return None

    resolved = resolve_named_type(codegen, semantic_type) if semantic_type is not None else None
    if (resolved is not None and needs_cleanup(resolved)
            and expression_is_temporary(codegen, receiver)):
        name = f"__recv_temp_{next(_SPILL_SEQ)}"
        return codegen.memory.create_local(name, value.type, value, resolved)

    slot = codegen.memory.entry_alloca(value.type, "recv_temp_slot")
    codegen.builder.store(value, slot)
    return slot
