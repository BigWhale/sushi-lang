#!/usr/bin/env python3
"""
Sushi Standard Library Build Script

Generates LLVM bitcode (.bc) files from Python stdlib implementations.
Organizes output by target platform for multiplatform support.
"""

import sys
import os
from pathlib import Path
import llvmlite.ir as ir
import llvmlite.binding as llvm

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from stdlib.src.collections import strings
from backend.types import primitives
from stdlib.src.io import stdio
from backend.platform_detect import get_current_platform, TargetPlatform


def init_llvm():
    """Initialize LLVM binding."""
    # llvm.initialize() is deprecated and handled automatically
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()


def create_module(name: str) -> ir.Module:
    """Create a new LLVM module."""
    return ir.Module(name=name)


def compile_module_to_bc(module: ir.Module, output_path: Path):
    """Compile LLVM module to bitcode file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Parse module to LLVM IR
    mod = llvm.parse_assembly(str(module))

    # Write bitcode
    with open(output_path, 'wb') as f:
        f.write(mod.as_bitcode())

    print(f"  → {output_path}")


def build_collections_strings(platform_dir: Path):
    """Build collections/strings unit (platform-agnostic)."""
    print("Building collections/strings...")

    # Use the new standalone IR generator
    from stdlib.src.collections import strings
    module = strings.generate_module_ir()

    output = platform_dir / "collections" / "strings.bc"
    compile_module_to_bc(module, output)


def build_core_primitives(platform_dir: Path):
    """Build core/primitives unit (platform-agnostic)."""
    print("Building core/primitives...")

    # Use the new standalone IR generator
    module = primitives.generate_module_ir()

    output = platform_dir / "core" / "primitives.bc"
    compile_module_to_bc(module, output)


def build_io_stdio(platform_dir: Path, platform: TargetPlatform):
    """Build io/stdio unit (platform-specific).

    This module uses platform-specific stdio handles (darwin vs linux).
    """
    print(f"Building io/stdio (platform: {platform.os})...")

    # Use the new standalone IR generator
    # The module will use platform-specific handles via common.py
    from stdlib.src.io import stdio
    module = stdio.generate_module_ir()

    output = platform_dir / "io" / "stdio.bc"
    compile_module_to_bc(module, output)


def build_io_files(platform_dir: Path):
    """Build io/files unit (platform-agnostic)."""
    print("Building io/files...")

    # Use the new standalone IR generator
    from stdlib.src.io import files
    module = files.generate_module_ir()

    output = platform_dir / "io" / "files.bc"
    compile_module_to_bc(module, output)


def build_time(platform_dir: Path):
    """Build time unit (includes platform-specific nanosleep declarations)."""
    print("Building time...")

    # Use the new standalone IR generator
    from stdlib.src import time
    module = time.generate_module_ir()

    output = platform_dir / "time.bc"
    compile_module_to_bc(module, output)


def build_math(platform_dir: Path):
    """Build math unit (platform-agnostic)."""
    print("Building math...")

    # Use the new standalone IR generator
    from stdlib.src import math
    module = math.generate_module_ir()

    output = platform_dir / "math.bc"
    compile_module_to_bc(module, output)


def build_sys_env(platform_dir: Path):
    """Build sys/env unit (includes platform-specific getenv/setenv declarations)."""
    print("Building sys/env...")

    # Use the new standalone IR generator
    from stdlib.src.sys import env
    module = env.generate_module_ir()

    output = platform_dir / "sys" / "env.bc"
    compile_module_to_bc(module, output)


def build_random(platform_dir: Path):
    """Build random unit (includes platform-specific random declarations)."""
    print("Building random...")

    # Use the new standalone IR generator
    from stdlib.src import random
    module = random.generate_module_ir()

    output = platform_dir / "random.bc"
    compile_module_to_bc(module, output)


def main():
    """Build all stdlib units for the current platform."""
    print("=" * 60)
    print("Sushi Standard Library Build Script")
    print("=" * 60)
    print()

    init_llvm()

    # Determine project root (parent of stdlib directory)
    # This script is at: <project_root>/stdlib/build.py
    script_dir = Path(__file__).parent.resolve()  # stdlib/
    project_root = script_dir.parent  # project root
    stdlib_dist = script_dir / "dist"  # stdlib/dist

    print(f"Project root: {project_root}")
    print(f"Build output: {stdlib_dist}")
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
    if platform.is_darwin:
        platform_name = "darwin"
    elif platform.is_linux:
        platform_name = "linux"
    else:
        print(f"ERROR: Unsupported platform: {platform.os}")
        print("       Currently supported platforms: darwin (macOS), linux")
        sys.exit(1)

    # Create platform-specific output directory
    platform_dir = stdlib_dist / platform_name
    print(f"Building stdlib for {platform_name}...")
    print(f"Output directory: {platform_dir}")
    print()

    # Build platform-agnostic modules
    build_collections_strings(platform_dir)
    build_core_primitives(platform_dir)
    build_io_files(platform_dir)
    build_time(platform_dir)
    build_math(platform_dir)
    build_sys_env(platform_dir)
    build_random(platform_dir)

    # Build platform-specific modules
    build_io_stdio(platform_dir, platform)

    # Note: core/results and core/maybe use inline emission only
    # They are not built as stdlib units because monomorphizing for
    # all possible user types is impractical.

    print()
    print("=" * 60)
    print("✓ Stdlib build complete!")
    print(f"  Platform: {platform_name}")
    print(f"  Artifacts: {platform_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
