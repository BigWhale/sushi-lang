"""The built-in methods of `List@(T)`: one emitter for each method (ruling Q7).

The SLIR emitter evaluates every operand before the family runs (docs/design/ir.md 11.9):
the receiver gives the ADDRESS of the List, and each argument gives its value. So each
emitter reads the length, the capacity and the data of the List after the last argument,
when it runs.
"""

from types import MappingProxyType
from typing import Any, Mapping, Sequence

import llvmlite.ir as ir

from sushi_lang.backend.generics.container_table import (
    ContainerMethod, emit_static_with_operands, emit_with_operands,
)
from sushi_lang.backend.ownership import copy_out
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import StructType

from .methods_simple import (
    emit_list_new,
    emit_list_with_capacity,
    emit_list_len,
    emit_list_capacity,
    emit_list_is_empty
)
from .methods_modify import (
    emit_list_push,
    emit_list_pop,
    emit_list_get,
    emit_list_end,
    emit_list_clear,
    emit_list_insert,
    emit_list_remove
)
from .methods_capacity import (
    emit_list_reserve,
    emit_list_shrink_to_fit
)
from .methods_destroy import (
    emit_list_destroy,
    emit_list_free
)
from .methods_debug import (
    emit_list_debug
)
from .methods_search import (
    emit_list_contains,
    emit_list_index_of,
    emit_list_index_of_from
)


def _iter(*_operands: Any) -> ir.Value:
    """SLIR lowering makes `iter()` an `AggIterator`, so no call reaches this row."""
    raise_internal_error("CE0000", detail="a call of iter() on a List reached the emitter")


#: Every built-in `List@(T)` method and its emitter, over operands. The key set is the key
#: set of `LIST_METHOD_ARITY`; `tests/unit/test_container_emitters_match_the_table.py`
#: holds it.
LIST_EMITTERS: Mapping[str, ContainerMethod] = MappingProxyType({
    "new": ContainerMethod(lambda c, _v, t, _a: emit_list_new(c, t), static=True),
    "with_capacity": ContainerMethod(
        lambda c, _v, t, a: emit_list_with_capacity(c, c.utils.require_i32(a[0]), t),
        static=True),
    "len": ContainerMethod(lambda c, v, _t, _a: emit_list_len(c, v)),
    "capacity": ContainerMethod(lambda c, v, _t, _a: emit_list_capacity(c, v)),
    "is_empty": ContainerMethod(lambda c, v, _t, _a: emit_list_is_empty(c, v), answers_bool=True),
    "push": ContainerMethod(emit_list_push, by_address=True),
    "pop": ContainerMethod(lambda c, v, t, _a: emit_list_pop(c, v, t)),
    "get": ContainerMethod(emit_list_get),
    "first": ContainerMethod(lambda c, v, t, _a: emit_list_end(c, v, t, last=False)),
    "last": ContainerMethod(lambda c, v, t, _a: emit_list_end(c, v, t, last=True)),
    "clear": ContainerMethod(lambda c, v, t, _a: emit_list_clear(c, v, t)),
    "insert": ContainerMethod(emit_list_insert),
    "remove": ContainerMethod(emit_list_remove),
    "reserve": ContainerMethod(emit_list_reserve),
    "shrink_to_fit": ContainerMethod(lambda c, v, t, _a: emit_list_shrink_to_fit(c, v, t)),
    "destroy": ContainerMethod(lambda c, v, t, _a: emit_list_destroy(c, v, t)),
    "free": ContainerMethod(lambda c, v, t, _a: emit_list_free(c, v, t)),
    "debug": ContainerMethod(lambda c, v, t, _a: emit_list_debug(c, v, t)),
    "iter": ContainerMethod(_iter),
    "clone": ContainerMethod(lambda c, v, t, _a: copy_out(c, v, t)),
    "contains": ContainerMethod(emit_list_contains, answers_bool=True),
    "index_of": ContainerMethod(emit_list_index_of),
    "index_of_from": ContainerMethod(emit_list_index_of_from),
})


def emit_list_method(codegen: Any, name: str, address: ir.Value, list_type: StructType,
                     args: Sequence[ir.Value]) -> ir.Value:
    """The value of the method `name` on the List at `address`, with the values of its
    arguments."""
    return emit_with_operands(LIST_EMITTERS, "CE0083", codegen, name, address, list_type,
                              args)


def emit_list_static(codegen: Any, name: str, list_type: StructType,
                     args: Sequence[ir.Value]) -> ir.Value:
    """The List that the static `name` builds (`List.new`, `List.with_capacity`)."""
    return emit_static_with_operands(LIST_EMITTERS, "CE0083", codegen, name, list_type, args)
