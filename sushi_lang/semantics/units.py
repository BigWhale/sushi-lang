"""Unit management system for multi-file compilation."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional
from enum import Enum

from sushi_lang.semantics.ast import Program, FuncDef, ConstDef
from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er
from sushi_lang.semantics.error_reporter import PassErrorReporter


class SymbolType(Enum):
    """Types of symbols that can be exported from units."""
    FUNCTION = "function"
    CONSTANT = "constant"


@dataclass
class Symbol:
    """Represents a symbol (function or constant) exported from a unit."""
    name: str
    symbol_type: SymbolType
    unit_name: str              # Which unit this symbol comes from
    definition: FuncDef | ConstDef  # Reference to the actual AST node


@dataclass
class Unit:
    """Represents a single .sushi compilation unit (file)."""
    name: str                       # Unit name like "math/integer"
    file_path: Path                 # Absolute path to the .sushi file
    ast: Optional[Program]          # Parsed AST (None until loaded)
    dependencies: List[str]         # Other unit names this depends on (from use statements)
    public_symbols: Dict[str, Symbol]  # Symbols exported by this unit
    # Set on every unit whose code the CONSUMER did not write -- a source library's
    # unit, and a bundled Sushi-source stdlib module. Every diagnostic raised against
    # the unit carries it as a note, so a consumer is never shown a bare error about
    # code they did not write, and the `docs` pass skips the unit entirely.
    provenance: Optional[str] = None
    # True only for a unit that came out of a `.slib`. A bundled stdlib module carries a
    # provenance as well, so provenance alone cannot answer "is this a library": the two
    # are different questions, and `generics/synthesis.py` needs this one.
    from_library: bool = False

    def __post_init__(self):
        """Initialize computed fields after dataclass creation."""
        if self.ast is None:
            self.dependencies = []
            self.public_symbols = {}
        else:
            self._extract_dependencies()
            self._extract_public_symbols()

    def _extract_dependencies(self) -> None:
        """Extract dependency unit names from use statements in the AST."""
        if self.ast is None:
            self.dependencies = []
            return

        # Only include source file dependencies (not stdlib or library imports)
        # Stdlib imports are handled by stdlib_linker
        # Library imports are handled by library_linker
        self.dependencies = [
            use_stmt.path for use_stmt in self.ast.uses
            if not use_stmt.is_stdlib and not use_stmt.is_library
        ]

    def _extract_public_symbols(self) -> None:
        """Extract public symbols (functions and constants) from the AST.

        A constant used to enter unconditionally, because every constant was public.
        Now that one carries a marker, an unmarked constant is this unit's own and
        leaves nothing behind here: it is not an export, it does not invalidate a
        consumer's cache, and a name clash with another unit's private is an ordinary
        duplicate (CE0105) rather than an ambiguous export (CE3003).
        """
        if self.ast is None:
            self.public_symbols = {}
            return

        symbols = {}

        for func in self.ast.functions:
            if func.is_public:
                symbol = Symbol(
                    name=func.name,
                    symbol_type=SymbolType.FUNCTION,
                    unit_name=self.name,
                    definition=func
                )
                symbols[func.name] = symbol

        for const in self.ast.constants:
            if const.is_public:
                symbol = Symbol(
                    name=const.name,
                    symbol_type=SymbolType.CONSTANT,
                    unit_name=self.name,
                    definition=const
                )
                symbols[const.name] = symbol

        self.public_symbols = symbols

    def load_ast(self, ast: Program) -> None:
        """Load the parsed AST and update computed fields."""
        self.ast = ast
        self._extract_dependencies()
        self._extract_public_symbols()


class UnitManager:
    """Manages compilation units, dependency resolution, and compilation ordering."""

    def __init__(self, root_path: Path = None, reporter: Reporter = None):
        """Initialize the unit manager."""
        self.root_path = root_path or Path.cwd()
        self.units: Dict[str, Unit] = {}
        self.global_symbols: Dict[str, Symbol] = {}
        self.reporter = reporter
        self.err = PassErrorReporter(reporter) if reporter else None

    def resolve_unit_path(self, unit_name: str) -> Path:
        """Resolve a unit name to its file path."""
        relative_path = Path(unit_name + ".sushi")
        return self.root_path / relative_path

    def load_unit(self, unit_name: str, ast: Program) -> Optional[Unit]:
        """Load a unit with its parsed AST."""
        file_path = self.resolve_unit_path(unit_name)

        if not file_path.exists():
            if self.reporter:
                self.err.emit(er.ERR.CE3002, None, name=unit_name, path=file_path)
            return None

        unit = Unit(
            name=unit_name,
            file_path=file_path,
            ast=ast,
            dependencies=[],
            public_symbols={}
        )

        unit.load_ast(ast)

        self.units[unit_name] = unit
        return unit

    def build_dependency_graph(self) -> Dict[str, List[str]]:
        """Build a dependency graph from all loaded units."""
        return {unit.name: unit.dependencies for unit in self.units.values()}

    def topological_sort(self) -> Optional[List[str]]:
        """Perform topological sorting to determine compilation order."""
        dependency_graph = self.build_dependency_graph()

        in_degree = {unit: 0 for unit in dependency_graph}
        for _unit, deps in dependency_graph.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        queue = [unit for unit, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            current = queue.pop(0)
            result.append(current)

            for dep in dependency_graph[current]:
                if dep in in_degree:
                    in_degree[dep] -= 1
                    if in_degree[dep] == 0:
                        queue.append(dep)

        if len(result) != len(dependency_graph):
            cycle = self._find_cycle(dependency_graph)
            if self.reporter:
                cycle_str = " -> ".join(cycle + [cycle[0]])
                self.err.emit(er.ERR.CE3001, None, cycle=cycle_str)
            return None

        return result

    def _find_cycle(self, graph: Dict[str, List[str]]) -> List[str]:
        """Find and return a cycle in the dependency graph using DFS."""
        visited = set()
        rec_stack = set()

        def dfs(node: str, path: List[str]) -> Optional[List[str]]:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    result = dfs(neighbor, path.copy())
                    if result:
                        return result
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    return path[cycle_start:]

            rec_stack.remove(node)
            return None

        for node in graph:
            if node not in visited:
                result = dfs(node, [])
                if result:
                    return result

        return []  # No cycle found

    def build_global_symbol_table(self) -> bool:
        """Build the global symbol table from all loaded units."""
        symbol_units: Dict[str, List[str]] = {}
        success = True

        for unit in self.units.values():
            for symbol_name, symbol in unit.public_symbols.items():
                if symbol_name not in symbol_units:
                    symbol_units[symbol_name] = []
                symbol_units[symbol_name].append(unit.name)

                if symbol_name not in self.global_symbols:
                    self.global_symbols[symbol_name] = symbol

        for symbol_name, units in symbol_units.items():
            if len(units) > 1:
                if self.reporter:
                    self._report_ambiguous_export(symbol_name, units)
                success = False

        return success

    def _report_ambiguous_export(self, symbol_name: str, units: List[str]) -> None:
        """CE3003, with a note at every declaration that claims the name.

        It used to render tier 1 -- no file, no line, no caret -- for a condition that
        has one declaration per unit to point at. Each unit's `public_symbols` carries
        the AST node, so the span is right there.
        """
        diagnostic = self.err.emit_with(
            er.ERR.CE3003, None,
            symbol=symbol_name, units=", ".join(units))
        for unit_name in units:
            unit = self.units.get(unit_name)
            if unit is None:
                continue
            symbol = unit.public_symbols.get(symbol_name)
            span = getattr(getattr(symbol, "definition", None), "name_span", None)
            if span is not None:
                diagnostic = diagnostic.note("exported here", span, str(unit.file_path))
        diagnostic.emit()

    def get_compilation_order(self) -> Optional[List[Unit]]:
        """Get units in compilation order, with dependencies compiled first."""
        sorted_names = self.topological_sort()
        if sorted_names is None:
            return None
        return [self.units[name] for name in sorted_names]

    def find_symbol(self, symbol_name: str) -> Optional[Symbol]:
        """Find a symbol in the global symbol table."""
        return self.global_symbols.get(symbol_name)


__all__ = [
    "Unit", "UnitManager", "Symbol", "SymbolType",
]
