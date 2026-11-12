"""The rows and the table-building of the built-in Maybe<T> methods.

The typecheck arm of the family is `passes/types/visit/calls.py:_check_maybe_method`.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional

from sushi_lang.semantics.typesys import EnumType, Type, BuiltinType
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.builtin_signatures import BuiltinParam, BuiltinSignature
from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter
from sushi_lang.semantics.param_modes import BuiltinModes


#: Every built-in `Maybe@(T)` method and the number of arguments it takes. The count is
#: checked in the typecheck pass (CE2009) before the check below runs.
MAYBE_METHOD_ARITY: Mapping[str, int] = MappingProxyType({
    "is_some": 0, "is_none": 0, "realise": 1, "expect": 1, "or_err": 1,
})

#: The `Maybe@(T)` methods whose return type comes from an argument
#: (docs/design/error-conversion.md section 4). Each is solved per call.
MAYBE_METHOD_SIGNATURES: Mapping[str, BuiltinSignature] = MappingProxyType({
    "or_err": BuiltinSignature(
        receiver_params=("T",), type_params=("E",),
        params=(BuiltinParam("e", TypeParameter("E"), is_nom=True),),
        ret_type=GenericTypeRef("Result", (TypeParameter("T"), TypeParameter("E"))),
        self_mode="nom", reads_borrow_through=True),
})

#: The parameter modes of the `Maybe@(T)` methods (#1173). A method with a signature row
#: reads its modes from that row; every other parameter borrows.
MAYBE_METHOD_MODES = BuiltinModes.from_signatures(MAYBE_METHOD_SIGNATURES)


def is_builtin_maybe_method(method_name: str) -> bool:
    """Return True if ``method_name`` is a recognized Maybe<T> method."""
    return method_name in MAYBE_METHOD_ARITY


@dataclass(frozen=True)
class WrapperMethodRow:
    """A `Maybe@(T)` or `Result@(T, E)` method with no signature row: the type of each
    argument, and the type that the call yields."""
    params: tuple[Type, ...]
    returns: Type


def payload_method_row(payload_type: Type, method_name: str) -> Optional[WrapperMethodRow]:
    """The two methods that a `Maybe` and a `Result` share, from the payload type T:
    `realise(T) T` and `expect(string) T`."""
    if method_name == "realise":
        return WrapperMethodRow((payload_type,), payload_type)
    if method_name == "expect":
        return WrapperMethodRow((BuiltinType.STRING,), payload_type)
    return None


def maybe_method_row(payload_type: Type, method_name: str) -> Optional[WrapperMethodRow]:
    """The row of a `Maybe<T>` method with no signature row, given the payload type T."""
    if method_name in ("is_some", "is_none"):
        return WrapperMethodRow((), BuiltinType.BOOL)
    return payload_method_row(payload_type, method_name)


def maybe_payload(maybe_type: EnumType) -> Type:
    """The payload type `T` of a concrete `Maybe<T>` enum."""
    some_variant = maybe_type.get_variant("Some")
    if some_variant is None:
        raise_internal_error("CE0092", enum=maybe_type.name)
    if len(some_variant.associated_types) != 1:
        raise_internal_error("CE0093", got=len(some_variant.associated_types))
    return some_variant.associated_types[0]


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
