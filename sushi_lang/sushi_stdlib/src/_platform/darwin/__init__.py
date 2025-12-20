"""
Darwin (macOS) platform-specific implementations.

Contains platform-specific external declarations and implementations
for macOS/Darwin systems. Re-exports POSIX-compliant modules.
"""

# Re-export POSIX modules that are identical across platforms
from sushi_lang.sushi_stdlib.src._platform.posix import time
from sushi_lang.sushi_stdlib.src._platform.posix import random
from sushi_lang.sushi_stdlib.src._platform.posix import env
from sushi_lang.sushi_stdlib.src._platform.posix import process

# Platform-specific modules remain in this directory
from . import stdio
from . import files

__all__ = ['time', 'random', 'env', 'process', 'stdio', 'files']
