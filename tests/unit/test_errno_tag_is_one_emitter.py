"""errno to an error tag is one emitter: `emit_errno_tag` in `src/errno_tags.py` (#913).

`<io/files>` and `<net/socket>` differ only in the table and the default tag, so each
family is a caller of the one emitter. A stdlib module that reads errno on its own is a copy
of the mapping, and the rule "read errno first, directly after the failed call" is then
written more than once.
"""
from __future__ import annotations

import ast
from pathlib import Path

import llvmlite.ir as ir

from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.backend.runtime.constants import (
    ERRNO_DEFAULT_FILE_ERROR,
    ERRNO_DEFAULT_NET_ERROR,
    errno_to_file_error_table,
    errno_to_net_error_table,
)
from sushi_lang.sushi_stdlib.src import errno_tags
from sushi_lang.sushi_stdlib.src.io.files import errno as file_errno
from sushi_lang.sushi_stdlib.src.net import errno as net_errno

SRC = Path(errno_tags.__file__).parent

# The modules that may read errno through its declaration, each with its reason.
READS_ERRNO = {
    "errno_tags.py": "the one errno-to-tag emitter",
    "io/files/errno.py": "emit_is_eintr asks for EINTR, which is a test and not a tag",
}


def _calls(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called == name:
                return True
    return False


def test_only_the_emitter_reads_errno() -> None:
    readers = {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if _calls(p, "declare_errno_location")
    }
    assert readers == set(READS_ERRNO)


def test_the_family_modules_build_no_mapping() -> None:
    for module in (file_errno, net_errno):
        assert not _calls(Path(module.__file__), "select"), module.__name__


def _emit(emit) -> str:
    module = ir.Module(name="probe")
    func = ir.Function(module, ir.FunctionType(ir.IntType(32), []), name="probe")
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))
    builder.ret(emit(builder, module))
    return str(module)


def test_each_family_is_a_caller_of_the_emitter() -> None:
    linux = get_current_platform().is_linux
    pairs = (
        (file_errno.emit_file_error_tag,
         errno_to_file_error_table(linux), ERRNO_DEFAULT_FILE_ERROR),
        (net_errno.emit_net_error_tag,
         errno_to_net_error_table(linux), ERRNO_DEFAULT_NET_ERROR),
    )
    for family, table, default in pairs:
        direct = _emit(lambda b, m, t=table, d=default: errno_tags.emit_errno_tag(b, m, t, d))
        assert _emit(family) == direct
        assert "select" in direct
