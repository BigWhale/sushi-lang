"""What the arms of the typecheck pass share: a name's function type, and the
fn-typed-field call.
"""
from __future__ import annotations
from typing import Optional

from sushi_lang.semantics.typesys import Type, BuiltinType


def function_type_of_sig(sig) -> Optional[Type]:
    """The FunctionType of this signature, bare or behind an alias. The tail of a variadic
    function is its parameter `T[]`: such a function is the target of a call, and no
    value (CE2093)."""
    from sushi_lang.semantics.param_modes import declared_modes
    from sushi_lang.semantics.typesys import FunctionType
    if sig is None:
        return None
    param_types = tuple(p.ty for p in sig.params)
    if any(pt is None for pt in param_types):
        return None
    ok_type = sig.ret_type if sig.ret_type is not None else BuiltinType.BLANK
    err_type = sig.err_type
    # The declared modes are part of the type, and it stays invariant in them
    # (docs/design/borrow-model.md S7). Without them a `nom` callee compared equal to a
    # borrow fn type, so binding one to the other was a double free (#368).
    return FunctionType(param_types=param_types, ok_type=ok_type, err_type=err_type,
                        param_modes=declared_modes(sig.params))


def fn_field_call_type(type_validator, recv_ty, method: str) -> Optional["Type"]:
    """The type of the field of a function type that a call of `method` goes through, or
    None. An extension method that applies wins over the field, and `hash` is never a field."""
    from sushi_lang.semantics.typesys import StructType, FunctionType, ReferenceType
    if isinstance(recv_ty, ReferenceType):
        recv_ty = recv_ty.referenced_type
    if not isinstance(recv_ty, StructType):
        return None
    field_ty = recv_ty.get_field_type(method)
    if not isinstance(field_ty, FunctionType):
        return None
    if method == "hash":
        return None
    if type_validator.extension_table.get_method(recv_ty, method) is not None:
        return None
    if '<' in recv_ty.name:
        # A method wins over a same-named fn-typed field only if it APPLIES to this
        # instantiation: `extend Box@(i32) handler()` leaves a `Box@(string)`'s field alone
        # (#393).
        base_name = recv_ty.name.split('<')[0]
        if type_validator.generic_extension_table.find_applicable(
                base_name, method, recv_ty.name) is not None:
            return None
    return field_ty
