"""File utility functions for io/files module."""
from sushi_lang.semantics.typesys import Type, BuiltinType


FILE_UTILITY_FUNCTIONS = [
    "exists", "is_file", "is_dir", "file_size",
    "remove", "rename", "copy", "mkdir", "rmdir", "read_dir",
    "mtime", "ctime", "mode", "is_symlink",
    # The DESCRIPTOR layer (HANDLES.md, Phase 4). These take an fd rather than a path,
    # which is what makes them the layer a `File` struct is written on top of -- the
    # same shape `<net/socket>` gives `net/tcp.sushi`. `fd_pread`/`fd_pwrite` take the
    # offset as an argument, so the descriptor's file position never moves.
    "fd_open", "fd_pread", "fd_pwrite", "fd_dup", "fd_close",
    # The SEQUENTIAL half (HANDLES.md, Phase 5). These move the descriptor's own
    # file position, which is what makes them the layer `File` is written on, and
    # what makes them the wrong answer for two readers of one descriptor.
    "fd_read", "fd_write", "fd_write_str", "fd_readln", "fd_seek", "fd_isatty",
]


def is_builtin_files_function(name: str) -> bool:
    """Check if a function name is a built-in files utility function."""
    return name in FILE_UTILITY_FUNCTIONS


def get_builtin_files_function_return_type(func_name: str) -> Type:
    """Get the return type of a built-in files utility function."""

    if func_name in ["exists", "is_file", "is_dir"]:
        return BuiltinType.BOOL
    elif func_name in ["mtime", "ctime"]:
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.I64, UnknownType("FileError")))
    elif func_name == "mode":
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.I32, UnknownType("FileError")))
    elif func_name == "is_symlink":
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.BOOL, UnknownType("FileError")))
    elif func_name == "file_size":
        # Return Result<i64, FileError> - FileError enum is defined in predefined_enums
        # For now, we need to fetch FileError from the global enum table during compilation
        # But since this is type inference, we'll use a placeholder approach
        # The actual FileError will be resolved during code generation
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.I64, UnknownType("FileError")))
    elif func_name in ["fd_pread", "fd_read"]:
        from sushi_lang.semantics.typesys import UnknownType, DynamicArrayType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (DynamicArrayType(BuiltinType.U8),
                                         UnknownType("FileError")))
    elif func_name == "fd_readln":
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.STRING, UnknownType("FileError")))
    elif func_name == "fd_seek":
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.I64, UnknownType("FileError")))
    elif func_name == "fd_isatty":
        # A BARE bool. Asking whether a descriptor is a terminal cannot fail in a way a
        # caller can act on, so there is no error arm to make it carry.
        return BuiltinType.BOOL
    elif func_name in ["fd_open", "fd_pwrite", "fd_dup", "fd_close",
                       "fd_write", "fd_write_str"]:
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType.I32, UnknownType("FileError")))
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
                     "read_dir", "mtime", "ctime", "mode", "is_symlink"]:
        if len(args) != 1:
            er.emit(reporter, er.ERR.CE2009, loc,
                   name=func_name, expected=1, got=len(args))
            return
    elif func_name in ["fd_dup", "fd_close", "fd_readln", "fd_isatty"]:
        if len(args) != 1:
            er.emit(reporter, er.ERR.CE2009, loc,
                   name=func_name, expected=1, got=len(args))
            return
    elif func_name in ["fd_read", "fd_write", "fd_write_str"]:
        if len(args) != 2:
            er.emit(reporter, er.ERR.CE2009, loc,
                   name=func_name, expected=2, got=len(args))
            return
    elif func_name in ["fd_open", "fd_pread", "fd_pwrite", "fd_seek"]:
        if len(args) != 3:
            er.emit(reporter, er.ERR.CE2009, loc,
                   name=func_name, expected=3, got=len(args))
            return
    elif func_name in ["rename", "copy", "mkdir"]:
        if len(args) != 2:
            er.emit(reporter, er.ERR.CE2009, loc,
                   name=func_name, expected=2, got=len(args))
            return

