"""Linux platform-specific implementations."""

from sushi_lang.sushi_stdlib.src._platform.posix import process

from . import stdio
from . import files
from . import net

__all__ = ['process', 'stdio', 'files', 'net']
