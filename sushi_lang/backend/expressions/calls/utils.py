"""Utility functions for method call emission."""
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


def stamped_semantic_type(codegen: 'LLVMCodegen', expr: Expr) -> Optional['Type']:
    """The type the typecheck pass recorded for this expression, or None.

    The one reader of the typecheck pass's type stamps. A shape with no stamp -- or a stamp the typecheck pass
    left abstract -- answers None, so a caller can fall back to its own reconstruction.
    """
    from sushi_lang.semantics.ast import IndexAccess, TryExpr
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    from sushi_lang.semantics.typesys import UnknownType

    if isinstance(expr, TryExpr):
        stamped = getattr(expr, 'inferred_unwrapped_type', None)
    elif isinstance(expr, (Call, MethodCall, DotCall)):
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
    # (#240). A no-op whenever the typecheck pass already handed over the interned instance, which it
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

    # Strategy 2: a struct-field member access. Resolving the field's semantic type is
    # what lets List/Own methods claim the call instead of the dynamic-array dispatch,
    # which crashes on a List backing struct.
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

    # Strategy 3: a chained method call, which neither strategy above can see. The typecheck pass
    # already typed it. The three are disjoint by node type, so the order is not a
    # priority.
    stamped = stamped_semantic_type(codegen, receiver)
    if isinstance(stamped, ReferenceType):
        stamped = stamped.referenced_type
    if isinstance(stamped, StructType) and stamped.name.startswith(prefix):
        return stamped

    return None


def _stdlib_call_return_enum(codegen: 'LLVMCodegen', func_name: str) -> Optional[EnumType]:
    """The Result/Maybe enum a direct stdlib-module call returns, or None."""
    from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType
    from sushi_lang.backend.generics.result_builder import intern_result

    enums = codegen.enum_table.by_name
    if func_name == 'getenv':
        return enums.get('Maybe<string>')

    result_specs = {
        'sleep': (BuiltinType.I32, 'StdError'),
        'msleep': (BuiltinType.I32, 'StdError'),
        'usleep': (BuiltinType.I32, 'StdError'),
        'nanosleep': (BuiltinType.I32, 'StdError'),
        'now': (BuiltinType.I64, 'StdError'),
        'monotonic_ns': (BuiltinType.I64, 'StdError'),
        'setenv': (BuiltinType.I32, 'EnvError'),
        'file_size': (BuiltinType.I64, 'FileError'),
        'remove': (BuiltinType.I32, 'FileError'),
        'rename': (BuiltinType.I32, 'FileError'),
        'copy': (BuiltinType.I32, 'FileError'),
        'mkdir': (BuiltinType.I32, 'FileError'),
        'rmdir': (BuiltinType.I32, 'FileError'),
        'read_dir': (DynamicArrayType(BuiltinType.STRING), 'FileError'),
        'mtime': (BuiltinType.I64, 'FileError'),
        'ctime': (BuiltinType.I64, 'FileError'),
        'mode': (BuiltinType.I32, 'FileError'),
        'is_symlink': (BuiltinType.BOOL, 'FileError'),
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

        # A known concrete enum is AUTHORITATIVE: answer None rather than falling through
        # to the layout heuristic, which cannot tell `Maybe<Color>` from
        # `Result<i32, StdError>` -- they share one LLVM type.
        if isinstance(semantic_type, EnumType):
            return semantic_type if semantic_type.name.startswith(prefix) else None

        if isinstance(semantic_type, GenericTypeRef):
            type_name = str(semantic_type)  # e.g., "Result<i32>"
            if type_name in codegen.enum_table.by_name:
                return codegen.enum_table.by_name[type_name] if type_name.startswith(prefix) else None

    # Strategy 1b: a method-call or `??` receiver carries the typecheck pass's stamp. Authoritative
    # like Strategy 1 -- the uniform enum layout (#300) makes the fallback heuristic unable
    # to tell `Maybe<string>` from `Result<i32, StdError>`.
    for stamp_attr in ('inferred_return_type', 'inferred_unwrapped_type'):
        stamped = getattr(receiver, stamp_attr, None)
        if isinstance(stamped, EnumType):
            return stamped if stamped.name.startswith(prefix) else None

    # Strategy 2: from the call's return type. Both lookups build the TWO-argument interned
    # name -- a one-argument `Result<T>` can never match, so they always missed and fell
    # through to layout matching, which cannot tell two same-shaped Results apart.
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
            result_type = codegen.function_return_types.lookup(
                func_name, codegen.emitting_unit, codegen.scope)
            if result_type is not None:
                if isinstance(result_type, EnumType) and result_type.name.startswith(prefix):
                    return result_type

            # Stdlib module functions are in no function table. Under the uniform enum
            # layout (#300) the fallback heuristic let the wrong family claim
            # `getenv(x).realise(d)`, so these resolve from the facts the instantiate pass registers
            # and a non-matching prefix answers None.
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
        # Local alloca, or the global backing a constant (#248). The semantic type comes
        # from the const table too: a constant is not a local, so the memory manager has no
        # type for it.
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
        # An inline variant-construction receiver (`Suit.Hearts().hash()`). Without this
        # the handlers map the LLVM layout back to a language type and fail as CE0019.
        semantic_type = _infer_enum_construction_type(codegen, receiver)
        if semantic_type is None:
            # Any other chained receiver. Enum construction keeps first place as the
            # narrower question, and the order is only safe because the probe above answers
            # the question it is named for -- claiming any node with `resolved_enum_type`
            # typed `go().realise("err")` as its receiver's enum and leaked the string
            # (#293).
            # The typecheck pass's stamp first, then reconstruction -- an inline `from([...])`
            # receiver carries no stamp, so it had no type here and therefore no owner.
            from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
            semantic_type = infer_expr_semantic_type(codegen, receiver)
            receiver_value = _own_receiver_temp(
                codegen, receiver, receiver_value, semantic_type)

    return receiver_value, receiver_type, semantic_type


def _own_receiver_temp(codegen: 'LLVMCodegen', receiver: Expr, value: ir.Value,
                       semantic_type: Optional['Type']) -> ir.Value:
    """Give a receiver that nobody owns an owner, and hand back what to read it through.

    A dynamic array is read through a GEP, so its owning slot IS its address and is handed
    over -- exactly as the `Name` branch above hands over its alloca. Returning the VALUE
    would make the array dispatcher spill it a second time, giving the same temporary two
    owners and freeing it twice.
    """
    from sushi_lang.backend.expressions.memory import own_temporary

    slot = own_temporary(codegen, receiver, value, semantic_type)
    if slot is not None and codegen.types.is_dynamic_array_type(value.type):
        return slot
    return value


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

    # Only SOME `X.Y(args)` construct a variant, but the typecheck pass stamps `resolved_enum_type` on
    # a Result/Maybe METHOD call too, where it names the enum called ON rather than what is
    # RETURNED. Claiming every stamped node skipped the temp registration and leaked (#293).
    # So ask the question this function is named for: `Ok` is a variant, `realise` is not.
    enum_type = getattr(receiver, 'resolved_enum_type', None)
    if enum_type is None and isinstance(receiver.receiver, Name):
        # Local-wins (#296): a local named after the enum shadows it.
        if codegen.memory.find_semantic_type(receiver.receiver.id) is None:
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
        # Local alloca, or the global backing a constant (#248). The List/HashMap probes
        # share the names `len` and `get`, so they ask for the address first -- safe,
        # because both gate on a `List<`/`HashMap<` semantic type.
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


def _spill_receiver(codegen: 'LLVMCodegen', receiver: Expr,
                    semantic_type: Optional['Type']) -> Optional[ir.Value]:
    """Park a receiver that names no storage in a slot, and give it an owner if it needs one."""
    from sushi_lang.backend.expressions.memory import park_value

    value = codegen.expressions.emit_expr(receiver)
    if value is None:
        return None

    return park_value(codegen, receiver, value, semantic_type)


def emit_borrowed_arg(codegen: 'LLVMCodegen', arg: Expr,
                      semantic_type: Optional['Type'] = None) -> ir.Value:
    """Emit ONE argument a BUILT-IN callee only borrows, giving an owning temporary an owner.

    THE built-in half of the call-argument seam, and the twin of `_own_receiver_temp`. A
    DECLARED callee reaches `settle_call_arguments`, which reads the parameter's mode off
    the signature; a built-in declares no parameters, so its emitter builds its own
    argument list and there is no mode to read. Every argument routed here is therefore a
    borrow by construction -- a built-in that TAKES ownership (List.push, HashMap.insert,
    Own.alloc) is a CONTAINER callee and goes through `consume` instead.

    Without this the temporary behind `src.contains(src.s(2, 5))` had no owner at all and
    leaked one block per owning argument (#475), which is the hole #358 left.

    `semantic_type` is the parameter's own type, for an emitter that already knows it --
    a HashMap key, say. It beats reconstructing the argument's type from its AST, which
    is only the fallback.
    """
    from sushi_lang.backend.expressions.memory import own_temporary
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type

    value = codegen.expressions.emit_expr(arg)
    if value is None:
        return value
    if semantic_type is None:
        semantic_type = infer_expr_semantic_type(codegen, arg)
    own_temporary(codegen, arg, value, semantic_type)
    return value


def emit_cstr_arg(codegen: 'LLVMCodegen', arg: Expr) -> ir.Value:
    """Marshal a `string` argument into a C `char*` the CALLER frees at scope exit.

    THE marshal seam for every callee that wants a C string: an FFI extern, a stdlib symbol,
    and `open()`. Only the FFI arm used to register the copy, so the other two leaked one
    block per string argument -- with a literal argument as much as with an owning one, since
    the copy is a separate allocation either way (#292, #291).

    The COPY is what this seam frees. The string it copied from is an ordinary borrowed
    argument, so it goes through `emit_borrowed_arg` like every other one (#475).
    """
    return marshal_cstr(codegen, emit_borrowed_arg(codegen, arg))


def marshal_cstr(codegen: 'LLVMCodegen', value: ir.Value) -> ir.Value:
    """The same seam for an already-emitted `string` value."""
    c_str = codegen.runtime.strings.emit_to_cstr(value)
    codegen.memory.register_cstr(c_str)
    return c_str
