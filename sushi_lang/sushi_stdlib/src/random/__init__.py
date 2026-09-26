"""Random module for Sushi standard library."""
from __future__ import annotations
from typing import Dict

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.signatures import Signature, params_of


# The ONE spelling of what each `<random>` function takes and answers (#827).
RANDOM_SIGNATURES: Dict[str, Signature] = {
    'rand':       Signature(bare=BuiltinType.U64),
    'rand_range': Signature(params_of(BuiltinType.I32, BuiltinType.I32), bare=BuiltinType.I32),
    'srand':      Signature(params_of(BuiltinType.U64), bare=BuiltinType.BLANK),
    'rand_f64':   Signature(bare=BuiltinType.F64),
}


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for random functions."""
    from sushi_lang.sushi_stdlib.src.random import generators
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("random")

    generators.generate_rand(module)
    generators.generate_rand_range(module)
    generators.generate_srand(module)
    generators.generate_rand_f64(module)

    return module
