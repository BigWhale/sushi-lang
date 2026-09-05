"""Standard library linking utilities."""
from __future__ import annotations
from typing import Iterable, TYPE_CHECKING
from pathlib import Path

import llvmlite.binding as llvm


if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Program


class StdlibLinker:
    """Manages stdlib module linking."""

    def __init__(self, codegen: LLVMCodegen):
        """Initialize the stdlib linker."""
        self.codegen = codegen
        self.stdlib_dir = Path(__file__).parent.parent / "sushi_stdlib" / "dist"
        self.platform = self._detect_platform()

    def _detect_platform(self) -> str:
        """Detect current platform for stdlib selection."""
        from sushi_lang.backend.platform_detect import get_current_platform

        platform = get_current_platform()
        if platform.is_darwin:
            return "darwin"
        elif platform.is_linux:
            return "linux"
        else:
            return "unknown"

    def extract_stdlib_units(self, program: Program) -> None:
        """Extract stdlib unit imports from the program and store them for conditional codegen.
        """
        stdlib_units = self.codegen.stdlib_units

        # The path AS WRITTEN, and nothing else. Recording an import's parent directory
        # too made every SIBLING module look imported, so `use <io/files>` walked
        # straight through the `io/stdio` gate (#501). A directory import is served by
        # `has_stdlib_unit`, which walks the parents of the name it is ASKED about.
        for use_stmt in program.uses:
            if use_stmt.is_stdlib:
                stdlib_units.add(use_stmt.path)

    def has_stdlib_unit(self, unit_path: str) -> bool:
        """Check if a stdlib unit has been imported."""
        stdlib_units = self.codegen.stdlib_units

        if unit_path in stdlib_units:
            return True

        parts = unit_path.split('/')
        for i in range(1, len(parts)):
            parent = '/'.join(parts[:i])
            if parent in stdlib_units:
                return True

        return False

    def link_stdlib_modules(self, llmod: llvm.ModuleRef,
                            programs: Iterable[Program]) -> None:
        """Link the stdlib .bc files that `programs` import into one LLVM IR module.

        The whole build's programs arrive together and each .bc is linked ONCE. The
        monolithic path gives every unit the SAME module, so linking per unit linked
        one module's globals twice as soon as two units named the same import (#493).
        """
        seen: set[Path] = set()
        bc_files: list[Path] = []
        for program in programs:
            for use_stmt in program.uses:
                if not use_stmt.is_stdlib:
                    continue
                for bc_path in self._resolve_stdlib_unit(use_stmt.path):
                    if bc_path not in seen:
                        seen.add(bc_path)
                        bc_files.append(bc_path)

        for bc_path in bc_files:
            with open(bc_path, 'rb') as f:
                bc_data = f.read()
                try:
                    stdlib_mod = llvm.parse_bitcode(bc_data)
                    llmod.link_in(stdlib_mod, preserve=True)
                except Exception as e:
                    # A stdlib .bc is compiler-produced; if it will not link, the build
                    # cannot succeed. Failing here beats printing a warning to stdout and
                    # continuing into an incoherent `cc` undefined-symbol error.
                    from sushi_lang.internals.errors import raise_internal_error
                    raise_internal_error(
                        "CE0007", detail=f"failed to link stdlib unit {bc_path}: {e}")

    # Virtual stdlib units that don't have .bc files (generic types emitted inline).
    # collections/iter is a bundled Sushi-SOURCE module (see stdlib_registry
    # SOURCE_STDLIB_MODULES): it is merged as a compilation unit and monomorphized
    # inline, so like the generic-provider units it resolves to no .bc.
    _virtual_units = {
        "collections/hashmap",
        "collections/iter",
        "compression/zlib",
        "encoding/msgpack",
        "io/buf",
        "io/contracts",
        "io/fs",
        "io/path",
        "net/dns",
        "net/error",
        "net/ip",
        "net/tcp",
        "net/udp",
        "net/url",
        "toolchain/slib",
    }

    def _resolve_stdlib_unit(self, unit_path: str) -> list[Path]:
        """Resolve stdlib unit path to .bc file(s)."""
        # Virtual units have no .bc files - they're emitted inline during codegen
        if unit_path in self._virtual_units:
            return []

        platform_dir = self.stdlib_dir / self.platform

        dir_path = platform_dir / unit_path
        if dir_path.is_dir():
            bc_files = sorted(dir_path.glob("*.bc"))
            if not bc_files:
                raise FileNotFoundError(
                    f"Stdlib directory exists but contains no .bc files: <{unit_path}>\n"
                    f"Platform: {self.platform}\n"
                    f"The stdlib may not be built. Try running: ./sushic --build-stdlib"
                )
            return bc_files

        bc_path = platform_dir / f"{unit_path}.bc"
        if bc_path.exists():
            return [bc_path]

        available_units = self._list_available_stdlib_units(platform_dir)
        if available_units:
            available_str = ', '.join(f"<{u}>" for u in sorted(available_units))
            raise FileNotFoundError(
                f"Stdlib unit not found: <{unit_path}>\n"
                f"Platform: {self.platform}\n"
                f"Available units: {available_str}\n"
                f"Note: Use angle brackets like 'use <io/stdio>' for stdlib imports\n"
                f"Hint: Try running './sushic --build-stdlib' to build stdlib for your platform"
            )
        else:
            raise FileNotFoundError(
                f"Stdlib unit not found: <{unit_path}>\n"
                f"No stdlib units are available. Try running: ./sushic --build-stdlib"
            )

    def _list_available_stdlib_units(self, stdlib_dist: Path) -> list[str]:
        """List all available stdlib units for error messages."""
        available = []

        available.extend(self._virtual_units)

        for bc_file in stdlib_dist.rglob("*.bc"):
            rel_path = bc_file.relative_to(stdlib_dist)
            unit_path = str(rel_path.with_suffix('')).replace('\\', '/')
            available.append(unit_path)

        for subdir in stdlib_dist.iterdir():
            if subdir.is_dir() and list(subdir.glob("*.bc")):
                available.append(subdir.name)

        return available
