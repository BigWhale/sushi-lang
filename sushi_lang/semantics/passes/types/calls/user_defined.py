"""User-defined and stdlib function call validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.results import result_ok_err
from sushi_lang.semantics.param_modes import ParamMode
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Stdlib
from sushi_lang.semantics.shir.typeck import ResolvedCall, Tail
from sushi_lang.semantics.stdlib_registry import StdlibFunction, stdlib_signature
from sushi_lang.semantics.typesys import DynamicArrayType, EnumType, FunctionType, Type
from ..arguments import (
    Parameters, check_arguments)
from ..utils import intern_declared_wrapper

if TYPE_CHECKING:
    from sushi_lang.sushi_stdlib.src.signatures import Signature
    from .. import TypeValidator


def own_concrete_function(func_table, name: str, unit: Optional[str]):
    """The concrete function the unit itself declares under a bare name, or None.

    Section 8's ladder crosses the two function tables here: the asking unit's OWN
    concrete declaration answers a bare name before a generic an import brought, in
    every position -- a call and a function value alike (#495, #963, #1003). The
    typecheck pass and the instantiate pass both ask this, so the two cannot drift.
    """
    if func_table is None or unit is None:
        return None
    return func_table.declared(name, unit)


def check_stdlib_call(validator: 'TypeValidator', node: e.Call, ref: e.GlobalRef,
                      func: StdlibFunction) -> Optional[Type]:
    """A call of a function of a registry stdlib module: a `Stdlib` callee (ir.md 7.6).

    The arguments go through the one argument check at the types of its row. Each argument
    borrows. The call yields the return type of its row, also after an error of an
    argument, and the reference of the target answers the function type of the row. A
    function whose last parameter is variadic (`run`) has the tail of S22, and a bloom into
    it borrows its source (ir.md 7.4).
    """
    module, name = func.module_path, func.name
    tail = _stdlib_tail(func)
    validator.body_answers().record_callee(node.shir_id,
                                           ResolvedCall(Stdlib(ref.decl, module), tail=tail))
    fixed = list(func.params) if tail is None else list(func.params)[:tail.position]
    declared = Parameters(node.shir_id, (ParamMode.BORROW,) * len(fixed),
                          [_row_parameter_name(ty) for ty in fixed])
    check_arguments(validator, name, fixed, node.args, ref.loc,
                    mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                    declared=declared, tail=None if tail is None else tail.ty,
                    bloom_borrows=True)
    row = stdlib_signature(module, name)
    if row is None:
        return None
    yielded = stdlib_call_type(validator, row)
    visit.answer(validator, ref, _function_type_of_row(row, yielded))
    return yielded


def _stdlib_tail(func: StdlibFunction) -> Optional[Tail]:
    """The tail of a stdlib function whose last parameter is `...T`: its row holds `T[]`,
    and the tail borrows as each parameter of a row does."""
    if not func.is_variadic or not func.params:
        return None
    ty = func.params[-1]
    if not isinstance(ty, DynamicArrayType):
        er.raise_internal_error("CE0000", detail="a variadic stdlib row whose tail is no T[]")
    return Tail(len(func.params) - 1, ty, ParamMode.BORROW.convention)


def _row_parameter_name(ty: Type) -> str:
    """A row names no parameter, so a diagnostic names it by the member of its type
    (`F64`), as the bare call and the call behind an alias did (#1125)."""
    return str(getattr(ty, "name", ty))


def _function_type_of_row(row: 'Signature', yielded: Type) -> FunctionType:
    """The function type of a stdlib row: the `Result` it answers is its channel."""
    params = tuple(param.ty for param in row.params)
    modes = (ParamMode.BORROW,) * len(params)
    if row.ok is None or not isinstance(yielded, EnumType):
        return FunctionType(params, yielded, None, param_modes=modes)
    ok, err = result_ok_err(yielded)
    return FunctionType(params, ok, err, param_modes=modes)


def stdlib_call_type(validator: 'TypeValidator', row: 'Signature') -> Type:
    """What a call of a stdlib row yields: its declared return type, with the `Result`, the
    `Maybe` or the tuple that it names interned. The typecheck pass and the inference of
    the early passes both read it."""
    declared = row.return_type()
    interned = intern_declared_wrapper(validator, declared)
    return interned if interned is not None else declared
