"""The ir-free half of HashMap<K, V>: method validation and type-table plumbing."""

from types import MappingProxyType
from typing import Any, Literal, Mapping, Optional, TYPE_CHECKING, overload
from sushi_lang.semantics.ast import MethodCall, Call
from sushi_lang.semantics.typesys import StructType, Type, BuiltinType
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.derived_methods import DerivedMethodTable

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.types import GenericStructType


#: Every built-in `HashMap@(K, V)` method and the number of arguments it takes. The count
#: is checked in the typecheck pass (CE2009) before the check below runs; `new` is the
#: static `HashMap.new()`, and the same row answers it.
HASHMAP_METHOD_ARITY: Mapping[str, int] = MappingProxyType({
    "new": 0, "insert": 2, "get": 1, "contains_key": 1, "remove": 1,
    "len": 0, "is_empty": 0, "tombstone_count": 0, "rehash": 0,
    "free": 0, "destroy": 0, "debug": 0,
    "keys": 0, "values": 0, "entries": 0, "clone": 0,
})


def is_builtin_hashmap_method(method_name: str) -> bool:
    """Check if a method name is a builtin HashMap<K, V> method."""
    return method_name in HASHMAP_METHOD_ARITY


def validate_hashmap_method_with_validator(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate a HashMap<K, V> method call whose count is correct: the argument types."""
    if call.method not in HASHMAP_METHOD_ARITY:
        raise_internal_error("CE0085", method=call.method)
    if call.method == "insert":
        _validate_hashmap_insert(call, hashmap_type, reporter, validator)
    elif call.method in _KEY_METHODS:
        _validate_hashmap_key_method(call, hashmap_type, reporter, validator)


#: The methods whose one argument is a key.
_KEY_METHODS = frozenset({"get", "contains_key", "remove"})


@overload
def parse_hashmap_types(hashmap_type: Any, tables: Any,
                        on_missing: Literal["none"] = "none") -> tuple[Optional[Type], Optional[Type]]: ...
@overload
def parse_hashmap_types(hashmap_type: Any, tables: Any,
                        on_missing: Literal["raise"]) -> tuple[Type, Type]: ...
def parse_hashmap_types(hashmap_type: Any, tables: Any,
                        on_missing: str = "none") -> tuple[Optional[Type], Optional[Type]]:
    """The K and V of a HashMap<K, V> instance, read from its `generic_args`.

    A type that is not a HashMap instance answers `(None, None)`, or CE0087 when the
    caller says `on_missing="raise"`: the backend reaches here only with a HashMap.
    """
    from sushi_lang.semantics.generics.list import instance_type_arguments

    args = instance_type_arguments(hashmap_type, "HashMap", tables)
    if args is None or len(args) != 2:
        if on_missing == "raise":
            if args is None:
                raise_internal_error("CE0087", type=str(hashmap_type))
            raise_internal_error("CE0050", generic="HashMap", expected=2, got=len(args))
        return None, None
    return args[0], args[1]


def reject_unusable_key(hashmap_type: StructType, validator: Any, span: Any) -> None:
    """CE2058 / CE2054 / CE2055: the key rule a written HashMap type breaks (#773).

    A rule on the KEY TYPE alone, so it is read where a HashMap type is written
    (`passes/types/utils.py:reject_unusable_hashmap_keys`) and never at `new()`.
    """
    key_type, _ = parse_hashmap_types(hashmap_type, validator)
    if key_type is None:
        return

    reporter = validator.reporter
    from sushi_lang.semantics.typesys import DynamicArrayType
    if isinstance(key_type, DynamicArrayType):
        er.emit(reporter, er.ERR.CE2058, span, key_type=display_type(key_type))
        return

    # The seam, not the derived-method table alone: that table holds what the derive pass
    # derived plus the primitive families, and nothing about an array or a container key
    # (#272). A perk implementation is the sanctioned hash override and counts too.
    from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
    has_hash = (builtin_method_exists(key_type, "hash", validator.derived_methods)
                or validator.perk_impl_table.get_method(key_type, "hash") is not None)
    if not has_hash:
        er.emit(reporter, er.ERR.CE2054, span, key_type=display_type(key_type))
        return

    if not _key_supports_equality(key_type, validator):
        er.emit(reporter, er.ERR.CE2055, span, key_type=display_type(key_type))


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
    from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
    from sushi_lang.semantics.passes.types.compatibility import types_compatible

    key_type, value_type = parse_hashmap_types(hashmap_type, validator)
    if key_type is None or value_type is None:
        for arg in call.args:
            validator.validate_expression(arg)
        return

    expected_types = [key_type, value_type]
    for i, (arg, expected_ty) in enumerate(zip(call.args, expected_types, strict=False)):
        propagate_types_to_value(validator, arg, expected_ty)

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


def _validate_hashmap_key_method(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate HashMap<K, V> methods that take a key argument (get, contains_key, remove)."""
    from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
    from sushi_lang.semantics.passes.types.compatibility import types_compatible

    key_type, _ = parse_hashmap_types(hashmap_type, validator)
    if key_type is None:
        validator.validate_expression(call.args[0])
        return

    arg = call.args[0]

    propagate_types_to_value(validator, arg, key_type)

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


def get_entry_type_name(key_type: Type, value_type: Type) -> str:
    """Get the name for a user-facing Entry<K, V> struct type."""
    return f"Entry<{key_type}, {value_type}>"


def ensure_entry_type_in_struct_table(struct_table: Any, derived: DerivedMethodTable,
                                      key_type: Type, value_type: Type) -> StructType:
    """Ensure that a user-facing Entry<K, V> struct exists in the struct table.

    `derived` is the compilation's auto-derived pair, and it is required: `.entries()`
    is typed after the derive pass walked the table, so this mint is the one supplier
    of the pair for an `Entry`. Without it the struct is the only one in the program
    with no derived hash and no derived clone, and `e.hash()` reads a CE2008 that is
    not true of a struct (#730).
    """
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

    from sushi_lang.semantics.passes.derive import derive_for_struct
    derive_for_struct(entry_struct, derived)

    return entry_struct
