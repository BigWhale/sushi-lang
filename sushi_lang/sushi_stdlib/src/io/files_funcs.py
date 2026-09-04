"""The <io/files> semantic interface: one table, and the three discovery names.

No IR lives here. FILES_SIGNATURES is the ONE spelling of what each function takes
and answers (#550): the registry's parameter specs, the arity check, the return type,
the Result the instantiate pass interns and the back end's emission all read it, so a
new primitive is one row here and nothing else. The return type and the arity used to
be two if/elif chains over the same names, in this file, beside the name list.
`tests/unit/test_stdlib_signature_tables.py` is the gate.

Two halves. The PATH utilities take a path and answer about the file system. The
DESCRIPTOR layer takes an fd, which is what makes it the layer a `File` struct is
written on top of -- the shape `<net/socket>` gives `net/tcp.sushi`. Inside that,
`fd_pread`/`fd_pwrite` take the offset as an ARGUMENT so the descriptor's position
never moves, while the sequential half moves it and is the wrong answer for two
readers of one descriptor (HANDLES.md, Phases 4 and 5).
"""
from typing import Dict, List

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, Type
from sushi_lang.sushi_stdlib.src.signatures import (
    Signature,
    cstr,
    params_of,
    validate_arity,
)

BOOL, I32, I64, STRING = (BuiltinType.BOOL, BuiltinType.I32, BuiltinType.I64,
                          BuiltinType.STRING)
BYTES = DynamicArrayType(BuiltinType.U8)
STRINGS = DynamicArrayType(BuiltinType.STRING)
MAYBE_STRING = GenericTypeRef("Maybe", (BuiltinType.STRING,))
FILE = "FileError"


FILES_SIGNATURES: Dict[str, Signature] = {
    # The PATH utilities. Asking whether a path exists cannot fail in a way a caller
    # can act on, so the three predicates answer a BARE bool.
    "exists":       Signature(params_of(cstr()), bare=BOOL),
    "is_file":      Signature(params_of(cstr()), bare=BOOL),
    "is_dir":       Signature(params_of(cstr()), bare=BOOL),
    "file_size":    Signature(params_of(cstr()), ok=I64, error=FILE),
    "remove":       Signature(params_of(cstr()), ok=I32, error=FILE),
    "rmdir":        Signature(params_of(cstr()), ok=I32, error=FILE),
    "rename":       Signature(params_of(cstr(), cstr()), ok=I32, error=FILE),
    "copy":         Signature(params_of(cstr(), cstr()), ok=I32, error=FILE),
    "mkdir":        Signature(params_of(cstr(), I32), ok=I32, error=FILE),
    "read_dir":     Signature(params_of(cstr()), ok=STRINGS, error=FILE),
    "mtime":        Signature(params_of(cstr()), ok=I64, error=FILE),
    "ctime":        Signature(params_of(cstr()), ok=I64, error=FILE),
    "mode":         Signature(params_of(cstr()), ok=I32, error=FILE),
    "is_symlink":   Signature(params_of(cstr()), ok=BOOL, error=FILE),
    # The DESCRIPTOR layer, positional half. A descriptor is a bare i32, and an offset
    # is i64 because `off_t` is 64-bit on both supported platforms (probe P6).
    "fd_open":      Signature(params_of(cstr(), I32, I32), ok=I32, error=FILE),
    "fd_pread":     Signature(params_of(I32, I64, I32), ok=BYTES, error=FILE),
    "fd_pwrite":    Signature(params_of(I32, I64, BYTES), ok=I32, error=FILE),
    "fd_dup":       Signature(params_of(I32), ok=I32, error=FILE),
    "fd_close":     Signature(params_of(I32), ok=I32, error=FILE),
    # The sequential half. A `string` crosses as its fat pointer here -- `fd_write_str`
    # takes a string VALUE, not a path, so it is not marshalled to `i8*`.
    "fd_read":      Signature(params_of(I32, I32), ok=BYTES, error=FILE),
    "fd_write":     Signature(params_of(I32, BYTES), ok=I32, error=FILE),
    "fd_write_str": Signature(params_of(I32, STRING), ok=I32, error=FILE),
    # A blank line is Some("") and end of file is None; an empty string can no longer
    # mean both (HANDLES.md, ruling R22).
    "fd_readln":    Signature(params_of(I32), ok=MAYBE_STRING, error=FILE),
    "fd_seek":      Signature(params_of(I32, I64, I32), ok=I64, error=FILE),
    # A BARE bool: asking whether a descriptor is a terminal cannot fail in a way a
    # caller can act on, so there is no error arm to make it carry.
    "fd_isatty":    Signature(params_of(I32), bare=BOOL),
}

FILE_UTILITY_FUNCTIONS: List[str] = list(FILES_SIGNATURES)


def is_builtin_files_function(name: str) -> bool:
    """Check if a function name is a built-in files utility function."""
    return name in FILES_SIGNATURES


def get_builtin_files_function_return_type(func_name: str) -> Type:
    """The declared return type, from the row: a Result, or a bare value."""
    sig = FILES_SIGNATURES.get(func_name)
    if sig is None:
        raise ValueError(f"Unknown files utility function: {func_name}")
    return sig.return_type()


def validate_files_function_call(func_name: str, args: list, reporter, loc) -> None:
    """Check the argument count against the row's own length (CE2009).

    This used to emit CE0004, which is registered as "duplicate struct '{name}'" and
    takes no `func`/`expected`/`got` -- so the message was about the wrong thing and
    none of the parameters reached it.
    """
    validate_arity(func_name, FILES_SIGNATURES, args, reporter, loc)
