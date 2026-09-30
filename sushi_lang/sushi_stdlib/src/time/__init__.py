"""Time module for Sushi standard library."""
from __future__ import annotations
from typing import Dict

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.signatures import BareOk, Signature, params_of


I32, I64 = BuiltinType.I32, BuiltinType.I64
_STATUS = BareOk(failure="Error")

# The ONE spelling of what each `<time>` function takes and answers (#550, #798).
# The clocks answer a bare i64; a negative sleep status is StdError.Error.
TIME_SIGNATURES: Dict[str, Signature] = {
    "sleep":        Signature(params_of(I64), ok=I32, error="StdError", bare_ok=_STATUS),
    "msleep":       Signature(params_of(I64), ok=I32, error="StdError", bare_ok=_STATUS),
    "usleep":       Signature(params_of(I64), ok=I32, error="StdError", bare_ok=_STATUS),
    "nanosleep":    Signature(params_of(I64, I64), ok=I32, error="StdError", bare_ok=_STATUS),
    "now":          Signature(bare=I64),
    "monotonic_ns": Signature(bare=I64),
}


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for time functions."""
    from sushi_lang.sushi_stdlib.src.time import sleep
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("time")

    sleep.generate_nanosleep(module)
    sleep.generate_sleep(module)
    sleep.generate_msleep(module)
    sleep.generate_usleep(module)

    from sushi_lang.sushi_stdlib.src.time import clock
    clock.generate_now(module)
    clock.generate_monotonic_ns(module)

    return module
