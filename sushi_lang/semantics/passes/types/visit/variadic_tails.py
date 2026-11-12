"""The variadic tail of a callee (docs/design/ir.md 7.4, S22).

A function whose last parameter is `...T` takes its trailing arguments as the values of
one array `T[]`. The callee row holds the `Tail`: the index of its first argument, the
resolved type `T[]` and the convention of the tail parameter. The one argument check
(`arguments.check_arguments`) checks the values of the tail.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.param_modes import param_mode
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.shir.typeck import Tail
from sushi_lang.semantics.typesys import DynamicArrayType

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import Param
    from sushi_lang.semantics.passes.types import TypeValidator


def variadic_tail(validator: TypeValidator, params: Sequence[Param]) -> Optional[Tail]:
    """The tail of a callee whose last parameter is `...T`, or None."""
    if not params or not params[-1].is_variadic:
        return None
    param = params[-1]
    ty = resolve_variable_type(validator, param.ty, param.type_span or param.name_span)
    if not isinstance(ty, DynamicArrayType):
        raise_internal_error("CE0000", span=param.name_span,
                             detail="a variadic parameter whose type is no T[]")
    return Tail(len(params) - 1, ty, param_mode(param).convention)
