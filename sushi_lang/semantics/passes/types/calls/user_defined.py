"""User-defined and stdlib function call validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.results import result_ok_err
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.ast import Call
from sushi_lang.semantics.ffi_boundary import ERRNO_FUNCTION, unit_declares_external_block
from sushi_lang.semantics.param_modes import ParamMode
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Stdlib
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.stdlib_registry import StdlibFunction, stdlib_row
from sushi_lang.semantics.typesys import EnumType, FunctionType, Type
from ..arguments import (
    Parameters, check_arguments, marked_type, record_conventions, reject_misplaced_spread)
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


def validate_errno_call(validator: 'TypeValidator', call: Call) -> None:
    """`errno()`: no argument, and a unit that declares an `unsafe external` block."""
    if not check_arguments(validator, ERRNO_FUNCTION, (), call.args, call.callee.loc,
                           mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                           stop_on_arity=True):
        return
    if not unit_declares_external_block(validator.external_table,
                                        validator.current_unit_name):
        er.emit(validator.reporter, er.ERR.CE5014, call.callee.loc)


def check_stdlib_call(validator: 'TypeValidator', node: e.Call, ref: e.GlobalRef,
                      func: StdlibFunction) -> Optional[Type]:
    """A call of a function of a registry stdlib module: a `Stdlib` callee (ir.md 7.6).

    A member of a `<math>` family measures its arguments against the types of its family
    (`_check_math_family`), and any other row goes through the one argument check at the
    types of its row. Each argument borrows. The call yields the return type of its row,
    also after an error of an argument, and the reference of the target answers the
    function type of the row. A family whose first argument has no row yields nothing. A
    function of a module that the pass does not type yet stops the walk (`Call(stdlib)`).
    """
    module, name = func.module_path, func.name
    if module in UNTYPED_STDLIB_MODULES:
        visit.stop_at(validator, node)
    validator.body_answers().record_callee(node.shir_id,
                                           ResolvedCall(Stdlib(ref.decl, module)))
    if func.params is None:
        arg_types = _check_math_family(validator, node, name, ref.loc)
    else:
        arg_types = list(func.params)
        declared = Parameters(node.shir_id, (ParamMode.BORROW,) * len(arg_types),
                              [_row_parameter_name(ty) for ty in arg_types])
        check_arguments(validator, name, arg_types, node.args, ref.loc,
                        mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                        declared=declared)
    row = stdlib_row(module, name, arg_types)
    if row is None:
        return None
    yielded = stdlib_call_type(validator, row)
    visit.answer(validator, ref, _function_type_of_row(row, yielded))
    return yielded


#: The registry stdlib modules whose calls the pass does not type yet (P6.D7.2). A call
#: of a function of one of them stops the walk of its body.
UNTYPED_STDLIB_MODULES = frozenset({"sys/process", "net/socket"})


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


def _check_math_family(validator: 'TypeValidator', node: e.Call, name: str,
                       loc: Span) -> list[Optional[Type]]:
    """A `<math>` family call (`abs`, `min`, `max`, `divmod`), measured against its table,
    and the type of each argument.

    The two arguments of a two-argument family are checked as a pair: a lone literal takes
    the type of the other argument. Then the count: a wrong count is CE2009 and skips the
    argument types. Each argument must have a type of the family, and the two arguments of
    a two-argument family must have one type (CE2006).
    """
    from sushi_lang.sushi_stdlib.src.math import FAMILY_TYPE_NAMES, MATH_FAMILIES
    from ..visit.operators import check_operand_pair
    arity, family_types = MATH_FAMILIES[name]
    args = node.args
    if arity == 2 and len(args) == 2 and not any(arg.bloom for arg in args):
        pair = check_operand_pair(validator, args[0].value, args[1].value)
        types = [marked_type(arg, ty) for arg, ty in zip(args, pair, strict=True)]
    else:
        types = [None if reject_misplaced_spread(validator, arg)
                 else marked_type(arg, visit.check_expr(validator, arg.value))
                 for arg in args]
    if len(args) != arity:
        er.emit(validator.reporter, er.ERR.CE2009, loc, name=name, expected=arity,
                got=len(args))
        return types
    declared = Parameters(node.shir_id, (ParamMode.BORROW,) * arity,
                          [f"#{index}" for index in range(1, arity + 1)])
    record_conventions(validator, declared, args)
    for index, (arg, ty) in enumerate(zip(args, types, strict=True), start=1):
        if ty is not None and ty not in family_types:
            er.emit(validator.reporter, er.ERR.CE2006, arg.value.loc, index=index,
                    expected=FAMILY_TYPE_NAMES[name], got=display_type(ty))
    if arity == 2 and None not in types and types[0] != types[1]:
        er.emit(validator.reporter, er.ERR.CE2006, args[1].value.loc, index=2,
                expected=display_type(types[0]), got=display_type(types[1]))
    return types


def stdlib_call_type(validator: 'TypeValidator', row: 'Signature') -> Type:
    """What a call of a stdlib row yields: its declared return type, with the `Result`, the
    `Maybe` or the tuple that it names interned. The typecheck pass and the inference of
    the early passes both read it."""
    declared = row.return_type()
    interned = intern_declared_wrapper(validator, declared)
    return interned if interned is not None else declared
