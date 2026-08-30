"""Sushi Standard Library Build Script"""

import argparse
import sys
from pathlib import Path
import llvmlite.ir as ir
import llvmlite.binding as llvm

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sushi_lang.sushi_stdlib.src.collections import strings
from sushi_lang.backend.types import primitives
from sushi_lang.sushi_stdlib.src.io import stdio
from sushi_lang.backend.platform_detect import get_current_platform, TargetPlatform


def init_llvm():
    """Initialize LLVM binding."""
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()


def create_module(name: str) -> ir.Module:
    """Create a new LLVM module."""
    return ir.Module(name=name)


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


def build_collections_strings(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build collections/strings unit (platform-agnostic)."""
    if not quiet:
        print("Building collections/strings...")

    module = strings.generate_module_ir()

    output = platform_dir / "collections" / "strings.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_core_primitives(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build core/primitives unit (platform-agnostic)."""
    if not quiet:
        print("Building core/primitives...")

    module = primitives.generate_module_ir()

    output = platform_dir / "core" / "primitives.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_io_stdio(platform_dir: Path, platform: TargetPlatform, quiet: bool = False) -> list[str]:
    """Build io/stdio unit (platform-specific)."""
    if not quiet:
        print(f"Building io/stdio (platform: {platform.os})...")

    module = stdio.generate_module_ir()

    output = platform_dir / "io" / "stdio.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_io_files(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build io/files unit (platform-agnostic)."""
    if not quiet:
        print("Building io/files...")

    from sushi_lang.sushi_stdlib.src.io import files
    module = files.generate_module_ir()

    output = platform_dir / "io" / "files.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_time(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build time unit (includes platform-specific nanosleep declarations)."""
    if not quiet:
        print("Building time...")

    from sushi_lang.sushi_stdlib.src import time
    module = time.generate_module_ir()

    output = platform_dir / "time.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_math(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build math unit (platform-agnostic)."""
    if not quiet:
        print("Building math...")

    from sushi_lang.sushi_stdlib.src import math
    module = math.generate_module_ir()

    output = platform_dir / "math.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_sys_env(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build sys/env unit (includes platform-specific getenv/setenv declarations)."""
    if not quiet:
        print("Building sys/env...")

    from sushi_lang.sushi_stdlib.src.sys import env
    module = env.generate_module_ir()

    output = platform_dir / "sys" / "env.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_net(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build the net/socket unit (platform-specific socket constants)."""
    if not quiet:
        print("Building net/socket...")

    from sushi_lang.sushi_stdlib.src import net
    module = net.generate_module_ir()

    output = platform_dir / "net" / "socket.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_random(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build random unit (includes platform-specific random declarations)."""
    if not quiet:
        print("Building random...")

    from sushi_lang.sushi_stdlib.src import random
    module = random.generate_module_ir()

    output = platform_dir / "random.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_sys_process(platform_dir: Path, quiet: bool = False) -> list[str]:
    """Build sys/process unit (includes platform-specific process control declarations)."""
    if not quiet:
        print("Building sys/process...")

    from sushi_lang.sushi_stdlib.src.sys import process
    module = process.generate_module_ir()

    output = platform_dir / "sys" / "process.bc"
    return compile_module_to_bc(module, output, quiet=quiet)


def build_all(platform_name: str, quiet: bool = False) -> None:
    """Build every stdlib unit for the given platform into dist/{platform_name}/."""
    init_llvm()

    script_dir = Path(__file__).parent.resolve()  # sushi_stdlib/
    platform_dir = script_dir / "dist" / platform_name

    platform = get_current_platform()

    if not quiet:
        print(f"Building stdlib for {platform_name}...")
        print(f"Output directory: {platform_dir}")
        print()

    defined: set[str] = set()
    defined.update(build_collections_strings(platform_dir, quiet=quiet))
    defined.update(build_core_primitives(platform_dir, quiet=quiet))
    defined.update(build_io_files(platform_dir, quiet=quiet))
    defined.update(build_time(platform_dir, quiet=quiet))
    defined.update(build_math(platform_dir, quiet=quiet))
    defined.update(build_sys_env(platform_dir, quiet=quiet))
    defined.update(build_sys_process(platform_dir, quiet=quiet))
    defined.update(build_random(platform_dir, quiet=quiet))
    defined.update(build_net(platform_dir, quiet=quiet))

    defined.update(build_io_stdio(platform_dir, platform, quiet=quiet))

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
