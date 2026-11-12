"""SHIR to SLIR: the facts of a function declaration that the lowering reads (ir.md 6.4).

The symbol of the function and the type of the return slot. They hold no state of the
lowering, and `lower.py` reads them once for each body.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import ExtendDef, FuncDef
from sushi_lang.semantics.channel import channel_result, has_channel
from sushi_lang.semantics.generics.name_mangling import extension_decl_symbol, method_symbol
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.shir.body import ShirBody
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.unit_symbols import function_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import ExtendWithDef
    from sushi_lang.semantics.passes.collect.perks import PerkImplementationTable


def symbol_of(decl: object, home: Optional[str], body: ShirBody,
              perk_impls: PerkImplementationTable) -> str:
    """The symbol of the function in its module: `extension_decl_symbol` for an extension
    method or a static, the perk symbol of the target for a method of a perk implementation
    (ir.md 8.2), and `function_symbol` for a free function. A body with a receiver that no
    perk implementation holds is an internal error. The copy of a generic function
    is a free function: its name is the instance name, and `home` is the unit that declares
    the template. A conversion is an extension method `as` with no unit, and its target is
    its one method type argument."""
    if isinstance(decl, ExtendDef):
        return extension_decl_symbol(decl, home)
    if isinstance(decl, FuncDef):
        impl = perk_impl_of(decl, perk_impls)
        if impl is not None:
            return method_symbol(impl.target_type, decl.name, None)
        if _has_receiver(body):
            er.raise_internal_error("CE0000", span=body.block.loc,
                                    detail=f"the method '{decl.name}' of no perk "
                                           f"implementation reached SLIR")
    elif not isinstance(decl, GenericFuncDef) or decl.type_params:
        er.raise_internal_error("CE0000", span=body.block.loc,
                                detail=f"a body of {type(decl).__name__} reached SLIR")
    return function_symbol(decl.name, home)


def perk_impl_of(decl: FuncDef, perk_impls: PerkImplementationTable) -> Optional[ExtendWithDef]:
    """The perk implementation that holds the method `decl`, else None."""
    for impl in perk_impls.implementations.values():
        if any(method is decl for method in impl.methods):
            return impl
    return None


def _has_receiver(body: ShirBody) -> bool:
    """Whether the first parameter of the body is the receiver `self` of a method."""
    names = {local.id: local.name for local in body.locals}
    return bool(body.params) and names.get(body.params[0].local) == "self"


def return_type_of(decl: object, tables: SymbolTables, body: ShirBody) -> Type:
    """The type of the return slot: the `Result` of a channel, else the declared type."""
    ret = getattr(decl, "ret", None)
    if ret is None:
        er.raise_internal_error("CE0000", span=body.block.loc,
                                detail="a function with no return type reached SLIR")
    if has_channel(decl):
        result = channel_result(tables.structs, tables.enums, ret, getattr(decl, "err_type", None))
        if result is None:
            er.raise_internal_error("CE0000", span=body.block.loc,
                                    detail="a function with a channel has no Result type")
        return result
    return resolve_unknown_type(ret, tables.structs.by_name, tables.enums.by_name)
