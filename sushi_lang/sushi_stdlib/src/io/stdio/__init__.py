"""
Built-in extension methods for standard I/O (stdin, stdout, stderr).

This module implements all built-in I/O operations for the Sushi language,
providing methods for reading from standard input and writing to standard output/error.

stdin methods:
- readln(): Read one line from standard input (returns string)
- read(): Read entire input until EOF (returns string)
- read_bytes(i32): Read n bytes from standard input (returns u8[])
- lines(): Create an iterator for reading lines (returns Iterator<string>)

stdout methods:
- write(string): Write string to stdout without newline (returns ~)
- write_bytes(u8[]): Write byte array to stdout (returns ~)

stderr methods:
- write(string): Write string to stderr without newline (returns ~)
- write_bytes(u8[]): Write byte array to stderr (returns ~)

Architecture:
- Standalone IR generation in stdio/ submodules (stdin.py, stdout.py, stderr.py)
- Inline emission fallback for backward compatibility (when use <io/stdio> not present)
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import Type, BuiltinType
import llvmlite.ir as ir
from sushi_lang.internals import errors as er


# ==============================================================================
# Standalone IR Generation (for stdlib module)
# ==============================================================================

def generate_module_ir() -> ir.Module:
    """Generate standalone LLVM IR module for stdio extension methods.

    This generates all stdin, stdout, and stderr methods as external functions
    that can be linked into programs using `use <io/stdio>`.

    Returns:
        An LLVM IR module containing all stdio method implementations.
    """
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

    module = create_stdlib_module("io.stdio")

    # Generate stdin methods
    generate_stdin_readln(module)
    generate_stdin_read(module)
    generate_stdin_read_bytes(module)
    generate_stdin_lines(module)

    # Generate stdout methods
    generate_stdout_write(module)
    generate_stdout_write_bytes(module)

    # Generate stderr methods
    generate_stderr_write(module)
    generate_stderr_write_bytes(module)

    return module


# ==============================================================================
# Validation and Type Checking (used by semantic analyzer)
# ==============================================================================


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

    # Validate argument is an i32 using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != BuiltinType.I32:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="i32", got=str(arg_type))


def _validate_write(call: MethodCall, stream_name: str, reporter: Any, validator: Any = None) -> None:
    """Validate write(string) method call on stdout/stderr."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{stream_name}.write", expected=1, got=len(call.args))
        return

    # Validate argument is a string using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != BuiltinType.STRING:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="string", got=str(arg_type))


def _validate_write_bytes(call: MethodCall, stream_name: str, reporter: Any, validator: Any = None) -> None:
    """Validate write_bytes(u8[]) method call on stdout/stderr."""
    from sushi_lang.semantics.typesys import DynamicArrayType

    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{stream_name}.write_bytes", expected=1, got=len(call.args))
        return

    # Validate argument is a u8[] using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        expected_type = DynamicArrayType(BuiltinType.U8)
        if arg_type is not None and arg_type != expected_type:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="u8[]", got=str(arg_type))


def is_builtin_stdio_method(method_name: str) -> bool:
    """Check if a method name is a built-in stdio method."""
    return method_name in {"readln", "read", "lines", "write", "read_bytes", "write_bytes"}


def validate_builtin_stdio_method_with_validator(call: MethodCall, stdio_type: BuiltinType,
                                                  reporter: Any, validator: Any) -> None:
    """Validate built-in stdio method calls with access to the validator for type checking."""
    method_name = call.method

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
            # Invalid method on stdin
            er.emit(reporter, er.ERR.CE2008, call.loc,
                   name=f"{stdio_type}.{method_name}")
    elif stdio_type == BuiltinType.STDOUT:
        if method_name == "write":
            _validate_write(call, "stdout", reporter, validator)
        elif method_name == "write_bytes":
            _validate_write_bytes(call, "stdout", reporter, validator)
        else:
            # Invalid method on stdout
            er.emit(reporter, er.ERR.CE2008, call.loc,
                   name=f"{stdio_type}.{method_name}")
    elif stdio_type == BuiltinType.STDERR:
        if method_name == "write":
            _validate_write(call, "stderr", reporter, validator)
        elif method_name == "write_bytes":
            _validate_write_bytes(call, "stderr", reporter, validator)
        else:
            # Invalid method on stderr
            er.emit(reporter, er.ERR.CE2008, call.loc,
                   name=f"{stdio_type}.{method_name}")


def get_builtin_stdio_method_return_type(method_name: str, stdio_type: BuiltinType) -> Type | None:
    """Get the return type of a built-in stdio method."""
    from sushi_lang.semantics.typesys import IteratorType, DynamicArrayType

    if method_name in {"readln", "read"}:
        # stdin methods return string
        return BuiltinType.STRING
    elif method_name == "lines":
        # stdin.lines() returns Iterator<string>
        return IteratorType(element_type=BuiltinType.STRING)
    elif method_name == "read_bytes":
        # stdin.read_bytes(n) returns u8[]
        return DynamicArrayType(BuiltinType.U8)
    elif method_name in {"write", "write_bytes"}:
        # stdout/stderr.write() and write_bytes() return blank type
        return BuiltinType.BLANK
    return None
