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

    `home_unit` names the unit that declared the generic this instance came from. It is
    honoured only for a SOURCE-LIBRARY unit, where the body is the library's code and
    has to be type-checked as such: a library generic calling a library-private helper
    is then an intra-unit call, which is what lets a source library work without the
    binary path's export closure.

    Deliberately not applied to every unit. The same reasoning would hold for an
    ordinary multi-unit program and for the bundled source stdlib, but moving those
    instances breaks lookup of a monomorphized `<collections/iter>` combinator, and
    chasing that down is its own change. Everything outside a source library keeps
    landing in the first unit, as before.

    The test for "a source library" is `Unit.from_library`, and not the provenance a
    bundled stdlib module now carries too: reading the provenance here moved every
    `<collections/iter>` instance and hit exactly the breakage above.
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

    # Marked so the unit fingerprint can see it: a synthesized body is NOT covered by
    # the unit's source hash, and which instances a unit carries depends on what the
    # rest of the program asked for.
    funcdef.is_synthesized = True

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
            target = next((u for u in units
                           if u.name == home_unit and u.ast
                           and getattr(u, "from_library", False)), None)
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
