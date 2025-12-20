"""
File utility functions for io/files module.

Provides the standard stdlib interface for function discovery and validation.
This is separate from __init__.py which handles file type methods.
"""
from sushi_lang.semantics.typesys import Type, BuiltinType, ResultType


# List of all file utility functions
FILE_UTILITY_FUNCTIONS = [
    "exists", "is_file", "is_dir", "file_size",
    "remove", "rename", "copy", "mkdir", "rmdir"
]


def is_builtin_files_function(name: str) -> bool:
    """Check if a function name is a built-in files utility function."""
    return name in FILE_UTILITY_FUNCTIONS


def get_builtin_files_function_return_type(func_name: str) -> Type:
    """Get the return type of a built-in files utility function."""
    from sushi_lang.semantics.typesys import EnumType

    if func_name in ["exists", "is_file", "is_dir"]:
        return BuiltinType.BOOL
    elif func_name == "file_size":
        # Return Result<i64, FileError> - FileError enum is defined in predefined_enums
        # For now, we need to fetch FileError from the global enum table during compilation
        # But since this is type inference, we'll use a placeholder approach
        # The actual FileError will be resolved during code generation
        from sushi_lang.semantics.typesys import UnknownType
        file_error = UnknownType("FileError")
        return ResultType(ok_type=BuiltinType.I64, err_type=file_error)
    elif func_name in ["remove", "rename", "copy", "mkdir", "rmdir"]:
        from sushi_lang.semantics.typesys import UnknownType
        file_error = UnknownType("FileError")
        return ResultType(ok_type=BuiltinType.I32, err_type=file_error)
    else:
        raise ValueError(f"Unknown files utility function: {func_name}")


def validate_files_function_call(func_name: str, args: list, reporter, loc) -> None:
    """Validate a files utility function call.

    Args:
        func_name: Name of the function being called
        args: List of argument expressions
        reporter: Error reporter
        loc: Source location for error reporting
    """
    from sushi_lang.internals import errors as er

    # Validate argument count
    if func_name in ["exists", "is_file", "is_dir", "file_size", "remove", "rmdir"]:
        # These take exactly 1 argument (string path)
        if len(args) != 1:
            er.emit(reporter, er.ERR.CE0004, loc,
                   func=func_name, expected=1, got=len(args))
            return
    elif func_name in ["rename", "copy", "mkdir"]:
        # These take exactly 2 arguments
        if len(args) != 2:
            er.emit(reporter, er.ERR.CE0004, loc,
                   func=func_name, expected=2, got=len(args))
            return

    # Argument types are validated in type checking pass
