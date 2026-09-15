"""The analyzer holds ONE set of symbol tables, and every reader goes through it.

`SemanticAnalyzer` used to carry each table twice: an `Optional[...] = None` attribute
per table, rebound to the member of `SymbolTables` after the collect loop. The shadows
answered `None` before `check()` and the real table after, so every reader that had to
survive both wrote a guard that could never fire once the analysis had run -- and
`externals`, which had no declaration at all, answered an `AttributeError` where its
eleven peers answered `None`.

One set, always present. `self.tables` is a `SymbolTables` from construction, the
back end reads its fields by name, and no guard stands between a reader and a table.
"""
from __future__ import annotations

import dataclasses

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.tables import SymbolTables

SOURCE = """
unsafe external "C" as sysio because "the back end reads the extern table":
    fn text_length(string s) i64 = "strlen"

const i32 LIMIT = 4

struct Crate:
    i32 weight

enum Cursor:
    Start

extend Crate heavier() i32:
    return self.weight + 1

fn main() i32:
    let Crate c = Crate(LIMIT)
    println(c.heavier())
    return Result.Ok(0)
"""


def test_the_analyzer_declares_no_shadow_of_a_program_table():
    """One home for each table: an attribute of the same name is a second one."""
    analyzer = SemanticAnalyzer(Reporter())

    shadows = [field.name for field in dataclasses.fields(SymbolTables)
               if hasattr(analyzer, field.name)]

    assert not shadows, (
        "these program tables are also attributes of the analyzer, so a reader may "
        f"take either: {shadows}")


def test_every_program_table_answers_before_check():
    """No guard is needed: the tables exist from construction, `externals` included."""
    analyzer = SemanticAnalyzer(Reporter())

    assert isinstance(analyzer.tables, SymbolTables)
    for field in dataclasses.fields(SymbolTables):
        assert getattr(analyzer.tables, field.name) is not None or \
            field.name == "intern_generic_ref"


def test_the_back_end_reads_the_analyzer_own_tables(analyze_program):
    """The pipeline's hand-off is typed and takes the tables, not an attribute guess."""
    from sushi_lang.compiler.pipeline import codegen_for

    analysis = analyze_program(SOURCE)
    tables = analysis.analyzer.tables
    cg = codegen_for(analysis.analyzer)

    assert cg.struct_table is tables.structs
    assert cg.enum_table is tables.enums
    assert cg.func_table is tables.funcs
    assert cg.perk_impl_table is tables.perk_impls
    assert cg.const_table is tables.constants
    assert cg.external_table is tables.externals
    assert cg.unit_namespaces == dict(tables.namespaces)
