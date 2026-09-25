"""HashMap<K, V> LLVM emission."""

from __future__ import annotations
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Union


from sushi_lang.semantics.generics.hashmap import (
    is_builtin_hashmap_method,
    validate_hashmap_method_with_validator,
)

from sushi_lang.backend.generics.list import ContainerMethod, emit_from_table
from .methods import (
    emit_hashmap_new,
    emit_hashmap_len,
    emit_hashmap_is_empty,
    emit_hashmap_tombstone_count,
    emit_hashmap_get,
    emit_hashmap_contains_key,
    emit_hashmap_insert,
    emit_hashmap_remove,
    emit_hashmap_rehash,
    emit_hashmap_free,
    emit_hashmap_destroy,
    emit_hashmap_debug,
    emit_hashmap_keys,
    emit_hashmap_values,
    emit_hashmap_entries,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import StructType
    from sushi_lang.semantics.ast import DotCall, MethodCall
    import llvmlite.ir as ir


def _emit_hashmap_clone(codegen, expr, receiver_value, receiver_type):
    # Routed through the seam's `copy_out`, which is the ONE deep clone in the backend, so
    # `.clone()` duplicates exactly what the destructor frees. The seam also accepts a
    # pointer, which is the shape a HashMap receiver arrives in. Mirrors emit_list_clone.
    from sushi_lang.backend.ownership import copy_out
    return copy_out(codegen, receiver_value, receiver_type)


#: Every built-in `HashMap@(K, V)` method and its emitter. The key set is the key set of
#: `HASHMAP_METHOD_ARITY`; `tests/unit/test_container_emitters_match_the_table.py` holds it.
HASHMAP_EMITTERS: Mapping[str, ContainerMethod] = MappingProxyType({
    "new": ContainerMethod(lambda c, e, v, t: emit_hashmap_new(c, t)),
    "insert": ContainerMethod(emit_hashmap_insert),
    "get": ContainerMethod(emit_hashmap_get),
    "contains_key": ContainerMethod(emit_hashmap_contains_key, answers_bool=True),
    "remove": ContainerMethod(emit_hashmap_remove),
    "rehash": ContainerMethod(lambda c, e, v, t: emit_hashmap_rehash(c, v, t)),
    "free": ContainerMethod(lambda c, e, v, t: emit_hashmap_free(c, v, t)),
    "destroy": ContainerMethod(lambda c, e, v, t: emit_hashmap_destroy(c, v, t)),
    "debug": ContainerMethod(lambda c, e, v, t: emit_hashmap_debug(c, v, t)),
    "len": ContainerMethod(lambda c, e, v, t: emit_hashmap_len(c, v)),
    "is_empty": ContainerMethod(lambda c, e, v, t: emit_hashmap_is_empty(c, v), answers_bool=True),
    "tombstone_count": ContainerMethod(lambda c, e, v, t: emit_hashmap_tombstone_count(c, v)),
    "keys": ContainerMethod(emit_hashmap_keys),
    "values": ContainerMethod(emit_hashmap_values),
    "entries": ContainerMethod(emit_hashmap_entries),
    "clone": ContainerMethod(_emit_hashmap_clone),
})


def emit_hashmap_method(
    codegen,
    expr: 'MethodCall | DotCall',
    receiver_value: Union['ir.Value', None],
    receiver_type: 'StructType',
    to_i1: bool
) -> 'ir.Value':
    """Emit LLVM IR for HashMap<K, V> method calls."""
    return emit_from_table(HASHMAP_EMITTERS, "CE0085", codegen, expr, receiver_value, receiver_type, to_i1)


__all__ = [
    'is_builtin_hashmap_method',
    'validate_hashmap_method_with_validator',
    'HASHMAP_EMITTERS',
    'emit_hashmap_method',
    'emit_hashmap_new',
    'emit_hashmap_insert',
    'emit_hashmap_get',
    'emit_hashmap_contains_key',
    'emit_hashmap_remove',
    'emit_hashmap_len',
    'emit_hashmap_is_empty',
    'emit_hashmap_tombstone_count',
    'emit_hashmap_rehash',
    'emit_hashmap_free',
    'emit_hashmap_destroy',
    'emit_hashmap_debug',
    'emit_hashmap_keys',
    'emit_hashmap_values',
    'emit_hashmap_entries',
]
