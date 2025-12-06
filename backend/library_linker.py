"""Custom library linking utilities.

This module handles loading and linking precompiled Sushi libraries (.bc files)
along with their metadata manifests (.sushilib files).
"""
from __future__ import annotations
import json
import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING

import llvmlite.binding as llvm

from internals.errors import ERR

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from backend.symbol_table import SymbolSource


class LibraryError(Exception):
    """Base exception for library-related errors."""

    def __init__(self, code: str, **kwargs):
        self.code = code
        self.kwargs = kwargs
        msg = ERR[code]
        self.message = msg.text.format(**kwargs)
        super().__init__(f"{code}: {self.message}")


class LibraryLinker:
    """Manages custom library loading and linking."""

    def __init__(self, codegen: 'LLVMCodegen' = None):
        """Initialize library linker.

        Args:
            codegen: The main LLVMCodegen instance (optional for manifest-only operations).
        """
        self.codegen = codegen
        self.search_paths = self._get_search_paths()
        self.loaded_libraries: dict[str, dict] = {}  # lib_name -> manifest dict

    def _get_search_paths(self) -> list[Path]:
        """Get library search paths from SUSHI_LIB_PATH environment variable.

        Returns:
            List of directories to search for libraries.

        Default:
            [current_directory] if SUSHI_LIB_PATH not set.
        """
        lib_path = os.environ.get('SUSHI_LIB_PATH')
        if not lib_path:
            return [Path.cwd()]

        # Split by : on Unix, ; on Windows
        separator = ';' if platform.system() == 'Windows' else ':'

        paths = []
        for path_str in lib_path.split(separator):
            path_str = path_str.strip()
            if path_str:
                paths.append(Path(path_str).expanduser())

        # Always include current directory as fallback
        if Path.cwd() not in paths:
            paths.append(Path.cwd())

        return paths

    def resolve_library(self, lib_path: str) -> tuple[Path, Path]:
        """Resolve library path to .bc and .sushilib files.

        Args:
            lib_path: Library path like "lib/mylib" or "lib/acme/utils".

        Returns:
            Tuple of (bitcode_path, manifest_path).

        Raises:
            LibraryError: CE3502 if library not found in search paths.
        """
        # Remove "lib/" prefix if present
        if lib_path.startswith("lib/"):
            lib_path = lib_path[4:]

        # Search each path in order
        for search_dir in self.search_paths:
            bc_path = search_dir / f"{lib_path}.bc"
            manifest_path = search_dir / f"{lib_path}.sushilib"

            if bc_path.exists() and manifest_path.exists():
                return (bc_path, manifest_path)

            # Also try without subdirectory (flat structure)
            lib_name = Path(lib_path).name
            bc_path_flat = search_dir / f"{lib_name}.bc"
            manifest_path_flat = search_dir / f"{lib_name}.sushilib"

            if bc_path_flat.exists() and manifest_path_flat.exists():
                return (bc_path_flat, manifest_path_flat)

        # Not found - generate helpful error with formal error code
        search_str = ', '.join(str(p) for p in self.search_paths)
        raise LibraryError("CE3502", lib=lib_path, paths=search_str)

    def load_manifest(self, manifest_path: Path) -> dict:
        """Load and parse .sushilib manifest file.

        Args:
            manifest_path: Path to .sushilib file.

        Returns:
            Parsed manifest dictionary.

        Raises:
            LibraryError: CE3503 if manifest is invalid or malformed.
        """
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
        except json.JSONDecodeError as e:
            raise LibraryError("CE3503", path=str(manifest_path), reason=str(e))

        # Validate required fields
        required = ["sushi_lib_version", "library_name", "platform"]
        for field in required:
            if field not in manifest:
                raise LibraryError("CE3503", path=str(manifest_path),
                                   reason=f"missing required field: {field}")

        # Check platform compatibility
        from backend.platform_detect import get_current_platform
        current_platform = get_current_platform()
        lib_platform = manifest["platform"]

        current_name = "darwin" if current_platform.is_darwin else "linux" if current_platform.is_linux else "unknown"

        if lib_platform != current_name:
            # Use warning code CW3505 for platform mismatch (non-fatal)
            msg = ERR["CW3505"]
            print(f"{msg.code}: {msg.text.format(lib_platform=lib_platform, current_platform=current_name)}")

        return manifest

    def link_library(self, llmod: llvm.ModuleRef, lib_path: str) -> None:
        """Link a custom library into the LLVM module.

        Args:
            llmod: The main LLVM module.
            lib_path: Library path like "lib/mylib".
        """
        # Resolve library files
        bc_path, manifest_path = self.resolve_library(lib_path)

        # Load and validate manifest
        manifest = self.load_manifest(manifest_path)

        # Store manifest for symbol resolution
        lib_name = manifest["library_name"]
        self.loaded_libraries[lib_name] = manifest

        # Link bitcode into module
        # Use preserve=False to allow the main module's definitions to take precedence
        # This handles cases where both library and main program have stdlib runtime functions
        with open(bc_path, 'rb') as f:
            bc_data = f.read()
            try:
                lib_mod = llvm.parse_bitcode(bc_data)
                llmod.link_in(lib_mod, preserve=False)
                print(f"Linked library: {lib_name} ({bc_path})")
            except Exception as e:
                raise LibraryError("CE3507", lib=str(bc_path), reason=str(e))

    def get_library_functions(self, lib_name: str) -> list[dict]:
        """Get public functions from a loaded library.

        Args:
            lib_name: Library name.

        Returns:
            List of function metadata dicts.
        """
        manifest = self.loaded_libraries.get(lib_name)
        if not manifest:
            return []
        return manifest.get("public_functions", [])

    def get_library_structs(self, lib_name: str) -> list[dict]:
        """Get struct definitions from a loaded library.

        Args:
            lib_name: Library name.

        Returns:
            List of struct metadata dicts.
        """
        manifest = self.loaded_libraries.get(lib_name)
        if not manifest:
            return []
        return manifest.get("structs", [])

    def get_library_enums(self, lib_name: str) -> list[dict]:
        """Get enum definitions from a loaded library.

        Args:
            lib_name: Library name.

        Returns:
            List of enum metadata dicts.
        """
        manifest = self.loaded_libraries.get(lib_name)
        if not manifest:
            return []
        return manifest.get("enums", [])

    def get_all_loaded_functions(self) -> list[tuple[str, dict]]:
        """Get all public functions from all loaded libraries.

        Returns:
            List of (library_name, function_info) tuples.
        """
        result = []
        for lib_name, manifest in self.loaded_libraries.items():
            for func in manifest.get("public_functions", []):
                result.append((lib_name, func))
        return result


class TwoPhaseLinker:
    """Implements two-phase linking with symbol deduplication.

    Two-phase linking resolves the "symbol multiply defined" error that occurs
    when both the main program and libraries define the same symbols (e.g.,
    runtime functions like utf8_char_count).

    Phase 1: Extract symbol tables from all modules
    Phase 2: Build dependency graph from entry points
    Phase 3: Resolve symbol conflicts using priority rules
    Phase 4: Merge resolved symbols into final module
    """

    def __init__(self, target_triple: str = "", data_layout: str = "", verbose: bool = False):
        """Initialize two-phase linker.

        Args:
            target_triple: LLVM target triple for the platform.
            data_layout: LLVM data layout string.
            verbose: If True, print detailed linking information.
        """
        self.target_triple = target_triple
        self.data_layout = data_layout
        self.verbose = verbose
        self.modules: list[tuple[llvm.ModuleRef, str, 'SymbolSource']] = []

    def add_main_module(self, module: llvm.ModuleRef, name: str = "main") -> None:
        """Add the main program module.

        Args:
            module: Parsed main program bitcode.
            name: Module name for debugging.
        """
        from backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.MAIN))

    def add_library_module(self, module: llvm.ModuleRef, name: str) -> None:
        """Add a user library module.

        Args:
            module: Parsed library bitcode.
            name: Library name.
        """
        from backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.LIBRARY))

    def add_stdlib_module(self, module: llvm.ModuleRef, name: str) -> None:
        """Add a standard library module.

        Args:
            module: Parsed stdlib bitcode.
            name: Stdlib unit name (e.g., "io/stdio").
        """
        from backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.STDLIB))

    def _find_global_constructors(self) -> set[str]:
        """Find all global constructor functions in the modules.

        LLVM uses @llvm.global_ctors to register initialization functions that
        must run before main(). These are entry points that must be included
        in the reachable set.

        Returns:
            Set of constructor function names.
        """
        import re
        constructors = set()

        # Pattern to match constructor entries in @llvm.global_ctors
        # Format: { i32 priority, void ()* @func_name, i8* null }
        ctor_pattern = re.compile(r'@llvm\.global_ctors.*?@([a-zA-Z_][a-zA-Z0-9_\.]*)')

        for module, name, source in self.modules:
            # Get full IR text and search for global_ctors
            ir_text = str(module)
            for match in ctor_pattern.finditer(ir_text):
                ctor_name = match.group(1)
                constructors.add(ctor_name)
                if self.verbose:
                    print(f"  Found global constructor: {ctor_name} in {name}")

        return constructors

    def link(self, entry_points: list[str] | None = None) -> llvm.ModuleRef:
        """Perform two-phase linking and return merged module.

        Args:
            entry_points: List of entry point symbols (default: ["main"]).

        Returns:
            New LLVM module with deduplicated symbols.
        """
        from backend.symbol_table import extract_symbol_table
        from backend.dependency_graph import build_dependency_graph
        from backend.symbol_resolver import SymbolResolver
        from backend.module_merger import ModuleMerger

        if entry_points is None:
            entry_points = ["main"]

        # Also include global constructors as entry points
        constructors = self._find_global_constructors()
        if constructors:
            entry_points = list(set(entry_points) | constructors)
            if self.verbose:
                print(f"Two-phase linking: Added {len(constructors)} global constructor(s) to entry points")

        if self.verbose:
            print("Two-phase linking: Extracting symbol tables...")

        # Phase 1: Extract symbol tables from all modules
        symbol_tables = []
        for module, name, source in self.modules:
            table = extract_symbol_table(module, name, source)
            symbol_tables.append(table)
            if self.verbose:
                print(f"  {table}")

        # Phase 2: Build dependency graph
        if self.verbose:
            print("Two-phase linking: Building dependency graph...")
        graph = build_dependency_graph(symbol_tables)
        if self.verbose:
            print(f"  {graph}")

        # Compute reachable symbols from entry points
        if self.verbose:
            print(f"Two-phase linking: Finding reachable symbols from {entry_points}...")
        reachable = graph.get_transitive_closure(set(entry_points))
        if self.verbose:
            print(f"  Found {len(reachable)} reachable symbols")

        # Phase 3: Resolve duplicates
        if self.verbose:
            print("Two-phase linking: Resolving symbol conflicts...")
        resolver = SymbolResolver(symbol_tables, verbose=self.verbose)
        resolved = resolver.resolve(reachable)

        if self.verbose:
            conflicts = resolver.get_conflicts()
            if conflicts:
                print(f"  Resolved {len(conflicts)} symbol conflict(s)")

        # Phase 4: Merge into new module
        if self.verbose:
            print("Two-phase linking: Merging symbols into final module...")
        merger = ModuleMerger(self.target_triple, self.data_layout)
        merged_module = merger.merge(resolved, "sushi_linked")

        if self.verbose:
            func_count = len(list(merged_module.functions))
            global_count = len(list(merged_module.global_variables))
            print(f"Two-phase linking: Complete. "
                  f"Final module has {func_count} functions, {global_count} globals")

        return merged_module

    def link_simple(self) -> llvm.ModuleRef:
        """Perform simple linking without full two-phase analysis.

        This is a fallback that uses LLVM's native link_in but handles
        conflicts more gracefully by catching errors and skipping
        duplicate definitions.

        Returns:
            The main module with library symbols linked in.
        """
        if not self.modules:
            raise RuntimeError("No modules to link")

        # Find the main module
        main_module = None
        other_modules = []

        for module, name, source in self.modules:
            from backend.symbol_table import SymbolSource
            if source == SymbolSource.MAIN:
                main_module = module
            else:
                other_modules.append((module, name))

        if main_module is None:
            raise RuntimeError("No main module found")

        # Link other modules into main
        for module, name in other_modules:
            try:
                main_module.link_in(module, preserve=False)
                if self.verbose:
                    print(f"Linked: {name}")
            except Exception as e:
                if self.verbose:
                    print(f"Skipped linking {name}: {e}")
                # Continue - duplicate symbols are expected

        return main_module
