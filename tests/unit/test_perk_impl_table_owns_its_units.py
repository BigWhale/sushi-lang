"""Which unit owns a perk implementation is a fact of the table, not of a collector.

Decision 11 of `docs/design/visibility.md` keeps the perk-implementation override: a
consumer's `extend X with P` wins over a library's, which
`tests/libs/test_lib_perk_impl_local_override.sushi` asserts with an observable result.
The record that answers "which unit declared this implementation" used to live in a
collector-private dict, so the only thing that could read it was the collector itself.
It sits beside `implementations` / `by_type` / `by_perk` now, so the collect pass files
it straight onto the table the whole program reads (#672).
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import ExtendWithDef
from sushi_lang.semantics.passes.collect import CollectorPass, PerkImplementationTable


def _impl(perk_name: str) -> ExtendWithDef:
    return ExtendWithDef(target_type=None, perk_name=perk_name, methods=[], loc=None)


def test_register_records_the_declaring_unit():
    table = PerkImplementationTable()
    assert table.register(_impl("Loud"), "Box", unit_name="main")
    assert table.owner("Box", "Loud") == "main"


def test_an_unregistered_pair_has_no_owner():
    table = PerkImplementationTable()
    assert table.owner("Box", "Loud") is None


def test_replace_takes_over_the_pair_and_returns_the_displaced_one():
    table = PerkImplementationTable()
    first = _impl("Loud")
    table.register(first, "Box", unit_name="lib/loudlib/loudlib")
    second = _impl("Loud")
    assert table.replace(second, "Box", unit_name="main") is first
    assert table.implementations[("Box", "Loud")] is second
    assert table.owner("Box", "Loud") == "main"


def test_the_collect_pass_files_the_owner_on_the_program_table():
    """End to end: the pass fills the table the analyzer hands on, owner included."""
    source = (
        "struct Box:\n"
        "    i32 v\n"
        "\n"
        "perk Loud:\n"
        "    fn shout() ~\n"
        "\n"
        "extend Box with Loud:\n"
        "    fn shout() ~:\n"
        "        return Result.Ok(~)\n"
    )
    program, _tree = parse_to_ast(source)
    tables = CollectorPass(Reporter()).run(program, unit_name="main")
    assert tables.perk_impls.owner("Box", "Loud") == "main"
