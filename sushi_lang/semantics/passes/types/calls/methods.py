"""Method call validation."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, ForeignPtrType, FunctionType,
    StructType)
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.param_modes import ParamMode, receiver_mode
from sushi_lang.semantics.places import Step, walk_place
from ..arguments import check_arguments
from ..method_registry import METHOD_TYPE_REGISTRY, arity_of_family
from ..utils import is_array_destroyed, mark_array_destroyed, reject_spread_args,\
    resolve_declared_type

# A receiver that can answer a method at all. `Own@(T)`, `List@(T)` and `HashMap@(K, V)`
# are named StructTypes, so the tuple covers them with every other struct.
RECEIVERS_WITH_METHODS = (BuiltinType, ArrayType, DynamicArrayType, EnumType,
                          FunctionType, StructType)

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
        err_type=err, err_span=getattr(template, "err_span", None),
        is_static=bool(getattr(template, "is_static", False)))
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


def resolve_extension_method(validator: 'TypeValidator', receiver_type,
                             method_name: str, call=None, report: bool = True,
                             static: bool = False):
    """The three EXTENSION rungs, in the order every caller reads them.

    The extension table, then a `T[]` template instantiated at this call site, then a
    method-generic template solved from the arguments. Returns None, the method, or
    RESOLUTION_REPORTED when the last rung emitted CE2063 and the caller must not add
    CE2008 on top.

    A caller with no call NODE -- the `foreach` protocol asks for a nullary `next()` --
    passes `call=None`, and the method-generic rung is skipped: it solves its type
    arguments from the call's own arguments, and there are none to read.

    `static` says which of the two callable shapes is being asked for (#542). The two
    share one table, keyed on (type, name), so ONE filter at the end keeps them apart:
    an instance call may not reach a static, and a call on the type name may not reach
    an instance method. Without it a static was called with a receiver it never
    declared.
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

    if method is not None and bool(getattr(method, "is_static", False)) != static:
        return None

    return method


def resolve_method(validator: 'TypeValidator', receiver_type, method_name: str,
                   call=None, report: bool = True, static: bool = False):
    """The whole ladder for a USER-written method: a perk implementation, then extensions.

    A perk implementation is the sanctioned override and wins
    (`docs/design/method-resolution.md`), so it is asked first. Built-in methods are NOT
    on this ladder: each family resolves its own before the ladder is reached, and that
    order is pinned by `tests/unit/test_method_resolution_family_order.py`.

    Returns a `ResolvedMethod`, None, or RESOLUTION_REPORTED. Four callers read it --
    the validation half, the inference half, the `foreach` protocol and the static path
    -- and the point of one function is that a fifth cannot drift.

    `static` skips the perk rung outright: a perk holds no static (CE4014), so a
    contract can never answer a call written on the type name.
    """
    if not static:
        perk_method = validator.perk_impl_table.get_method(receiver_type, method_name)
        if perk_method is not None:
            return ResolvedMethod(method=perk_method)

    method = resolve_extension_method(validator, receiver_type, method_name,
                                      call=call, report=report, static=static)
    if method is RESOLUTION_REPORTED:
        return RESOLUTION_REPORTED
    if method is None:
        return None
    return ResolvedMethod(method=method)


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
    from sushi_lang.semantics.generics.pack_inference import (
        infer_call_arg_type, solve_leading_type_args)
    from sushi_lang.semantics.generics.types import substitute_type_params
    from sushi_lang.semantics.passes.collect.functions import ExtensionMethod, Param
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    template, receiver_subst = _find_method_generic_template(
        validator, receiver_type, call.method)
    if template is None:
        return None

    expected_params = [
        substitute_type_params(p.ty, receiver_subst) if p.ty is not None else None
        for p in template.params]

    margs_names = list(template.method_type_params)
    arg_types = [infer_call_arg_type(validator, arg) if expected is not None else None
                 for arg, expected in zip(call.args, expected_params, strict=False)]
    type_param_map, unsolved = solve_leading_type_args(
        template, arg_types, None, None, partial=True,
        param_types=expected_params, type_param_names=margs_names)
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
        err_type=err, err_span=getattr(template, "err_span", None),
        is_static=bool(getattr(template, "is_static", False)))

    call.callee_method_type_args = margs

    receiver_names = [p.name if hasattr(p, "name") else p for p in template.type_params]
    receiver_args = tuple(receiver_subst[n] for n in receiver_names)
    _queue_extension_instantiation(validator, template, receiver_type,
                                   receiver_args, margs)
    return concrete


def _perk_answers(validator: 'TypeValidator', call: MethodCall, receiver_type) -> bool:
    """Does a perk implementation answer this call? Not when a family beats the perk."""
    family = METHOD_TYPE_REGISTRY.claim(receiver_type, call.method, validator)
    if family is not None and family.beats_perk:
        return False
    return validator.perk_impl_table.get_method(receiver_type, call.method) is not None


def _reject_clone_of_resource(validator: 'TypeValidator', call: MethodCall,
                              receiver_type) -> bool:
    """CE2431: a resource type has no deep copy (HANDLES.md ruling R3).

    A derived clone copies field by field, so cloning a handle copies its descriptor
    number and leaves two values that both drop. The refusal reaches anything HOLDING
    one -- a struct field, an array element, a container -- because the copy happens one
    level down there just the same.

    Asked before every clone family rather than inside one: `.clone()` is registered on
    a struct, an enum, an array and a container by different seams, and the rule is the
    receiver's, not the seam's. The container and array families beat a perk, so the
    refusal comes before them too (#770); only a perk implementation that answers the
    call is the sanctioned override.
    """
    from sushi_lang.semantics.typesys import holds_declared_resource
    if call.method != "clone":
        return False
    drops = validator.drop_type_names
    if not drops:
        return False

    def resolve(ty):
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        return resolve_unknown_type(ty, validator.struct_table.by_name,
                                    validator.enum_table.by_name)

    if not holds_declared_resource(receiver_type, drops, resolve=resolve):
        return False

    er.emit_with(validator.reporter, er.ERR.CE2431, call.loc,
                 type=display_type(receiver_type)) \
        .help("a second owner of a handle is '.share()'; for a value that holds "
              "handles, build a new one from a '.share()' of each handle") \
        .emit()
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
    receiver_loc = getattr(receiver, "loc", None)
    if receiver_loc is not None:
        diag.note_at("the unhandled channel comes from this call", receiver_loc)
    fix = ""
    if isinstance(receiver, (MethodCall, DotCall)):
        fix = f" -- e.g. '{receiver.method}()??.{call.method}()'"
    diag.help("handle the channel first: match on it, '.realise(default)', or "
              f"propagate with '??'{fix}").emit()
    return True


def _reject_unreachable_receiver(validator: 'TypeValidator', call: MethodCall,
                                 mode) -> None:
    """A MARKED receiver must name storage the call can reach (#327, ruling R25).

    A `poke self` writes the receiver, so read-only storage cannot take it (CE2400); a
    temporary receiver is the borrow pass's rule (CE2429). A `peek self` receiver only
    reads, so it never arrives here.

    A `nom self` TAKES the receiver, and that is the borrow pass's rule for storage of
    either kind (CE2436, #726): `stdout.close()` and a constant's `release()` read one
    code. A temporary is owned by construction, so it is legal there -- the same rule
    ruling R11 states for a match scrutinee.
    """
    from sushi_lang.semantics.constant_borrow import reject_borrow_of_constant

    root = walk_place(call.receiver, Step.MEMBER | Step.INDEX | Step.CALL).name
    if root is None:
        return
    if root.id in validator.variable_types or mode.consumes:
        return
    # Only the WRITE asks the borrow gate. A unit variable has an address for a `poke
    # self`, and a take of either kind of unit-level storage is CE2436. The lookup is
    # SCOPED: the flat view is first-wins over the whole program, so it answered with
    # another unit's declaration of the same name (#685).
    reject_borrow_of_constant(validator.err, root.id, validator.const_sig(root.id),
                              root.loc, mode=mode.value)


def validate_method_call(validator: 'TypeValidator', call: MethodCall) -> None:
    """The method-call ladder: a built-in family, a perk implementation, an extension.

    Each rung is a named step that answers whether it took the call. The order is
    `docs/design/method-resolution.md`'s and the codegen dispatcher's alike, and the
    families are one table both halves of the pass read (`method_registry.py`, #751).
    """
    # A bloom spread `arr...` is never valid in a method call (methods cannot be
    # variadic, CE0115). Reject early so it never reaches the backend (CE0120).
    if reject_spread_args(validator, call.args):
        return

    if _reject_destroyed_receiver(validator, call):
        return

    validator.validate_expression(call.receiver)
    receiver_type = validator.infer_expression_type(call.receiver)

    # CE5011: a foreign ptr is an opaque handle - no methods (no hash, no
    # string form, nothing). Wrap it in a struct and extend the struct instead.
    if isinstance(receiver_type, ForeignPtrType):
        er.emit(validator.reporter, er.ERR.CE5011, call.loc, method=call.method)
        return

    if receiver_type is None:
        # A receiver that named no value may still name a TYPE: `List.new()` and
        # `HashMap.new()` are statics, and a static has no receiver to dispatch on.
        if isinstance(call.receiver, Name):
            _validate_type_name_call(validator, call, call.receiver.id)
        return

    # StructType for perk and auto-derived methods; FunctionType because a function value
    # carries clone(). Without the latter a method call on a fn value was never validated
    # AT ALL, and reached codegen to die mangling `fn(i32) - i32_clone`.
    if not isinstance(receiver_type, RECEIVERS_WITH_METHODS):
        return

    if (not _perk_answers(validator, call, receiver_type)
            and _reject_clone_of_resource(validator, call, receiver_type)):
        return

    if METHOD_TYPE_REGISTRY.validate_method(validator, call, receiver_type,
                                            beats_perk=True):
        return

    if _validate_perk_method(validator, call, receiver_type):
        return

    if METHOD_TYPE_REGISTRY.validate_method(validator, call, receiver_type,
                                            beats_perk=False):
        return

    _validate_extension_call(validator, call, receiver_type)


def _reject_destroyed_receiver(validator: 'TypeValidator', call: MethodCall) -> bool:
    """CE2024: the array this name held was destroyed, so it answers no method."""
    if not isinstance(call.receiver, Name):
        return False
    if not is_array_destroyed(validator, call.receiver.id):
        return False
    er.emit(validator.reporter, er.ERR.CE2024, call.receiver.loc, name=call.receiver.id)
    return True


def _validate_type_name_call(validator: 'TypeValidator', call: MethodCall,
                             type_name: str) -> None:
    """A call written on a built-in generic's NAME: `List.new()`, `HashMap.new()`.

    There is no receiver here, so no family claims it; the count is still the family's
    row, and a miscount is CE2009 (#799). Every other static goes through `passes/types/calls/statics.py`;
    these two keep their narrow emitters because a container static has no `ExtendDef`
    to converge onto (`docs/design/method-resolution.md`).
    """
    # A static on a GATED generic obeys the scope like the type does: behind
    # an aliased import the bare name is refused, and the qualified form is
    # what folds to this shape (#506, decision A-strict).
    from sushi_lang.semantics.namespaces import GATED_GENERIC_NAMES
    if (type_name in GATED_GENERIC_NAMES
            and getattr(call, "namespace_ref", None) is None):
        from sushi_lang.semantics.passes.types.visibility import reject_out_of_scope_type
        if reject_out_of_scope_type(validator, type_name, call.receiver.loc):
            return

    from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
    family, statics = _CONTAINER_STATICS.get(type_name, ("", ()))
    if call.method in statics:
        if reject_builtin_miscount(validator.reporter, call, f"{type_name}.{call.method}",
                                   arity_of_family(family)):
            return
        if call.method in _CONTAINER_STATIC_COUNTS:
            from sushi_lang.semantics.passes.types.arrays import reject_non_i32
            count = call.args[0]
            reject_non_i32(validator, count, validator.validate_expression(count), argument=1)


#: The statics a built-in container answers on its type NAME, and the family whose count
#: row each one reads (`MethodFamily.arity`).
_CONTAINER_STATICS = {"List": ("list", ("new", "with_capacity")),
                      "HashMap": ("hashmap", ("new",))}

#: The container statics whose one argument is a count, an i32 position (#870).
_CONTAINER_STATIC_COUNTS = frozenset({"with_capacity"})


def _validate_perk_method(validator: 'TypeValidator', call: MethodCall,
                          receiver_type) -> bool:
    """A perk implementation answered the call. Answers whether one did.

    A perk implementation is the sanctioned override
    (`docs/design/method-resolution.md`), so the ladder asks it between the two halves
    of the family table: after the families a built-in wins outright, before the ones
    that decline to it.

    It takes the SAME check as an extension method, and the same two codes with it: a
    perk method and an extension method are one rule -- a user-written method on a type
    -- so one rule gets one checker. CE0023 is in the INTERNAL family and documented as
    a codegen check, and CE2023 says "dynamic array method" about whatever it is handed;
    both were wrong here, and neither was visible until io/contracts made `write(string)`
    a perk method.
    """
    perk_method = validator.perk_impl_table.get_method(receiver_type, call.method)
    if perk_method is None:
        return False

    if not _check_user_method(validator, call, receiver_type, perk_method,
                              stop_on_arity=True):
        return True

    if perk_method.ret is not None:
        call.inferred_return_type = extension_call_result_type(validator, perk_method)
    return True


def _validate_extension_call(validator: 'TypeValidator', call: MethodCall,
                             receiver_type) -> None:
    """The last rung: a user-written extension method, or the unknown-method refusal.

    A generic-target extension is resolved through the monomorphized copy the
    monomorphize pass put in the extension table under the concrete receiver type. There
    used to be a second lookup here, by base name -- it repeated the first verbatim, so
    it could only ever find None again, and under #393 asking by base name is the wrong
    question anyway: a concrete target answers its own instantiation and no other.
    """
    method = resolve_extension_method(validator, receiver_type, call.method, call=call)
    if method is RESOLUTION_REPORTED:
        return

    if method is None:
        if _reject_unhandled_channel_chain(validator, call, receiver_type):
            return
        if _was_refused(validator, receiver_type, call.method):
            return
        er.emit(validator.reporter, er.ERR.CE2008, call.loc,
                name=f"{display_type(receiver_type)}.{call.method}")
        return

    _check_user_method(validator, call, receiver_type, method, stop_on_arity=False)


def _was_refused(validator: 'TypeValidator', receiver_type, method_name: str) -> bool:
    """The collect pass refused this method's declaration, and said so there.

    An extension (#808) and a perk implementation (#860, #864) keep one record each,
    by the same key.
    """
    from sushi_lang.semantics.type_predicates import generic_base_of
    base = generic_base_of(receiver_type) or display_type(receiver_type)
    return any(record.was_refused(base, method_name)
               for record in (validator.generic_extension_table,
                              validator.tables.generic_perk_impls))


def _check_user_method(validator: 'TypeValidator', call: MethodCall, receiver_type,
                       method, *, stop_on_arity: bool) -> bool:
    """The check a USER-written method takes, perk implementation or extension alike.

    Stamp what the borrow pass and the backend read off the call -- the receiver's mode
    and the declared parameter modes -- then measure the arguments through the one
    argument check (`passes/types/arguments.py`). Answers whether the COUNT fit.

    `stop_on_arity` is the one thing the two rungs do not share: a perk call reports a
    wrong count and says no more, an extension call reads the remaining arguments as a
    plain function call does.
    """
    _check_receiver_mode(validator, call, method)
    _stamp_param_modes(call, method)
    return check_arguments(
        validator, f"{display_type(receiver_type)}.{call.method}",
        [p.ty for p in method.params], call.args, call.loc,
        mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
        stop_on_arity=stop_on_arity)


def _check_receiver_mode(validator: 'TypeValidator', call: MethodCall, method) -> None:
    """A `poke self` / `peek self` method (#327) receives its receiver's ADDRESS.

    Stamp the mode on the call node -- the borrow pass treats a poke-self call as a
    WRITE to the receiver root (the CE2408/CE2412 gates), and the backend passes a
    pointer instead of a value. Both read the stamp instead of re-resolving the method.
    A poke receiver must also name storage the call can reach; a peek one only reads.
    """
    self_mode = getattr(method, "self_mode", None)
    if self_mode is None:
        return
    call.callee_self_mode = self_mode
    mode = receiver_mode(self_mode)
    if mode is not ParamMode.PEEK:
        _reject_unreachable_receiver(validator, call, mode)


def _reject_missing_method_module(validator: 'TypeValidator', call: MethodCall,
                                  name: str, module: str) -> None:
    """A built-in method whose body lives in a stdlib module needs THIS unit's import.

    The question is per unit (#942): the unit that holds the call imports the module,
    or a directory above it, directly, behind an alias, or through a `public use` of
    its own imports. An import in another unit of the program does not count.
    """
    scope = validator.scope
    parts = module.split("/")
    if any(scope.holds_unit("/".join(parts[:depth]))
           for depth in range(1, len(parts) + 1)):
        return
    er.emit_with(validator.reporter, er.ERR.CE3015, call.loc, name=name, module=module) \
        .help(f"add `use <{module}>` above the first declaration of this unit")


# The validation half of each built-in family, in the table's order. The claim is the
# registry's and is written once; what stands here is the CHECK that follows it.


@METHOD_TYPE_REGISTRY.validator("array")
def _validate_array_family(validator: 'TypeValidator', call: MethodCall,
                           receiver_type) -> None:
    from sushi_lang.semantics.passes.types.arrays import validate_builtin_array_method
    validate_builtin_array_method(call, receiver_type, validator.reporter, validator)
    if (call.method == "destroy"
            and isinstance(receiver_type, DynamicArrayType)
            and isinstance(call.receiver, Name)):
        mark_array_destroyed(validator, call.receiver.id)


@METHOD_TYPE_REGISTRY.validator("string")
def _validate_string_family(validator: 'TypeValidator', call: MethodCall,
                            receiver_type) -> None:
    from sushi_lang.sushi_stdlib.src.collections.strings import (
        METHOD_SPECS, validate_builtin_string_method_with_validator)
    if call.method in METHOD_SPECS:
        _reject_missing_method_module(validator, call, f"string.{call.method}()",
                                      "collections/strings")
    validate_builtin_string_method_with_validator(
        call, receiver_type, validator.reporter, validator)


@METHOD_TYPE_REGISTRY.validator("result")
def _validate_result_family(validator: 'TypeValidator', call: MethodCall,
                            receiver_type) -> None:
    from sushi_lang.semantics.generics.results import validate_result_method_with_validator
    validate_result_method_with_validator(
        call, receiver_type, validator.reporter, validator)


@METHOD_TYPE_REGISTRY.validator("maybe")
def _validate_maybe_family(validator: 'TypeValidator', call: MethodCall,
                           receiver_type) -> None:
    from sushi_lang.semantics.generics.maybe import validate_maybe_method_with_validator
    validate_maybe_method_with_validator(
        call, receiver_type, validator.reporter, validator)


@METHOD_TYPE_REGISTRY.validator("own")
def _validate_own_family(validator: 'TypeValidator', call: MethodCall,
                         receiver_type) -> None:
    from sushi_lang.semantics.generics.own import validate_own_method_with_validator
    validate_own_method_with_validator(call, receiver_type, validator.reporter, validator)


@METHOD_TYPE_REGISTRY.validator("hashmap")
def _validate_hashmap_family(validator: 'TypeValidator', call: MethodCall,
                             receiver_type) -> None:
    from sushi_lang.semantics.generics.hashmap import validate_hashmap_method_with_validator
    validate_hashmap_method_with_validator(
        call, receiver_type, validator.reporter, validator)


@METHOD_TYPE_REGISTRY.validator("list")
def _validate_list_family(validator: 'TypeValidator', call: MethodCall,
                          receiver_type) -> None:
    from sushi_lang.semantics.generics.list import validate_list_method_with_validator
    validate_list_method_with_validator(call, receiver_type, validator.reporter, validator)


@METHOD_TYPE_REGISTRY.validator("derived_hash")
@METHOD_TYPE_REGISTRY.validator("derived_clone")
def _validate_derived_method(validator: 'TypeValidator', call: MethodCall,
                             receiver_type) -> None:
    """A derived `hash` or `clone` takes no argument, and the count is the whole rule.

    The registry checked it against the family's row before this hook ran.
    """


@METHOD_TYPE_REGISTRY.validator("function")
def _validate_function_family(validator: 'TypeValidator', call: MethodCall,
                              receiver_type) -> None:
    # A closure read out of a struct field or a container is a borrow, so consuming it is
    # CE2411 and `.clone()` is the escape the diagnostic names -- it has to resolve here,
    # or dispatch falls through to the extension path and mangles the type name into a
    # symbol nobody defines.
    from sushi_lang.semantics.generics.closures import (
        validate_function_method_with_validator)
    validate_function_method_with_validator(
        call, receiver_type, validator.reporter, validator)


@METHOD_TYPE_REGISTRY.validator("primitive")
def _validate_primitive_family(validator: 'TypeValidator', call: MethodCall,
                               receiver_type) -> None:
    from sushi_lang.semantics.generics.primitives import validate_primitive_method
    validate_primitive_method(call, receiver_type, validator.reporter)


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

    if builtin_method_exists(payload, method_name, validator.derived_methods):
        return True
    if validator.extension_table.get_method(payload, method_name) is not None:
        return True
    if validator.perk_impl_table.get_method(payload, method_name) is not None:
        return True
    if isinstance(payload, DynamicArrayType):
        if validator.generic_extension_table.declarations(ARRAY_BASE_KEY, method_name):
            return True
    base_name = getattr(payload, "generic_base", None)
    if base_name is not None:
        if validator.generic_extension_table.find_applicable(
                base_name, method_name, payload.name) is not None:
            return True
    return False
