"""Resolve a `Type` from its string representation."""

from typing import Any, Callable
import re

from sushi_lang.semantics.typesys import Type, BuiltinType, ArrayType, DynamicArrayType
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.type_display import display_type_name


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


def split_type_arguments(text: str, sep: str = ",") -> list[str]:
    """Split `text` on `sep` at the top level, the one splitter for a type string.

    Nesting is `<>`, `()` and `[]`. The `>` of a `->` arrow closes nothing: a function
    type such as `fn(i32) -> i32` reads as one argument, with its parameter list whole.
    """
    parts = []
    current: list[str] = []
    depth = 0
    previous = ""
    for char in text:
        if char in "<([":
            depth += 1
        elif char in ")]" or (char == ">" and previous != "-"):
            depth -= 1
        if char == sep and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        previous = char
    if current:
        parts.append("".join(current).strip())
    return parts


def strip_grouping(text: str) -> str:
    """`(T)` is `T`: the parentheses around a function element of an array (#1128)."""
    while text.startswith("(") and text.endswith(")") and _closing_paren(text) == len(text) - 1:
        text = text[1:-1].strip()
    return text


def _closing_paren(text: str) -> int:
    """The index of the `)` that closes the `(` at index 0, or -1."""
    depth = 0
    for index, char in enumerate(text):
        depth += char == "("
        depth -= char == ")"
        if depth == 0:
            return index
    return -1


def parse_function_type_string(type_str: str, resolve: Callable[[str], Type]) -> Type:
    """Read a first-class function type string: "fn(P0, P1, ...) -> T [| E]".

    The one reader of the spelling. `resolve` reads each component type, so the
    table-backed reader here and the manifest reader (`type_resolution.parse_type_string`)
    share it.
    """
    from sushi_lang.semantics.param_modes import ParamMode, normalize_modes
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

    pipe_parts = split_type_arguments(rest, "|")
    ret_str = pipe_parts[0].strip()
    err_str = pipe_parts[1].strip() if len(pipe_parts) > 1 else None

    # A `nom` parameter is spelled with the marker, which is not part of any type name.
    # `str(FunctionType)` writes it, so reading one back must accept it -- it used to reach
    # the type lookup as the text `nom string` and raise CE0022 (#368). `peek` and `poke`
    # need no case: they ARE part of the type, and the reference branch takes them.
    param_texts = [p for p in split_type_arguments(params_str) if p]
    nom_flags = [text.startswith("nom ") for text in param_texts]
    param_types = tuple(
        resolve(text[4:] if flag else text)
        for text, flag in zip(param_texts, nom_flags, strict=True)
    )
    ok_type = resolve(ret_str)
    err_type = None if err_str is None else resolve(err_str)
    return FunctionType(
        param_types=param_types, ok_type=ok_type, err_type=err_type,
        param_modes=normalize_modes(param_types, [
            ParamMode.NOM if flag else ParamMode.BORROW for flag in nom_flags
        ]),
    )


def resolve_type_from_string(type_str: str, tables: Any) -> Type:
    """Resolve a type from its string representation."""
    type_str = strip_grouping(type_str.strip())

    # First-class function type: must be handled before the array branch (its return
    # type may legitimately end with "[]", which the array regex would misparse).
    if type_str.startswith("fn(") or type_str.startswith("fn ("):
        return parse_function_type_string(
            type_str, lambda text: resolve_type_from_string(text, tables))

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
        raise_internal_error("CE0045", type=display_type_name(type_str))

    if type_str in tables.struct_table.by_name:
        return tables.struct_table.by_name[type_str]

    if type_str in tables.enum_table.by_name:
        return tables.enum_table.by_name[type_str]

    raise_internal_error("CE0022", type=type_str)
