"""Built-in extension methods for standard I/O (stdin, stdout, stderr)."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import Type, BuiltinType
import llvmlite.ir as ir
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type


def generate_module_ir() -> ir.Module:
    """Generate standalone LLVM IR module for stdio extension methods."""
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
    from sushi_lang.sushi_stdlib.src.io.stdio.stdin import (
        generate_stdin_readln,
        generate_stdin_read,
        generate_stdin_read_bytes
    )
    from sushi_lang.sushi_stdlib.src.io.stdio.stdout import (
        generate_stdout_write,
        generate_stdout_write_bytes
    )
    from sushi_lang.sushi_stdlib.src.io.stdio.stderr import (
        generate_stderr_write,
        generate_stderr_write_bytes
    )
    from sushi_lang.sushi_stdlib.src.io.stdio.iterators import (
        generate_stdin_lines
    )
    from sushi_lang.sushi_stdlib.src.io.stdio.terminal import (
        STANDARD_DESCRIPTORS,
        generate_is_terminal,
    )

    module = create_stdlib_module("io.stdio")

    generate_stdin_readln(module)
    generate_stdin_read(module)
    generate_stdin_read_bytes(module)
    generate_stdin_lines(module)

    generate_stdout_write(module)
    generate_stdout_write_bytes(module)

    generate_stderr_write(module)
    generate_stderr_write_bytes(module)

    # The first method valid on every stream, so it is generated from the stream table
    # rather than once per stream.
    for stream_name in STANDARD_DESCRIPTORS:
        generate_is_terminal(module, stream_name)

    return module


def _validate_readln(call: MethodCall, reporter: Any) -> None:
    """Validate readln() method call on stdin."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="stdin.readln", expected=0, got=len(call.args))


def _validate_read(call: MethodCall, reporter: Any) -> None:
    """Validate read() method call on stdin."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="stdin.read", expected=0, got=len(call.args))


def _validate_lines(call: MethodCall, reporter: Any) -> None:
    """Validate lines() method call on stdin."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="stdin.lines", expected=0, got=len(call.args))


def _validate_read_bytes(call: MethodCall, reporter: Any, validator: Any = None) -> None:
    """Validate read_bytes(i32) method call on stdin."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="stdin.read_bytes", expected=1, got=len(call.args))
        return

    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != BuiltinType.I32:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="i32", got=display_type(arg_type))


def _validate_no_arguments(call: MethodCall, stream_name: str, reporter: Any) -> None:
    """Validate a stream method that takes nothing, on whichever stream it was called."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{stream_name}.{call.method}", expected=0, got=len(call.args))


def _validate_write(call: MethodCall, stream_name: str, reporter: Any, validator: Any = None) -> None:
    """Validate write(string) method call on stdout/stderr."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{stream_name}.write", expected=1, got=len(call.args))
        return

    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != BuiltinType.STRING:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="string", got=display_type(arg_type))


def _validate_write_bytes(call: MethodCall, stream_name: str, reporter: Any, validator: Any = None) -> None:
    """Validate write_bytes(u8[]) method call on stdout/stderr."""
    from sushi_lang.semantics.typesys import DynamicArrayType

    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{stream_name}.write_bytes", expected=1, got=len(call.args))
        return

    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        expected_type = DynamicArrayType(BuiltinType.U8)
        if arg_type is not None and arg_type != expected_type:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="u8[]", got=display_type(arg_type))


def is_builtin_stdio_method(method_name: str) -> bool:
    """Check if a method name is a built-in stdio method."""
    return method_name in {"readln", "read", "lines", "write", "read_bytes",
                           "write_bytes", "is_terminal"}


def validate_builtin_stdio_method_with_validator(call: MethodCall, stdio_type: BuiltinType,
                                                  reporter: Any, validator: Any) -> None:
    """Validate built-in stdio method calls with access to the validator for type checking."""
    method_name = call.method

    # The one method every stream answers. The rest of the table is split -- stdin reads,
    # stdout and stderr write -- so this is checked before the split.
    if method_name == "is_terminal":
        _validate_no_arguments(call, display_type(stdio_type), reporter)
        return

    if stdio_type == BuiltinType.STDIN:
        if method_name == "readln":
            _validate_readln(call, reporter)
        elif method_name == "read":
            _validate_read(call, reporter)
        elif method_name == "lines":
            _validate_lines(call, reporter)
        elif method_name == "read_bytes":
            _validate_read_bytes(call, reporter, validator)
        else:
            er.emit(reporter, er.ERR.CE2008, call.loc,
                   name=f"{display_type(stdio_type)}.{method_name}")
    elif stdio_type == BuiltinType.STDOUT:
        if method_name == "write":
            _validate_write(call, "stdout", reporter, validator)
        elif method_name == "write_bytes":
            _validate_write_bytes(call, "stdout", reporter, validator)
        else:
            er.emit(reporter, er.ERR.CE2008, call.loc,
                   name=f"{display_type(stdio_type)}.{method_name}")
    elif stdio_type == BuiltinType.STDERR:
        if method_name == "write":
            _validate_write(call, "stderr", reporter, validator)
        elif method_name == "write_bytes":
            _validate_write_bytes(call, "stderr", reporter, validator)
        else:
            er.emit(reporter, er.ERR.CE2008, call.loc,
                   name=f"{display_type(stdio_type)}.{method_name}")


def get_builtin_stdio_method_return_type(method_name: str, stdio_type: BuiltinType) -> Type | None:
    """Get the return type of a built-in stdio method."""
    from sushi_lang.semantics.typesys import IteratorType, DynamicArrayType

    if method_name in {"readln", "read"}:
        return BuiltinType.STRING
    elif method_name == "lines":
        return IteratorType(element_type=BuiltinType.STRING)
    elif method_name == "read_bytes":
        return DynamicArrayType(BuiltinType.U8)
    elif method_name in {"write", "write_bytes"}:
        return BuiltinType.BLANK
    elif method_name == "is_terminal":
        return BuiltinType.BOOL
    return None
