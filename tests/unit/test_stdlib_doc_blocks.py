"""R24: a bundled Sushi-source stdlib module is a library unit to the `docs` pass.

`_inject_source_stdlib_units` used to build a `Unit` with no provenance, and the pass
skips a unit only when it has one. A doc-block mistake in a bundled module was
therefore reported in EVERY program that imported the module, against source the user
never wrote. Measured before the fix: a `CW7001` appended to `collections/iter.sushi`
warned in an ordinary program that did `use <collections/iter>`.

The provenance silences that for a user, so it silences it for us as well. This module
is the repo-side gate that takes its place: every bundled module's blocks are checked
here, by hand, where the author can see the answer.

`docs/design/documentation.md` section 10 is the authority.

The second gate below is phase 5's, and it is a BUDGET rather than an assertion of zero
(R37): documenting the bundled modules is an editorial pass that comes after the
implementation. The number may fall and may never rise, so an undocumented symbol cannot
be added to the stdlib unnoticed, and paying the debt is incremental.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.docs import check_docs, check_missing_docs
from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES


@pytest.mark.parametrize("module", sorted(SOURCE_STDLIB_MODULES))
def test_a_bundled_module_carries_no_doc_diagnostic(module: str):
    path: Path = SOURCE_STDLIB_MODULES[module]
    program, _tree = parse_to_ast(path.read_text(encoding="utf-8"), dump_parse=False)
    reporter = Reporter(filename=str(path))
    check_docs(reporter, program)
    assert reporter.items == [], (
        f"<{module}> reports {[item.code for item in reporter.items]}. A bundled "
        "module's doc diagnostics reach nobody but this gate, so fix the block."
    )


def test_an_injected_bundled_module_is_skipped_in_a_user_build():
    """The provenance is what makes the `docs` pass skip the unit."""
    from sushi_lang.compiler.pipeline import _inject_source_stdlib_units
    from sushi_lang.semantics.units import Unit, UnitManager

    program, _tree = parse_to_ast(
        "use <collections/iter>\n\nfn main() i32:\n    return Result.Ok(0)\n",
        dump_parse=False)

    reporter = Reporter(filename="main.sushi")
    manager = UnitManager(root_path=Path.cwd(), reporter=reporter)
    manager.units["main"] = Unit(name="main", file_path=Path("main.sushi"),
                                ast=program, dependencies=[], public_symbols={})

    assert _inject_source_stdlib_units(manager, reporter) is True
    injected = manager.units["collections/iter"]
    assert injected.provenance is not None
    assert "collections/iter" in injected.provenance


# Every CW7002/CW7006 the four bundled modules report today. SHRINK-ONLY: document a
# symbol and lower it. Raising it means a new bundled symbol arrived with no block.
MISSING_DOCS_BUDGET = 114


def test_the_bundled_modules_stay_within_the_missing_docs_budget():
    total = 0
    per_module = {}
    for module, path in sorted(SOURCE_STDLIB_MODULES.items()):
        program, _tree = parse_to_ast(path.read_text(encoding="utf-8"), dump_parse=False)
        reporter = Reporter(filename=str(path))
        check_missing_docs(reporter, program)
        per_module[module] = len(reporter.items)
        total += len(reporter.items)

    assert total <= MISSING_DOCS_BUDGET, (
        f"the bundled modules report {total} missing-docs warnings, over the budget of "
        f"{MISSING_DOCS_BUDGET}: {per_module}. A new bundled symbol needs a doc block."
    )
    assert total == MISSING_DOCS_BUDGET, (
        f"the bundled modules report {total}, under the budget of "
        f"{MISSING_DOCS_BUDGET}. Lower MISSING_DOCS_BUDGET to {total}."
    )
