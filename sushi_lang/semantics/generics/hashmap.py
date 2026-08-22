"""The ir-free half of HashMap<K, V>: method validation and type-table plumbing."""

from typing import Any, Optional, TYPE_CHECKING
from sushi_lang.semantics.ast import MethodCall, Call
from sushi_lang.semantics.typesys import StructType, Type, BuiltinType
from sushi_lang.semantics.generics.type_strings import (
    resolve_type_from_string,
    split_type_arguments,
)
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.types import GenericStructType


def is_builtin_hashmap_method(method_name: str) -> bool:
    """Check if a method name is a builtin HashMap<K, V> method."""
    return method_name in (
        "new", "insert", "get", "contains_key", "remove",
        "len", "is_empty", "tombstone_count", "rehash", "free", "destroy", "debug",
        "keys", "values", "entries", "clone"
    )


def validate_hashmap_method_with_validator(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V> method calls."""
    method = call.method

    if method == "new":
        _validate_hashmap_new(call, hashmap_type, reporter, validator)
    elif method == "insert":
        _validate_hashmap_insert(call, hashmap_type, reporter, validator)
    elif method == "get":
        _validate_hashmap_get(call, hashmap_type, reporter, validator)
    elif method == "contains_key":
        _validate_hashmap_contains_key(call, hashmap_type, reporter, validator)
    elif method == "remove":
        _validate_hashmap_remove(call, hashmap_type, reporter, validator)
    elif method == "len":
        _validate_hashmap_len(call, hashmap_type, reporter)
    elif method == "is_empty":
        _validate_hashmap_is_empty(call, hashmap_type, reporter)
    elif method == "tombstone_count":
        _validate_hashmap_tombstone_count(call, hashmap_type, reporter)
    elif method == "rehash":
        _validate_hashmap_rehash(call, hashmap_type, reporter)
    elif method == "free":
        _validate_hashmap_free(call, hashmap_type, reporter)
    elif method == "destroy":
        _validate_hashmap_destroy(call, hashmap_type, reporter)
    elif method == "debug":
        _validate_hashmap_debug(call, hashmap_type, reporter)
    elif method == "keys":
        _validate_hashmap_keys(call, hashmap_type, reporter)
    elif method == "values":
        _validate_hashmap_values(call, hashmap_type, reporter)
    elif method == "entries":
        _validate_hashmap_entries(call, hashmap_type, reporter)
    elif method == "clone":
        _validate_hashmap_clone(call, hashmap_type, reporter)
    else:
        raise_internal_error("CE0085", method=method)


def parse_hashmap_types(hashmap_type: StructType, validator: Any) -> tuple[Optional[Type], Optional[Type]]:
    """Parse K and V types from HashMap<K, V> type name."""

    if not hashmap_type.name.startswith("HashMap<"):
        return None, None

    type_params_str = hashmap_type.name[8:-1]  # Remove "HashMap<" and ">"

    bracket_depth = 0
    comma_pos = -1
    for i, c in enumerate(type_params_str):
        if c == '<':
            bracket_depth += 1
        elif c == '>':
            bracket_depth -= 1
        elif c == ',' and bracket_depth == 0:
            comma_pos = i
            break

    if comma_pos == -1:
        return None, None

    key_type_str = type_params_str[:comma_pos].strip()
    value_type_str = type_params_str[comma_pos + 1:].strip()

    key_type = _resolve_type_string(key_type_str, validator)
    value_type = _resolve_type_string(value_type_str, validator)

    return key_type, value_type


def _resolve_type_string(type_str: str, validator: Any) -> Optional[Type]:
    """Resolve one of HashMap<K, V>'s type arguments, through the shared reader."""
    from sushi_lang.semantics.generics.type_strings import resolve_type_argument
    return resolve_type_argument(type_str, validator)


def _validate_hashmap_new(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.new() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="new", expected=0, got=len(call.args))

    key_type, _ = parse_hashmap_types(hashmap_type, validator)
    if key_type is None:
        return

    from sushi_lang.semantics.typesys import DynamicArrayType
    if isinstance(key_type, DynamicArrayType):
        er.emit(reporter, er.ERR.CE2058, call.loc, key_type=display_type(key_type))
        return

    # The seam, not the backend-populated registry: during the typecheck pass the registry holds
    # only what the derive pass derived, so reading it directly rejected every primitive key
    # (#272). A perk implementation is the sanctioned hash override and counts too.
    from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
    has_hash = (builtin_method_exists(key_type, "hash")
                or validator.perk_impl_table.get_method(key_type, "hash") is not None)
    if not has_hash:
        er.emit(reporter, er.ERR.CE2054, call.loc, key_type=display_type(key_type))
        return

    if not _key_supports_equality(key_type, validator):
        er.emit(reporter, er.ERR.CE2055, call.loc, key_type=display_type(key_type))


_EQUALITY_SCALARS = (
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.BOOL, BuiltinType.F32, BuiltinType.F64, BuiltinType.STRING,
)


def _key_supports_equality(key_type: Type, validator: Any, _seen: Optional[set] = None) -> bool:
    """Can the probe compare two keys of this type?

    Mirrors the kinds `emit_key_equality_check` (backend/generics/hashmap/utils.py)
    implements; anything it declines here would be a NotImplementedError there. A
    foreign `ptr` has no identity (CE5010), and `Own`/`List` backing structs carry a
    raw pointer field, so a key reaching one of those has no equality.
    """
    from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, EnumType, UnknownType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if _seen is None:
        _seen = set()

    if isinstance(key_type, UnknownType):
        resolved = (validator.struct_table.by_name.get(key_type.name)
                    or validator.enum_table.by_name.get(key_type.name))
        if resolved is None:
            return True
        key_type = resolved

    if isinstance(key_type, BuiltinType):
        return key_type in _EQUALITY_SCALARS
    if isinstance(key_type, StructType):
        if key_type.name in _seen:
            return True
        _seen.add(key_type.name)
        return all(_key_supports_equality(field_type, validator, _seen)
                   for _, field_type in key_type.fields)
    if isinstance(key_type, EnumType):
        if key_type.name in _seen:
            return True
        _seen.add(key_type.name)
        return all(_key_supports_equality(assoc, validator, _seen)
                   for variant in key_type.variants
                   for assoc in variant.associated_types)
    if isinstance(key_type, (ArrayType, DynamicArrayType)):
        return _key_supports_equality(key_type.base_type, validator, _seen)
    if isinstance(key_type, GenericTypeRef):
        return all(_key_supports_equality(arg, validator, _seen)
                   for arg in key_type.type_args)
    return False


def _validate_hashmap_insert(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.insert(key, value) method call."""
    from sushi_lang.semantics.passes.types.utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall
    from sushi_lang.semantics.passes.types.compatibility import types_compatible

    if len(call.args) != 2:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="insert", expected=2, got=len(call.args))
        return

    key_type, value_type = parse_hashmap_types(hashmap_type, validator)
    if key_type is None or value_type is None:
        for arg in call.args:
            validator.validate_expression(arg)
        return

    expected_types = [key_type, value_type]
    for i, (arg, expected_ty) in enumerate(zip(call.args, expected_types, strict=False)):
        propagate_enum_type_to_dotcall(validator, arg, expected_ty)

        propagate_struct_type_to_dotcall(validator, arg, expected_ty)

        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(expected_ty, StructType):
            struct_name = arg.callee.id
            if struct_name in validator.generic_struct_table.by_name:
                arg.callee.id = expected_ty.name

        validator.validate_expression(arg)

        if expected_ty is not None:
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, expected_ty):
                er.emit(reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=display_type(expected_ty), got=display_type(arg_type))


def _validate_hashmap_get(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.get(key) method call."""
    _validate_hashmap_key_method(call, hashmap_type, reporter, validator, method_name="get")


def _validate_hashmap_contains_key(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.contains_key(key) method call."""
    _validate_hashmap_key_method(call, hashmap_type, reporter, validator, method_name="contains_key")


def _validate_hashmap_remove(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.remove(key) method call."""
    _validate_hashmap_key_method(call, hashmap_type, reporter, validator, method_name="remove")


def _validate_hashmap_key_method(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any,
    method_name: str
) -> None:
    """Validate HashMap<K, V> methods that take a key argument (get, contains_key, remove)."""
    from sushi_lang.semantics.passes.types.utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall
    from sushi_lang.semantics.passes.types.compatibility import types_compatible

    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc, method=method_name, expected=1, got=len(call.args))
        return

    key_type, _ = parse_hashmap_types(hashmap_type, validator)
    if key_type is None:
        validator.validate_expression(call.args[0])
        return

    arg = call.args[0]

    propagate_enum_type_to_dotcall(validator, arg, key_type)

    propagate_struct_type_to_dotcall(validator, arg, key_type)

    if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(key_type, StructType):
        struct_name = arg.callee.id
        if struct_name in validator.generic_struct_table.by_name:
            arg.callee.id = key_type.name

    validator.validate_expression(arg)

    if key_type is not None:
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, key_type):
            er.emit(reporter, er.ERR.CE2006, arg.loc,
                   index=1, expected=display_type(key_type), got=display_type(arg_type))


def _validate_hashmap_len(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.len() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="len", expected=0, got=len(call.args))


def _validate_hashmap_clone(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.clone() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="clone", expected=0, got=len(call.args))


def _validate_hashmap_is_empty(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.is_empty() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_empty", expected=0, got=len(call.args))


def _validate_hashmap_tombstone_count(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.tombstone_count() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="tombstone_count", expected=0, got=len(call.args))


def _validate_hashmap_rehash(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.rehash() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="rehash", expected=0, got=len(call.args))


def _validate_hashmap_free(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.free() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="free", expected=0, got=len(call.args))


def _validate_hashmap_destroy(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.destroy() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="destroy", expected=0, got=len(call.args))


def _validate_hashmap_debug(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.debug() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="debug", expected=0, got=len(call.args))


def _validate_hashmap_keys(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.keys() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="keys", expected=0, got=len(call.args))


def _validate_hashmap_values(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.values() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="values", expected=0, got=len(call.args))


def _validate_hashmap_entries(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.entries() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="entries", expected=0, got=len(call.args))


def hashmap_generic_struct() -> 'GenericStructType':
    """The HashMap<K, V> generic struct, as the collect pass registers it."""
    from sushi_lang.semantics.generics.types import GenericStructType, TypeParameter
    from sushi_lang.semantics.typesys import DynamicArrayType

    return GenericStructType(
        name="HashMap",
        type_params=(TypeParameter(name="K"), TypeParameter(name="V")),
        fields=(
            ("buckets", DynamicArrayType(base_type=BuiltinType.I32)),
            ("size", BuiltinType.I32),
            ("capacity", BuiltinType.I32),
            ("tombstones", BuiltinType.I32),
        ),
    )


def extract_key_value_types(hashmap_type: StructType, tables: Any) -> tuple[Type, Type]:
    """Extract K and V types from HashMap<K, V>."""
    name = hashmap_type.name

    if not name.startswith("HashMap<") or not name.endswith(">"):
        raise_internal_error("CE0087", type=name)

    type_args_str = name[len("HashMap<"):-1]

    parts = split_type_arguments(type_args_str)
    if len(parts) != 2:
        raise_internal_error("CE0050", generic="HashMap", expected=2, got=len(parts))

    key_type_str, value_type_str = parts[0].strip(), parts[1].strip()

    key_type = resolve_type_from_string(key_type_str, tables)
    value_type = resolve_type_from_string(value_type_str, tables)

    return (key_type, value_type)


def get_entry_type_name(key_type: Type, value_type: Type) -> str:
    """Get the name for a user-facing Entry<K, V> struct type."""
    key_str = str(key_type).lower() if isinstance(key_type, BuiltinType) else str(key_type)
    val_str = str(value_type).lower() if isinstance(value_type, BuiltinType) else str(value_type)
    return f"Entry<{key_str}, {val_str}>"


def ensure_entry_type_in_struct_table(struct_table: Any, key_type: Type, value_type: Type) -> StructType:
    """Ensure that a user-facing Entry<K, V> struct exists in the struct table."""
    entry_name = get_entry_type_name(key_type, value_type)

    if entry_name in struct_table.by_name:
        return struct_table.by_name[entry_name]

    entry_struct = StructType(
        name=entry_name,
        fields=(("key", key_type), ("value", value_type)),
        generic_base="Entry",
        generic_args=(key_type, value_type),
    )

    struct_table.by_name[entry_name] = entry_struct
    if hasattr(struct_table, 'order'):
        struct_table.order.append(entry_name)

    return entry_struct
