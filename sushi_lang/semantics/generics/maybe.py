"""Validation and table-building for the built-in Maybe<T> methods."""
from types import MappingProxyType
from typing import Any, Mapping, Optional

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type, BuiltinType
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.type_display import display_type


#: Every built-in `Maybe@(T)` method and the number of arguments it takes. The count is
#: checked in the typecheck pass (CE2009) before the check below runs.
MAYBE_METHOD_ARITY: Mapping[str, int] = MappingProxyType({
    "is_some": 0, "is_none": 0, "realise": 1, "expect": 1,
})


def is_builtin_maybe_method(method_name: str) -> bool:
    """Return True if ``method_name`` is a recognized Maybe<T> method."""
    return method_name in MAYBE_METHOD_ARITY


def maybe_method_return_type(payload_type: Type, method_name: str) -> Optional[Type]:
    """Return type of a Maybe<T> method, given the payload type T."""
    if method_name in ("realise", "expect"):
        return payload_type
    if method_name in ("is_some", "is_none"):
        return BuiltinType.BOOL
    return None


def validate_maybe_method_with_validator(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate a Maybe<T> method call whose count is correct."""
    if call.method not in MAYBE_METHOD_ARITY:
        raise_internal_error("CE0094", method=call.method)
    check = _CHECKS.get(call.method)
    if check is not None:
        check(call, maybe_type, reporter, validator)


def _validate_maybe_realise(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate Maybe<T>.realise(default): the default's type matches T."""
    some_variant = maybe_type.get_variant("Some")
    if some_variant is None:
        raise_internal_error("CE0092", enum=maybe_type.name)

    if len(some_variant.associated_types) != 1:
        raise_internal_error("CE0093", got=len(some_variant.associated_types))

    t_type = some_variant.associated_types[0]

    if t_type == BuiltinType.BLANK:
        er.emit(reporter, er.ERR.CE2506, call.loc)
        return

    default_arg = call.args[0]

    from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
    propagate_types_to_value(validator, default_arg, t_type)

    validator.validate_expression(default_arg)

    arg_type = validator.infer_expression_type(default_arg)
    if arg_type is not None and not validator._types_compatible(arg_type, t_type):
        er.emit(reporter, er.ERR.CE2503, default_arg.loc,
                expected=display_type(t_type), got=display_type(arg_type))


def _validate_maybe_expect(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate Maybe<T>.expect(message): the message is a string."""
    message_arg = call.args[0]

    validator.validate_expression(message_arg)

    arg_type = validator.infer_expression_type(message_arg)
    if arg_type is not None and arg_type != BuiltinType.STRING:
        er.emit(reporter, er.ERR.CE2503, message_arg.loc,
                expected="string", got=display_type(arg_type))


#: The methods that check more than their count.
_CHECKS = {"realise": _validate_maybe_realise, "expect": _validate_maybe_expect}


def ensure_maybe_type_in_table(
    enum_table: Any,
    value_type: Type,
    struct_table: Optional[dict] = None,
) -> Optional[EnumType]:
    """Ensure ``Maybe<value_type>`` exists in ``enum_table``, creating it if needed."""
    from sushi_lang.semantics.typesys import EnumVariantInfo
    from sushi_lang.semantics.generics.results import intern_wrapper_enum

    return intern_wrapper_enum(
        enum_table, "Maybe", (value_type,),
        lambda value: (EnumVariantInfo(name="Some", associated_types=(value,)),
                       EnumVariantInfo(name="None", associated_types=())),
        struct_table,
    )
