"""Shared fixtures for the compiler unit-test layer."""
from __future__ import annotations

from typing import NamedTuple

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.units import Unit, UnitManager


def _ensure_newline(src: str) -> str:
    """.sushi sources should end with a trailing newline (avoids a warning)."""
    return src if src.endswith("\n") else src + "\n"


@pytest.fixture
def make_unit(tmp_path):
    """Factory that parses `src` into a Unit backed by a real file on disk."""
    def _make(src: str, name: str = "main") -> Unit:
        file_path = tmp_path / f"{name}.sushi"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        text = _ensure_newline(src)
        file_path.write_text(text, encoding="utf-8")
        program, _tree = parse_to_ast(text)
        return Unit(name=name, file_path=file_path, ast=program,
                    dependencies=[], public_symbols={})
    return _make


def _analyze_source(tmp_path, src: str, name: str) -> "Analysis":
    """Run the production semantic flow over `src` and return everything it produced."""
    from sushi_lang.semantics.generics.active_generics import reset_active_generics
    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry

    text = _ensure_newline(src)
    file_path = tmp_path / f"{name}.sushi"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")
    program, _tree = parse_to_ast(text)

    reporter = Reporter(source=text, filename=name)

    # Match compile_multi_file's pre-analysis setup.
    reset_active_generics()
    get_stdlib_registry()

    unit_manager = UnitManager(root_path=tmp_path, reporter=reporter)
    unit = unit_manager.load_unit(name, program)
    if unit is None:
        return Analysis(reporter=reporter, program=program, analyzer=None)
    unit_manager.build_global_symbol_table()
    unit_manager.get_compilation_order()

    analyzer = SemanticAnalyzer(reporter, filename=name, unit_manager=unit_manager)
    try:
        analyzer.check(program)
    except ValueError:
        pass
    return Analysis(reporter=reporter, program=program, analyzer=analyzer)


class Analysis(NamedTuple):
    """What one semantic-analysis run produced."""
    reporter: Reporter
    program: object
    analyzer: object


@pytest.fixture
def analyze(tmp_path):
    """Factory that semantically analyzes `src`, returning the Reporter."""
    def _analyze(src: str, name: str = "main") -> Reporter:
        return _analyze_source(tmp_path, src, name).reporter

    return _analyze


@pytest.fixture
def analyze_program(tmp_path):
    """Factory that semantically analyzes `src`, returning the whole `Analysis`."""
    def _analyze(src: str, name: str = "main") -> Analysis:
        return _analyze_source(tmp_path, src, name)

    return _analyze
