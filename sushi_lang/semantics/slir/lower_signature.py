"""SHIR to SLIR: the facts of a function declaration that the lowering reads (ir.md 6.4).

The symbol of the function, the kind of a body that is no free function, no extension
method and no `drop` of a `Drop` implementation, and the type of the return slot. They
hold no state of the lowering, and `lower.py` reads them once for each body.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.ast import ExtendDef, FuncDef
from sushi_lang.semantics.channel import channel_result, has_channel
from sushi_lang.semantics.generics.name_mangling import extension_decl_symbol, method_symbol
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.shir.body import ShirBody
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.unit_symbols import function_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import ExtendWithDef
    from sushi_lang.semantics.passes.collect.perks import PerkImplementationTable


def symbol_of(decl: object, home: Optional[str], body: ShirBody,
              drop_impl: Optional[ExtendWithDef]) -> str:
    """The symbol of the function in its module: `function_symbol` for a free function,
    `extension_decl_symbol` for an extension method or a static, and the perk symbol of
    the target for the `drop` of the `Drop` implementation `drop_impl` (ir.md 8.2). Any
    other body stops with the name of its kind."""
    if isinstance(decl, ExtendDef) and not decl.is_conversion:
        return extension_decl_symbol(decl, home)
    if drop_impl is not None and isinstance(decl, FuncDef):
        return method_symbol(drop_impl.target_type, decl.name, None)
    if not isinstance(decl, FuncDef) or len(body.params) > len(decl.params):
        raise StageStop(STAGE, kind_of(decl, body), body.block.loc)
    return function_symbol(decl.name, home)


def drop_impl_of(decl: object, perk_impls: PerkImplementationTable,
                 drops: frozenset[str]) -> Optional[ExtendWithDef]:
    """The `Drop` implementation whose `drop` is `decl`, else None."""
    if not isinstance(decl, FuncDef) or decl.name != "drop":
        return None
    for type_name in drops:
        impl = perk_impls.get(type_name, "Drop")
        if impl is not None and any(method is decl for method in impl.methods):
            return impl
    return None


def kind_of(decl: object, body: ShirBody) -> str:
    """The kind of a body that is no free function, no extension method and no `drop` of a
    `Drop` implementation. A perk method is a `FuncDef` whose body has a receiver before
    its declared parameters."""
    if isinstance(decl, FuncDef):
        return "perk method"
    if isinstance(decl, GenericFuncDef):
        return "generic instance"
    if isinstance(decl, ExtendDef):
        return "conversion"
    er.raise_internal_error("CE0000", span=body.block.loc,
                            detail=f"a body of {type(decl).__name__} reached SLIR")


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
