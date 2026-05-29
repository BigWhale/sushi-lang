"""Smoke tests for the semantic-analysis passes via the public SemanticAnalyzer.

These assert that representative programs produce the expected diagnostic codes,
giving localized regression signal that the coarse-grained test_err_*.sushi corpus
cannot. Intentionally small; deeper per-pass coverage is later work.

Codes asserted here were confirmed against the analyzer, not assumed.
"""
from __future__ import annotations

from sushi_lang.internals.report import Reporter


CLEAN = "fn main() i32:\n    return Result.Ok(0)\n"


def _error_codes(reporter: Reporter) -> set[str]:
    return {d.code for d in reporter.items if d.kind == "error"}


def test_clean_program_has_no_errors(analyze):
    assert not analyze(CLEAN).has_errors


def test_missing_let_type_annotation_reports_CE2007(analyze):
    src = "fn main() i32:\n    let x = 5\n    return Result.Ok(0)\n"
    assert "CE2007" in _error_codes(analyze(src))


def test_non_result_return_reports_CE2030(analyze):
    # foo() returns a bare value instead of Result.Ok()/Result.Err().
    src = "fn foo() i32:\n    return 5\nfn main() i32:\n    return Result.Ok(0)\n"
    assert "CE2030" in _error_codes(analyze(src))


def test_modify_through_peek_reports_CE2408(analyze):
    # Writing through a read-only &peek borrow is rejected by the borrow checker.
    src = (
        "fn bad(&peek i32 x) ~:\n"
        "    x := x + 1\n"
        "    return Result.Ok(~)\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2408" in _error_codes(analyze(src))
