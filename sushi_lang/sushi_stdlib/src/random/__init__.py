"""Random module for Sushi standard library."""
from __future__ import annotations
import typing
from llvmlite import ir

if typing.TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


def is_builtin_random_function(name: str) -> bool:
    """Check if name is a built-in random module function."""
    return name in {
        'rand',
        'rand_range',
        'srand',
        'rand_f64',
    }


def get_builtin_random_function_return_type(name: str) -> Type:
    """Get the return type for a built-in random function."""
    from sushi_lang.semantics.typesys import BuiltinType

    if name == 'rand':
        return BuiltinType.U64
    elif name == 'rand_range':
        return BuiltinType.I32
    elif name == 'srand':
        return BuiltinType.BLANK  # ~ (void/blank)
    elif name == 'rand_f64':
        return BuiltinType.F64

    raise ValueError(f"Unknown random function: {name}")


def validate_random_function_call(name: str, signature: typing.Any) -> None:
    """Validate a call to a built-in random function."""
    from sushi_lang.semantics.typesys import BuiltinType

    if name == 'rand':
        if len(signature.params) != 0:
            raise TypeError(f"rand expects 0 arguments, got {len(signature.params)}")

    elif name == 'rand_range':
        if len(signature.params) != 2:
            raise TypeError(f"rand_range expects 2 arguments, got {len(signature.params)}")

        param1_type = signature.params[0].type
        param2_type = signature.params[1].type

        if param1_type != BuiltinType.I32:
            raise TypeError(f"rand_range expects i32 for min, got {param1_type}")
        if param2_type != BuiltinType.I32:
            raise TypeError(f"rand_range expects i32 for max, got {param2_type}")

        # TODO: Add compile-time validation that min < max (requires constant evaluation)

    elif name == 'srand':
        if len(signature.params) != 1:
            raise TypeError(f"srand expects 1 argument, got {len(signature.params)}")

        param_type = signature.params[0].type
        if param_type != BuiltinType.U64:
            raise TypeError(f"srand expects u64, got {param_type}")

    elif name == 'rand_f64':
        if len(signature.params) != 0:
            raise TypeError(f"rand_f64 expects 0 arguments, got {len(signature.params)}")


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
