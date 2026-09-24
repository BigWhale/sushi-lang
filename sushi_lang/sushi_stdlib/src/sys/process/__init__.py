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


def validate_process_function_call(name: str, signature) -> None:
    """Validate process function call parameters."""
    from sushi_lang.semantics.typesys import BuiltinType

    if name == 'getcwd':
        if len(signature.params) != 0:
            raise TypeError(f"getcwd() takes no arguments, got {len(signature.params)}")

    elif name == 'run':
        if len(signature.params) != 2:
            raise TypeError(f"run() takes 2 arguments (string cmd, string[] args), got {len(signature.params)}")
        if signature.params[0].param_type != BuiltinType.STRING:
            raise TypeError(f"run() first argument must be string, got {signature.params[0].param_type}")

    elif name == 'chdir':
        if len(signature.params) != 1:
            raise TypeError(f"chdir() takes 1 argument (string path), got {len(signature.params)}")
        if signature.params[0].param_type != BuiltinType.STRING:
            raise TypeError(f"chdir() argument must be string, got {signature.params[0].param_type}")

    elif name == 'exit':
        if len(signature.params) != 1:
            raise TypeError(f"exit() takes 1 argument (i32 code), got {len(signature.params)}")
        if signature.params[0].param_type != BuiltinType.I32:
            raise TypeError(f"exit() argument must be i32, got {signature.params[0].param_type}")

    elif name == 'getpid':
        if len(signature.params) != 0:
            raise TypeError(f"getpid() takes no arguments, got {len(signature.params)}")

    elif name == 'getuid':
        if len(signature.params) != 0:
            raise TypeError(f"getuid() takes no arguments, got {len(signature.params)}")

    else:
        raise ValueError(f"Unknown process function: {name}")


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
