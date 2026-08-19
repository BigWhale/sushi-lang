"""Method call validation."""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, EnumType, FunctionType, StructType, ForeignPtrType
from sushi_lang.semantics.ast import MethodCall, Name
from ..compatibility import types_compatible
from ..propagation import propagate_declared_type_to_value
from ..utils import is_array_destroyed, mark_array_destroyed, reject_spread_args,\
    resolve_declared_type

if TYPE_CHECKING:
    from .. import TypeValidator


def _reject_immutable_poke_receiver(validator: 'TypeValidator', call: MethodCall) -> None:
    """A `poke self` method call writes through its receiver's ADDRESS (#327)."""
    from sushi_lang.semantics.ast import DotCall, MemberAccess

    root = call.receiver
    while isinstance(root, (MethodCall, DotCall, MemberAccess)):
        root = root.receiver if not isinstance(root, MemberAccess) else root.obj
    if not isinstance(root, Name):
        er.emit(validator.reporter, er.ERR.CE2404, call.receiver.loc,
                expr=f"<expression>.{call.method}() receiver")
        return
    if (root.id not in validator.variable_types
            and root.id in validator.const_table.by_name):
        er.emit(validator.reporter, er.ERR.CE2400, root.loc, name=root.id)


def validate_method_call(validator: 'TypeValidator', call: MethodCall) -> None:
    """Validate method call - receiver type, method existence, argument types."""
    # A bloom spread `arr...` is never valid in a method call (methods cannot be
    # variadic, CE0115). Reject early so it never reaches the backend (CE0120).
    if reject_spread_args(validator, call.args):
        return

    # Check for use-after-destroy (CE2024)
    if isinstance(call.receiver, Name):
        if is_array_destroyed(validator, call.receiver.id):
            er.emit(validator.reporter, er.ERR.CE2024, call.receiver.loc, name=call.receiver.id)
            return

    validator.validate_expression(call.receiver)

    receiver_type = validator.infer_expression_type(call.receiver)

    # CE5011: a foreign ptr is an opaque handle - no methods (no hash, no
    # string form, nothing). Wrap it in a struct and extend the struct instead.
    if isinstance(receiver_type, ForeignPtrType):
        er.emit(validator.reporter, er.ERR.CE5011, call.loc, method=call.method)
        return

    if receiver_type is None and isinstance(call.receiver, Name):
        type_name = call.receiver.id
        if type_name == "List" and call.method in ("new", "with_capacity"):
            from sushi_lang.semantics.generics.list import is_builtin_list_method
            if is_builtin_list_method(call.method):
                expected_args = {"new": 0, "with_capacity": 1}
                expected = expected_args.get(call.method, 0)
                got = len(call.args)
                if got != expected:
                    er.emit(validator.reporter, er.ERR.CE2053, call.loc,
                            method=call.method, expected=expected, got=got)
            return
        elif type_name == "HashMap" and call.method == "new":
            # The receiver is a type NAME, so the concrete HashMap type comes from the
            # propagation stamp -- reading it is what makes the key gate reachable (#272).
            from sushi_lang.semantics.generics.hashmap import validate_hashmap_method_with_validator
            hashmap_type = getattr(call, 'resolved_struct_type', None)
            if isinstance(hashmap_type, StructType) and hashmap_type.name.startswith("HashMap<"):
                validate_hashmap_method_with_validator(call, hashmap_type, validator.reporter, validator)
            elif len(call.args) != 0:
                er.emit(validator.reporter, er.ERR.CE2016, call.loc,
                        method=call.method, expected=0, got=len(call.args))
            return

    if receiver_type is None:
        return

    is_generic_struct = (isinstance(receiver_type, StructType) and
                         (receiver_type.name.startswith("Own<") or
                          receiver_type.name.startswith("HashMap<") or
                          receiver_type.name.startswith("List<")))

    # StructType for perk and auto-derived methods; FunctionType because a function value
    # carries clone(). Without the latter a method call on a fn value was never validated
    # AT ALL, and reached codegen to die mangling `fn(i32) - i32_clone`.
    if not isinstance(receiver_type, (BuiltinType, ArrayType, DynamicArrayType, EnumType, FunctionType, StructType)) and not is_generic_struct:
        return

    if isinstance(receiver_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.semantics.passes.types.arrays import is_builtin_array_method, validate_builtin_array_method
        if is_builtin_array_method(call.method):
            validate_builtin_array_method(call, receiver_type, validator.reporter, validator)

            if (call.method == "destroy" and
                isinstance(receiver_type, DynamicArrayType) and
                isinstance(call.receiver, Name)):
                mark_array_destroyed(validator, call.receiver.id)
            return

    if receiver_type == BuiltinType.STRING:
        from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method, validate_builtin_string_method_with_validator
        if is_builtin_string_method(call.method):
            validate_builtin_string_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    if receiver_type in [BuiltinType.STDIN, BuiltinType.STDOUT, BuiltinType.STDERR]:
        from sushi_lang.sushi_stdlib.src.io.stdio import is_builtin_stdio_method, validate_builtin_stdio_method_with_validator
        if is_builtin_stdio_method(call.method):
            validate_builtin_stdio_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    if receiver_type == BuiltinType.FILE:
        from sushi_lang.sushi_stdlib.src.io.files import is_builtin_file_method, validate_builtin_file_method_with_validator
        if is_builtin_file_method(call.method):
            validate_builtin_file_method_with_validator(call, validator.reporter, validator)
            return

    if isinstance(receiver_type, EnumType) and receiver_type.name.startswith("Result<"):
        from sushi_lang.semantics.generics.results import is_builtin_result_method, validate_result_method_with_validator
        if is_builtin_result_method(call.method):
            validate_result_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    if isinstance(receiver_type, EnumType) and receiver_type.name.startswith("Maybe<"):
        from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method, validate_maybe_method_with_validator
        if is_builtin_maybe_method(call.method):
            validate_maybe_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("Own<"):
        from sushi_lang.semantics.generics.own import is_builtin_own_method, validate_own_method_with_validator
        if is_builtin_own_method(call.method):
            validate_own_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("HashMap<"):
        from sushi_lang.semantics.generics.hashmap import is_builtin_hashmap_method, validate_hashmap_method_with_validator
        if is_builtin_hashmap_method(call.method):
            validate_hashmap_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("List<"):
        from sushi_lang.semantics.generics.list import is_builtin_list_method, validate_list_method_with_validator
        if is_builtin_list_method(call.method):
            validate_list_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    perk_method = validator.perk_impl_table.get_method(receiver_type, call.method)
    if perk_method is not None:
        # Found a perk method - validate it
        # A `poke self` / `peek self` perk method (#327): stamp the mode for Pass 3
        # and the backend, and reject a receiver with no address for the poke form --
        # the same rule as the extension arm below.
        perk_self_mode = getattr(perk_method, "self_mode", None)
        if perk_self_mode is not None:
            call.callee_self_mode = perk_self_mode
            if perk_self_mode == "poke":
                _reject_immutable_poke_receiver(validator, call)

        _stamp_param_modes(call, perk_method)

        expected = len(perk_method.params)
        got = len(call.args)
        if got != expected:
            er.emit(validator.reporter, er.ERR.CE2007, call.loc,
                   method=call.method, expected=expected, got=got)
            return

        for _i, (arg, param) in enumerate(zip(call.args, perk_method.params, strict=False)):
            # PROPAGATE before validating -- see the extension arm below (#387).
            expected_ty = propagate_declared_type_to_value(validator, arg, param.ty)

            validator.validate_expression(arg)
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and expected_ty is not None:
                if not types_compatible(validator, arg_type, expected_ty):
                    er.emit(validator.reporter, er.ERR.CE2023, arg.loc if hasattr(arg, 'loc') else call.loc,
                           method=call.method, expected=display_type(expected_ty), got=display_type(arg_type))

        if perk_method.ret is not None:
            call.inferred_return_type = resolve_declared_type(validator, perk_method.ret)
        return

    if isinstance(receiver_type, BuiltinType) and receiver_type in [
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
        BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
    ]:
        from sushi_lang.semantics.generics.primitives import has_primitive_method, validate_primitive_method
        # A method name may be a builtin primitive method in general (to_str/hash/to_bits)
        # but only exist on some types (e.g. to_bits only on f32/f64). Ask about THIS
        # receiver; otherwise fall through so a call like i32.to_bits() gets a clean
        # unknown-method error.
        if has_primitive_method(receiver_type, call.method):
            validate_primitive_method(call, receiver_type, validator.reporter)
            return

    # Check for built-in methods on a function value (clone). A closure read out of a
    # struct field or a container is a borrow, so consuming it is CE2411 and `.clone()` is
    # the escape the diagnostic names -- it has to resolve here, or dispatch falls through
    # to the extension path and mangles the type name into a symbol nobody defines.
    if isinstance(receiver_type, FunctionType):
        from sushi_lang.semantics.generics.closures import (
            is_builtin_function_method, validate_function_method_with_validator,
        )
        if is_builtin_function_method(call.method):
            validate_function_method_with_validator(
                call, receiver_type, validator.reporter, validator)
            return

    if isinstance(receiver_type, StructType) and call.method == "hash":
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method
        struct_hash_method = get_builtin_method(receiver_type, "hash")
        if struct_hash_method is not None:
            struct_hash_method.semantic_validator(call, receiver_type, validator.reporter)
            return

    if isinstance(receiver_type, EnumType) and call.method == "hash":
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method
        enum_hash_method = get_builtin_method(receiver_type, "hash")
        if enum_hash_method is not None:
            enum_hash_method.semantic_validator(call, receiver_type, validator.reporter)
            return

    # Check for auto-derived struct/enum clone (#134) - AFTER perks. Own/List/HashMap
    # named structs keep their own method paths and are not registered here.
    if isinstance(receiver_type, (StructType, EnumType)) and call.method == "clone":
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method
        clone_method = get_builtin_method(receiver_type, "clone")
        if clone_method is not None:
            clone_method.semantic_validator(call, receiver_type, validator.reporter)
            return

    # A generic-target extension is resolved through the monomorphized copy Pass 1.6 put in
    # the extension table under the concrete receiver type. There used to be a second lookup
    # here, by base name -- it repeated the lookup above verbatim, so it could only ever find
    # None again, and under #393 asking by base name is the wrong question anyway: a concrete
    # target answers its own instantiation and no other.
    method = validator.extension_table.get_method(receiver_type, call.method)

    if method is None:
        er.emit(validator.reporter, er.ERR.CE2008, call.loc, name=f"{display_type(receiver_type)}.{call.method}")
        return

    # A `poke self` / `peek self` method (#327) receives its receiver's ADDRESS. Stamp
    # the mode on the call node -- Pass 3 treats a poke-self call as a WRITE to the
    # receiver root (the CE2408/CE2412 gates), and the backend passes a pointer instead
    # of a value. Both read the stamp instead of re-resolving the method.
    self_mode = getattr(method, "self_mode", None)
    if self_mode is not None:
        call.callee_self_mode = self_mode
        if self_mode == "poke":
            _reject_immutable_poke_receiver(validator, call)

    _stamp_param_modes(call, method)

    expected_params = method.params
    actual_args = call.args

    if len(actual_args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(receiver_type)}.{call.method}", expected=len(expected_params), got=len(actual_args))

    for i, (arg, param) in enumerate(zip(actual_args, expected_params, strict=False)):
        # PROPAGATE before validating, as a plain function's call site does: a generic enum
        # or struct constructor handed to a method parameter is unstamped otherwise, and
        # reached the backend as a CE0113 (#387, the argument half).
        expected_ty = propagate_declared_type_to_value(validator, arg, param.ty)

        validator.validate_expression(arg)

        if expected_ty is not None:  # Skip if parameter has unknown type
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, expected_ty):
                er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=display_type(expected_ty), got=display_type(arg_type))

    for i in range(len(expected_params), len(actual_args)):
        validator.validate_expression(actual_args[i])


def _stamp_param_modes(call, method) -> None:
    """Record the resolved method's declared parameter modes on the call node."""
    from sushi_lang.semantics.param_modes import CalleeKind, modes_for
    params = getattr(method, "params", None) or ()
    call.callee_param_modes = modes_for(params, CalleeKind.METHOD)
    call.callee_param_names = tuple(p.name for p in params)
    call.callee_param_types = tuple(p.ty for p in params)
