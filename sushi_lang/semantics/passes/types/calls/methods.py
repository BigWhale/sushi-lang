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


def instantiate_array_extension(validator: 'TypeValidator',
                                receiver_type: DynamicArrayType,
                                method_name: str):
    """Resolve a method miss on a `T[]` receiver against the array templates (ruling 3).

    A dynamic-array receiver has no instantiation the monomorphize pass could have
    collected -- the CALL SITE is what names the element type -- so a miss consults the
    `$array` templates here. The substituted signature enters the extension table (the
    next call site hits it directly, in this pass and in the backend), and the
    instantiation is queued for the analyzer's fixpoint round, which monomorphizes and
    checks the body copy after the per-unit loop.
    """
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY
    from sushi_lang.semantics.generics.types import substitute_type_params
    from sushi_lang.semantics.passes.collect.functions import ExtensionMethod, Param

    templates = validator.generic_extension_table.declarations(ARRAY_BASE_KEY, method_name)
    template = next((t for t in templates if not t.target_key), None)
    if template is None:
        return None

    element = receiver_type.base_type
    substitution = {template.type_params[0]: element}
    ret = (substitute_type_params(template.ret_type, substitution)
           if template.ret_type is not None else None)
    params = [Param(
        name=p.name,
        ty=substitute_type_params(p.ty, substitution) if p.ty is not None else None,
        name_span=p.name_span, type_span=p.type_span, index=p.index,
        is_variadic=getattr(p, "is_variadic", False),
        is_nom=getattr(p, "is_nom", False),
    ) for p in template.params]

    err = (substitute_type_params(template.err_type, substitution)
           if getattr(template, "err_type", None) is not None else None)
    concrete = ExtensionMethod(
        target_type=receiver_type, name=method_name, params=params, ret_type=ret,
        loc=template.loc, target_type_span=template.target_type_span,
        name_span=template.name_span, ret_span=template.ret_span,
        self_mode=template.self_mode, filename=template.filename,
        unit_name=template.unit_name,
        err_type=err, err_span=getattr(template, "err_span", None))
    validator.extension_table.add_method(concrete)
    validator.tables.pending_array_extensions.append((template, element))
    return concrete


def extension_call_result_type(validator: 'TypeValidator', method):
    """What a resolved extension call YIELDS: the bare return, or its channel Result.

    A `| E` method (ruling 1) returns the interned Result@(ret, E) at every call site;
    `??` and the chain gate (CE2515) both read that answer.
    """
    from ..utils import resolve_declared_type

    ret = resolve_declared_type(validator, method.ret_type)
    err = getattr(method, "err_type", None)
    if err is None:
        return ret
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    resolved_err = resolve_unknown_type(
        err, validator.struct_table.by_name, validator.enum_table.by_name)
    return ensure_result_type_in_table(
        validator.enum_table, ret, resolved_err,
        struct_table=validator.struct_table.by_name)


def _unhandled_channel_payload(receiver_type):
    """The Ok/Some payload when the receiver is result-like or maybe-like, else None."""
    if not isinstance(receiver_type, EnumType):
        return None
    ok_variant = receiver_type.get_variant("Ok")
    err_variant = receiver_type.get_variant("Err")
    if ok_variant and err_variant and len(ok_variant.associated_types) == 1:
        return ok_variant.associated_types[0]
    some_variant = receiver_type.get_variant("Some")
    none_variant = receiver_type.get_variant("None")
    if (some_variant and none_variant and len(some_variant.associated_types) == 1
            and len(none_variant.associated_types) == 0):
        return some_variant.associated_types[0]
    return None


def _reject_unhandled_channel_chain(validator: 'TypeValidator', call: MethodCall,
                                    receiver_type) -> bool:
    """CE2515, the resolution FALLBACK of ruling 5.

    Fires only when resolution missed on a Result/Maybe receiver AND the method exists
    on the payload type -- which is what tells an unhandled channel from a typo. The
    diagnostic is relational: the primary names the missing method, the note points at
    the call that returned the wrapper, and the help spells the `??` fix.
    """
    from sushi_lang.semantics.ast import DotCall

    payload = _unhandled_channel_payload(receiver_type)
    if payload is None or not _method_exists_on(validator, payload, call.method):
        return False

    diag = er.emit_with(validator.reporter, er.ERR.CE2515, call.loc,
                        method=call.method, wrapper=display_type(receiver_type))
    receiver = call.receiver
    if getattr(receiver, "loc", None) is not None:
        diag.note("the unhandled channel comes from this call", receiver.loc)
    fix = ""
    if isinstance(receiver, (MethodCall, DotCall)):
        fix = f" -- e.g. '{receiver.method}()??.{call.method}()'"
    diag.help("handle the channel first: match on it, '.realise(default)', or "
              f"propagate with '??'{fix}").emit()
    return True


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
        # A static on a GATED generic obeys the scope like the type does: behind
        # an aliased import the bare name is refused, and the qualified form is
        # what folds to this shape (#506, decision A-strict).
        from sushi_lang.semantics.namespaces import GATED_GENERIC_NAMES
        if (type_name in GATED_GENERIC_NAMES
                and getattr(call, "namespace_ref", None) is None):
            from sushi_lang.semantics.passes.types.visibility import (
                reject_out_of_scope_type)
            if reject_out_of_scope_type(validator, type_name, call.receiver.loc):
                return
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
        # A `poke self` / `peek self` perk method (#327): stamp the mode for the borrow pass
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
            er.emit(validator.reporter, er.ERR.CE0023, call.loc,
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

    # The family order from here on matches the codegen dispatcher exactly --
    # derived hash, derived clone, function clone, primitive, extension. The receiver
    # kinds are disjoint, so the order is arbitrary; stating ONE order in both layers
    # is the point (#273), and tests/unit/test_method_resolution_family_order.py pins it.
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

    # A generic-target extension is resolved through the monomorphized copy that the monomorphize pass put in
    # the extension table under the concrete receiver type. There used to be a second lookup
    # here, by base name -- it repeated the lookup above verbatim, so it could only ever find
    # None again, and under #393 asking by base name is the wrong question anyway: a concrete
    # target answers its own instantiation and no other.
    method = validator.extension_table.get_method(receiver_type, call.method)

    if method is None and isinstance(receiver_type, DynamicArrayType):
        method = instantiate_array_extension(validator, receiver_type, call.method)

    if method is None:
        if _reject_unhandled_channel_chain(validator, call, receiver_type):
            return
        er.emit(validator.reporter, er.ERR.CE2008, call.loc, name=f"{display_type(receiver_type)}.{call.method}")
        return

    # A `poke self` / `peek self` method (#327) receives its receiver's ADDRESS. Stamp
    # the mode on the call node -- the borrow pass treats a poke-self call as a WRITE to the
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


def _method_exists_on(validator: 'TypeValidator', payload, method_name: str) -> bool:
    """Whether a method of this name would resolve on the payload type."""
    from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY

    if builtin_method_exists(payload, method_name):
        return True
    if validator.extension_table.get_method(payload, method_name) is not None:
        return True
    if validator.perk_impl_table.get_method(payload, method_name) is not None:
        return True
    if isinstance(payload, DynamicArrayType):
        if validator.generic_extension_table.declarations(ARRAY_BASE_KEY, method_name):
            return True
    name = getattr(payload, "name", None)
    if isinstance(name, str) and "<" in name:
        base_name = name.split("<")[0]
        if validator.generic_extension_table.find_applicable(
                base_name, method_name, name) is not None:
            return True
    return False
