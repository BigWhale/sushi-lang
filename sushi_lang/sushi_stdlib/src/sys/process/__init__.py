"""Process control module for Sushi standard library."""

from typing import Dict

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, Type, UnknownType
from sushi_lang.sushi_stdlib.src.signatures import Signature, cstr, params_of
from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
from sushi_lang.sushi_stdlib.src.sys.process.functions import (
    generate_getcwd,
    generate_chdir,
    generate_exit,
    generate_getpid,
    generate_getuid,
    generate_run,
)


# The ONE spelling of what each `<sys/process>` function takes and answers (#550,
# #798). `run` takes its command as a string VALUE, not as a C string.
PROCESS_SIGNATURES: Dict[str, Signature] = {
    "getcwd": Signature(ok=BuiltinType.STRING, error="ProcessError"),
    "chdir":  Signature(params_of(cstr()), ok=BuiltinType.I32, error="ProcessError"),
    "exit":   Signature(params_of(BuiltinType.I32), bare=BuiltinType.BLANK),
    "getpid": Signature(bare=BuiltinType.I32),
    "getuid": Signature(bare=BuiltinType.I32),
    "run":    Signature(params_of(BuiltinType.STRING, DynamicArrayType(BuiltinType.STRING)),
                        ok=UnknownType("ProcessOutput"), error="ProcessError"),
}


def is_builtin_process_function(name: str) -> bool:
    """Check if name is a built-in process function."""
    return name in PROCESS_SIGNATURES


def get_builtin_process_function_return_type(name: str) -> Type:
    """The declared return type, from the row."""
    sig = PROCESS_SIGNATURES.get(name)
    if sig is None:
        raise ValueError(f"Unknown process function: {name}")
    return sig.return_type()


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for process control functions."""
    module = create_stdlib_module("sys.process")

    generate_getcwd(module)
    generate_chdir(module)
    generate_exit(module)
    generate_getpid(module)
    generate_getuid(module)
    generate_run(module)

    return module
