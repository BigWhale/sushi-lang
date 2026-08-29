"""File utility functions for io/files module."""
from sushi_lang.semantics.typesys import Type, BuiltinType


FILE_UTILITY_FUNCTIONS = [
    "exists", "is_file", "is_dir", "file_size",
    "remove", "rename", "copy", "mkdir", "rmdir", "read_dir"
]


def is_builtin_files_function(name: str) -> bool:
    """Check if a function name is a built-in files utility function."""
    return name in FILE_UTILITY_FUNCTIONS


def get_builtin_files_function_return_type(func_name: str) -> Type:
    """Get the return type of a built-in files utility function."""

    if func_name in ["exists", "is_file", "is_dir"]:
        return BuiltinType.BOOL
    elif func_name == "file_size":
        # Return Result<i64, FileError> - FileError enum is defined in predefined_enums
        # For now, we need to fetch FileError from the global enum table during compilation
        # But since this is type inference, we'll use a placeholder approach
        # The actual FileError will be resolved during code generation
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.I64, UnknownType("FileError")))
    elif func_name in ["remove", "rename", "copy", "mkdir", "rmdir"]:
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.I32, UnknownType("FileError")))
    elif func_name == "read_dir":
        from sushi_lang.semantics.typesys import UnknownType, DynamicArrayType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (DynamicArrayType(BuiltinType.STRING),
                                         UnknownType("FileError")))
    else:
        raise ValueError(f"Unknown files utility function: {func_name}")


def validate_files_function_call(func_name: str, args: list, reporter, loc) -> None:
    """Validate a files utility function call."""
    from sushi_lang.internals import errors as er

    # CE2009, the arity code. This used to emit CE0004, which is registered as
    # "duplicate struct '{name}'" and takes no `func`/`expected`/`got` -- so the
    # message was about the wrong thing and none of the parameters reached it.
    if func_name in ["exists", "is_file", "is_dir", "file_size", "remove", "rmdir",
                     "read_dir"]:
        if len(args) != 1:
            er.emit(reporter, er.ERR.CE2009, loc,
                   name=func_name, expected=1, got=len(args))
            return
    elif func_name in ["rename", "copy", "mkdir"]:
        if len(args) != 2:
            er.emit(reporter, er.ERR.CE2009, loc,
                   name=func_name, expected=2, got=len(args))
            return

