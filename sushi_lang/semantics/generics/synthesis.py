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
    home_unit: Optional[str] = None,
    defer_to: Optional[list] = None,
) -> bool:
    """Register a synthesized concrete function and queue it for backend emission.

    `home_unit` names the unit that declared the generic this instance came from, and
    the instance goes home to it (#495, D3): its `FuncSig` carries the unit, its body
    is appended to that unit's AST, and the backend gives it that unit's symbol
    prefix. Two units' instances of one mangled base name are then two symbols, and
    each unit's call binds to its own. A `home_unit` that names no unit in the build
    lands in the entry unit with no unit identity.

    A lifted lambda passes no `home_unit`: its name already carries the per-unit
    lifter's counter (#402), and it keeps its bare symbol.

    `defer_to` is a list when the body must not go into its AST yet: an instance cut
    while the per-unit passes run is put on the list as (AST, body), and the analyzer
    puts it into that AST and checks it after the per-unit loop (#1155).
    """
    from sushi_lang.semantics.passes.collect import FuncSig

    if home_unit is not None and units:
        if not any(u.name == home_unit and u.ast for u in units):
            home_unit = None

    name = funcdef.name
    declared = (func_table.by_unit.get(home_unit, {}) if home_unit is not None
                else func_table.by_name)
    if name in declared:
        return False

    sig = FuncSig(
        name=name,
        params=funcdef.params,
        ret_type=funcdef.ret,
        ret_span=funcdef.ret_span,
        is_public=funcdef.is_public,
        loc=None,
        name_span=funcdef.name_span,
        unit_name=home_unit,
        # The channel is part of the signature, not decoration on the declaration. A
        # `FuncSig` that drops it types every call site as a bare call (#538).
        err_type=funcdef.err_type,
        borrowed_return=getattr(funcdef, "borrowed_return", None),
    )
    func_table.declare(name, sig)

    # Marked so the unit fingerprint can see it: a synthesized body is NOT covered by
    # the unit's source hash, and which instances a unit carries depends on what the
    # rest of the program asked for.
    funcdef.is_synthesized = True
    # The backend reads this for the symbol: an instance takes its home unit's
    # prefix, a lifted lambda stays bare (`backend/functions/declarations.py`).
    funcdef.home_unit = home_unit

    container = program if program is not None else _home_ast(units, home_unit)
    if container is None:
        return True
    if defer_to is not None:
        defer_to.append((container, funcdef))
    else:
        container.functions.append(funcdef)
    return True


def _home_ast(units: Optional[List], home_unit: Optional[str]) -> Optional[Program]:
    """The AST of the unit a synthesized body goes into, or None when it is dropped."""
    if units:
        target = None
        if home_unit is not None:
            target = next((u for u in units if u.name == home_unit and u.ast), None)
        if target is None:
            # The ENTRY unit, which is what "the first unit" meant while the compilation
            # order put a dependent before its dependency. A position is not a home.
            target = next((u for u in units if getattr(u, "is_entry", False) and u.ast),
                          None)
        if target is None and units[0].ast:
            # NOT the analyzer's `_entry_unit` rule, which scans on for the first unit
            # that has an AST (#736). An instance has a home unit and reaches this line
            # only when that home is gone, so the FIRST unit is the last guess and no
            # further one is made: the body is dropped rather than parked on a unit that
            # never asked for it.
            target = units[0]
        if target is not None and target.ast:
            return target.ast
    return None
