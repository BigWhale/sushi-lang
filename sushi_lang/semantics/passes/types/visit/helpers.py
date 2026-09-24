"""What the three visitors share: a name's function type, a lambda's type,
and the fn-typed-field call.
"""
from __future__ import annotations
from typing import Optional

from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.semantics.ast import (
    Lambda,
    Block
)


def function_value_type_of(type_validator, name: str) -> Optional[Type]:
    """Build the FunctionType for a bare reference to a plain top-level function."""
    from sushi_lang.semantics.param_modes import declared_modes
    from sushi_lang.semantics.typesys import FunctionType, UnknownType
    sig = type_validator.func_sig(name)
    if sig is None:
        return None
    for p in sig.params:
        if getattr(p, "is_variadic", False) or getattr(p, "is_pack", False):
            return None
    param_types = tuple(p.ty for p in sig.params)
    if any(pt is None for pt in param_types):
        return None
    ok_type = sig.ret_type if sig.ret_type is not None else BuiltinType.BLANK
    err_type = sig.err_type if sig.err_type is not None else UnknownType("StdError")
    # The declared modes are part of the type, and it stays invariant in them
    # (docs/design/borrow-model.md S7). Without them a `nom` callee compared equal to a
    # borrow fn type, so binding one to the other was a double free (#368).
    return FunctionType(param_types=param_types, ok_type=ok_type, err_type=err_type,
                        param_modes=declared_modes(sig.params))


def infer_lambda_type(type_validator, lam: Lambda, *, stamp: bool = True):
    """Compute (and, by default, cache on the node) the FunctionType of a lambda literal."""
    from sushi_lang.semantics.param_modes import declared_modes
    from sushi_lang.semantics.typesys import FunctionType, UnknownType
    if stamp and getattr(lam, "resolved_type", None) is not None:
        return lam.resolved_type

    expected = getattr(lam, "expected_type", None)
    saved = dict(type_validator.variable_types)

    param_types = []
    for idx, p in enumerate(lam.params):
        pty = p.ty
        if pty is None and isinstance(expected, FunctionType) and idx < len(expected.param_types):
            pty = expected.param_types[idx]
            if stamp:
                p.ty = pty  # persist the inferred type for the lift pass / backend
        param_types.append(pty)
        if pty is not None:
            type_validator.variable_types[p.name] = pty

    if stamp:
        for cap in (lam.captures or []):
            if cap.ty is None:
                cap.ty = saved.get(cap.name)

    ok_type: Optional[Type]
    if lam.ret is not None:
        ok_type = lam.ret
    else:
        # An EXPRESSION body says what it builds, and the DECLARED type answers when it
        # cannot: a built-in static and a generic variant read their type from the
        # position and infer nothing of their own (#625). A block body has no expression
        # to ask at all.
        ok_type = (None if isinstance(lam.body, Block)
                   else type_validator.infer_expression_type(lam.body))
        if ok_type is None and isinstance(expected, FunctionType):
            ok_type = expected.ok_type

    err_type = lam.err_type
    if err_type is None:
        err_type = expected.err_type if isinstance(expected, FunctionType) else UnknownType("StdError")

    type_validator.variable_types.clear()
    type_validator.variable_types.update(saved)

    ft = FunctionType(
        param_types=tuple(param_types),
        ok_type=ok_type,
        err_type=err_type,
        captures=tuple(lam.captures or ()),
        param_modes=declared_modes(lam.params),
    )
    if stamp:
        lam.resolved_type = ft
    return ft


def resolve_fn_field_call(type_validator, node) -> Optional["Type"]:
    """Field-vs-method rule for `obj.handler(args)` (a DotCall)."""
    from sushi_lang.semantics.typesys import StructType, FunctionType, ReferenceType
    recv_ty = type_validator.infer_expression_type(node.receiver)
    if isinstance(recv_ty, ReferenceType):
        recv_ty = recv_ty.referenced_type
    if not isinstance(recv_ty, StructType):
        return None
    field_ty = recv_ty.get_field_type(node.method)
    if not isinstance(field_ty, FunctionType):
        return None
    if node.method == "hash":
        return None
    if type_validator.extension_table.get_method(recv_ty, node.method) is not None:
        return None
    if '<' in recv_ty.name:
        # A method wins over a same-named fn-typed field only if it APPLIES to this
        # instantiation: `extend Box@(i32) handler()` leaves a `Box@(string)`'s field alone
        # (#393).
        base_name = recv_ty.name.split('<')[0]
        if type_validator.generic_extension_table.find_applicable(
                base_name, node.method, recv_ty.name) is not None:
            return None
    return field_ty


def validate_fn_field_call_args(type_validator, node, fn_ty) -> None:
    """Validate `obj.handler(args)` against the field's FunctionType."""
    from sushi_lang.semantics.passes.types.calls.user_defined import (
        validate_fn_value_call_args,
    )
    validate_fn_value_call_args(type_validator, node.args, fn_ty, node.loc)

