"""Built-in extension methods for file type."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import Type, BuiltinType, IteratorType, DynamicArrayType
import llvmlite.ir as ir
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type


# ===========================
# Standalone IR Generation
# ===========================

def generate_module_ir() -> ir.Module:
    """Generate standalone LLVM IR module for file methods."""
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
    from sushi_lang.sushi_stdlib.src.io.files.read import (
        generate_read, generate_readln, generate_readch, generate_lines
    )
    from sushi_lang.sushi_stdlib.src.io.files.write import (
        generate_write, generate_writeln
    )
    from sushi_lang.sushi_stdlib.src.io.files.binary import (
        generate_read_bytes, generate_write_bytes
    )
    from sushi_lang.sushi_stdlib.src.io.files.seek import (
        generate_seek, generate_tell
    )
    from sushi_lang.sushi_stdlib.src.io.files.status import (
        generate_close, generate_is_open
    )
    from sushi_lang.sushi_stdlib.src.io.files.utils import generate_ir as generate_utils_ir

    # Create module
    module = create_stdlib_module("io.files")

    # Generate read methods (4 methods)
    generate_read(module)
    generate_readln(module)
    generate_readch(module)
    generate_lines(module)

    # Generate write methods (2 methods)
    generate_write(module)
    generate_writeln(module)

    # Generate binary I/O methods (2 methods)
    generate_read_bytes(module)
    generate_write_bytes(module)

    # Generate seeking methods (2 methods)
    generate_seek(module)
    generate_tell(module)

    # Generate status methods (2 methods)
    generate_close(module)
    generate_is_open(module)

    # Generate utility functions (4 functions)
    generate_utils_ir(module)

    return module


# ===========================
# Validation Functions
# ===========================

def _validate_read(call: MethodCall, reporter: Any) -> None:
    """Validate read() method call on file."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.read", expected=0, got=len(call.args))


def _validate_readln(call: MethodCall, reporter: Any) -> None:
    """Validate readln() method call on file."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.readln", expected=0, got=len(call.args))


def _validate_readch(call: MethodCall, reporter: Any) -> None:
    """Validate readch() method call on file."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.readch", expected=0, got=len(call.args))


def _validate_lines(call: MethodCall, reporter: Any) -> None:
    """Validate lines() method call on file."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.lines", expected=0, got=len(call.args))


def _validate_write(call: MethodCall, reporter: Any, validator: Any = None) -> None:
    """Validate write(string) method call on file."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.write", expected=1, got=len(call.args))
        return

    # Validate argument is a string using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != BuiltinType.STRING:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="string", got=display_type(arg_type))


def _validate_writeln(call: MethodCall, reporter: Any, validator: Any = None) -> None:
    """Validate writeln(string) method call on file."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.writeln", expected=1, got=len(call.args))
        return

    # Validate argument is a string using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != BuiltinType.STRING:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="string", got=display_type(arg_type))


def _validate_read_bytes(call: MethodCall, reporter: Any, validator: Any = None) -> None:
    """Validate read_bytes(i32) method call on file."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.read_bytes", expected=1, got=len(call.args))
        return

    # Validate argument is an i32 using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != BuiltinType.I32:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="i32", got=display_type(arg_type))


def _validate_write_bytes(call: MethodCall, reporter: Any, validator: Any = None) -> None:
    """Validate write_bytes(u8[]) method call on file."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.write_bytes", expected=1, got=len(call.args))
        return

    # Validate argument is a u8[] using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        expected_type = DynamicArrayType(BuiltinType.U8)
        if arg_type is not None and arg_type != expected_type:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="u8[]", got=display_type(arg_type))


def _validate_seek(call: MethodCall, reporter: Any, validator: Any = None) -> None:
    """Validate seek(i64, SeekFrom) method call on file."""
    from sushi_lang.semantics.typesys import EnumType

    if len(call.args) != 2:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.seek", expected=2, got=len(call.args))
        return

    # Validate first argument is i64
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != BuiltinType.I64:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="i64", got=display_type(arg_type))

    # Validate second argument is SeekFrom enum
    if validator:
        validator.validate_expression(call.args[1])
        arg_type = validator.infer_expression_type(call.args[1])
        if arg_type is not None:
            if not isinstance(arg_type, EnumType) or arg_type.name != "SeekFrom":
                er.emit(reporter, er.ERR.CE2006, call.args[1].loc,
                       index=2, expected="SeekFrom", got=display_type(arg_type))


def _validate_tell(call: MethodCall, reporter: Any) -> None:
    """Validate tell() method call on file."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.tell", expected=0, got=len(call.args))


def _validate_close(call: MethodCall, reporter: Any) -> None:
    """Validate close() method call on file."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.close", expected=0, got=len(call.args))


def _validate_is_open(call: MethodCall, reporter: Any) -> None:
    """Validate is_open() method call on file."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name="file.is_open", expected=0, got=len(call.args))


# ===========================
# Public API Functions
# ===========================

def is_builtin_file_method(method_name: str) -> bool:
    """Check if a method name is a built-in file method."""
    return method_name in {
        "read", "readln", "readch", "lines",
        "write", "writeln",
        "read_bytes", "write_bytes",
        "seek", "tell",
        "close", "is_open"
    }


def validate_builtin_file_method_with_validator(call: MethodCall, reporter: Any, validator: Any) -> None:
    """Validate built-in file method calls with access to the validator for type checking."""
    method_name = call.method

    # Text reading methods
    if method_name == "read":
        _validate_read(call, reporter)
    elif method_name == "readln":
        _validate_readln(call, reporter)
    elif method_name == "readch":
        _validate_readch(call, reporter)
    elif method_name == "lines":
        _validate_lines(call, reporter)
    # Text writing methods
    elif method_name == "write":
        _validate_write(call, reporter, validator)
    elif method_name == "writeln":
        _validate_writeln(call, reporter, validator)
    # Binary I/O methods
    elif method_name == "read_bytes":
        _validate_read_bytes(call, reporter, validator)
    elif method_name == "write_bytes":
        _validate_write_bytes(call, reporter, validator)
    # Seeking methods
    elif method_name == "seek":
        _validate_seek(call, reporter, validator)
    elif method_name == "tell":
        _validate_tell(call, reporter)
    # Status methods
    elif method_name == "close":
        _validate_close(call, reporter)
    elif method_name == "is_open":
        _validate_is_open(call, reporter)
    else:
        # Invalid method on file
        er.emit(reporter, er.ERR.CE2008, call.loc,
               name=f"file.{method_name}")


def get_builtin_file_method_return_type(method_name: str) -> Type | None:
    """Get the return type of a built-in file method."""
    # Text reading methods return string
    if method_name in {"read", "readln", "readch"}:
        return BuiltinType.STRING
    # lines() returns Iterator<string>
    elif method_name == "lines":
        return IteratorType(element_type=BuiltinType.STRING)
    # Text writing methods return blank type
    elif method_name in {"write", "writeln"}:
        return BuiltinType.BLANK
    # read_bytes() returns u8[]
    elif method_name == "read_bytes":
        return DynamicArrayType(BuiltinType.U8)
    # write_bytes() returns blank type
    elif method_name == "write_bytes":
        return BuiltinType.BLANK
    # seek() and close() return blank type
    elif method_name in {"seek", "close"}:
        return BuiltinType.BLANK
    # tell() returns i64
    elif method_name == "tell":
        return BuiltinType.I64
    # is_open() returns bool
    elif method_name == "is_open":
        return BuiltinType.BOOL
    return None


