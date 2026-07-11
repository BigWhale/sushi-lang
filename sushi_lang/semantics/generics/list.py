"""Validation and element-type parsing for the built-in List<T> methods.

The ir-free half of ``backend/generics/list/``: method recognition, Pass-2
argument validation, and List<T> element-type resolution. LLVM emission stays in
``backend/generics/list/``.
"""
from typing import Any, Optional

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type, BuiltinType
import sushi_lang.internals.errors as er
from sushi_lang.internals.errors import raise_internal_error


# All supported List<T> methods
BUILTIN_LIST_METHODS = {
    "new",           # List.new() -> List<T>
    "with_capacity", # List.with_capacity(i32) -> List<T>
    "len",           # list.len() -> i32
    "capacity",      # list.capacity() -> i32
    "is_empty",      # list.is_empty() -> bool
    "push",          # list.push(T) -> ~
    "pop",           # list.pop() -> Maybe<T>
    "get",           # list.get(i32) -> Maybe<T>
    "clear",         # list.clear() -> ~
    "reserve",       # list.reserve(i32) -> ~
    "shrink_to_fit", # list.shrink_to_fit() -> ~
    "insert",        # list.insert(i32, T) -> Result<~>
    "remove",        # list.remove(i32) -> Maybe<T>
    "destroy",       # list.destroy() -> ~
    "free",          # list.free() -> ~
    "debug",         # list.debug() -> ~
    "iter",          # list.iter() -> Iterator<T>
}


def is_builtin_list_method(method_name: str) -> bool:
    """Return True if ``method_name`` is a built-in List<T> method."""
    return method_name in BUILTIN_LIST_METHODS


def validate_list_method_with_validator(
    call: MethodCall,
    list_type: StructType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate a List<T> method call: arity, then element-type where relevant."""
    method = call.method
    num_args = len(call.args)

    expected_args = {
        # 0 arguments
        "new": 0, "len": 0, "capacity": 0, "is_empty": 0,
        "pop": 0, "clear": 0, "shrink_to_fit": 0, "destroy": 0, "free": 0, "debug": 0, "iter": 0,
        # 1 argument
        "with_capacity": 1, "push": 1, "get": 1, "reserve": 1, "remove": 1,
        # 2 arguments
        "insert": 2,
    }

    if method not in expected_args:
        raise_internal_error("CE0083", method=method)

    expected = expected_args[method]
    if num_args != expected:
        er.emit(reporter, er.ERR.CE2053, call.loc,
                method=method, expected=expected, got=num_args)
        return

    # Element-type validation for methods taking a T argument:
    #   push(T) -> args[0], insert(i32, T) -> args[1] (issue #47).
    element_arg_index = {"push": 0, "insert": 1}.get(method)
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
        # T could not be resolved; still validate the argument expression itself.
        validator.validate_expression(arg)
        return

    from sushi_lang.semantics.passes.types.utils import (
        propagate_enum_type_to_dotcall,
        propagate_struct_type_to_dotcall,
    )
    from sushi_lang.semantics.passes.types.compatibility import types_compatible

    propagate_enum_type_to_dotcall(validator, arg, element_type)
    propagate_struct_type_to_dotcall(validator, arg, element_type)
    validator.validate_expression(arg)

    arg_type = validator.infer_expression_type(arg)
    if arg_type is not None and not types_compatible(validator, arg_type, element_type):
        er.emit(reporter, er.ERR.CE2006, arg.loc,
                index=arg_index + 1, expected=str(element_type), got=str(arg_type))


def parse_list_types(list_type: StructType, validator: Any) -> Optional[Type]:
    """Resolve the element type T from a List<T> struct type, or None."""
    if not list_type.name.startswith("List<"):
        return None

    type_param_str = list_type.name[5:-1]  # strip "List<" and ">"

    # First-class function element type (e.g. List<fn(i32) -> i32>).
    if type_param_str.startswith("fn(") or type_param_str.startswith("fn ("):
        from sushi_lang.sushi_stdlib.generics.collections.hashmap.types import resolve_type_from_string
        try:
            return resolve_type_from_string(type_param_str, validator)
        except Exception:
            return None

    builtin_map = {
        'i8': BuiltinType.I8, 'i16': BuiltinType.I16, 'i32': BuiltinType.I32, 'i64': BuiltinType.I64,
        'u8': BuiltinType.U8, 'u16': BuiltinType.U16, 'u32': BuiltinType.U32, 'u64': BuiltinType.U64,
        'f32': BuiltinType.F32, 'f64': BuiltinType.F64,
        'bool': BuiltinType.BOOL, 'string': BuiltinType.STRING,
    }
    if type_param_str in builtin_map:
        return builtin_map[type_param_str]

    if hasattr(validator, 'enum_table') and type_param_str in validator.enum_table.by_name:
        return validator.enum_table.by_name[type_param_str]

    if hasattr(validator, 'struct_table') and type_param_str in validator.struct_table.by_name:
        return validator.struct_table.by_name[type_param_str]

    return None
