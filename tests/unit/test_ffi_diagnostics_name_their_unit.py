"""The `externs` pass reports against the unit that holds the fault, and reads once.

Two faults in one place (#599). The pass loop was given the ENTRY unit's reporter, so a
CE5009, a CE5003 or a CW5001 that a SECOND unit caused printed the entry file's name,
line and source text -- a caret under a line with nothing wrong on it, while the passes
under it named the right file. And `_unit_reporter` read the unit's file from disk on
every call, once per per-unit pass loop, on top of the parser's own read.
"""
from __future__ import annotations

from pathlib import Path

from sushi_lang.compiler.loader import load_unit_recursively
from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.units import UnitManager


PTR_GATE = "fn hold(ptr p) i32:\n    return Result.Ok(0)\n"
BAD_ABI = ('unsafe external "C" as bad because "test":\n'
           '    fn takes_maybe(Maybe@(i32) m) i32 = "takes_maybe"\n')
NO_REASON = ('unsafe external "C" as quiet:\n'
             '    fn quiet_abs(i32 x) i32 = "abs"\n')

MAIN = 'use "helper"\n\nfn main() i32:\n    return Result.Ok(0)\n'


def _analyze_two_units(tmp_path: Path, helper_src: str) -> Reporter:
    """Compile `main` plus its `helper` the way the pipeline does, and report."""
    (tmp_path / "helper.sushi").write_text(helper_src, encoding="utf-8")
    (tmp_path / "main.sushi").write_text(MAIN, encoding="utf-8")

    main_ast, _tree = parse_to_ast(MAIN)

    reporter = Reporter(source=MAIN, filename=str(tmp_path / "main.sushi"))
    unit_manager = UnitManager(root_path=tmp_path, reporter=reporter)
    main_unit = unit_manager.load_unit("main", main_ast)
    assert main_unit is not None
    main_unit.is_entry = True
    assert load_unit_recursively(unit_manager, "helper", {"main"}, reporter)
    unit_manager.get_compilation_order()

    analyzer = SemanticAnalyzer(reporter, filename="main",
                                unit_manager=unit_manager)
    try:
        analyzer.check(main_ast)
    except ValueError:
        pass
    return reporter


def _located(reporter: Reporter, code: str) -> list[str]:
    return [f"{Path(d.filename).name}:{d.span.line}" for d in reporter.items
            if d.code == code and d.span is not None]


def test_the_ptr_unit_gate_names_the_unit_that_spelled_ptr(tmp_path):
    reporter = _analyze_two_units(tmp_path, PTR_GATE)
    assert _located(reporter, "CE5009") == ["helper.sushi:1"]


def test_a_non_abi_extern_signature_names_its_own_unit(tmp_path):
    reporter = _analyze_two_units(tmp_path, BAD_ABI)
    assert _located(reporter, "CE5003") == ["helper.sushi:2"]


def test_the_unacknowledged_external_warning_names_its_own_unit(tmp_path):
    reporter = _analyze_two_units(tmp_path, NO_REASON)
    assert _located(reporter, "CW5001") == ["helper.sushi:1"]


def test_a_unit_source_is_read_once(tmp_path, monkeypatch):
    """Four per-unit pass loops ask for a Reporter, and none of them re-reads the file."""
    reads: list[str] = []
    original = Path.read_text

    def counted(self, *args, **kwargs):
        reads.append(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    _analyze_two_units(tmp_path, PTR_GATE)

    for unit_file in ("main.sushi", "helper.sushi"):
        assert reads.count(unit_file) <= 1, (
            f"{unit_file} was read {reads.count(unit_file)} times; a Unit holds its own "
            "text, so a per-unit pass loop costs no disk read at all."
        )
