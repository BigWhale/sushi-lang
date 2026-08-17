"""Resolve a `Type` from its string representation."""

from typing import Any
import re

from sushi_lang.semantics.typesys import Type, BuiltinType, ArrayType, DynamicArrayType
from sushi_lang.internals.errors import raise_internal_error


_BUILTIN_TYPES = {
    "i8": BuiltinType.I8,
    "i16": BuiltinType.I16,
    "i32": BuiltinType.I32,
    "i64": BuiltinType.I64,
    "u8": BuiltinType.U8,
    "u16": BuiltinType.U16,
    "u32": BuiltinType.U32,
    "u64": BuiltinType.U64,
    "f32": BuiltinType.F32,
    "f64": BuiltinType.F64,
    "bool": BuiltinType.BOOL,
    "string": BuiltinType.STRING,
}


def split_type_arguments(type_args_str: str) -> list[str]:
    """Split comma-separated type arguments while respecting angle brackets."""
    parts = []
    current: list[str] = []
    depth = 0

    for char in type_args_str:
        if char == '<':
            depth += 1
            current.append(char)
        elif char == '>':
            depth -= 1
            current.append(char)
        elif char == ',' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(char)

    if current:
        parts.append(''.join(current).strip())

    return parts


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split `s` on `sep`, ignoring separators nested inside <>, (), or []."""
    parts = []
    current: list[str] = []
    depth = 0
    for char in s:
        if char in '<([':
            depth += 1
        elif char in '>)]':
            depth -= 1
        if char == sep and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append(''.join(current).strip())
    return parts


def _resolve_function_type_from_string(type_str: str, tables: Any) -> Type:
    """Resolve a first-class function type string: "fn(P0, P1, ...) -> T [| E]"."""
    from sushi_lang.semantics.typesys import FunctionType

    open_idx = type_str.index("(")
    depth = 0
    close_idx = -1
    for i in range(open_idx, len(type_str)):
        if type_str[i] == "(":
            depth += 1
        elif type_str[i] == ")":
            depth -= 1
            if depth == 0:
                close_idx = i
                break

    params_str = type_str[open_idx + 1:close_idx].strip()
    rest = type_str[close_idx + 1:].strip()
    if rest.startswith("->"):
        rest = rest[2:].strip()

    pipe_parts = _split_top_level(rest, "|")
    ret_str = pipe_parts[0].strip()
    err_str = pipe_parts[1].strip() if len(pipe_parts) > 1 else "StdError"

    param_types = tuple(
        resolve_type_from_string(p, tables)
        for p in _split_top_level(params_str, ",") if p
    )
    ok_type = resolve_type_from_string(ret_str, tables)
    err_type = resolve_type_from_string(err_str, tables)
    return FunctionType(param_types=param_types, ok_type=ok_type, err_type=err_type)


def resolve_type_from_string(type_str: str, tables: Any) -> Type:
    """Resolve a type from its string representation."""
    type_str = type_str.strip()

    # First-class function type: must be handled before the array branch (its return
    # type may legitimately end with "[]", which the array regex would misparse).
    if type_str.startswith("fn(") or type_str.startswith("fn ("):
        return _resolve_function_type_from_string(type_str, tables)

    if '[' in type_str and type_str.endswith(']'):
        match = re.match(r'^(.+)\[(\d*)\]$', type_str)
        if match:
            base_type_str = match.group(1)
            size_str = match.group(2)

            base_type = resolve_type_from_string(base_type_str, tables)

            if size_str:
                return ArrayType(base_type=base_type, size=int(size_str))
            return DynamicArrayType(base_type=base_type)

    if type_str in _BUILTIN_TYPES:
        return _BUILTIN_TYPES[type_str]

    if '<' in type_str and type_str.endswith('>'):
        if type_str in tables.enum_table.by_name:
            return tables.enum_table.by_name[type_str]
        if type_str in tables.struct_table.by_name:
            return tables.struct_table.by_name[type_str]
        raise_internal_error("CE0045", type=type_str)

    if type_str in tables.struct_table.by_name:
        return tables.struct_table.by_name[type_str]

    if type_str in tables.enum_table.by_name:
        return tables.enum_table.by_name[type_str]

    raise_internal_error("CE0022", type=type_str)
