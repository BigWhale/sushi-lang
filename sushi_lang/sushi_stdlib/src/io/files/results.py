"""Result construction for the <io/files> generators.

The layout lives one level up in src/results.py, so <net/socket> builds the
same bytes without importing the filesystem module. This re-export keeps the
<io/files> generators reading as they did.
"""
from sushi_lang.sushi_stdlib.src.results import (
    emit_err_result, emit_none, emit_ok_result, emit_some)

__all__ = ["emit_err_result", "emit_none", "emit_ok_result", "emit_some"]
