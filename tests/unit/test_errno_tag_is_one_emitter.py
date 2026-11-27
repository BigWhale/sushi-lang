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
    ERRNO_DEFAULT_NET_ERROR,
    ERRNO_ENOTDIR,
    errno_to_net_error_table,
)
from sushi_lang.sushi_stdlib.src import errno_tags
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
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


def _emit_err(emit) -> str:
    module = ir.Module(name="probe")
    result_type = get_result_type(ir.IntType(32), get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, []), name="probe")
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))
    builder.ret(emit(builder, module, result_type))
    return str(module)


def test_each_family_is_a_caller_of_the_emitter() -> None:
    linux = get_current_platform().is_linux
    table = errno_to_net_error_table(linux)
    direct = _emit(lambda b, m: errno_tags.emit_errno_tag(b, m, table, ERRNO_DEFAULT_NET_ERROR))
    assert _emit(net_errno.emit_net_error_tag) == direct
    assert "select" in direct

    file_table = file_errno.errno_to_file_error_table()
    file_direct = _emit_err(lambda b, m, r: errno_tags.emit_errno_err_result(
        b, m, r, file_table, file_errno.ERRNO_DEFAULT_FILE_ERROR))
    assert _emit_err(file_errno.emit_errno_err_result) == file_direct
    assert "select" in file_direct


def test_the_file_table_reads_the_platform_errno_values(monkeypatch) -> None:
    files = get_platform_module("files")
    monkeypatch.setattr(files, "ENAMETOOLONG", 900)
    monkeypatch.setattr(files, "ELOOP", 901)
    table = file_errno.errno_to_file_error_table()
    invalid_path = table[ERRNO_ENOTDIR]
    assert table[900] == table[901] == invalid_path
