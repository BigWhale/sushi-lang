"""Method call validation."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, EnumType, FunctionType, StructType, ForeignPtrType
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.param_modes import ParamMode, receiver_mode
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
    template = next((t for t in templates
                     if not t.target_key
                     and not getattr(t, "method_type_params", ())), None)
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
    _queue_extension_instantiation(validator, template, receiver_type, (element,), ())
    return concrete


def _queue_extension_instantiation(validator: 'TypeValidator', template, target_type,
                                   receiver_args, method_type_args) -> None:
    """Queue one monomorphization request, deduped by (receiver, method, margs)."""
    key = (str(target_type), template.name,
           tuple(str(a) for a in method_type_args))
    tables = validator.tables
    if key in tables.queued_extension_keys:
        return
    tables.queued_extension_keys.add(key)
    tables.pending_extension_instantiations.append(
        (template, target_type, tuple(receiver_args), tuple(method_type_args)))


# Returned by resolve_method_generic_extension when it found the template but the
# call cannot use it (CE2063 already reported): the caller must not add CE2008 on top.
RESOLUTION_REPORTED = object()


@dataclass(frozen=True)
class ResolvedMethod:
    """A user-written method a receiver answers to, and which table answered."""
    method: object
    is_perk: bool


def resolve_extension_method(validator: 'TypeValidator', receiver_type,
                             method_name: str, call=None, report: bool = True):
    """The three EXTENSION rungs, in the order every caller reads them.

    The extension table, then a `T[]` template instantiated at this call site, then a
    method-generic template solved from the arguments. Returns None, the method, or
    RESOLUTION_REPORTED when the last rung emitted CE2063 and the caller must not add
    CE2008 on top.

    A caller with no call NODE -- the `foreach` protocol asks for a nullary `next()` --
    passes `call=None`, and the method-generic rung is skipped: it solves its type
    arguments from the call's own arguments, and there are none to read.
    """
    method = validator.extension_table.get_method(receiver_type, method_name)

    if method is None and isinstance(receiver_type, DynamicArrayType):
        method = instantiate_array_extension(validator, receiver_type, method_name)

    if method is None and call is not None:
        resolved = resolve_method_generic_extension(validator, receiver_type, call,
                                                   report=report)
        if resolved is RESOLUTION_REPORTED:
            return RESOLUTION_REPORTED
        method = resolved

    return method


def resolve_method(validator: 'TypeValidator', receiver_type, method_name: str,
                   call=None, report: bool = True):
    """The whole ladder for a USER-written method: a perk implementation, then extensions.

    A perk implementation is the sanctioned override and wins
    (`docs/design/method-resolution.md`), so it is asked first. Built-in methods are NOT
    on this ladder: each family resolves its own before the ladder is reached, and that
    order is pinned by `tests/unit/test_method_resolution_family_order.py`.

    Returns a `ResolvedMethod`, None, or RESOLUTION_REPORTED. Three callers read it --
    the validation half, the inference half, and the `foreach` protocol -- and the point
    of one function is that a fourth cannot drift.
    """
    perk_method = validator.perk_impl_table.get_method(receiver_type, method_name)
    if perk_method is not None:
        return ResolvedMethod(method=perk_method, is_perk=True)

    method = resolve_extension_method(validator, receiver_type, method_name,
                                      call=call, report=report)
    if method is RESOLUTION_REPORTED:
        return RESOLUTION_REPORTED
    if method is None:
        return None
    return ResolvedMethod(method=method, is_perk=False)


def _find_method_generic_template(validator: 'TypeValidator', receiver_type,
                                  method_name: str):
    """The method-generic template a receiver answers to, plus its receiver substitution.

    Three receiver shapes: a `T[]` (the `$array` templates, element substitution), an
    instantiation of a generic base (`List<i32>` -- base-name lookup, args from the
    interned type), and any concrete type (its display name is the base key, empty
    substitution). Returns (None, None) when no margs template answers.
    """
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY

    def margs_template(declarations, target_key=""):
        for t in declarations:
            if not getattr(t, "method_type_params", ()):
                continue
            if t.target_key and t.target_key != target_key:
                continue
            return t
        return None

    table = validator.generic_extension_table
    if isinstance(receiver_type, DynamicArrayType):
        template = margs_template(table.declarations(ARRAY_BASE_KEY, method_name))
        if template is not None:
            subst = ({template.type_params[0]: receiver_type.base_type}
                     if template.type_params else {})
            return template, subst
        # A concrete-element array template files under its display name below.

    generic_base = getattr(receiver_type, "generic_base", None)
    if generic_base is not None:
        template = margs_template(
            table.declarations(generic_base, method_name),
            target_key=getattr(receiver_type, "name", ""))
        if template is not None:
            names = [p.name if hasattr(p, "name") else p for p in template.type_params]
            args = getattr(receiver_type, "generic_args", None) or ()
            subst = dict(zip(names, args, strict=False)) if not template.target_key else {}
            return template, subst

    template = margs_template(table.declarations(display_type(receiver_type), method_name))
    if template is not None:
        return template, {}
    return None, None


def resolve_method_generic_extension(validator: 'TypeValidator', receiver_type, call,
                                     report: bool = True):
    """Resolve a call against a method-generic template (`name@(U)`, Phase 4).

    The receiver substitution and the method-parameter unification COMPOSE: the
    receiver names T, the arguments solve U. The substituted signature is returned as
    a synthetic ExtensionMethod -- never added to the ExtensionTable, which keys on
    (type, name) alone -- the solved margs are stamped on the call (the backend's half
    of the symbol identity), and the instantiation is queued for the analyzer's
    fixpoint round. Returns None (no template), RESOLUTION_REPORTED (CE2063 emitted),
    or the synthetic method.
    """
    from sushi_lang.semantics.ast import Lambda
    from sushi_lang.semantics.generics.types import substitute_type_params
    from sushi_lang.semantics.generics.unify import unify_types
    from sushi_lang.semantics.passes.collect.functions import ExtensionMethod, Param
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    template, receiver_subst = _find_method_generic_template(
        validator, receiver_type, call.method)
    if template is None:
        return None

    expected_params = [
        substitute_type_params(p.ty, receiver_subst) if p.ty is not None else None
        for p in template.params]

    type_param_map: dict = {}
    for expected, arg in zip(expected_params, call.args, strict=False):
        if expected is None:
            continue
        if isinstance(arg, Lambda):
            from sushi_lang.semantics.passes.types.visitor import infer_lambda_type
            arg_type = infer_lambda_type(validator, arg, stamp=False)
        else:
            arg_type = validator.infer_expression_type(arg)
        if arg_type is None:
            continue
        unify_types(expected, arg_type, type_param_map)

    margs_names = list(template.method_type_params)
    unsolved = [n for n in margs_names if n not in type_param_map]
    if unsolved:
        if report:
            names = ", ".join(f"'{n}'" for n in unsolved)
            er.emit_with(validator.reporter, er.ERR.CE2063, call.loc,
                         plural="s" if len(unsolved) > 1 else "",
                         names=names, method=call.method) \
                .help("annotate the lambda's parameter types "
                      "('|i32 x| ...'), or pass a named function -- a bare-param "
                      "lambda has no type of its own to infer from").emit()
            return RESOLUTION_REPORTED
        return None

    margs = tuple(
        resolve_unknown_type(type_param_map[n], validator.struct_table.by_name,
                             validator.enum_table.by_name)
        for n in margs_names)

    full_subst = dict(receiver_subst)
    full_subst.update(dict(zip(margs_names, margs, strict=True)))

    ret = (substitute_type_params(template.ret_type, full_subst)
           if template.ret_type is not None else None)
    err = (substitute_type_params(template.err_type, full_subst)
           if getattr(template, "err_type", None) is not None else None)
    params = [Param(
        name=p.name,
        ty=substitute_type_params(p.ty, full_subst) if p.ty is not None else None,
        name_span=p.name_span, type_span=p.type_span, index=p.index,
        is_variadic=getattr(p, "is_variadic", False),
        is_nom=getattr(p, "is_nom", False),
    ) for p in template.params]

    # A solved argument can name an instantiation nothing else in the program names
    # (risk 1): intern it NOW, so this very unit's bodies resolve against it.
    interner = getattr(validator.tables, "intern_generic_ref", None)
    if interner is not None:
        for ty in (ret, err, *(p.ty for p in params)):
            if ty is not None:
                interner(ty)

    concrete = ExtensionMethod(
        target_type=receiver_type, name=call.method, params=params, ret_type=ret,
        loc=template.loc, target_type_span=template.target_type_span,
        name_span=template.name_span, ret_span=template.ret_span,
        self_mode=template.self_mode, filename=template.filename,
        unit_name=template.unit_name,
        err_type=err, err_span=getattr(template, "err_span", None))

    call.callee_method_type_args = margs

    receiver_names = [p.name if hasattr(p, "name") else p for p in template.type_params]
    receiver_args = tuple(receiver_subst[n] for n in receiver_names)
    _queue_extension_instantiation(validator, template, receiver_type,
                                   receiver_args, margs)
    return concrete


def _reject_clone_of_resource(validator: 'TypeValidator', call: MethodCall,
                              receiver_type) -> bool:
    """CE2431: a resource type has no deep copy (HANDLES.md ruling R3).

    A derived clone copies field by field, so cloning a handle copies its descriptor
    number and leaves two values that both drop. The refusal reaches anything HOLDING
    one -- a struct field, an array element, a container -- because the copy happens one
    level down there just the same.

    Placed before every clone family rather than inside one: `.clone()` is registered on
    a struct, an enum, an array and a container by different seams, and the rule is the
    receiver's, not the seam's.
    """
    from sushi_lang.semantics.typesys import holds_declared_resource
    drops = validator.drop_type_names
    if not drops:
        return False

    def resolve(ty):
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        return resolve_unknown_type(ty, validator.struct_table.by_name,
                                    validator.enum_table.by_name)

    if not holds_declared_resource(receiver_type, drops, resolve=resolve):
        return False

    er.emit(validator.reporter, er.ERR.CE2431, call.loc,
            type=display_type(receiver_type))
    return True


def extension_call_result_type(validator: 'TypeValidator', method):
    """What a resolved extension call YIELDS: the bare return, or its channel Result.

    A `| E` method (ruling 1) returns the interned Result@(ret, E) at every call site;
    `??` and the chain gate (CE2515) both read that answer. One reader for both method
    kinds: a perk-implementation method spells its bare return `ret` and an
    ExtensionMethod spells it `ret_type`, and they yield by the same rule.
    """
    declared = getattr(method, "ret_type", None)
    if declared is None:
        declared = getattr(method, "ret", None)
    ret = resolve_declared_type(validator, declared)
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


def _reject_unreachable_receiver(validator: 'TypeValidator', call: MethodCall,
                                 mode) -> None:
    """A MARKED receiver must name storage the call can reach (#327, ruling R25).

    One check for both marked kinds, because they refuse the same thing for one reason:
    a `const` lives in read-only memory, so there is no frame slot to point a `poke` at
    and no owner to hand a `nom` away from. `stdout.close()` is the case that matters.

    They differ on a TEMPORARY. A `poke self` needs an address the caller keeps, so a
    call result is CE2404; a `nom self` takes ownership, and a temporary is owned by
    construction -- the same rule ruling R11 states for a match scrutinee.
    """
    from sushi_lang.semantics.ast import DotCall, MemberAccess

    root = call.receiver
    while isinstance(root, (MethodCall, DotCall, MemberAccess)):
        root = root.receiver
    if not isinstance(root, Name):
        if mode.by_pointer:
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
            mode = receiver_mode(perk_self_mode)
            if mode is not ParamMode.PEEK:
                _reject_unreachable_receiver(validator, call, mode)

        _stamp_param_modes(call, perk_method)

        # Arity and argument type read the SAME codes as the extension arm below: a perk
        # method and an extension method are one rule -- a user-written method on a type --
        # so one rule gets one code. CE0023 is in the INTERNAL family and documented as a
        # codegen check, and CE2023 says "dynamic array method" about whatever it is
        # handed; both were wrong here, and neither was visible until io/contracts made
        # `write(string)` a perk method.
        expected = len(perk_method.params)
        got = len(call.args)
        if got != expected:
            er.emit(validator.reporter, er.ERR.CE2009, call.loc,
                   name=f"{display_type(receiver_type)}.{call.method}", expected=expected, got=got)
            return

        for _i, (arg, param) in enumerate(zip(call.args, perk_method.params, strict=False)):
            # PROPAGATE before validating -- see the extension arm below (#387).
            expected_ty = propagate_declared_type_to_value(validator, arg, param.ty)

            validator.validate_expression(arg)
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and expected_ty is not None:
                if not types_compatible(validator, arg_type, expected_ty):
                    er.emit(validator.reporter, er.ERR.CE2006, arg.loc if hasattr(arg, 'loc') else call.loc,
                           index=_i + 1, expected=display_type(expected_ty), got=display_type(arg_type))

        if perk_method.ret is not None:
            call.inferred_return_type = extension_call_result_type(validator, perk_method)
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

    if call.method == "clone" and _reject_clone_of_resource(validator, call, receiver_type):
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
    method = resolve_extension_method(validator, receiver_type, call.method, call=call)
    if method is RESOLUTION_REPORTED:
        return

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
        mode = receiver_mode(self_mode)
        if mode is not ParamMode.PEEK:
            _reject_unreachable_receiver(validator, call, mode)

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
