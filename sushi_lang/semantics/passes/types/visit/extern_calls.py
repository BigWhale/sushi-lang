"""The call of a C function of an `unsafe external` block (docs/design/ir.md 7.6).

A call names the C function behind the alias of its block (`libc.snprintf`). The callee row
is `Extern`, with the link name of the declaration. Each declared argument goes through the
one argument check at its declared type, and has the convention of its declared mode: a
`poke u8[]` is `Inout`. A `nom` marker is not compared with the mode, because C takes
nothing from Sushi. Each value of the variadic tail is `Guaranteed`, and a value that C
cannot hold as itself is CE5005. The call yields the declared return type, with no channel.
The reference of the target answers the function type of the declared parameters.

The pass types an extern whose declared parameters are numbers, `string` and byte buffers,
and whose return is a number or `~`. Every other extern, and a `ptr` in the tail, stops the
walk (`Call(extern)`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ffi_boundary import is_byte_buffer, is_c_abi_scalar
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import mode_of_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import (
    Parameters, check_arguments, marked_type, record_reads, reject_misplaced_spread)
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Extern
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.type_predicates import is_numeric_type
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.externals import ExternalSig
    from sushi_lang.semantics.passes.types import TypeValidator


def check_extern_call(validator: TypeValidator, node: e.Call, ref: e.GlobalRef,
                      sig: ExternalSig) -> Optional[Type]:
    if not _is_typed(sig):
        visit.stop_at(validator, node)
    written = validator.decl_name(ref.decl)
    answers = validator.body_answers()
    answers.record_callee(node.shir_id, ResolvedCall(Extern(ref.decl, sig.link_name),
                                                     unit=sig.unit_name))
    params = list(sig.param_types)
    modes = tuple(mode_of_type(ty) for ty in params)
    ret = sig.ret_type or BuiltinType.BLANK
    visit.answer(validator, ref, FunctionType(param_types=tuple(params), ok_type=ret,
                                              err_type=None, param_modes=modes))
    declared = Parameters(node.shir_id, modes, [f"#{index + 1}" for index in range(len(params))])
    check_arguments(validator, written, params, node.args, ref.loc,
                    mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009, declared=declared,
                    minimum_arity=sig.is_variadic, compares_nom=False)
    if sig.is_variadic:
        record_reads(validator, node.shir_id, [
            index for index, arg in enumerate(node.args[len(params):], start=len(params))
            if _check_tail_value(validator, node, written, arg)])
    return ret


def _check_tail_value(validator: TypeValidator, node: e.Call, written: str,
                      arg: e.Arg) -> bool:
    """One value of the variadic tail: a number, a `bool` or a `string` (CE5005 for a value
    that C cannot hold as itself). A `ptr` stops the walk. True when the value fits."""
    if reject_misplaced_spread(validator, arg):
        return False
    ty = marked_type(arg, visit.check_expr(validator, arg.value))
    if ty is None:
        return False
    if not is_c_abi_scalar(ty):
        er.emit(validator.reporter, er.ERR.CE5005, arg.value.loc, type=display_type(ty),
                name=written)
        return False
    if not (is_numeric_type(ty) or ty in (BuiltinType.BOOL, BuiltinType.STRING)):
        visit.stop_at(validator, node)
    return True


def _is_typed(sig: ExternalSig) -> bool:
    """An extern whose parameters are numbers, strings and byte buffers, and whose return
    is a number or `~`."""
    ret = sig.ret_type
    return ((ret is None or ret == BuiltinType.BLANK or is_numeric_type(ret))
            and all(is_numeric_type(ty) or ty == BuiltinType.STRING or is_byte_buffer(ty)
                    for ty in sig.param_types))
