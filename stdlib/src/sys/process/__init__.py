"""Process control module for Sushi standard library.

Provides:
    - getcwd() -> Result<string>: Get current working directory
    - chdir(string path) -> Result<i32>: Change working directory
    - exit(i32 code) -> ~: Terminate process with exit code
    - getpid() -> i32: Get process ID
    - getuid() -> i32: Get user ID
"""

from llvmlite import ir
from stdlib.src.ir_common import create_stdlib_module
from stdlib.src.sys.process.functions import (
    generate_getcwd,
    generate_chdir,
    generate_exit,
    generate_getpid,
    generate_getuid,
)


# Function registry
PROCESS_FUNCTIONS = {
    'getcwd',
    'chdir',
    'exit',
    'getpid',
    'getuid',
}


def is_builtin_process_function(name: str) -> bool:
    """Check if name is a built-in process function."""
    return name in PROCESS_FUNCTIONS


def get_builtin_process_function_return_type(name: str):
    """Get return type for a process function."""
    from semantics.typesys import BuiltinType, ResultType

    if name == 'getcwd':
        from semantics.typesys import UnknownType
        return ResultType(ok_type=BuiltinType.STRING, err_type=UnknownType("ProcessError"))
    elif name == 'chdir':
        from semantics.typesys import UnknownType
        return ResultType(ok_type=BuiltinType.I32, err_type=UnknownType("ProcessError"))
    elif name == 'exit':
        return BuiltinType.BLANK
    elif name == 'getpid':
        return BuiltinType.I32
    elif name == 'getuid':
        return BuiltinType.I32
    else:
        raise ValueError(f"Unknown process function: {name}")


def validate_process_function_call(name: str, signature) -> None:
    """Validate process function call parameters."""
    from semantics.typesys import BuiltinType

    if name == 'getcwd':
        if len(signature.params) != 0:
            raise TypeError(f"getcwd() takes no arguments, got {len(signature.params)}")

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

    # Generate all functions
    generate_getcwd(module)
    generate_chdir(module)
    generate_exit(module)
    generate_getpid(module)
    generate_getuid(module)

    return module
