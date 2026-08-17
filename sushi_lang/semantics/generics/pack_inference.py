"""Pack-aware type-argument inference (shared between Pass 1.5 and Pass 2)."""
from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


def has_pack_value_param(generic_func) -> bool:
    """True if the generic function has a trailing pack VALUE-parameter."""
    params = getattr(generic_func, "params", None) or []
    return any(getattr(p, "is_pack", False) for p in params)


def _pack_value_param_index(generic_func) -> Optional[int]:
    """Index of the (single) pack value-parameter, or None if there is none."""
    params = getattr(generic_func, "params", None) or []
    for i, p in enumerate(params):
        if getattr(p, "is_pack", False):
            return i
    return None


def infer_flat_type_args(
    generic_func,
    arg_types: Sequence["Type"],
    *,
    infer_leading: Callable[
        [object, Sequence["Type"]], Optional[Tuple["Type", ...]]
    ],
) -> Optional[Tuple["Type", ...]]:
    """Infer the flat tuple of concrete type-args for a generic call."""
    pack_idx = _pack_value_param_index(generic_func)

    # No pack value-param: existing behavior, byte-for-byte unchanged. The
    # caller's leading-inference handles ALL params/args and ALL type-params.
    if pack_idx is None:
        return infer_leading(generic_func, list(arg_types))

    arg_types = list(arg_types)

    # A pack absorbs zero-or-more trailing args, so a call may legally pass
    # fewer args than there are parameters only down to the leading count.
    if len(arg_types) < pack_idx:
        return None

    leading_arg_types = arg_types[:pack_idx]
    trailing_arg_types = tuple(arg_types[pack_idx:])

    # Delegate leading (non-pack) inference to the caller's existing logic,
    # restricted to the fixed prefix so it never sees the pack value-param.
    leading_type_args = infer_leading(generic_func, leading_arg_types)
    if leading_type_args is None:
        return None

    # Flat key: leading inferred type-args followed by ALL trailing arg types
    # (these are the concrete types bound to the pack type-param, arity == len).
    return tuple(leading_type_args) + trailing_arg_types
