"""Pack-aware type-argument inference (shared between Pass 1.5 and Pass 2).

A generic function may end in a *type pack* (`fn f<...Ts>(...Ts args)`): a
trailing pack VALUE-parameter (`Param.is_pack == True`, whose `ty` is the bare
pack type-param reference) backed by a trailing pack TYPE-parameter
(`BoundedTypeParam.is_pack == True`, always last, at most one -- enforced by
collect/T4).

Both instantiation collection (Pass 1.5) and call validation (Pass 2) must agree
on the FLAT tuple of concrete type-args produced for a given call, otherwise the
call will not resolve to the monomorphized symbol. This module factors out the
pack-tail handling so both sites share it. The site-specific *leading* (non-pack)
inference is delegated back to the caller via a callback so existing,
byte-for-byte unchanged inference logic is reused for the fixed prefix.

Flat-tuple contract (matches Phase 0's monomorphizer / `mangle_function_name`):
the result is ``(<leading inferred type-args in type_param order>, *<trailing
arg types>)``. The pack type-param contributes ALL trailing arg types (zero or
more); pack arity == number of trailing args. Phase 0 derives the arity itself
from the `is_pack` type-params plus the flat tuple length.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


def has_pack_value_param(generic_func) -> bool:
    """True if the generic function has a trailing pack VALUE-parameter."""
    params = getattr(generic_func, "params", None) or []
    return any(getattr(p, "is_pack", False) for p in params)


def _pack_value_param_index(generic_func) -> Optional[int]:
    """Index of the (single) pack value-parameter, or None if there is none.

    T4 validation guarantees at most one such parameter and that it is last,
    but we locate it by flag rather than assuming position.
    """
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
    """Infer the flat tuple of concrete type-args for a generic call.

    This is the SHARED entry point. It does pack-tail handling and delegates the
    fixed-prefix (leading, non-pack) inference to ``infer_leading`` so each call
    site reuses its own existing unification logic unchanged.

    Args:
        generic_func: Generic function definition. Its ``type_params`` may end in
            a pack type-param (``BoundedTypeParam.is_pack``) and its ``params``
            may end in a pack value-param (``Param.is_pack``).
        arg_types: Concrete (already-inferred) positional argument types, in call
            order. Callers must resolve each to a concrete ``Type`` beforehand
            (e.g. ``UnknownType`` -> concrete) exactly as they do today.
        infer_leading: Callback ``(generic_func, leading_arg_types) -> flat
            leading type-args | None``. It receives the generic function and ONLY
            the leading argument types (those before the pack value-param) and
            must return the leading type-args in ``type_param`` order, or None on
            failure. For a non-pack function it is given ALL arg types and ALL
            type-params, i.e. it reproduces the existing behavior exactly.

    Returns:
        Flat tuple ``(<leading...>, *<trailing arg types>)`` or None if leading
        inference fails. For a function WITHOUT a pack value-param, returns
        exactly ``infer_leading(generic_func, arg_types)`` (byte-for-byte
        unchanged path).
    """
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
