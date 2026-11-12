"""The call of a C function of an `unsafe external` block (docs/design/ir.md 7.6).

A call names the C function behind the alias of its block (`libc.snprintf`). The callee row
is `Extern`, with the link name of the declaration. Each declared argument goes through the
one argument check at its declared type, and has the convention of its declared mode: a
`poke u8[]` is `Inout`. A `nom` marker is not compared with the mode, because C takes
nothing from Sushi. Each value of the variadic tail is `Guaranteed`, and a value that C
cannot hold as itself is CE5005. The call yields the declared return type, with no channel.
The reference of the target answers the function type of the declared parameters.

`errno()` is an intrinsic with no argument that answers an `i32`. A unit that declares no
`unsafe external` block cannot call it (CE5014).

A method of a `ptr` is a `Builtin` callee of the `foreign_ptr` family: each argument at the
type of its row, and the answer of its row (`load_ptr` answers the interned `Maybe@(ptr)`).
Any other method of a `ptr` is CE5011. An extern variable behind the alias of its block
(`libc.optind`) answers its declared type, and its global row is its link name. A write of
one is CE5016.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ffi_boundary import (
    ERRNO_FUNCTION, is_c_abi_scalar, unit_declares_external_block)
from sushi_lang.semantics.foreign_memory import FOREIGN_PTR_METHODS
from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.method_effects import FOREIGN_PTR_FAMILY
from sushi_lang.semantics.param_modes import mode_of_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import (
    Parameters, check_arguments, marked_type, record_reads, reject_misplaced_spread)
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Builtin, Extern, Intrinsic
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType, FunctionType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.externals import ExternalSig, ExternalVarSig
    from sushi_lang.semantics.passes.types import TypeValidator


@dataclass(frozen=True)
class ErrnoFunction:
    """What a bare call of `errno` names when no function of the scope takes the name."""


ERRNO = ErrnoFunction()


def check_extern_call(validator: TypeValidator, node: e.Call, ref: e.GlobalRef,
                      sig: ExternalSig) -> Optional[Type]:
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
            if _check_tail_value(validator, written, arg)])
    return ret


def _check_tail_value(validator: TypeValidator, written: str, arg: e.Arg) -> bool:
    """One value of the variadic tail: a number, a `bool`, a `string` or a `ptr` (CE5005
    for a value that C cannot hold as itself). True when the value fits."""
    if reject_misplaced_spread(validator, arg):
        return False
    ty = marked_type(arg, visit.check_expr(validator, arg.value))
    if ty is None:
        return False
    if not is_c_abi_scalar(ty):
        er.emit(validator.reporter, er.ERR.CE5005, arg.value.loc, type=display_type(ty),
                name=written)
        return False
    return True


def check_errno_call(validator: TypeValidator, node: e.Call, ref: e.GlobalRef) -> Type:
    """`errno()`: the `Intrinsic` callee, no argument (CE2009), and a unit that declares an
    `unsafe external` block (CE5014)."""
    validator.body_answers().record_callee(node.shir_id, ResolvedCall(Intrinsic(ERRNO_FUNCTION)))
    visit.answer(validator, ref, FunctionType(param_types=(), ok_type=BuiltinType.I32,
                                              err_type=None, param_modes=()))
    if not check_arguments(validator, ERRNO_FUNCTION, (), node.args, ref.loc,
                           mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                           stop_on_arity=True):
        return BuiltinType.I32
    if not unit_declares_external_block(validator.external_table,
                                        validator.current_unit_name):
        er.emit(validator.reporter, er.ERR.CE5014, ref.loc)
    return BuiltinType.I32


def check_foreign_ptr_call(validator: TypeValidator, node: e.Call, target: e.Dotted,
                           receiver: Type) -> Optional[Type]:
    """`p.m(args)` on a `ptr`: the `Builtin` callee of the family, each argument at the type
    of its row (CE2009 for a count, CE2006 for a type), and the answer of its row. A method
    that the family does not hold is CE5011."""
    row = FOREIGN_PTR_METHODS.get(target.name)
    if row is None:
        er.emit(validator.reporter, er.ERR.CE5011, node.loc, method=target.name)
        return None
    family = METHOD_TYPE_REGISTRY.family(FOREIGN_PTR_FAMILY)
    validator.body_answers().record_callee(
        node.shir_id, ResolvedCall(Builtin(target.recv, family.callee(target.name))))
    modes = family.modes.of(target.name, len(node.args)) or ()
    declared = Parameters(node.shir_id, modes, [f"#{i}" for i in range(1, len(modes) + 1)])
    check_arguments(validator, f"{display_type(receiver)}.{target.name}", row.params,
                    node.args, node.loc, mismatch_code=er.ERR.CE2006,
                    arity_code=er.ERR.CE2009, declared=declared)
    if isinstance(row.returns, str):
        return ensure_maybe_type_in_table(validator.enum_table, ForeignPtrType(),
                                          struct_table=validator.struct_table.by_name)
    return row.returns


def check_extern_variable(validator: TypeValidator, node: e.GlobalRef,
                          var: ExternalVarSig) -> Optional[Type]:
    """A read of a C global: its declared type, and the global row of its link name."""
    if var.ty is None:
        return None
    validator.body_answers().record_global(node.shir_id, var.link_name)
    return var.ty


def reject_extern_variable_write(validator: TypeValidator, target: e.ShirExpr) -> bool:
    """CE5016 for a write of a C global, at the target. True when it reported."""
    if not isinstance(target, e.GlobalRef):
        return False
    written = validator.decl_name(target.decl)
    alias, dot, name = written.rpartition(".")
    binding = validator.namespaces.lookup(alias, name) if dot else None
    if binding is None or binding.kind != "extern variable":
        return False
    er.emit(validator.reporter, er.ERR.CE5016, target.loc, name=written)
    return True
