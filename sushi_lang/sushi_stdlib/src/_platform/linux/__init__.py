"""Linux platform-specific implementations."""

from sushi_lang.sushi_stdlib.src._platform.posix import env
from sushi_lang.sushi_stdlib.src._platform.posix import process

from . import stdio
from . import files
from . import net

__all__ = ['env', 'process', 'stdio', 'files', 'net']
