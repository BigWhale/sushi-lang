"""Environment variable module for Sushi standard library."""
from __future__ import annotations
from typing import Dict

from llvmlite import ir

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.signatures import BareOk, Signature, cstr, params_of


# The ONE spelling of what each `<sys/env>` function takes and answers (#550, #798).
# `getenv` answers its Maybe BARE: an absent key is a value, not a failure. `setenv`'s
# generated function answers a bare status, and a negative one is EnvError.InvalidValue.
ENV_SIGNATURES: Dict[str, Signature] = {
    "getenv": Signature(params_of(cstr()),
                        bare=GenericTypeRef("Maybe", (BuiltinType.STRING,))),
    "setenv": Signature(params_of(cstr(), cstr()), ok=BuiltinType.I32, error="EnvError",
                        bare_ok=BareOk(failure="InvalidValue")),
}


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for env functions."""
    from sushi_lang.sushi_stdlib.src.sys.env import functions
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("sys.env")

    functions.generate_getenv(module)
    functions.generate_setenv(module)

    return module
