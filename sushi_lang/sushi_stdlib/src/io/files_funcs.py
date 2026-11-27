"""The <io/files> semantic interface of the bitcode half: one table.

No IR lives here. FILES_SIGNATURES is the ONE spelling of what each function takes
and answers (#550): the registry's parameter specs, the arity check, the return type,
the Result the instantiate pass interns and the back end's emission all read it.
`tests/unit/test_stdlib_signature_tables.py` is the gate.

Every other function of `<io/files>` is Sushi source (`src_sushi/io/files.sushi`).
`fd_write_str` stays here because it writes the bytes of the string with no copy
(issue #1359).
"""
from typing import Dict, List

from sushi_lang.semantics.typesys import BuiltinType, Type
from sushi_lang.sushi_stdlib.src.signatures import (
    Signature,
    params_of,
)

I32, I64, STRING = BuiltinType.I32, BuiltinType.I64, BuiltinType.STRING
FILE = "FileError"


FILES_SIGNATURES: Dict[str, Signature] = {
    # A descriptor is a bare i32. The string crosses as its fat pointer: it is a VALUE,
    # not a path, so it is not marshalled to `i8*`.
    "fd_write_str": Signature(params_of(I32, STRING), ok=I64, error=FILE),
}

FILE_UTILITY_FUNCTIONS: List[str] = list(FILES_SIGNATURES)


def get_builtin_files_function_return_type(func_name: str) -> Type:
    """The declared return type, from the row: a Result."""
    sig = FILES_SIGNATURES.get(func_name)
    if sig is None:
        raise ValueError(f"Unknown files utility function: {func_name}")
    return sig.return_type()
