"""The rows of the built-in `List@(T)` methods, and the reader of the element type.

The typecheck pass checks a call of a method with the arm of the family
(`passes/types/visit/list_methods.py`).
"""
from types import MappingProxyType
from typing import Any, Mapping, Optional

from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.semantics.method_effects import CONTAINER_INSERT_METHODS
from sushi_lang.semantics.param_modes import BuiltinModes


#: Every built-in `List@(T)` method and the number of arguments it takes. The family
#: claims a name from these keys, and the typecheck pass checks the count (CE2009)
#: before it reads the arguments.
LIST_METHOD_ARITY: Mapping[str, int] = MappingProxyType({
    "new": 0, "with_capacity": 1,
    "len": 0, "capacity": 0, "is_empty": 0,
    "push": 1, "pop": 0, "get": 1, "insert": 2, "remove": 1,
    "first": 0, "last": 0,
    "clear": 0, "reserve": 1, "shrink_to_fit": 0,
    "destroy": 0, "free": 0, "debug": 0, "iter": 0, "clone": 0,
    "contains": 1, "index_of": 1, "index_of_from": 2,
})


#: The parameter modes of the `List@(T)` methods (#1173). `push` and `insert` store into
#: a slot, which takes ownership by position; every other parameter borrows.
LIST_METHOD_MODES = BuiltinModes(slots=CONTAINER_INSERT_METHODS & LIST_METHOD_ARITY.keys())


def is_builtin_list_method(method_name: str) -> bool:
    """Return True if ``method_name`` is a built-in List<T> method."""
    return method_name in LIST_METHOD_ARITY


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
