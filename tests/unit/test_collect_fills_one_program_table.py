"""The collect pass fills ONE set of tables, and those tables are the program's.

`_check_multi_file` builds one `CollectorPass` and runs it once per unit. The
collector's tables are instance fields and nothing resets them, so each run adds to
what the runs before it left. A merger that copied the result into a second set
therefore replayed the whole accumulation once per unit -- a cost triangular in the
unit count -- and it copied 13 of the 19 fields, with `externals` repaired by hand
afterwards.

The collect pass owns the tables and the analyzer takes them. These are the two rules
that keeps: the pass answers ONE table for every unit, and every field of
`SymbolTables` is either a table the pass owns or a field a later step fills.
"""
from __future__ import annotations

import dataclasses

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.collect import CollectorPass
from sushi_lang.semantics.tables import SymbolTables

# The fields the collect pass does NOT fill, and the step that does.
FILLED_LATER = {
    "namespaces": "the namespaces pass",
    "library_not_exported": "the libraries step",
    "pending_extension_instantiations": "the typecheck pass",
    "queued_extension_keys": "the typecheck pass",
    "intern_generic_ref": "the analyzer's late interner",
}









def test_every_program_table_is_the_collect_pass_own_or_filled_later():
    """Totality over `SymbolTables`: no field is left for a merge step to forget."""
    collector = CollectorPass(Reporter())
    tables = collector.tables

    unaccounted = []
    for field in dataclasses.fields(SymbolTables):
        if field.name in FILLED_LATER:
            continue
        owned = getattr(collector, field.name, None)
        if owned is None or getattr(tables, field.name) is not owned:
            unaccounted.append(field.name)

    assert not unaccounted, (
        "these `SymbolTables` fields are neither the collect pass's own table nor "
        f"named in FILLED_LATER: {unaccounted}")


