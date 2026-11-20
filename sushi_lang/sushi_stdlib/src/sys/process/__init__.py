"""Process control module for Sushi standard library."""

from typing import Dict

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, UnknownType
from sushi_lang.sushi_stdlib.src.signatures import Signature, params_of
from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
from sushi_lang.sushi_stdlib.src.sys.process.functions import generate_exit, generate_run


# The ONE spelling of what each generated `<sys/process>` function takes and answers
# (#550, #798). The other functions of the module are Sushi source
# (`src_sushi/sys/process.sushi`). `run` takes its command as a string VALUE, not as a
# C string.
PROCESS_SIGNATURES: Dict[str, Signature] = {
    "exit":   Signature(params_of(BuiltinType.I32), bare=BuiltinType.BLANK),
    "run":    Signature(params_of(BuiltinType.STRING, DynamicArrayType(BuiltinType.STRING)),
                        ok=UnknownType("ProcessOutput"), error="ProcessError"),
}


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for the generated half of the process module."""
    module = create_stdlib_module("sys.process")

    generate_exit(module)
    generate_run(module)

    return module
