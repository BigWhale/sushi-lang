"""Linux platform-specific implementations."""

from sushi_lang.sushi_stdlib.src._platform.posix import time
from sushi_lang.sushi_stdlib.src._platform.posix import random
from sushi_lang.sushi_stdlib.src._platform.posix import env
from sushi_lang.sushi_stdlib.src._platform.posix import process

from . import stdio
from . import files
from . import net

__all__ = ['time', 'random', 'env', 'process', 'stdio', 'files', 'net']
