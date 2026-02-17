"""Source file loading and unit resolution."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.parse_errors import handle_parse_exception
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import Program
from sushi_lang.semantics.units import UnitManager


def get_effective_cwd() -> Path:
    """Get the effective current working directory for file resolution.

    Checks for the SUSHI_CWD environment variable set by the sushic script.
    If present, uses that directory. Otherwise falls back to os.getcwd().

    Returns:
        Path where .sushi files should be resolved from.
    """
    sushi_cwd = os.environ.get('SUSHI_CWD')
    if sushi_cwd:
        return Path(sushi_cwd)
    return Path.cwd()


def check_duplicate_uses(ast: Program, reporter: Reporter) -> None:
    """Check for duplicate use statements in a single file and emit warnings."""
    from sushi_lang.internals import errors as er

    seen_units = {}  # unit_path -> first occurrence location

    for use_stmt in ast.uses:
        if use_stmt.path in seen_units:
            prev_loc = seen_units[use_stmt.path]
            er.emit(reporter, er.ERR.CW3001, use_stmt.loc,
                   unit=use_stmt.path,
                   prev_loc=f"{prev_loc.line}:{prev_loc.col}")
        else:
            seen_units[use_stmt.path] = use_stmt.loc


def load_unit_recursively(unit_manager: UnitManager, unit_name: str,
                          loaded: set[str], reporter: Reporter) -> bool:
    """Recursively load a unit and all its dependencies.

    Args:
        unit_manager: The unit manager instance.
        unit_name: Name of the unit to load.
        loaded: Set of already loaded unit names to prevent infinite recursion.
        reporter: Reporter for error reporting.

    Returns:
        True if successful, False if there were errors.
    """
    if unit_name in loaded:
        return True

    loaded.add(unit_name)

    # Resolve unit file path and read source
    unit_path = unit_manager.resolve_unit_path(unit_name)
    if not unit_path.exists():
        if unit_manager.reporter:
            from sushi_lang.internals import errors as er
            er.emit(unit_manager.reporter, er.ERR.CE3002, None, name=unit_name, path=unit_path)
        return False

    try:
        unit_src = unit_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"error: cannot read {unit_path}: {e}", file=sys.stderr)
        return False

    # Create unit-specific reporter
    unit_reporter = Reporter(source=unit_src, filename=str(unit_path))

    try:
        unit_ast, _ = parse_to_ast(unit_src, dump_parse=False)

        # Check for missing trailing newline
        if unit_src and not unit_src.endswith('\n'):
            from sushi_lang.internals import errors as er
            er.emit(unit_reporter, er.ERR.CW0001, None)

        check_duplicate_uses(unit_ast, unit_reporter)

        unit = unit_manager.load_unit(unit_name, unit_ast)
        if unit is None:
            reporter.items.extend(unit_reporter.items)
            return False

        # Recursively load dependencies
        for dep_name in unit.dependencies:
            if not load_unit_recursively(unit_manager, dep_name, loaded, reporter):
                reporter.items.extend(unit_reporter.items)
                return False

    except Exception as exc:
        if handle_parse_exception(exc, unit_reporter, source_path=unit_path):
            reporter.items.extend(unit_reporter.items)
            unit_reporter.print()
            return False
        raise

    # Merge unit reporter into main reporter
    reporter.items.extend(unit_reporter.items)
    return True
