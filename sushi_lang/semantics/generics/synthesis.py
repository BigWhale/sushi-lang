"""Shared function-synthesis wiring."""
from __future__ import annotations
from typing import Optional, List

from sushi_lang.internals.report import Origin
from sushi_lang.semantics.ast import FuncDef, Program


def register_synthesized_function(
    func_table,
    funcdef: FuncDef,
    *,
    program: Optional[Program] = None,
    units: Optional[List] = None,
    home_unit: Optional[str] = None,
    from_library_template: bool = False,
    origin: Optional[Origin] = None,
) -> bool:
    """Register a synthesized concrete function and queue it for backend emission.

    `home_unit` names the unit that declared the generic this instance came from, and
    the instance goes home to it (#495, D3): its `FuncSig` carries the unit, its body
    is appended to that unit's AST, and the backend gives it that unit's symbol
    prefix. Two units' instances of one mangled base name are then two symbols, and
    each unit's call binds to its own. A `home_unit` that names no unit in the build
    -- a binary library's template, whose units exist only at the producer -- lands in
    the entry unit with no unit identity, exactly as before.

    A lifted lambda passes no `home_unit`: its name already carries the per-unit
    lifter's counter (#402), and it keeps its bare symbol.
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
        # `FuncSig` that drops it types every call site `Result@(T, StdError)`, and a
        # caller that declares the same channel then answers CE2511 (#538).
        err_type=funcdef.err_type,
    )
    func_table.declare(name, sig)

    # Marked so the unit fingerprint can see it: a synthesized body is NOT covered by
    # the unit's source hash, and which instances a unit carries depends on what the
    # rest of the program asked for.
    funcdef.is_synthesized = True
    # The backend reads this for the symbol: an instance takes its home unit's
    # prefix, a lifted lambda stays bare (`backend/functions/declarations.py`).
    funcdef.home_unit = home_unit

    # Whose code this body is. An instance of a `.slib` template is the library's, wherever
    # it lands, and it keeps calling the private helpers the export closure shipped for it
    # (#468). An instance of the consumer's own generic is the consumer's, and reaches no
    # further than the consumer's own code.
    if from_library_template:
        funcdef.is_library_template = True
        # The rendering half of the same answer: the body's spans came from the
        # manifest slice, so a diagnostic raised in it is rendered against the slice
        # and named for the library (#471).
        funcdef.library_origin = origin

    if program is not None:
        program.functions.append(funcdef)
    elif units:
        target = None
        if home_unit is not None:
            target = next((u for u in units if u.name == home_unit and u.ast), None)
        if target is None:
            # The ENTRY unit, which is what "the first unit" meant while the compilation
            # order put a dependent before its dependency. A position is not a home.
            target = next((u for u in units if getattr(u, "is_entry", False) and u.ast),
                          None)
        if target is None and units[0].ast:
            target = units[0]
        if target is not None and target.ast:
            target.ast.functions.append(funcdef)

    return True
