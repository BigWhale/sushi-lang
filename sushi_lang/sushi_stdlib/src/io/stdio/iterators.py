"""stdin iterator module - stdin.lines() iterator for foreach loops."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.io.iterator_builders import build_stdin_lines_iterator


def generate_stdin_lines(module: ir.Module) -> None:
    """Generate IR for stdin.lines() -> Iterator<string>."""
    # Use shared builder - eliminates 75 lines of duplication
    build_stdin_lines_iterator(module)
