"""Shared function-synthesis wiring.

Both the generic monomorphizer and the lambda-lifting pass synthesize a concrete
top-level `FuncDef` and must wire it into the compiler identically: register a
`FuncSig` in the function table (so name resolution and the backend find it) and
append the `FuncDef` to the program (single-file) or the main unit (multi-file) so
the backend emits it.

This is that single wiring point. It is intentionally dependency-free — it takes the
`func_table` and program/units explicitly rather than reaching into the `Monomorphizer`
god-object (whose reason for existing is type-parameter substitution, irrelevant to a
lambda that has no type params) — so the two callers cannot drift apart.
"""
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
    """Register a synthesized concrete function and queue it for backend emission.

    Builds a `FuncSig` from `funcdef`, inserts it into `func_table` (`by_name` + `order`),
    and appends `funcdef` to `program.functions` (single-file) or `units[0].ast.functions`
    (multi-file). Exactly one of `program` / `units` should be provided.

    Returns True if newly registered, or False if a function of the same (mangled) name
    already exists (a no-op — the caller may skip re-processing it).
    """
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
