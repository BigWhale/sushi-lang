"""Shared function-synthesis wiring."""
from __future__ import annotations
from typing import Optional, List

from sushi_lang.semantics.ast import FuncDef, Program


def register_synthesized_function(
    func_table,
    funcdef: FuncDef,
    *,
    program: Optional[Program] = None,
    units: Optional[List] = None,
) -> bool:
    """Register a synthesized concrete function and queue it for backend emission."""
    from sushi_lang.semantics.passes.collect import FuncSig

    name = funcdef.name
    if name in func_table.by_name:
        return False

    sig = FuncSig(
        name=name,
        params=funcdef.params,
        ret_type=funcdef.ret,
        ret_span=funcdef.ret_span,
        is_public=funcdef.is_public,
        loc=None,
        name_span=funcdef.name_span,
        unit_name=None,
    )
    func_table.by_name[name] = sig
    func_table.order.append(name)

    if program is not None:
        program.functions.append(funcdef)
    elif units and len(units) > 0 and units[0].ast:
        units[0].ast.functions.append(funcdef)

    return True
