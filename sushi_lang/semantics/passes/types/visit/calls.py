"""The call, text and borrow arms of the typecheck pass over SHIR (docs/design/ir.md 7.3 to 7.6).

A `Call` answers what its callee yields. Its written target decides the callee:

- `Named` of a `GlobalRef` is a call of a free function (`Free`). The function is the
  declaration that the written name of the `GlobalRef` names: behind an alias, what the
  namespace holds (the one resolver of lowering); a bare name, the function of the unit's
  scope. The arguments go through the one argument check (`arguments.check_arguments`),
  which gives each argument its convention. The call yields the `Result` of the channel of
  the function, or its bare return value (`call_yield`). The function decides that type,
  so the call answers it also when an argument does not fit, or when the function is
  private to another unit: a later fault of the value, such as an unhandled `Result`, is a
  fault of its own. A name that is a function of a registry stdlib module, bare or behind
  an alias, is a call of that function (`Stdlib`, `calls/user_defined.py`). A C function
  behind the alias of its `unsafe external` block is an `Extern` (`visit/extern_calls.py`).
- `BuiltinFn` is `print` or `println` (`Intrinsic`): one argument with a string form, which
  the intrinsic reads (`Guaranteed`). Its display type is an answer.
- `Dotted` is a method call. The receiver is typed first, and the ladder of the call
  (`calls/dotcall.py:resolve_dotted`) finds the callee: a built-in family, a perk
  implementation, an extension method, or nothing (CE2008). A family answer is the callee
  row `Builtin`, and the arm of the family in `FAMILY_ARMS` types the call. A family with
  no arm stops with the label `Call(method <family>)`, for a receiver of any type. A
  method is never variadic, so a bloom argument is CE0120. An extension method of a
  receiver of a plain type is the callee row `Method`. A call through a field of a
  function type stops the walk.
- `Static` is a call on a type name. On an enum whose dot holds no such member it is
  CE2045 (`visit/results.py:reject_unknown_variant`). A static method of a plain type is
  the callee row `Static`, and a type whose dot holds no static of the name is CE2102. A
  static of a primitive type name (`f64.from_bits`, `string.from_bytes`) is the callee
  row `BuiltinStatic`, with the one parameter of its row of `PRIMITIVE_STATICS`. A static
  of a built-in container (`List.new`, `Own.alloc`) has the arm of `container_statics.py`,
  and a static of a generic type has the instance of `constructions.py`.

An extension method and a static resolve through `resolve_extension_method`, which
chooses the one that the calling unit may call (R4 to R7, C3 to C5). The arguments go
through the one argument check with the declared modes. The call yields the `Result` of
the channel of the method, or its bare return value.

A plain type is a primitive, or a struct or an enum that is no instance of a generic type
and whose name the unit did not lose. Every other target, and a call that another group
types (a generic function, a function of a stdlib module other than `<math>` and
`<time>`, a C function that `visit/extern_calls.py` does not type, a variadic function, a
method of a type that is not plain, a method or a static with a method-level type
parameter, a perk method), stops the walk of its body. A call of a function or a method
with a `peek` return has the source row of the call (`record_call_sources`).

An `Interp` is a `string`, and each hole has a string form; the display type of each hole
is an answer. A `Borrow` (`peek x`, `poke x` outside a call argument) is a value of a
reference type, and the position that holds it decides whether it takes one.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Mapping, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ffi_boundary import ERRNO_FUNCTION
from sushi_lang.semantics.generics.contracts import DISPLAY
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.opaque import explain_unpromised
from sushi_lang.semantics.generics.primitives import primitive_method_return_type
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.maybe import (
    MAYBE_METHOD_SIGNATURES, WrapperMethodRow, maybe_method_row, maybe_payload)
from sushi_lang.semantics.generics.results import RESULT_METHOD_SIGNATURES, result_method_row
from sushi_lang.semantics.method_effects import (
    ARRAY_FAMILY, CLONE_FAMILY, HASHMAP_FAMILY, LIST_FAMILY, MAYBE_FAMILY, OWN_FAMILY,
    PRIMITIVE_FAMILY, RESULT_FAMILY, STRING_FAMILY)
from sushi_lang.semantics.passes.collect.externals import ExternalSig
from sushi_lang.semantics.param_modes import (
    CalleeKind, Convention, ParamMode, declared_modes, modes_for, param_mode, receiver_mode)
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import (
    NOTHING_TYPED, Parameters, check_arguments, reject_misplaced_spread)
from sushi_lang.semantics.passes.types.arrays import check_array_method
from sushi_lang.semantics.passes.types.calls.dotcall import (
    PRIMITIVE_STATICS, MethodRung, PrimitiveStatic, reject_unknown_method, resolve_dotted)
from sushi_lang.semantics.passes.types.calls.enums import reject_missing_variant
from sushi_lang.semantics.passes.types.calls.methods import (
    RESOLUTION_REPORTED, extension_call_result_type, reject_uncloneable,
    reject_unhandled_channel_chain, resolve_extension_method)
from sushi_lang.semantics.passes.types.calls.statics import reject_missing_static
from sushi_lang.semantics.passes.types.calls.structs import template_shape
from sushi_lang.semantics.passes.types.borrowed_returns import record_call_sources
from sushi_lang.semantics.passes.types.calls.user_defined import (
    check_stdlib_call, own_concrete_function)
from sushi_lang.semantics.passes.types.method_registry import (
    METHOD_TYPE_REGISTRY, MethodFamily, string_method_return_type)
from sushi_lang.semantics.passes.types.expressions import top_level_contract
from sushi_lang.semantics.passes.types.resolution import call_yield, resolve_variable_type
from sushi_lang.semantics.passes.types.utils import names_no_type
from sushi_lang.semantics.passes.types.visibility import (
    name_is_contested, name_was_refused, out_of_scope_help, reject_ambiguous_name,
    reject_private_call, reject_private_kept_call, reject_private_type, type_is_contested,
    type_name_is_contested)
from sushi_lang.semantics.passes.types.visit.constructions import (
    Constructed, generic_static_owner)
from sushi_lang.semantics.passes.types.visit.container_statics import (
    check_container_static, container_static_base)
from sushi_lang.semantics.passes.types.visit.extern_calls import check_extern_call
from sushi_lang.semantics.passes.types.visit.helpers import function_type_of_sig
from sushi_lang.semantics.passes.types.visit.hashmap_methods import check_hashmap_method
from sushi_lang.semantics.passes.types.visit.list_methods import check_list_method
from sushi_lang.semantics.passes.types.visit.own_methods import check_own_method
from sushi_lang.semantics.passes.types.visit.results import reject_unknown_variant
from sushi_lang.semantics.passes.types.visit.signature_methods import check_signature_method
from sushi_lang.semantics.shir import dispatch, exprs as e
from sushi_lang.semantics.shir.callee import (
    Builtin, BuiltinStatic, Free, Intrinsic, Method, Static)
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.statics import is_builtin_static
from sushi_lang.semantics.stdlib_registry import StdlibFunction
from sushi_lang.semantics.type_predicates import (
    generic_base_of, is_instance_of, is_string_convertible)
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, FunctionType, ReferenceType,
    StructType, Type, deref_type, owns_resource)
from sushi_lang.semantics.written_types import written_name

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import ExtensionMethod, FuncSig, Param
    from sushi_lang.semantics.passes.types import TypeValidator


def check_call(validator: TypeValidator, node: e.Call,
               expected: Optional[Type]) -> Optional[Type]:
    target = node.target
    if isinstance(target, e.BuiltinFn):
        return _check_print(validator, node, target.name)
    if isinstance(target, e.Dotted):
        return _check_method_call(validator, node, target)
    if (isinstance(target, e.Named) and isinstance(target.ref, e.GlobalRef)
            and not target.type_args):
        return _check_free_call(validator, node, target.ref)
    if isinstance(target, e.Static):
        if reject_unknown_variant(validator, node, target, expected):
            return None
        base = container_static_base(validator, target)
        if base is not None:
            return check_container_static(validator, node, target, base, expected)
        return _check_static_call(validator, node, target, expected)
    visit.stop_at(validator, node)


def _check_free_call(validator: TypeValidator, node: e.Call,
                     ref: e.GlobalRef) -> Optional[Type]:
    written = validator.decl_name(ref.decl)
    alias, dot, name = written.rpartition(".")
    sig = (_function_behind_alias(validator, node, alias, name) if dot
           else _function_of_scope(validator, node, ref, written))
    if isinstance(sig, StdlibFunction):
        return check_stdlib_call(validator, node, ref, sig)
    if sig is None:
        return None
    if isinstance(sig, ExternalSig):
        return check_extern_call(validator, node, ref, sig)
    if reject_private_call(validator, "function", sig, ref.loc):
        return call_yield(validator, sig.ret_type, sig.err_type)
    if any(p.is_variadic or p.is_pack or p.ty is None for p in sig.params):
        visit.stop_at(validator, node)
    answers = validator.body_answers()
    answers.record_callee(node.shir_id, ResolvedCall(Free(ref.decl), unit=sig.unit_name))
    record_call_sources(validator, node, sig)
    function_type = function_type_of_sig(sig)
    if function_type is not None:
        visit.answer(validator, ref, function_type)
    params = [_parameter_type(validator, param) for param in sig.params]
    declared = Parameters(node.shir_id, declared_modes(sig.params),
                          [param.name for param in sig.params])
    check_arguments(validator, written, params, node.args, ref.loc,
                    mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009, declared=declared)
    return call_yield(validator, sig.ret_type, sig.err_type)


def _check_method_call(validator: TypeValidator, node: e.Call,
                       target: e.Dotted) -> Optional[Type]:
    """`x.f(args)`: the receiver, then the callee that the ladder finds (ir.md 7.3).

    A family answer goes to the arm of the family in `FAMILY_ARMS`, and a family with no
    arm stops the walk with the label of the family, on a receiver of any type. A perk
    implementation stops the walk (C6), and so does any other method of a receiver that
    takes no user method yet (`_takes_user_methods`). An extension method of a concrete
    target is a `Method` callee. A method of a `T[]` that no concrete target declares is the
    copy of a template (`extend T[]`, `xs.map(f)`), and it stops the walk before the copy is
    made (C1); so does the copy of any other template.
    A method that nothing answers on a local whose `let` the pass refused reports nothing
    more.
    """
    refused = [reject_misplaced_spread(validator, arg) for arg in node.args]
    if any(refused):
        return None
    received = visit.check_expr(validator, target.recv)
    if received is None:
        return None
    receiver = deref_type(received)
    answer = resolve_dotted(validator, receiver, target.name)
    if answer.family is not None:
        arm = FAMILY_ARMS.get(answer.family.name)
        if arm is None:
            dispatch.stop_at_family(visit.STAGE, node, answer.family.name)
        callee = Builtin(target.recv, answer.family.callee(target.name))
        validator.body_answers().record_callee(node.shir_id, ResolvedCall(callee))
        return arm(validator, node, receiver, target.name, answer.family)
    if answer.rung is not MethodRung.PERK and _rejects_a_chain(validator, node, target,
                                                               receiver):
        return None
    if (answer.rung is MethodRung.PERK or not _takes_user_methods(validator, receiver)
            or _calls_a_function_field(validator, receiver, target.name)):
        visit.stop_at(validator, node)
    method = None
    if answer.rung is MethodRung.EXTENSION:
        if (isinstance(receiver, DynamicArrayType)
                and not validator.extension_table.declarations(receiver, target.name)):
            visit.stop_at(validator, node)
        method = _user_method(validator, node, receiver, target.name, static=False)
        if method is RESOLUTION_REPORTED:
            return None
        if getattr(method, "template_id", None) is not None:
            visit.stop_at(validator, node)
    if method is None:
        if not (isinstance(target.recv, e.LocalRef)
                and target.recv.local in validator.refused_locals):
            reject_unknown_method(validator, receiver, target.name, node.loc)
        return None
    convention = receiver_mode(method.self_mode).convention
    callee = Method(target.recv, validator.method_decl(receiver, target.name), convention)
    return _check_user_method(validator, node, receiver, target.name, method, callee)


def _check_static_call(validator: TypeValidator, node: e.Call, target: e.Static,
                       expected: Optional[Type]) -> Optional[Type]:
    """`T.m(args)`: the static method `m` of the type `T` (#542).

    A type whose dot holds no static `m` is CE2102, and an enum CE2045. A static of a
    primitive type name (`f64.from_bits`) is a `BuiltinStatic`. A static of a generic type
    has the instance that its arguments and its position solve (`generic_static_owner`).
    `Iterator.new` and a static of a type whose name the unit lost stop the walk.
    """
    primitive = (PRIMITIVE_STATICS.get((target.ty.value, target.name))
                 if isinstance(target.ty, BuiltinType) else None)
    if primitive is not None:
        return _check_primitive_static(validator, node, target, primitive)
    built = _static_owner(validator, node, target, expected)
    owner = built.instance
    if owner is None:
        return None
    method = _user_method(validator, node, owner, target.name, static=True)
    if method is RESOLUTION_REPORTED:
        return None
    if method is None:
        if isinstance(owner, EnumType):
            reject_missing_variant(validator, node.loc, target.name, display_type(owner))
        else:
            reject_missing_static(validator, owner, target.name, node.loc)
        return None
    callee = Static(owner, validator.method_decl(owner, target.name))
    return _check_user_method(validator, node, owner, target.name, method, callee, built.typed)


def _check_primitive_static(validator: TypeValidator, node: e.Call, target: e.Static,
                            static: PrimitiveStatic) -> Type:
    """`f64.from_bits(bits)`, `f32.from_bits(bits)`, `string.from_bytes(nom b)`: the one
    argument through the one argument check, with the mode of the row. A wrong count is
    CE2009 and skips the argument. The call yields the type of the row after either fault."""
    family = STRING_FAMILY if target.ty == BuiltinType.STRING else PRIMITIVE_FAMILY
    callee = BuiltinStatic(target.ty, METHOD_TYPE_REGISTRY.family(family).callee(target.name))
    validator.body_answers().record_callee(node.shir_id, ResolvedCall(callee))
    mode = ParamMode.NOM if static.consumes else ParamMode.BORROW
    check_arguments(validator, f"{target.ty.value}.{target.name}", [static.param], node.args,
                    node.loc, mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                    declared=Parameters(node.shir_id, (mode,), [static.param_name]),
                    stop_on_arity=True)
    return static.returns


def _static_owner(validator: TypeValidator, node: e.Call, target: e.Static,
                  expected: Optional[Type]) -> Constructed:
    """The type that a static call names, or no instance after its error: behind an alias
    (`geo.Vec.at()`), the type that the namespace holds. Any other type stops the walk."""
    if isinstance(target.ty, BuiltinType):
        if is_builtin_static(target.ty.value, target.name):
            visit.stop_at(validator, node)
        return Constructed(target.ty)
    alias, dot, name = written_name(target.ty).rpartition(".")
    if dot:
        binding = validator.namespaces.lookup(alias, name)
        if binding is None or binding.kind not in ("struct", "enum"):
            visit.stop_at(validator, node)
        name = binding.name
    if is_builtin_static(name, target.name) or type_name_is_contested(validator, name):
        visit.stop_at(validator, node)
    if dot and reject_private_type(validator, name, node.loc):
        return Constructed(None)
    if (name in validator.generic_struct_table.by_name
            or name in validator.generic_enum_table.by_name):
        built = generic_static_owner(validator, node, target, name, expected)
        if built is None:
            _reject_generic_member(validator, node, target, name)
            return Constructed(None)
        return built
    owner = (validator.struct_table.by_name.get(name)
             or validator.enum_table.by_name.get(name))
    if owner is None or not _is_plain(validator, owner):
        visit.stop_at(validator, node)
    return Constructed(owner)


def _reject_generic_member(validator: TypeValidator, node: e.Call, target: e.Static,
                           name: str) -> None:
    """The dot of a generic type with no instance holds no static of the name: CE2045 on
    an enum and CE2102 on a struct. The arguments are still checked."""
    if name in validator.generic_enum_table.by_name:
        reject_missing_variant(validator, node.loc, target.name, name)
    else:
        reject_missing_static(validator,
                              template_shape(validator.generic_struct_table.by_name[name], ()),
                              target.name, node.loc)
    for arg in node.args:
        visit.check_expr(validator, arg.value)


def _is_plain(validator: TypeValidator, ty: Type) -> bool:
    """A primitive, or a struct or an enum that is no instance of a generic type and whose
    name the unit did not lose: a type whose methods and statics the pass types."""
    if isinstance(ty, BuiltinType):
        return True
    return (isinstance(ty, (StructType, EnumType)) and generic_base_of(ty) is None
            and not type_is_contested(validator, ty))


def _rejects_a_chain(validator: TypeValidator, node: e.Call, target: e.Dotted,
                     receiver: Type) -> bool:
    """CE2515 for a method of the payload called on a `Result` or a `Maybe` that no method
    of the wrapper answers (`xs.first_big().doubled()`). True when it was reported."""
    if not (isinstance(receiver, EnumType)
            and (is_instance_of(receiver, "Result") or is_instance_of(receiver, "Maybe"))):
        return False
    if resolve_extension_method(validator, receiver, target.name, report=False,
                                site=node.loc) is not None:
        return False
    return reject_unhandled_channel_chain(validator, target.name, receiver, node.loc,
                                          target.recv.loc, _called_name(validator,
                                                                        target.recv))


def _called_name(validator: TypeValidator, recv: e.ShirExpr) -> Optional[str]:
    """The name after the dot of a receiver that is a call written with a dot (a method,
    a static, a function behind an alias), for the help of CE2515. A bare call has none."""
    if not isinstance(recv, e.Call):
        return None
    called = recv.target
    if isinstance(called, (e.Dotted, e.Static)):
        return called.name
    if isinstance(called, e.Named) and isinstance(called.ref, e.GlobalRef):
        _, dot, name = validator.decl_name(called.ref.decl).rpartition(".")
        return name if dot else None
    return None


def _takes_user_methods(validator: TypeValidator, ty: Type) -> bool:
    """A receiver whose extension methods the pass types: a plain type, and an array of a
    plain element that owns nothing (`extend i32[]`). A method of an instance of a generic
    type (`extend Maybe@(i32)`) is the copy of a template, and stops (C1)."""
    if _is_plain(validator, ty):
        return True
    return (isinstance(ty, (ArrayType, DynamicArrayType)) and _is_plain(validator, ty.base_type)
            and not owns_resource(ty.base_type, validator.drop_type_names))


def _calls_a_function_field(validator: TypeValidator, receiver: Type, name: str) -> bool:
    """`h.op(v)` through a field of a function type, where no extension method of the
    struct has the name: a call of a function value (C2). A method wins over the field."""
    return (isinstance(receiver, StructType)
            and isinstance(receiver.get_field_type(name), FunctionType)
            and validator.extension_table.get_method(receiver, name) is None)


def _user_method(validator: TypeValidator, node: e.Call, owner: Type, name: str, *,
                 static: bool):
    """The extension method or the static `name` of the plain type `owner` that the
    calling unit calls, None when the type has none, or `RESOLUTION_REPORTED` after a
    visibility error (CE3005, CE3022, CE3023). A method with a method-level type parameter
    stops the walk (C1)."""
    if validator.generic_extension_table.declarations(display_type(owner), name):
        visit.stop_at(validator, node)
    return resolve_extension_method(validator, owner, name, report=True, static=static,
                                    site=node.loc)


def _check_user_method(validator: TypeValidator, node: e.Call, owner: Type, name: str,
                       method: ExtensionMethod, callee: Method | Static,
                       typed: Mapping[int, Optional[Type]] = NOTHING_TYPED) -> Optional[Type]:
    """The arguments of an extension method or a static through the one argument check,
    with the declared mode of each parameter. The call yields the `Result` of the channel
    of the method, or its bare return value, also after an error of an argument. A method
    with a `peek` return has the source row of the call."""
    validator.body_answers().record_callee(
        node.shir_id, ResolvedCall(callee, unit=method.unit_name))
    record_call_sources(validator, node, method)
    params = [_parameter_type(validator, param) for param in method.params]
    kind = CalleeKind.STATIC_METHOD if isinstance(callee, Static) else CalleeKind.METHOD
    declared = Parameters(node.shir_id, modes_for(method.params, kind),
                          [param.name for param in method.params])
    check_arguments(validator, f"{display_type(owner)}.{name}", params, node.args, node.loc,
                    mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009, declared=declared,
                    typed=typed)
    return extension_call_result_type(validator, method)


def _check_primitive_method(validator: TypeValidator, node: e.Call, receiver: Type,
                            name: str, family: MethodFamily) -> Optional[Type]:
    """A primitive method takes no argument and yields the type of its row. A wrong count
    is CE2009, and the call still yields that type."""
    reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                            f"{display_type(receiver)}.{name}", family.arity)
    return primitive_method_return_type(receiver, name)


def _check_string_method(validator: 'TypeValidator', node: e.Call, receiver: Type,
                         name: str, family: MethodFamily) -> Optional[Type]:
    """A string method takes the arguments of its row and yields the type of its row.

    A wrong count is CE2009 and skips the argument types; a wrong argument type is
    CE2006 at the argument. The call yields its type after either fault.
    """
    from sushi_lang.sushi_stdlib.src.collections.strings import METHOD_SPECS
    spec = METHOD_SPECS.get(name)
    miscount = reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                                       f"{display_type(receiver)}.{name}", family.arity)
    if not miscount and spec is not None:
        _check_builtin_arguments(validator, node, name, family, spec.arg_types,
                                 er.ERR.CE2006, callee_name=spec.name,
                                 also_accepts=spec.also_accepts)
    yielded = string_method_return_type(validator, name)
    return yielded


def _check_builtin_arguments(validator: TypeValidator, node: e.Call, name: str,
                             family: MethodFamily, params: Sequence[Type],
                             mismatch_code: er.ErrorMessage, *,
                             callee_name: Optional[str] = None,
                             also_accepts: Sequence[Sequence[Type]] = ()) -> None:
    """The arguments of the built-in method `name` whose count fits, through the one
    argument check: each argument at the type of its row, with the mode of its row."""
    modes = family.modes.of(name, len(node.args)) or ()
    declared = Parameters(node.shir_id, modes, [f"#{i}" for i in range(1, len(modes) + 1)])
    check_arguments(validator, callee_name or name, params, node.args, node.loc,
                    mismatch_code=mismatch_code, arity_code=er.ERR.CE2009,
                    declared=declared, also_accepts=also_accepts)


def _check_result_method(validator: TypeValidator, node: e.Call, receiver: Type,
                         name: str, family: MethodFamily) -> Optional[Type]:
    """A method of `Result@(T, E)`. A method with a signature row (`map_err`) has the arm
    of `visit/signature_methods.py`."""
    signature = RESULT_METHOD_SIGNATURES.get(name)
    if signature is not None:
        return check_signature_method(validator, node, receiver, name, family, signature)
    row = result_method_row(receiver, name, validator.enum_table,
                            validator.struct_table.by_name)
    return _check_wrapper_method(validator, node, receiver, name, family, row)


def _check_maybe_method(validator: TypeValidator, node: e.Call, receiver: Type,
                        name: str, family: MethodFamily) -> Optional[Type]:
    """A method of `Maybe@(T)`. A method with a signature row (`or_err`) has the arm of
    `visit/signature_methods.py`."""
    signature = MAYBE_METHOD_SIGNATURES.get(name)
    if signature is not None:
        return check_signature_method(validator, node, receiver, name, family, signature)
    row = maybe_method_row(maybe_payload(receiver), name)
    return _check_wrapper_method(validator, node, receiver, name, family, row)


def _check_wrapper_method(validator: TypeValidator, node: e.Call, receiver: Type,
                          name: str, family: MethodFamily,
                          row: Optional[WrapperMethodRow]) -> Optional[Type]:
    """A method of a `Result` or a `Maybe` takes the arguments of its row and yields the
    type of its row.

    A wrong count is CE2009 and skips the argument types. `realise` on a `~` payload is
    CE2506, and its default is not checked. An argument of another type than its row is
    CE2503 at the argument. The call yields its type after each fault.
    """
    if row is None:
        raise_internal_error("CE0094", method=name)
    if reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                               f"{display_type(receiver)}.{name}", family.arity):
        return row.returns
    if name == "realise" and row.returns == BuiltinType.BLANK:
        er.emit(validator.reporter, er.ERR.CE2506, node.loc, ty=display_type(receiver))
        return row.returns
    _check_builtin_arguments(validator, node, name, family, row.params, er.ERR.CE2503)
    return row.returns


def _check_derived_clone(validator: TypeValidator, node: e.Call, receiver: Type,
                         name: str, family: MethodFamily) -> Type:
    """`x.clone()` of a struct or an enum yields the type of `x` and takes no argument. A
    type that holds a resource has no copy (CE2431, CE4018 for an opaque type parameter),
    and a wrong count is CE2009. The call yields its type after either fault."""
    if not reject_uncloneable(validator, node, receiver):
        reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                                f"{display_type(receiver)}.{name}", family.arity)
    return receiver


FamilyArm = Callable[['TypeValidator', e.Call, Type, str, MethodFamily], Optional[Type]]

#: The arm of each built-in family that the pass types, keyed by the registry name of the
#: family. A family that is not here stops the walk (`Call(method <family>)`).
FAMILY_ARMS: Mapping[str, FamilyArm] = MappingProxyType({
    ARRAY_FAMILY: check_array_method,
    LIST_FAMILY: check_list_method,
    HASHMAP_FAMILY: check_hashmap_method,
    OWN_FAMILY: check_own_method,
    PRIMITIVE_FAMILY: _check_primitive_method,
    STRING_FAMILY: _check_string_method,
    RESULT_FAMILY: _check_result_method,
    MAYBE_FAMILY: _check_maybe_method,
    CLONE_FAMILY: _check_derived_clone,
})


def _function_of_scope(validator: TypeValidator, node: e.Call, ref: e.GlobalRef,
                       name: str) -> Optional[FuncSig | StdlibFunction]:
    """The concrete function that a bare name calls in this unit, or None after its error.

    A name that no function of the unit's scope takes is the function of a registry
    stdlib module that the unit imports, when one has it. A generic function and `errno()`
    are not typed here, and their call stops the walk of its body. A bare call that
    constructs a struct is a `StructInit` (lowering, #1076), never a call.
    """
    own = own_concrete_function(validator.func_table, name, validator.current_unit_name)
    if ((own is None and validator.generic_sig(name) is not None)
            or type_name_is_contested(validator, name)):
        visit.stop_at(validator, node)
    sig = validator.func_sig(name)
    if sig is None:
        stdlib = validator.func_table.lookup_stdlib_by_name(name, validator.scope)
        if stdlib is not None:
            return stdlib[1]
        if name == ERRNO_FUNCTION:
            visit.stop_at(validator, node)
        reject_undefined_function(validator, name, ref.loc)
        return None
    if reject_ambiguous_name(validator, "function", name, ref.loc):
        return None
    if (name_is_contested(validator, "function", name)
            and sig.unit_name != validator.current_unit_name):
        visit.stop_at(validator, node)
    return sig


def _function_behind_alias(validator: TypeValidator, node: e.Call, alias: str,
                           name: str) -> FuncSig | StdlibFunction | ExternalSig:
    """The function that `alias.name` names: what the namespace holds under the name, a
    function of a unit or of a registry stdlib module, or a C function of an `unsafe
    external` block."""
    binding = validator.namespaces.lookup(alias, name)
    if binding is None or binding.kind not in ("function", "extern"):
        visit.stop_at(validator, node)
    return binding.record


def reject_undefined_function(validator: TypeValidator, name: str, loc: Span) -> None:
    """CE2008 for a name that no function of the unit's scope takes.

    A name that a library keeps is refused as kept (#469), and a name that the unit
    declared under another kind was refused at its declaration (#1102).
    """
    kept = validator.library_not_exported.get(name)
    if kept is not None and reject_private_kept_call(validator, name, loc, library=kept[0],
                                                     kind=kept[1]):
        return
    if name_was_refused(validator, name):
        return
    diag = er.emit_with(validator.reporter, er.ERR.CE2008, loc, name=name)
    missing = out_of_scope_help(validator, "function", name)
    if missing is not None:
        diag.help(missing)
    diag.emit()


def _parameter_type(validator: TypeValidator, param: Param) -> Type:
    """The declared type of a parameter, resolved; a `peek` or a `poke` parameter is a
    borrow of the resolved type."""
    mode = param_mode(param)
    span = param.type_span or param.name_span
    resolved = resolve_variable_type(validator, deref_type(param.ty), span)
    return ReferenceType(resolved, mode.borrow_mode) if mode.by_pointer else resolved


def _check_print(validator: TypeValidator, node: e.Call, name: str) -> Optional[Type]:
    """`print(v)` and `println(v)`: the intrinsic reads one value with a string form.

    An unhandled `Result` is CE2037, and any other value with no string form is CE2115.
    A `peek` or a `poke` marker on the argument reads the value too.
    """
    answers = validator.body_answers()
    answers.record_callee(node.shir_id, ResolvedCall(Intrinsic(name)))
    (arg,) = node.args
    answers.record_convention(node.shir_id, 0, Convention.GUARANTEED)
    ty = visit.check_expr(validator, arg.value)
    if ty is None:
        return None
    if is_instance_of(ty, "Result"):
        er.emit(validator.reporter, er.ERR.CE2037, arg.value.loc)
        return None
    if reject_no_string_form(validator, ty, arg.value.loc, printed=True):
        return None
    answers.record_display(node.shir_id, 0, deref_type(ty))
    return BuiltinType.BLANK


def check_interp(validator: TypeValidator, node: e.Interp,
                 _expected: Optional[Type]) -> Optional[Type]:
    """An interpolated string: each hole has a string form (CE2035)."""
    answers = validator.body_answers()
    refused = False
    for index, part in enumerate(node.parts):
        if isinstance(part, str):
            continue
        ty = visit.check_expr(validator, part)
        if ty is None or reject_no_string_form(validator, ty, part.loc):
            refused = True
            continue
        answers.record_display(node.shir_id, index, deref_type(ty))
    return None if refused else BuiltinType.STRING


def reject_no_string_form(validator: TypeValidator, ty: Type, loc: Span, *,
                          printed: bool = False) -> bool:
    """A value with no string form: CE2115 for `print`, CE2035 for a hole. True when it
    was refused.

    A primitive has a string form, and so does a struct, an enum, an array, a `List@(T)`
    and an `Own@(T)` through `Display`; `print` takes `~` too. A type that a written-type
    check refused was reported there. A type that holds something with no string form
    says which field or element it is.
    """
    shown = deref_type(ty)
    if (is_string_convertible(shown) or (printed and shown == BuiltinType.BLANK)
            or names_no_type(validator, shown)):
        return False
    refused: list = []
    printable, reason = top_level_contract(validator, ty, DISPLAY, refused)
    if printable:
        return False
    spelled = display_type(ty)
    report = (er.emit_with(validator.reporter, er.ERR.CE2115, loc, type=spelled) if printed
              else er.emit_with(validator.reporter, er.ERR.CE2035, loc, type=spelled))
    if reason is not None:
        report = report.note(f"no derived Display: {reason}")
    explain_unpromised(report, DISPLAY, refused).emit()
    return True


def check_borrow(validator: TypeValidator, node: e.Borrow,
                 expected: Optional[Type]) -> Optional[Type]:
    """`peek x` or `poke x` outside a call argument: a borrow of the type of `x`."""
    ty = visit.check_expr(validator, node.place, deref_type(expected))
    return None if ty is None else ReferenceType(ty, node.mode)
