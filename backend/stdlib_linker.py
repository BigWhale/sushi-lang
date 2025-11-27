"""Standard library linking utilities.

Handles extraction of stdlib bitcode files and linking into main module.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from pathlib import Path

import llvmlite.binding as llvm

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.ast import Program


class StdlibLinker:
    """Manages stdlib module linking.

    This class handles platform-specific stdlib bitcode resolution and linking,
    supporting both individual units (e.g., "io/stdio") and directory imports
    (e.g., "io" importing all files in io/).
    """

    def __init__(self, codegen: LLVMCodegen):
        """Initialize the stdlib linker.

        Args:
            codegen: The LLVM code generator instance.
        """
        self.codegen = codegen
        self.stdlib_dir = Path(__file__).parent.parent / "stdlib" / "dist"
        self.platform = self._detect_platform()

    def _detect_platform(self) -> str:
        """Detect current platform for stdlib selection.

        Returns:
            Platform name: "darwin", "linux", or "unknown".

        Raises:
            RuntimeError: If platform is unsupported.
        """
        from backend.platform_detect import get_current_platform

        platform = get_current_platform()
        if platform.is_darwin:
            return "darwin"
        elif platform.is_linux:
            return "linux"
        else:
            return "unknown"

    def extract_stdlib_units(self, program: Program) -> None:
        """Extract stdlib unit imports from the program and store them for conditional codegen.

        This enables the backend to conditionally emit code based on which stdlib units
        are imported. For example, if "core/primitives" is imported, method calls like
        i32.to_str() will emit external function calls instead of inline IR.

        Args:
            program: The program AST containing use statements.
        """
        stdlib_units = self.codegen.stdlib_units

        for use_stmt in program.uses:
            if use_stmt.is_stdlib:
                stdlib_units.add(use_stmt.path)
                # Also add parent units for directory imports
                # e.g., "core/primitives" should also register "core"
                parts = use_stmt.path.split('/')
                for i in range(1, len(parts)):
                    stdlib_units.add('/'.join(parts[:i]))

    def has_stdlib_unit(self, unit_path: str) -> bool:
        """Check if a stdlib unit has been imported.

        Args:
            unit_path: Unit path like "core/primitives" or "collections/strings"

        Returns:
            True if the unit was imported via use <unit> syntax

        Note:
            Supports directory imports. If "collections" is imported,
            then has_stdlib_unit("collections/strings") returns True.
        """
        stdlib_units = self.codegen.stdlib_units

        # Check exact match first
        if unit_path in stdlib_units:
            return True

        # Check if any parent directory of this unit was imported
        # e.g., if "collections" is imported, then "collections/strings" is available
        parts = unit_path.split('/')
        for i in range(1, len(parts)):
            parent = '/'.join(parts[:i])
            if parent in stdlib_units:
                return True

        return False

    def link_stdlib_modules(self, llmod: llvm.ModuleRef, program: Program) -> None:
        """Link stdlib .bc files into the current LLVM IR module.

        Args:
            llmod: The main LLVM module to link into.
            program: The program AST containing use statements.
        """
        # Collect stdlib units to link
        stdlib_units = []
        for use_stmt in program.uses:
            if use_stmt.is_stdlib:
                bc_paths = self._resolve_stdlib_unit(use_stmt.path)
                stdlib_units.extend(bc_paths)

        # Link each stdlib unit
        for bc_path in stdlib_units:
            with open(bc_path, 'rb') as f:
                bc_data = f.read()
                try:
                    stdlib_mod = llvm.parse_bitcode(bc_data)
                    llmod.link_in(stdlib_mod, preserve=True)
                except Exception as e:
                    print(f"Warning: Failed to link stdlib unit {bc_path}: {e}")

    def _resolve_stdlib_unit(self, unit_path: str) -> list[Path]:
        """Resolve stdlib unit path to .bc file(s).

        Supports both individual units and directory imports with platform-specific resolution:
        - "core/primitives" -> [stdlib/dist/darwin/core/primitives.bc]
        - "io" -> [stdlib/dist/darwin/io/stdio.bc, stdlib/dist/darwin/io/files.bc]

        Search order:
        1. Platform-specific path (e.g., dist/darwin/io/stdio.bc)

        Args:
            unit_path: Unit path like "core/primitives" or "io"

        Returns:
            List of paths to .bc files

        Raises:
            FileNotFoundError: If the stdlib unit does not exist or is empty
        """
        platform_dir = self.stdlib_dir / self.platform

        # Check if it's a directory import (platform-specific)
        dir_path = platform_dir / unit_path
        if dir_path.is_dir():
            # Return all .bc files in the directory
            bc_files = sorted(dir_path.glob("*.bc"))
            if not bc_files:
                raise FileNotFoundError(
                    f"Stdlib directory exists but contains no .bc files: <{unit_path}>\n"
                    f"Platform: {self.platform}\n"
                    f"The stdlib may not be built. Try running: python stdlib/build.py"
                )
            return bc_files

        # Check single unit file (platform-specific)
        bc_path = platform_dir / f"{unit_path}.bc"
        if bc_path.exists():
            return [bc_path]

        # Unit not found - provide helpful error message
        available_units = self._list_available_stdlib_units(platform_dir)
        if available_units:
            available_str = ', '.join(f"<{u}>" for u in sorted(available_units))
            raise FileNotFoundError(
                f"Stdlib unit not found: <{unit_path}>\n"
                f"Platform: {self.platform}\n"
                f"Available units: {available_str}\n"
                f"Note: Use angle brackets like 'use <io/stdio>' for stdlib imports\n"
                f"Hint: Try running 'python stdlib/build.py' to build stdlib for your platform"
            )
        else:
            raise FileNotFoundError(
                f"Stdlib unit not found: <{unit_path}>\n"
                f"No stdlib units are available. Try running: python stdlib/build.py"
            )

    def _list_available_stdlib_units(self, stdlib_dist: Path) -> list[str]:
        """List all available stdlib units for error messages.

        Args:
            stdlib_dist: Path to stdlib/dist directory

        Returns:
            List of available unit paths (e.g., ["core/primitives", "io/stdio"])
        """
        available = []

        # Find all .bc files recursively
        for bc_file in stdlib_dist.rglob("*.bc"):
            # Get relative path from stdlib_dist
            rel_path = bc_file.relative_to(stdlib_dist)
            # Remove .bc extension and convert to forward slashes
            unit_path = str(rel_path.with_suffix('')).replace('\\', '/')
            available.append(unit_path)

        # Also list directories (for directory imports like "io")
        for subdir in stdlib_dist.iterdir():
            if subdir.is_dir() and list(subdir.glob("*.bc")):
                available.append(subdir.name)

        return available
