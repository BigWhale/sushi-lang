"""The built-in methods of `HashMap@(K, V)`: one emitter for each method (ruling Q7).

The SLIR emitter evaluates every operand before the family runs (docs/design/ir.md 11.9):
the receiver gives the ADDRESS of the map, and each argument gives its value, the key
first, then the value. So each emitter reads the size, the capacity and the buckets of the
map after the last argument, when it runs. Each lookup goes through the one probe loop
(`probe.py`).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Sequence

import llvmlite.ir as ir

from sushi_lang.backend.generics.container_table import (
    ContainerMethod, emit_static_with_operands, emit_with_operands,
)
from sushi_lang.backend.ownership import copy_out
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import StructType

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
)


def _iterator(*_operands: Any) -> ir.Value:
    """SLIR lowering makes each producer an `AggIterator`, so no call reaches this row."""
    raise_internal_error("CE0000", detail="a call of an iterator of a HashMap reached the "
                                          "emitter")


#: Every built-in `HashMap@(K, V)` method and its emitter, over operands. The key set is
#: the key set of `HASHMAP_METHOD_ARITY`;
#: `tests/unit/test_container_emitters_match_the_table.py` holds it.
HASHMAP_EMITTERS: Mapping[str, ContainerMethod] = MappingProxyType({
    "new": ContainerMethod(lambda c, _v, t, _a: emit_hashmap_new(c, t), static=True),
    "insert": ContainerMethod(emit_hashmap_insert),
    "get": ContainerMethod(emit_hashmap_get),
    "contains_key": ContainerMethod(emit_hashmap_contains_key, answers_bool=True),
    "remove": ContainerMethod(emit_hashmap_remove),
    "rehash": ContainerMethod(lambda c, v, t, _a: emit_hashmap_rehash(c, v, t)),
    "free": ContainerMethod(lambda c, v, t, _a: emit_hashmap_free(c, v, t)),
    "destroy": ContainerMethod(lambda c, v, t, _a: emit_hashmap_destroy(c, v, t)),
    "debug": ContainerMethod(lambda c, v, t, _a: emit_hashmap_debug(c, v, t)),
    "len": ContainerMethod(lambda c, v, _t, _a: emit_hashmap_len(c, v)),
    "is_empty": ContainerMethod(lambda c, v, _t, _a: emit_hashmap_is_empty(c, v),
                                answers_bool=True),
    "tombstone_count": ContainerMethod(lambda c, v, _t, _a: emit_hashmap_tombstone_count(c, v)),
    "keys": ContainerMethod(_iterator),
    "values": ContainerMethod(_iterator),
    "entries": ContainerMethod(_iterator),
    "pairs": ContainerMethod(_iterator),
    "clone": ContainerMethod(lambda c, v, t, _a: copy_out(c, v, t)),
})


def emit_hashmap_method(codegen: Any, name: str, address: ir.Value, map_type: StructType,
                        args: Sequence[ir.Value]) -> ir.Value:
    """The value of the method `name` on the map at `address`, with the values of its
    arguments."""
    return emit_with_operands(HASHMAP_EMITTERS, "CE0085", codegen, name, address, map_type,
                              args)


def emit_hashmap_static(codegen: Any, name: str, map_type: StructType,
                        args: Sequence[ir.Value]) -> ir.Value:
    """The map that the static `name` builds (`HashMap.new`)."""
    return emit_static_with_operands(HASHMAP_EMITTERS, "CE0085", codegen, name, map_type,
                                     args)
