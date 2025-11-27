"""
Linux platform-specific implementations.

Contains platform-specific external declarations and implementations
for Linux systems. Re-exports POSIX-compliant modules.
"""

# Re-export POSIX modules that are identical across platforms
from stdlib.src._platform.posix import time
from stdlib.src._platform.posix import random
from stdlib.src._platform.posix import env
from stdlib.src._platform.posix import process

# Platform-specific modules remain in this directory
from . import stdio
from . import files

__all__ = ['time', 'random', 'env', 'process', 'stdio', 'files']
