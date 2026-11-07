"""SHIR to SLIR: the arms of a call and of an interpolation (docs/design/ir.md 8.3, 8.9).

A call is ONE `Call` of its callee: the receiver of a method first, then each argument with
the convention of its typecheck row. `clone`, and `expect`, `realise` and `err` of a
`Result` or a `Maybe`, are no call (`lower_copies.py`). A get-out whose answer owns (`answers_view`) answers a
view of its receiver, and a call with a `peek` return whose answer owns answers a view of its
source arguments (`lower_views.py`). Each arm is a free function that takes the `Lowering`
of `lower.py` first; the tables of `lower.py` name them. This module reads `lower.py` for
its types only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.name_mangling import method_symbol
from sushi_lang.semantics.method_effects import answers_view, receiver_convention
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import (
    Builtin, BuiltinStatic, Callee, Extern, Free, Intrinsic, Method, Static, Stdlib)
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import lower_copies as copies
from sushi_lang.semantics.slir import lower_views as views
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.lower_generics import free_callee_name
from sushi_lang.semantics.slir.lower_values import CallValue, Value
from sushi_lang.semantics.stdlib_registry import stdlib_symbol
from sushi_lang.semantics.type_predicates import generic_base_of
from sushi_lang.semantics.typesys import BuiltinType, Type, deref_type
from sushi_lang.semantics.unit_symbols import function_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def call(low: Lowering, node: e.Call) -> Value:
    resolved = low.typeck.callee_of(node.shir_id)
    if resolved is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a call with no callee reached SLIR")
    if isinstance(resolved.callee, Builtin) and copies.lowers_inline(resolved.callee.method):
        return copies.lower(low, node, resolved.callee)
    target = callee(low, node, resolved)
    value = CallValue(target, receiver(low, resolved.callee, node)
                      + tuple(argument(low, node, index, arg)
                              for index, arg in enumerate(node.args)))
    if (isinstance(resolved.callee, Builtin) and answers_view(resolved.callee.method)
            and low.owns(low.type_of(node))):
        return views.view_of(value, value.args[0][0])
    sources = low.typeck.sources_of(node.shir_id)
    if sources is not None and low.owns(low.type_of(node)):
        return views.view_of_sources(value, sources, isinstance(resolved.callee, Method))
    return value


def receiver(low: Lowering, callee: Callee,
             node: e.Call) -> tuple[tuple[p.Operand, Convention], ...]:
    """The receiver of a method call, the first operand: a built-in method with the
    answer of the receiver partition, an extension method with the convention of its
    `self`. Any other callee has none."""
    if isinstance(callee, Builtin):
        return ((low.operand(callee.recv, in_place=True),
                 _receiver_convention(callee, node.loc)),)
    if isinstance(callee, Method):
        return ((_by_convention(low, callee.recv, callee.receiver, node.args),
                 callee.receiver),)
    return ()


def callee(low: Lowering, node: e.Call, resolved: ResolvedCall) -> st.SlirCallee:
    """A free function by its symbol and its source name; an extension method and a
    static by their symbol and their written name `<type>.<method>`; a function of a
    registry stdlib module by the symbol of its generated function, its name and its
    module (`stdlib_symbol`, at the type of its arguments); a C function by its link name
    and its name, with no module; an intrinsic with
    the display type of each argument (L7); a static of a primitive type name or of a
    built-in container as the intrinsic of its written name (`f64.from_bits`, `List.new`),
    with the type of each argument; a built-in method by
    its family and its name, with no types: the receiver is the first operand of the
    call (`receiver_convention`)."""
    callee = resolved.callee
    if isinstance(callee, Free):
        if resolved.unit is None:
            raise StageStop(STAGE, "library function", node.loc)
        name = free_callee_name(low.decls, node, callee)
        return st.Direct(function_symbol(name, resolved.unit), name)
    if isinstance(callee, (Method, Static)):
        if resolved.unit is None:
            raise StageStop(STAGE, "library function", node.loc)
        owner = (callee.ty if isinstance(callee, Static)
                 else deref_type(low.type_of(callee.recv)))
        name = low.decls.name_of(callee.decl)
        return st.Direct(method_symbol(owner, name.rpartition(".")[2], resolved.unit),
                         name)
    if isinstance(callee, Stdlib):
        name = low.decls.name_of(callee.decl).rpartition(".")[2]
        types = [low.type_of(arg.value) for arg in node.args]
        return st.Direct(stdlib_symbol(callee.module, name, types), name, callee.module)
    if isinstance(callee, Extern):
        return st.Direct(callee.symbol, low.decls.name_of(callee.decl).rpartition(".")[2])
    if isinstance(callee, Intrinsic):
        return st.Builtin(callee, tuple(display_type(low, node.shir_id, index, node.loc)
                                        for index in range(len(node.args))))
    if isinstance(callee, BuiltinStatic):
        type_name = generic_base_of(callee.ty) or str(callee.ty)
        return st.Builtin(Intrinsic(f"{type_name}.{callee.method.name}"),
                          tuple(low.type_of(arg.value) for arg in node.args))
    if isinstance(callee, Builtin):
        return st.Builtin(callee.method, ())
    raise StageStop(STAGE, type(callee).__name__, node.loc)


def argument(low: Lowering, node: e.Call, index: int,
             arg: e.Arg) -> tuple[p.Operand, Convention]:
    """An argument with the convention of its typecheck row (L8)."""
    if arg.bloom:
        raise StageStop(STAGE, "bloom", arg.value.loc)
    convention = low.typeck.convention_of(node.shir_id, index)
    if convention is None:
        er.raise_internal_error("CE0000", span=arg.value.loc,
                                detail="an argument with no convention reached SLIR")
    return _by_convention(low, arg.value, convention, node.args[index + 1:]), convention


def _by_convention(low: Lowering, value: e.ShirExpr, convention: Convention,
                   later: list[e.Arg]) -> p.Operand:
    """The operand of an argument: by address where it is, else by value, read before
    each later argument that can call (`settled`)."""
    if convention.by_address:
        return low.operand(value, in_place=True)
    return agg.settled(low, value, low.operand(value), [arg.value for arg in later])


def interp(low: Lowering, node: e.Interp) -> Value:
    """ONE call of the intrinsic `interp`: a `Guaranteed` operand for each part, with
    its display type; an empty text part gives none (L17)."""
    args: list[tuple[p.Operand, Convention]] = []
    types: list[Type] = []
    for index, part in enumerate(node.parts):
        if isinstance(part, str):
            if not part:
                continue
            args.append((p.Const(k.text(part)), Convention.GUARANTEED))
            types.append(BuiltinType.STRING)
        else:
            later = [each for each in node.parts[index + 1:] if not isinstance(each, str)]
            args.append((agg.settled(low, part, low.operand(part), later),
                         Convention.GUARANTEED))
            types.append(display_type(low, node.shir_id, index, part.loc))
    return CallValue(st.Builtin(Intrinsic("interp"), tuple(types)), tuple(args))


def display_type(low: Lowering, node: ShirId, part: int, span: Span) -> Type:
    ty = low.typeck.display_type_of(node, part)
    if ty is None:
        er.raise_internal_error("CE0000", span=span,
                                detail="a value with no display type reached SLIR")
    return ty


def _receiver_convention(callee: Builtin, span: Span) -> Convention:
    """The convention of the receiver of a built-in method: the answer of the receiver
    partition (`receiver_convention`, docs/design/ir.md 8.3). A family with no answer stops
    in `typecheck` or `borrow` first, so its call here is an internal error."""
    convention = receiver_convention(callee.method)
    if convention is None:
        er.raise_internal_error(
            "CE0000", span=span,
            detail=f"a call of a method of the family '{callee.method.family}' reached "
                   f"SLIR, and no receiver convention is written for that family")
    return convention
