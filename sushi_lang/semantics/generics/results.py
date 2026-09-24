"""Validation and table-building for the built-in Result<T, E> methods."""
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.derive import derive_for_enum


#: Every built-in `Result@(T, E)` method and the number of arguments it takes. The count
#: is checked in the typecheck pass (CE2009) before the check below runs.
RESULT_METHOD_ARITY: Mapping[str, int] = MappingProxyType({
    "is_ok": 0, "is_err": 0, "err": 0, "realise": 1, "expect": 1,
})


def is_builtin_result_method(method_name: str) -> bool:
    """Check if a method name is a builtin Result<T, E> method."""
    return method_name in RESULT_METHOD_ARITY


def validate_result_method_with_validator(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T, E> method calls."""
    # CRITICAL: Annotate the MethodCall with the resolved Result<T, E> type
    # This allows the backend to use the correct type during code generation
    # instead of relying on unreliable LLVM type matching
    call.resolved_enum_type = result_type

    if call.method not in RESULT_METHOD_ARITY:
        raise_internal_error("CE0094", method=call.method)
    check = _CHECKS.get(call.method)
    if check is not None:
        check(call, result_type, reporter, validator)


def _validate_result_expect(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T, E>.expect(message): the message is a string."""
    message_arg = call.args[0]

    validator.validate_expression(message_arg)

    from sushi_lang.semantics.typesys import BuiltinType
    arg_type = validator.infer_expression_type(message_arg)
    if arg_type is not None and arg_type != BuiltinType.STRING:
        er.emit(reporter, er.ERR.CE2503, message_arg.loc,
               expected="string", got=display_type(arg_type))


def validate_result_realise_method_with_validator(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T>.realise(default): the default's type matches T."""
    # Extract T from Result<T> by getting the Ok variant's associated type
    # Result<T> has two variants: Ok(T) and Err()
    # We need to find the Ok variant and extract its associated type
    ok_variant = result_type.get_variant("Ok")
    if ok_variant is None:
        raise_internal_error("CE0089", enum=result_type.name)

    if len(ok_variant.associated_types) != 1:
        # Ok variant should have exactly one associated type (T)
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))

    t_type = ok_variant.associated_types[0]

    from sushi_lang.semantics.typesys import BuiltinType
    if t_type == BuiltinType.BLANK:
        er.emit(reporter, er.ERR.CE2506, call.loc)
        return

    default_arg = call.args[0]

    from sushi_lang.semantics.type_resolution import TypeResolver
    type_resolver = TypeResolver(validator.struct_table.by_name, validator.enum_table.by_name)
    resolved_t_type = type_resolver.resolve_generic_type_ref(t_type)

    from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
    propagate_types_to_value(validator, default_arg, resolved_t_type)

    validator.validate_expression(default_arg)

    arg_type = validator.infer_expression_type(default_arg)
    if arg_type is not None and not validator._types_compatible(arg_type, t_type):
        er.emit(reporter, er.ERR.CE2503, default_arg.loc,
               expected=display_type(t_type), got=display_type(arg_type))


#: The methods that check more than their count.
_CHECKS = {"realise": validate_result_realise_method_with_validator,
           "expect": _validate_result_expect}


def is_result_enum(t: Any) -> bool:
    """Whether ``t`` is a concrete ``Result<T, E>`` enum."""
    return isinstance(t, EnumType) and t.generic_base == "Result"


def is_builtin_wrapper_enum(t: Any) -> bool:
    """Whether ``t`` is a built-in wrapper: a ``Result<T, E>`` or a ``Maybe<T>``.

    Both are ordinary interned enums, so an "is this an enum" test admits them. The
    positions that mean an error VOCABULARY have to ask this as well (CE2086, #668).
    """
    return isinstance(t, EnumType) and t.generic_base in ("Result", "Maybe")


def result_ok_err(result_enum: EnumType) -> tuple[Type, Type]:
    """The ``(ok, err)`` payload types of a concrete ``Result<T, E>`` enum."""
    ok_variant = result_enum.get_variant("Ok")
    err_variant = result_enum.get_variant("Err")
    if ok_variant is None or err_variant is None:
        raise_internal_error("CE0089", enum=result_enum.name)
    if len(ok_variant.associated_types) != 1 or len(err_variant.associated_types) != 1:
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))
    return ok_variant.associated_types[0], err_variant.associated_types[0]


def _differs_only_in_nested_resolution(stored, rebuilt, structs, enums) -> bool:
    """True when two variant tuples are one type seen at two resolution DEPTHS.

    This seam has always resolved a payload's TOP LEVEL, so a stored payload that IS a bare
    name can only come from a Result built structurally somewhere else -- the bypass CE0126
    exists to catch, and it stays caught. What it did NOT resolve until now is a name NESTED
    inside a payload: the error arm of a `fn(i32) -> i32`, or the element of an `IpAddr[]`.
    An entry stored that way is this function's own earlier output, so a rebuild that
    resolves further is the same type and not a divergence.

    The line is therefore drawn at the top level: a bare name there is still a bug, and a
    bare name below it is depth.
    """
    from sushi_lang.semantics.typesys import UnknownType
    from sushi_lang.semantics.type_resolution import resolve_type_recursively

    if len(stored) != len(rebuilt):
        return False
    for stored_variant, rebuilt_variant in zip(stored, rebuilt, strict=False):
        if stored_variant.name != rebuilt_variant.name:
            return False
        stored_payloads = stored_variant.associated_types or ()
        rebuilt_payloads = rebuilt_variant.associated_types or ()
        if len(stored_payloads) != len(rebuilt_payloads):
            return False
        for stored_payload, rebuilt_payload in zip(stored_payloads, rebuilt_payloads, strict=False):
            if stored_payload == rebuilt_payload:
                continue
            if isinstance(stored_payload, UnknownType):
                return False
            if resolve_type_recursively(stored_payload, structs, enums) != rebuilt_payload:
                return False
    return True


def _names_an_unbuilt_instance(ty: Type, structs: dict, enums: dict) -> bool:
    """Does a payload still spell a generic instance the monomorphize pass has not built?

    Asked AFTER `resolve_type_recursively`, which turns every reference whose instance
    exists into that instance. What survives as a `GenericTypeRef` therefore names one
    that does not exist yet. The walk stops at a declaration: what a built type holds is
    its own declaration's business.
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.type_walk import walk_named_types

    return any(isinstance(inner, GenericTypeRef)
               for inner in walk_named_types(ty, structs, enums,
                                             through_declarations=False))


def signature_result_arms(ret_type: Optional[Type], err_type: Optional[Type],
                          std_error: Optional[Type]) -> Optional[tuple[Type, Type]]:
    """The Ok and Err arm a call to a signature yields, or None when there is nothing to intern.

    ONE derivation, read by the typecheck pass and by the backend's declaration of a
    library function, so a call is typed and declared as the same Result (#541). An
    explicit `Result@(T, E)` return is its own two arms and is never wrapped again;
    any other return is wrapped with the spelled `| E`, or with `StdError` when the
    signature says none. None means the caller decides: no return type, a return that
    is already the interned enum, or a `Result` reference with the wrong arity.
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if ret_type is None or is_result_enum(ret_type):
        return None
    if isinstance(ret_type, GenericTypeRef) and ret_type.base_name == "Result":
        if len(ret_type.type_args) == 2:
            return ret_type.type_args[0], ret_type.type_args[1]
        return None
    err = err_type if err_type is not None else std_error
    if err is None:
        return None
    return ret_type, err


def ensure_result_type_in_table(
    enum_table: Any,
    ok_type: Type,
    err_type: Type,
    struct_table: Optional[dict] = None,
) -> Optional[EnumType]:
    """Ensure ``Result<ok_type, err_type>`` exists in ``enum_table``, creating it if needed."""
    from sushi_lang.semantics.typesys import EnumVariantInfo

    return intern_wrapper_enum(
        enum_table, "Result", (ok_type, err_type),
        lambda ok, err: (EnumVariantInfo(name="Ok", associated_types=(ok,)),
                         EnumVariantInfo(name="Err", associated_types=(err,))),
        struct_table,
    )


def intern_wrapper_enum(
    enum_table: Any,
    base: str,
    payloads: tuple[Type, ...],
    make_variants: Callable[..., tuple],
    struct_table: Optional[dict] = None,
) -> EnumType:
    """Intern ``base<payloads...>``, the one seam under the Result and the Maybe intern.

    Each payload is resolved RECURSIVELY, not one level. A payload that IS a name resolved
    before; a payload that CONTAINS one -- `IpAddr[]`, whose element is the name -- did not,
    and it interned with the element unresolved. The interned NAME is identical either way,
    so the next intern rebuilt different variants and the guard fired CE0126 with two
    spellings that read the same. A named type is terminal in this walk, so the table stays
    the sole authority for its contents (docs/design/type-identity.md).

    An abstract instance, whose payloads still name an enclosing template's type params, is
    not a real type: it is handed back but kept OUT of the table, or every later walk of the
    tables reads a type that was never built. A PROVISIONAL one is the same answer for the
    same reason (#556): the walk leaves a `GenericTypeRef` exactly when the instance it
    names has not been built yet, so storing over one parks an unresolved payload under a
    name whose resolved form arrives later.
    """
    from sushi_lang.semantics.type_resolution import resolve_type_recursively
    from sushi_lang.semantics.type_predicates import is_abstract_type

    enums = enum_table.by_name
    structs = struct_table if struct_table is not None else {}
    resolved = tuple(resolve_type_recursively(t, structs, enums) for t in payloads)

    name = interned_name(base, resolved)
    variants = make_variants(*resolved)

    def build() -> EnumType:
        return EnumType(name=name, variants=variants, generic_base=base,
                        generic_args=resolved)

    if any(is_abstract_type(t, structs, enums) or _names_an_unbuilt_instance(t, structs, enums)
           for t in resolved):
        return build()

    existing = enums.get(name)
    if existing is not None:
        if existing.variants and existing.variants != variants:
            if not _differs_only_in_nested_resolution(existing.variants, variants, structs, enums):
                raise_internal_error(
                    "CE0126",
                    name=name,
                    existing=str([str(t) for v in existing.variants for t in v.associated_types]),
                    rebuilt=str([str(t) for v in variants for t in v.associated_types]),
                )
        return existing

    interned = build()
    enums[name] = interned
    enum_table.order.append(name)
    derive_for_enum(interned, enum_table.derived)
    return interned
