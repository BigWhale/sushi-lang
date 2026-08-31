"""The <io/files> module: the descriptor primitives, and the one builtin File method.

Every other file method is an ordinary extension in `src_sushi/io/fs.sushi`, written
over those primitives. See `is_builtin_file_method` for why `lines()` is not.
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import Type, BuiltinType, IteratorType
import llvmlite.ir as ir
from sushi_lang.internals import errors as er


def generate_module_ir() -> ir.Module:
    """Generate standalone LLVM IR module for file methods."""
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
    from sushi_lang.sushi_stdlib.src.io.files.read import generate_lines
    from sushi_lang.sushi_stdlib.src.io.files import (
        syscalls, stat, read_dir, copy, positional, sequential)

    module = create_stdlib_module("io.files")

    generate_lines(module)

    syscalls.generate_ir(module)
    stat.generate_ir(module)
    read_dir.generate_ir(module)
    copy.generate_ir(module)
    positional.generate_ir(module)
    sequential.generate_ir(module)

    return module


def is_builtin_file_method(method_name: str) -> bool:
    """The ONE method the compiler still defines on a File.

    The other twelve are ordinary extension methods in `src_sushi/io/fs.sushi` now,
    written over the descriptor primitives. `lines()` did not move with them: an
    `Iterator@(T)` is a CURSOR over a contiguous buffer with no `next` to call, so a
    source `lines()` would have to read to EOF before the loop body ran once --
    untenable on a large file and non-terminating on a stdin that never closes. Ruling
    R13 sends the question to Phase 7, where a buffered reader and `Reader.read_line()`
    are both there to design against, and leaves the sentinel iterator untouched until
    then.
    """
    return method_name == "lines"


def validate_builtin_file_method_with_validator(call: MethodCall, reporter: Any,
                                                validator: Any) -> None:
    """Validate the one built-in File method. It takes no arguments."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"File.{call.method}", expected=0, got=len(call.args))


def get_builtin_file_method_return_type(method_name: str) -> Type | None:
    """The return type of the one built-in File method."""
    if method_name == "lines":
        return IteratorType(element_type=BuiltinType.STRING)
    return None
