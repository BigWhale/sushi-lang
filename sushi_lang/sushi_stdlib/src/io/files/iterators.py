"""
file iterator module - file.lines() iterator for foreach loops.

This module implements IR generation for file.lines() -> Iterator<string>.
The iterator is used with foreach loops to read file line by line.

REFACTORED: Now uses shared iterator_builders module to eliminate duplication.
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.io.iterator_builders import build_file_lines_iterator


def generate_file_lines(module: ir.Module) -> None:
    """Generate IR for file.lines() -> Iterator<string>.

    Returns an iterator struct that can be used in foreach loops.
    The foreach loop detects the sentinel length value (-1) and uses
    runtime file reading instead of array iteration.

    Args:
        module: The LLVM module to add the function to.
    """
    # Use shared builder - eliminates 75 lines of duplication
    build_file_lines_iterator(module)
