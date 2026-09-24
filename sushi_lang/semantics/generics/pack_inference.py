"""Type-argument inference for a generic call: the one solver, shared by every pass."""
from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


def _pack_value_param_index(generic_func) -> Optional[int]:
    """Index of the (single) pack value-parameter, or None if there is none."""
    params = getattr(generic_func, "params", None) or []
    for i, p in enumerate(params):
        if p.is_pack:
            return i
    return None


def solve_leading_type_args(
    generic_func,
    arg_types: Sequence["Type | None"],
    structs: Any,
    enums: Any,
    *,
    ret_type: "Type | None" = None,
    partial: bool = False,
    param_types: "Sequence[Type | None] | None" = None,
    type_param_names: Optional[Sequence[str]] = None,
) -> Any:
    """The leading (non-pack) type arguments that `arg_types` bind. None when unsolved.

    Unify each non-pack parameter with its argument type -- and the return with
    `ret_type`, for a bare generic-fn reference solved against an expected function
    type -- then read each leading type parameter off the map and resolve it against
    the tables. The one home of this step: the instantiate, monomorphize and typecheck
    passes all call it, so a call one pass solves is a call every pass solves (#795).

    `partial=True` is the contract of a caller that has a second source or a report of
    its own (#809): a generic static's target (the stamp fills the rest) and the
    method-level type parameters of an extension (CE2063 names the rest). An argument
    whose type is None solves nothing, a count or unify miss is not a failure, and the
    answer is `(solved, unsolved)`: the map as unified, not resolved, and the names it
    does not hold, in declaration order. `param_types` replaces the declared parameter
    types when the caller substituted them first, and `type_param_names` the names read
    off the map.
    """
    from sushi_lang.semantics.generics.unify import unify_types
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    if param_types is None:
        param_types = [p.ty for p in generic_func.params
                       if not getattr(p, "is_pack", False)]
    if type_param_names is None:
        type_param_names = [tp.name if hasattr(tp, "name") else str(tp)
                            for tp in generic_func.type_params or ()
                            if not getattr(tp, "is_pack", False)]

    if not partial and len(arg_types) != len(param_types):
        return None

    type_param_map: dict[str, "Type"] = {}
    for arg_type, param_ty in zip(arg_types, param_types, strict=False):
        if partial:
            if param_ty is not None and arg_type is not None:
                unify_types(param_ty, arg_type, type_param_map)
        elif param_ty is None or not unify_types(param_ty, arg_type, type_param_map):
            return None
    if ret_type is not None and generic_func.ret is not None:
        if not unify_types(generic_func.ret, ret_type, type_param_map) and not partial:
            return None

    if partial:
        unsolved = tuple(n for n in type_param_names if n not in type_param_map)
        return type_param_map, unsolved

    solved = []
    for name in type_param_names:
        if name not in type_param_map:
            return None
        solved.append(resolve_unknown_type(type_param_map[name], structs, enums))
    return tuple(solved)


def infer_flat_type_args(
    generic_func,
    arg_types: Sequence["Type"],
    structs: Any,
    enums: Any,
) -> Optional[Tuple["Type", ...]]:
    """The flat tuple of type arguments for a generic call: the leading ones, then the pack.

    The arguments before the pack value-parameter solve the leading type parameters;
    each argument from it on is one element of the pack, taken as it is.
    """
    pack_idx = _pack_value_param_index(generic_func)
    arg_types = list(arg_types)
    if pack_idx is None:
        return solve_leading_type_args(generic_func, arg_types, structs, enums)
    if len(arg_types) < pack_idx:
        return None
    leading = solve_leading_type_args(generic_func, arg_types[:pack_idx], structs, enums)
    if leading is None:
        return None
    return leading + tuple(arg_types[pack_idx:])


def infer_call_arg_type(validator, arg_expr) -> "Type | None":
    """One generic-call argument's type, read before its call is solved. Silent.

    For the passes that solve a call ahead of the typecheck pass (instantiate,
    monomorphize): a lambda is typed from its written parameters and never stamped, and
    `.clone()` answers its receiver's type when the receiver is still a generic
    reference -- `.clone()` returns its receiver's type by definition, and the interned
    instance does not exist until the monomorphize pass (F8).
    """
    from sushi_lang.semantics.ast import Lambda
    if validator is None:
        return None
    if isinstance(arg_expr, Lambda):
        from sushi_lang.semantics.passes.types.visitor import infer_lambda_type
        return infer_lambda_type(validator, arg_expr, stamp=False)
    arg_type = validator.infer_expression_type(arg_expr)
    if arg_type is None and getattr(arg_expr, "method", None) == "clone":
        receiver = getattr(arg_expr, "receiver", None)
        if receiver is not None:
            arg_type = validator.infer_expression_type(receiver)
    return arg_type
