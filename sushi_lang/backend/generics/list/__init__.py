"""Built-in extension methods for List<T> generic struct."""

from types import MappingProxyType
from typing import Any, Mapping
from sushi_lang.semantics.ast import DotCall, MethodCall
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir

from sushi_lang.backend.generics.container_table import ContainerMethod, emit_from_table

from .methods_simple import (
    emit_list_clone,
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
from .methods_iter import (
    emit_list_iter
)



#: Every built-in `List@(T)` method and its emitter. The key set is the key set of
#: `LIST_METHOD_ARITY`; `tests/unit/test_container_emitters_match_the_table.py` holds it.
LIST_EMITTERS: Mapping[str, ContainerMethod] = MappingProxyType({
    "new": ContainerMethod(lambda c, e, v, t: emit_list_new(c, t)),
    "with_capacity": ContainerMethod(lambda c, e, v, t: emit_list_with_capacity(c, e, t)),
    "len": ContainerMethod(lambda c, e, v, t: emit_list_len(c, v)),
    "capacity": ContainerMethod(lambda c, e, v, t: emit_list_capacity(c, v)),
    "is_empty": ContainerMethod(lambda c, e, v, t: emit_list_is_empty(c, v), answers_bool=True),
    "push": ContainerMethod(emit_list_push),
    "pop": ContainerMethod(lambda c, e, v, t: emit_list_pop(c, v, t)),
    "get": ContainerMethod(emit_list_get),
    "clear": ContainerMethod(lambda c, e, v, t: emit_list_clear(c, v, t)),
    "insert": ContainerMethod(emit_list_insert),
    "remove": ContainerMethod(emit_list_remove),
    "reserve": ContainerMethod(emit_list_reserve),
    "shrink_to_fit": ContainerMethod(lambda c, e, v, t: emit_list_shrink_to_fit(c, v, t)),
    "destroy": ContainerMethod(lambda c, e, v, t: emit_list_destroy(c, v, t)),
    "free": ContainerMethod(lambda c, e, v, t: emit_list_free(c, v, t)),
    "debug": ContainerMethod(lambda c, e, v, t: emit_list_debug(c, v, t)),
    "iter": ContainerMethod(emit_list_iter),
    "clone": ContainerMethod(lambda c, e, v, t: emit_list_clone(c, v, t)),
})


def emit_list_method(
    codegen: Any,
    expr: MethodCall | DotCall,
    receiver_value: ir.Value,
    receiver_type: StructType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM IR for List<T> method calls."""
    return emit_from_table(LIST_EMITTERS, "CE0083", codegen, expr, receiver_value, receiver_type, to_i1)
