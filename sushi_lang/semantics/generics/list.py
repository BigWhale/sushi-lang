"""Validation and element-type parsing for the built-in List<T> methods."""
from types import MappingProxyType
from typing import Any, Mapping, Optional

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type
import sushi_lang.internals.errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.type_display import display_type


#: Every built-in `List@(T)` method and the number of arguments it takes. The family
#: claims a name from these keys, and the typecheck pass checks the count (CE2009)
#: before it runs the check below.
LIST_METHOD_ARITY: Mapping[str, int] = MappingProxyType({
    "new": 0, "with_capacity": 1,
    "len": 0, "capacity": 0, "is_empty": 0,
    "push": 1, "pop": 0, "get": 1, "insert": 2, "remove": 1,
    "clear": 0, "reserve": 1, "shrink_to_fit": 0,
    "destroy": 0, "free": 0, "debug": 0, "iter": 0, "clone": 0,
})


def is_builtin_list_method(method_name: str) -> bool:
    """Return True if ``method_name`` is a built-in List<T> method."""
    return method_name in LIST_METHOD_ARITY


#: The methods whose ELEMENT argument is checked against T, and its position.
_ELEMENT_ARGUMENT = {"push": 0, "insert": 1}

#: The methods whose INDEX or COUNT argument is an i32 position (#870), and its position.
#: `with_capacity` is the static twin of `reserve`, read where a static is read.
_INDEX_ARGUMENT = {"get": 0, "remove": 0, "insert": 0, "reserve": 0}


def validate_list_method_with_validator(
    call: MethodCall,
    list_type: StructType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate a List<T> method call whose count is correct: the index, then the element."""
    if call.method not in LIST_METHOD_ARITY:
        raise_internal_error("CE0083", method=call.method)

    index_arg = _INDEX_ARGUMENT.get(call.method)
    if index_arg is not None:
        from sushi_lang.semantics.passes.types.arrays import reject_non_i32
        arg = call.args[index_arg]
        reject_non_i32(validator, arg, validator.validate_expression(arg),
                       argument=index_arg + 1)

    element_arg_index = _ELEMENT_ARGUMENT.get(call.method)
    if element_arg_index is not None:
        _validate_list_element_type(call, list_type, element_arg_index, reporter, validator)


def _validate_list_element_type(
    call: MethodCall,
    list_type: StructType,
    arg_index: int,
    reporter: Any,
    validator: Any,
) -> None:
    """Check that args[arg_index] is compatible with the List's element type T."""
    arg = call.args[arg_index]
    element_type = parse_list_types(list_type, validator)
    if element_type is None:
        validator.validate_expression(arg)
        return

    from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
    from sushi_lang.semantics.passes.types.compatibility import types_compatible

    propagate_types_to_value(validator, arg, element_type)
    validator.validate_expression(arg)

    arg_type = validator.infer_expression_type(arg)
    if arg_type is not None and not types_compatible(validator, arg_type, element_type):
        er.emit(reporter, er.ERR.CE2006, arg.loc,
                index=arg_index + 1, expected=display_type(element_type), got=display_type(arg_type))


def instance_type_arguments(instance: Any, base: str, tables: Any) -> Optional[tuple[Type, ...]]:
    """The type arguments of a `base<...>` instance, each resolved RECURSIVELY, or None.

    An instance carries its arguments in `generic_args`; its interned NAME is a spelling
    and not a thing to parse (#794). The monomorphize pass can leave a nested reference
    unresolved there (`List<List<i32>>` holds a `GenericTypeRef`), so each argument is
    resolved against the tables at read time.
    """
    from sushi_lang.semantics.type_resolution import resolve_type_recursively

    if not isinstance(instance, StructType) or instance.generic_base != base:
        return None
    if instance.generic_args is None:
        return None
    structs = tables.struct_table.by_name
    enums = tables.enum_table.by_name
    return tuple(resolve_type_recursively(arg, structs, enums) for arg in instance.generic_args)


def parse_list_types(list_type: StructType, validator: Any) -> Optional[Type]:
    """Resolve the element type T from a List<T> struct type, or None."""
    args = instance_type_arguments(list_type, "List", validator)
    if args is None or len(args) != 1:
        return None
    return args[0]
