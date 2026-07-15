#!/usr/bin/env python3
"""
Sushi Standard Library Build Script

Generates LLVM bitcode (.bc) files from Python stdlib implementations.
Organizes output by target platform for multiplatform support.
"""

import argparse
import sys
from pathlib import Path
import llvmlite.ir as ir
import llvmlite.binding as llvm

# Add project root to path (two levels up: sushi_lang/sushi_stdlib -> project root)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sushi_lang.sushi_stdlib.src.collections import strings
from sushi_lang.backend.types import primitives
from sushi_lang.sushi_stdlib.src.io import stdio
from sushi_lang.backend.platform_detect import get_current_platform, TargetPlatform


def init_llvm():
    """Initialize LLVM binding."""
    # llvm.initialize() is deprecated and handled automatically
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()


def create_module(name: str) -> ir.Module:
    """Create a new LLVM module."""
    return ir.Module(name=name)


def compile_module_to_bc(module: ir.Module, output_path: Path, quiet: bool = False):
    """Compile LLVM module to bitcode file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Parse module to LLVM IR
    mod = llvm.parse_assembly(str(module))

    # Write bitcode
    with open(output_path, 'wb') as f:
        f.write(mod.as_bitcode())

    if not quiet:
        print(f"  → {output_path}")


def build_collections_strings(platform_dir: Path, quiet: bool = False):
    """Build collections/strings unit (platform-agnostic)."""
    if not quiet:
        print("Building collections/strings...")

    # Use the new standalone IR generator
    module = strings.generate_module_ir()

    output = platform_dir / "collections" / "strings.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_core_primitives(platform_dir: Path, quiet: bool = False):
    """Build core/primitives unit (platform-agnostic)."""
    if not quiet:
        print("Building core/primitives...")

    # Use the new standalone IR generator
    module = primitives.generate_module_ir()

    output = platform_dir / "core" / "primitives.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_io_stdio(platform_dir: Path, platform: TargetPlatform, quiet: bool = False):
    """Build io/stdio unit (platform-specific).

    This module uses platform-specific stdio handles (darwin vs linux).
    """
    if not quiet:
        print(f"Building io/stdio (platform: {platform.os})...")

    # Use the new standalone IR generator
    # The module will use platform-specific handles via common.py
    module = stdio.generate_module_ir()

    output = platform_dir / "io" / "stdio.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_io_files(platform_dir: Path, quiet: bool = False):
    """Build io/files unit (platform-agnostic)."""
    if not quiet:
        print("Building io/files...")

    # Use the new standalone IR generator
    from sushi_lang.sushi_stdlib.src.io import files
    module = files.generate_module_ir()

    output = platform_dir / "io" / "files.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_time(platform_dir: Path, quiet: bool = False):
    """Build time unit (includes platform-specific nanosleep declarations)."""
    if not quiet:
        print("Building time...")

    # Use the new standalone IR generator
    from sushi_lang.sushi_stdlib.src import time
    module = time.generate_module_ir()

    output = platform_dir / "time.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_math(platform_dir: Path, quiet: bool = False):
    """Build math unit (platform-agnostic)."""
    if not quiet:
        print("Building math...")

    # Use the new standalone IR generator
    from sushi_lang.sushi_stdlib.src import math
    module = math.generate_module_ir()

    output = platform_dir / "math.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_sys_env(platform_dir: Path, quiet: bool = False):
    """Build sys/env unit (includes platform-specific getenv/setenv declarations)."""
    if not quiet:
        print("Building sys/env...")

    # Use the new standalone IR generator
    from sushi_lang.sushi_stdlib.src.sys import env
    module = env.generate_module_ir()

    output = platform_dir / "sys" / "env.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_random(platform_dir: Path, quiet: bool = False):
    """Build random unit (includes platform-specific random declarations)."""
    if not quiet:
        print("Building random...")

    # Use the new standalone IR generator
    from sushi_lang.sushi_stdlib.src import random
    module = random.generate_module_ir()

    output = platform_dir / "random.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_sys_process(platform_dir: Path, quiet: bool = False):
    """Build sys/process unit (includes platform-specific process control declarations)."""
    if not quiet:
        print("Building sys/process...")

    # Use the new standalone IR generator
    from sushi_lang.sushi_stdlib.src.sys import process
    module = process.generate_module_ir()

    output = platform_dir / "sys" / "process.bc"
    compile_module_to_bc(module, output, quiet=quiet)


def build_all(platform_name: str, quiet: bool = False) -> None:
    """Build every stdlib unit for the given platform into dist/{platform_name}/.

    The generated IR reflects the *current* host platform (via common.py); the
    platform_name selects only the output directory. This is the single build
    path shared by the CLI (--build-stdlib) and the compiler's on-the-fly
    auto-builder.

    Args:
        platform_name: Output platform directory name ("darwin" or "linux").
        quiet: Suppress the per-unit progress banner (used by auto-build).
    """
    init_llvm()

    script_dir = Path(__file__).parent.resolve()  # sushi_stdlib/
    platform_dir = script_dir / "dist" / platform_name

    # io/stdio's IR-generator logs the host OS; get the current platform for it.
    platform = get_current_platform()

    if not quiet:
        print(f"Building stdlib for {platform_name}...")
        print(f"Output directory: {platform_dir}")
        print()

    # Build platform-agnostic modules
    build_collections_strings(platform_dir, quiet=quiet)
    build_core_primitives(platform_dir, quiet=quiet)
    build_io_files(platform_dir, quiet=quiet)
    build_time(platform_dir, quiet=quiet)
    build_math(platform_dir, quiet=quiet)
    build_sys_env(platform_dir, quiet=quiet)
    build_sys_process(platform_dir, quiet=quiet)
    build_random(platform_dir, quiet=quiet)

    # Build platform-specific modules
    build_io_stdio(platform_dir, platform, quiet=quiet)

    # Note: core/results and core/maybe use inline emission only
    # They are not built as stdlib units because monomorphizing for
    # all possible user types is impractical.

    # Record the generator-source fingerprint so the compiler can detect
    # staleness and skip a rebuild when nothing changed.
    from sushi_lang.backend.stdlib_builder import write_build_marker
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

    # Detect target platform
    platform = get_current_platform()
    print(f"Detected platform: {platform.triple}")
    print(f"  Architecture: {platform.arch}")
    print(f"  OS: {platform.os}")
    print(f"  Vendor: {platform.vendor}")
    if platform.abi:
        print(f"  ABI: {platform.abi}")
    print()

    # Determine platform directory name
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
