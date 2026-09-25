"""Random module for Sushi standard library."""
from __future__ import annotations
import typing
from typing import Dict

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.signatures import Signature, params_of

if typing.TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


# The ONE spelling of what each `<random>` function takes and answers (#827).
RANDOM_SIGNATURES: Dict[str, Signature] = {
    'rand':       Signature(bare=BuiltinType.U64),
    'rand_range': Signature(params_of(BuiltinType.I32, BuiltinType.I32), bare=BuiltinType.I32),
    'srand':      Signature(params_of(BuiltinType.U64), bare=BuiltinType.BLANK),
    'rand_f64':   Signature(bare=BuiltinType.F64),
}


def is_builtin_random_function(name: str) -> bool:
    """Check if name is a built-in random module function."""
    return name in RANDOM_SIGNATURES


def get_builtin_random_function_return_type(name: str) -> Type:
    """The declared return type, from the row."""
    sig = RANDOM_SIGNATURES.get(name)
    if sig is None:
        raise ValueError(f"Unknown random function: {name}")
    return sig.return_type()


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
