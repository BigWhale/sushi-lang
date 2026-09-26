"""Sushi Standard Library Build Script"""

import argparse
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
import llvmlite.ir as ir
import llvmlite.binding as llvm

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sushi_lang.backend.platform_detect import get_current_platform


def init_llvm():
    """Initialize LLVM binding."""
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()


def compile_module_to_bc(module: ir.Module, output_path: Path, quiet: bool = False) -> list[str]:
    """Compile LLVM module to bitcode file, and return the symbols it DEFINES.

    The generators are the only authority on these names, and CE5013 needs them to
    refuse an `unsafe external` that reaches for one (#472). They are read where the
    bitcode is written, so the list cannot drift from the artifact.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    mod = llvm.parse_assembly(str(module))

    with open(output_path, 'wb') as f:
        f.write(mod.as_bitcode())

    if not quiet:
        print(f"  → {output_path}")

    return [fn.name for fn in mod.functions if not fn.is_declaration]


@dataclass(frozen=True)
class StdlibBitcodeUnit:
    """One stdlib bitcode unit: its `use` path and the module that generates its IR.

    The linker resolves `use <unit>` to `<unit>.bc`, so the output path and the progress
    label are both read from the unit path and cannot drift from it.
    """

    unit: str
    generator: str

    @property
    def output(self) -> str:
        return f"{self.unit}.bc"


# The BUILD ORDER. A generator module is imported when its row is built.
STDLIB_BITCODE_UNITS: tuple[StdlibBitcodeUnit, ...] = (
    StdlibBitcodeUnit("collections/strings", "sushi_lang.sushi_stdlib.src.collections.strings"),
    StdlibBitcodeUnit("core/primitives", "sushi_lang.backend.types.primitives"),
    StdlibBitcodeUnit("io/files", "sushi_lang.sushi_stdlib.src.io.files"),
    StdlibBitcodeUnit("time", "sushi_lang.sushi_stdlib.src.time"),
    StdlibBitcodeUnit("math", "sushi_lang.sushi_stdlib.src.math"),
    StdlibBitcodeUnit("sys/env", "sushi_lang.sushi_stdlib.src.sys.env"),
    StdlibBitcodeUnit("sys/process", "sushi_lang.sushi_stdlib.src.sys.process"),
    StdlibBitcodeUnit("random", "sushi_lang.sushi_stdlib.src.random"),
    StdlibBitcodeUnit("net/socket", "sushi_lang.sushi_stdlib.src.net"),
)


def build_all(platform_name: str, quiet: bool = False) -> None:
    """Build every stdlib unit for the given platform into dist/{platform_name}/."""
    init_llvm()

    script_dir = Path(__file__).parent.resolve()  # sushi_stdlib/
    platform_dir = script_dir / "dist" / platform_name

    if not quiet:
        print(f"Building stdlib for {platform_name}...")
        print(f"Output directory: {platform_dir}")
        print()

    defined: set[str] = set()
    for row in STDLIB_BITCODE_UNITS:
        if not quiet:
            print(f"Building {row.unit}...")
        module = importlib.import_module(row.generator).generate_module_ir()
        defined.update(compile_module_to_bc(module, platform_dir / row.output, quiet=quiet))

    # Note: core/results and core/maybe use inline emission only
    # They are not built as stdlib units because monomorphizing for
    # all possible user types is impractical.

    from sushi_lang.backend.stdlib_builder import (
        write_build_marker, write_symbol_manifest,
    )
    # The manifest before the marker: the marker is the freshness token, so a build
    # that dies in between leaves no marker and the next compile rebuilds.
    write_symbol_manifest(platform_name, defined)
    write_build_marker(platform_name)


def main():
    """Build all stdlib units for the current or specified platform."""
    parser = argparse.ArgumentParser(description="Sushi Standard Library Build Script")
    parser.add_argument(
        "--platform",
        choices=["darwin", "linux"],
        default=None,
        help="Target platform for output directory (default: auto-detect)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Sushi Standard Library Build Script")
    print("=" * 60)
    print()

    platform = get_current_platform()
    print(f"Detected platform: {platform.triple}")
    print(f"  Architecture: {platform.arch}")
    print(f"  OS: {platform.os}")
    print(f"  Vendor: {platform.vendor}")
    if platform.abi:
        print(f"  ABI: {platform.abi}")
    print()

    if args.platform:
        platform_name = args.platform
    elif platform.is_darwin:
        platform_name = "darwin"
    elif platform.is_linux:
        platform_name = "linux"
    else:
        print(f"ERROR: Unsupported platform: {platform.os}")
        print("       Currently supported platforms: darwin (macOS), linux")
        sys.exit(1)

    build_all(platform_name)

    platform_dir = Path(__file__).parent.resolve() / "dist" / platform_name
    print()
    print("=" * 60)
    print("✓ Stdlib build complete!")
    print(f"  Platform: {platform_name}")
    print(f"  Artifacts: {platform_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
