"""The ir-free half of HashMap<K, V>: method validation and type-table plumbing."""

from types import MappingProxyType
from typing import Any, Literal, Mapping, Optional, TYPE_CHECKING, overload
from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.semantics.ast import MethodCall, Call
from sushi_lang.semantics.method_effects import CONTAINER_INSERT_METHODS
from sushi_lang.semantics.param_modes import BuiltinModes
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
    "keys": 0, "values": 0, "entries": 0, "pairs": 0, "clone": 0,
})


#: The parameter modes of the `HashMap@(K, V)` methods (#1173). `insert` stores its key
#: and its value into a slot, which takes ownership by position; every other parameter
#: borrows.
HASHMAP_METHOD_MODES = BuiltinModes(
    slots=CONTAINER_INSERT_METHODS & HASHMAP_METHOD_ARITY.keys())


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
    elif call.method in _ITERATOR_METHODS:
        _reject_iterator_outside_foreach(call, reporter, validator)


#: The methods whose one argument is a key.
_KEY_METHODS = frozenset({"get", "contains_key", "remove"})

#: The methods that answer an iterator over the buckets.
_ITERATOR_METHODS = frozenset({"keys", "values", "entries", "pairs"})


def _reject_iterator_outside_foreach(call: MethodCall, reporter: Any, validator: Any) -> None:
    """CE2127: a bucket iterator is legal only as the iterable of a `foreach`.

    The bucket walk needs K and V, and the loop reads them from the receiver of this
    call. A value that left the call carries only `Iterator@(K)`, and the array walk
    that a `foreach` gives it then walks zero entries. A dot call reaches here as a
    view that shares the receiver node, so the test reads the receiver and the method.
    """
    walked = validator.walked_iterable
    if (walked is not None and getattr(walked, "receiver", None) is call.receiver
            and getattr(walked, "method", None) == call.method):
        return
    er.emit_with(reporter, er.ERR.CE2127, call.loc, method=call.method).help(
        f"walk the map where the call is written, `foreach(x in map.{call.method}())`, "
        f"or pass the map and call '.{call.method}()' in the function that walks it").emit()


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


# The base name `HashMap` is registered under.
HASHMAP_BASE = "HashMap"

# The key rule of `HashMap@(K, V)` as a bound on `K` (#1070, R1): an extension or a perk
# implementation on `HashMap@(K, V)` inherits it, as it inherits the constraints of a
# user generic. It is not put on the registered `K`: the monomorphizer would judge it at
# each instance beside the key rule below, and one fault would have two diagnostics.
KEY_CONTRACT = ("Hashable", "Eq")


def reject_unusable_key(hashmap_type: StructType, validator: Any, span: Any) -> None:
    """CE2058 / CE2054 / CE2055: the key rule a written HashMap type breaks (#773).

    A rule on the KEY TYPE alone, so it is read where a HashMap type is written
    (`passes/types/utils.py:reject_unusable_hashmap_keys`) and never at `new()`.
    """
    key_type, _ = parse_hashmap_types(hashmap_type, validator)
    if key_type is None:
        return

    reporter = validator.reporter
    from sushi_lang.semantics.type_predicates import holds_dynamic_array
    if holds_dynamic_array(key_type):
        er.emit(reporter, er.ERR.CE2058, span, key_type=display_type(key_type))
        return

    # The seam, not the derived-method table alone: that table holds what the derive pass
    # derived plus the primitive families, and nothing about an array or a container key
    # (#272). A perk implementation is the sanctioned hash override and counts too.
    from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
    has_hash = (builtin_method_exists(key_type, "hash", validator.derived_methods)
                or validator.perk_impl_table.get_method(key_type, "hash") is not None)
    from sushi_lang.semantics.generics.opaque import explain_unpromised
    if not has_hash:
        explain_unpromised(er.emit_with(reporter, er.ERR.CE2054, span,
                                        key_type=display_type(key_type)),
                           "Hashable", _unhashable_parts(key_type, validator)).emit()
        return

    refused: list = []
    if not _key_supports_equality(key_type, validator, refused):
        explain_unpromised(er.emit_with(reporter, er.ERR.CE2055, span,
                                        key_type=display_type(key_type)),
                           "Eq", refused).emit()


def _unhashable_parts(key_type: Type, validator: Any) -> list:
    """The held types that stop a derived hash of the key: the derive pass's predicate
    (`hashability_of`) names them, for the help of CE2054 (#1070)."""
    from sushi_lang.semantics.generics.hashing import hash_override_of, hashability_of
    from sushi_lang.semantics.passes.resolve import table_resolver
    refused: list = []
    tables = validator.tables
    hashability_of(key_type, resolve=table_resolver(validator.struct_table,
                                                    validator.enum_table),
                   overridden=hash_override_of(validator.perk_impl_table,
                                               tables.generic_perk_impls, tables.holds_bound),
                   refused=refused)
    return refused


def _key_supports_equality(key_type: Type, validator: Any,
                           refused: Optional[list] = None) -> bool:
    """Can the probe compare two keys of this type? The `Eq` contract, HELD rule.

    The probe compares keys through `emit_value_eq`, which reads an `Eq`
    implementation first and the derived equality after it, so the rule here is the
    one that function can emit. A `Hashable` implementation gives a hash and not an
    equality (#936): the two are separate contracts, and a key needs both.
    """
    from sushi_lang.semantics.generics.contracts import EQ, contract_of, override_of
    from sushi_lang.semantics.passes.resolve import table_resolver
    resolve = table_resolver(validator.struct_table, validator.enum_table)
    return contract_of(key_type, EQ, resolve=resolve,
                       overridden=override_of(validator.derived_methods, EQ),
                       refused=refused)[0]


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
        name=HASHMAP_BASE,
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
    return interned_name("Entry", (key_type, value_type))


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
