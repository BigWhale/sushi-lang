"""SHIR to SLIR: the facts of a function declaration that the lowering reads (ir.md 6.4).

The symbol of the function, the kind of a body that is no free function, and the type of
the return slot. The initializer body of a constant or a `var` takes the symbol of its
global and the declared type (ir.md 8.2). They hold no state of the lowering, and
`lower.py` reads them once for each body.
"""
from __future__ import annotations

from typing import Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.ast import ConstDef, ExtendDef, FuncDef
from sushi_lang.semantics.channel import channel_result, has_channel
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.shir.body import ShirBody
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.unit_symbols import emitted_symbol, function_symbol


def symbol_of(decl: object, home: Optional[str], body: ShirBody) -> str:
    """The symbol of the function in its module (`function_symbol`). A body that is not a
    free function stops with the name of its kind."""
    if isinstance(decl, ConstDef):
        return emitted_symbol(decl.name, decl.home_unit or decl.scope_unit or home,
                              getattr(decl, "link_symbol", None))
    if not isinstance(decl, FuncDef) or len(body.params) > len(decl.params):
        raise StageStop(STAGE, kind_of(decl, body), body.block.loc)
    return function_symbol(decl.name, home)


def kind_of(decl: object, body: ShirBody) -> str:
    """The kind of a body that is not a free function. A perk method is a `FuncDef` whose
    body has a receiver before its declared parameters."""
    if isinstance(decl, FuncDef):
        return "perk method"
    if isinstance(decl, GenericFuncDef):
        return "generic instance"
    if isinstance(decl, ExtendDef):
        return "conversion" if decl.is_conversion else "method"
    er.raise_internal_error("CE0000", span=body.block.loc,
                            detail=f"a body of {type(decl).__name__} reached SLIR")


def return_type_of(decl: object, tables: SymbolTables, body: ShirBody) -> Type:
    """The type of the return slot: the `Result` of a channel, else the declared type."""
    ret = decl.ty if isinstance(decl, ConstDef) else getattr(decl, "ret", None)
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
