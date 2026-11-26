"""The <io/files> semantic interface: one table, and the two discovery names.

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

from sushi_lang.semantics.typesys import BuiltinType, Type
from sushi_lang.sushi_stdlib.src.signatures import (
    Signature,
    params_of,
)

# What `use <io/files>` hands on beside its own functions (`StdlibModule.reexports`,
# `docs/design/unit-namespaces.md` section 8.1): the home of `FileError`, which every
# path utility and `fd_*` primitive answers.
REEXPORTS = ("io/error",)

I32, I64, STRING = BuiltinType.I32, BuiltinType.I64, BuiltinType.STRING
FILE = "FileError"


FILES_SIGNATURES: Dict[str, Signature] = {
    # The path utilities, the stat readers, `fd_read`, `fd_pread`, `fd_write`, `fd_pwrite`
    # and `fd_readln` are Sushi source (`src_sushi/io/files.sushi`).
    # The DESCRIPTOR layer. A descriptor is a bare i32. A `string` crosses as its fat
    # pointer here -- `fd_write_str` takes a string VALUE, not a path, so it is not
    # marshalled to `i8*`.
    "fd_write_str": Signature(params_of(I32, STRING), ok=I64, error=FILE),
}

FILE_UTILITY_FUNCTIONS: List[str] = list(FILES_SIGNATURES)


def get_builtin_files_function_return_type(func_name: str) -> Type:
    """The declared return type, from the row: a Result, or a bare value."""
    sig = FILES_SIGNATURES.get(func_name)
    if sig is None:
        raise ValueError(f"Unknown files utility function: {func_name}")
    return sig.return_type()
